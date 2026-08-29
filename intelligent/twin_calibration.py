"""L8 — Twin Calibration Agent: periodic Bayesian recalibration of the
Farquhar/Ball-Berry parameters against buffered EKF residuals.

Per the architecture doc (section 12.3): "weekly Bayesian optimisation
using a Gaussian Process surrogate with Expected Improvement acquisition
function... recalibrates Vcmax, Jmax, Ball-Berry slope m, Rd... by
minimising RMSE vs EKF estimates... within 30-50 evaluations."

Adaptations made here:
  - Only Vcmax25 and the Ball-Berry slope m are treated as independent
    calibration targets; Jmax25 and Rd25 are already tied to Vcmax25 by a
    fixed literature ratio in simulation/farquhar_c4.py, so calibrating
    Vcmax25 recalibrates them too.
  - The EKF's own Vcmax_eff state is a separate, continuously-adapting
    online estimate (see reactive/ekf_estimator.py's docstring) — this
    agent's calibrated Vcmax25 instead becomes L4's (BioticPodDT's) static
    go-forward baseline, avoiding two mechanisms fighting over one value.
  - Historical replay data comes from a rolling in-memory buffer of
    "data_management.cycle_complete" events rather than an InfluxDB range
    query, sidestepping the measurement-blind get_latest() ambiguity noted
    in reactive/health_score.py.
  - Bayesian optimisation is hand-rolled (GaussianProcessRegressor + a
    grid-search Expected Improvement maximiser) since the search space is
    only 2-dimensional — no dedicated BO library dependency needed.
  - "Weekly" becomes a configurable interval (default 5 min); 30-50
    evaluations becomes 20, both scaled down for a live-demo cadence
    rather than a real deployment's calendar.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import TYPE_CHECKING

import numpy as np
import yaml
from scipy.stats import norm
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern

from dyon.core.base import LayerBase
from dyon.core.events import DomainEvent

from simulation.farquhar_c4 import solve_farquhar_ball_berry, water_stress_factor

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import CacheStore, DocumentStore

log = logging.getLogger(__name__)

_VCMAX25_BOUNDS = (30.0, 90.0)
_BB_SLOPE_M_BOUNDS = (3.0, 7.0)
_MIN_SAMPLES = 15
_N_INITIAL_RANDOM = 5
_N_EVALUATIONS = 20    # doc: 30-50; trimmed for a live-demo cadence
_GRID_RESOLUTION = 25  # per-dimension candidate density for the EI maximiser


class TwinCalibrationAgent(LayerBase):
    layer_name = "autonomous"

    def __init__(
        self,
        config: "TwinConfig",
        event_bus: "EventBus",
        *,
        cache: "CacheStore",
        doc_store: "DocumentStore",
        profiles_path: str = "config/sensor_profiles.yaml",
        buffer_size: int = 60,
        calibration_interval: int = 300,
    ):
        super().__init__(config, event_bus)
        self.cache = cache
        self.doc = doc_store
        self.calibration_interval = calibration_interval

        with open(profiles_path) as f:
            self._profiles: dict = yaml.safe_load(f)

        self._buffer: deque[dict] = deque(maxlen=buffer_size)

    async def initialise(self) -> None:
        self.bus.subscribe("data_management.cycle_complete", self._on_cycle_complete)

    async def _on_cycle_complete(self, event: DomainEvent) -> None:
        if event.source_asset != self.config.asset_id:
            return
        self._buffer.append(dict(event.payload))

    def _objective(self, vcmax25: float, bb_slope_m: float) -> float:
        """Normalised RMSE between model predictions and buffered EKF estimates."""
        errors_a: list[float] = []
        errors_gs: list[float] = []
        for sample in self._buffer:
            band = self._profiles.get("soil_moisture", {}).get("by_stage", {}).get(sample["growth_stage"])
            if not band:
                continue
            beta = water_stress_factor(sample["soil_moisture"], band["crit_low"], band["nominal"])
            result = solve_farquhar_ball_berry(
                leaf_temp_c=sample["canopy_temperature"],
                par_umol_m2_s=sample["par"],
                co2_ppm=sample["co2"],
                air_temp_c=sample["air_temperature"],
                relative_humidity_pct=sample["relative_humidity"],
                water_stress_beta=beta,
                vcmax25_override=vcmax25,
                bb_slope_m_override=bb_slope_m,
            )
            errors_a.append(result.net_assimilation - sample["ekf_net_assimilation"])
            errors_gs.append(result.stomatal_conductance - sample["ekf_stomatal_conductance"])

        if not errors_a:
            return float("inf")

        rmse_a = float(np.sqrt(np.mean(np.square(errors_a))))
        rmse_gs = float(np.sqrt(np.mean(np.square(errors_gs))))
        # A is O(10-30), gs is O(0.1-0.3) -- normalise so neither dominates.
        return rmse_a / 20.0 + rmse_gs / 0.2

    def _calibrate(self) -> tuple[float, float, float]:
        rng = np.random.default_rng()
        x_observed: list[list[float]] = []
        y_observed: list[float] = []

        for _ in range(_N_INITIAL_RANDOM):
            vcmax = float(rng.uniform(*_VCMAX25_BOUNDS))
            slope = float(rng.uniform(*_BB_SLOPE_M_BOUNDS))
            x_observed.append([vcmax, slope])
            y_observed.append(self._objective(vcmax, slope))

        vcmax_grid = np.linspace(*_VCMAX25_BOUNDS, _GRID_RESOLUTION)
        slope_grid = np.linspace(*_BB_SLOPE_M_BOUNDS, _GRID_RESOLUTION)
        candidates = np.array([[v, s] for v in vcmax_grid for s in slope_grid])

        gp = GaussianProcessRegressor(kernel=Matern(nu=2.5), normalize_y=True, n_restarts_optimizer=2)

        for _ in range(_N_EVALUATIONS - _N_INITIAL_RANDOM):
            gp.fit(np.array(x_observed), np.array(y_observed))
            mu, sigma = gp.predict(candidates, return_std=True)
            best_so_far = min(y_observed)

            improvement = best_so_far - mu
            with np.errstate(divide="ignore", invalid="ignore"):
                z = np.where(sigma > 0, improvement / sigma, 0.0)
                ei = np.where(sigma > 0, improvement * norm.cdf(z) + sigma * norm.pdf(z), 0.0)

            next_point = candidates[int(np.argmax(ei))]
            next_objective = self._objective(float(next_point[0]), float(next_point[1]))
            x_observed.append([float(next_point[0]), float(next_point[1])])
            y_observed.append(next_objective)

        best_idx = int(np.argmin(y_observed))
        return x_observed[best_idx][0], x_observed[best_idx][1], y_observed[best_idx]

    async def _run_calibration(self) -> None:
        if len(self._buffer) < _MIN_SAMPLES:
            self.log.debug(
                "Skipping calibration: %d buffered samples (need %d)",
                len(self._buffer), _MIN_SAMPLES,
            )
            return

        best_vcmax, best_slope, best_objective = await asyncio.to_thread(self._calibrate)

        self.cache.set_latest("calibrated_vcmax25", best_vcmax)
        self.cache.set_latest("calibrated_bb_slope_m", best_slope)
        self.doc.log_event(
            "twin_calibration",
            {
                "vcmax25": best_vcmax,
                "bb_slope_m": best_slope,
                "objective": best_objective,
                "n_samples": len(self._buffer),
            },
            severity="info",
        )
        self.log.info(
            "Calibration complete: Vcmax25=%.2f BB_slope_m=%.2f objective=%.4f (n=%d)",
            best_vcmax, best_slope, best_objective, len(self._buffer),
        )

    async def start(self) -> None:
        self._running = True
        self.log.info(
            "TwinCalibrationAgent started (interval=%ds, min_samples=%d)",
            self.calibration_interval, _MIN_SAMPLES,
        )
        while self._running:
            try:
                await self._run_calibration()
            except Exception as exc:
                self.log.error("Twin calibration cycle error: %s", exc)
            await asyncio.sleep(self.calibration_interval)
