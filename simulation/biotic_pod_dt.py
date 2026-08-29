"""L4 — Simulation/Model Layer: the Biotic Pod DT (Farquhar/Ball-Berry/PM).

Continuous-subprocess equivalent of the doc's model_runner.py. Each cycle:
  1. Reads current environment (canopy/air temp, RH, CO2, PAR, soil
     moisture) from InfluxDB.
  2. Computes a water-stress factor from soil moisture against the current
     growth stage's nominal/crit_low bands (proxy field-capacity/wilting
     point — a-opdt has no dedicated soil hydraulic properties dataset, so
     this reuses the existing per-stage sensor thresholds).
  3. Solves the coupled Farquhar C4 / Ball-Berry model for net
     photosynthesis (A) and stomatal conductance (gs).
  4. Derives transpiration (E) from gs via the simplified big-leaf PM form.
  5. Integrates A into a running cumulative-carbon total — a lightweight
     stand-in for full source-sink carbon allocation/partitioning, which
     isn't implemented.
  6. Writes model outputs to InfluxDB (asset_simulation) and a residual
     signal (asset_residuals) comparing the model's implied canopy-air
     temperature delta against the sensed value, for future anomaly
     detection / EKF use.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import yaml

from dyon.core.base import LayerBase
from dyon.core.events import DomainEvent

from reactive.growth_stage_tracker import GrowthStageTracker
from simulation.farquhar_c4 import (
    solve_farquhar_ball_berry,
    vapor_pressure_deficit_kpa,
    water_stress_factor,
)
from simulation.penman_monteith import transpiration_mm_per_hour

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import CacheStore, TimeSeriesStore

_REQUIRED_FIELDS = (
    "canopy_temperature", "air_temperature", "relative_humidity",
    "co2", "par", "soil_moisture", "canopy_air_delta",
)

# Empirically scaled so predicted canopy-air delta lands in the same order
# of magnitude as the sensed field under well-watered conditions (see
# config/sensor_profiles.yaml canopy_air_delta nominal band). A coarse
# proxy, not a calibrated energy-balance fit.
_CANOPY_DELTA_GAIN = 0.05


class BioticPodDT(LayerBase):
    layer_name = "simulation"

    def __init__(
        self,
        config: "TwinConfig",
        event_bus: "EventBus",
        *,
        ts_store: "TimeSeriesStore",
        cache: "CacheStore | None" = None,
        profiles_path: str = "config/sensor_profiles.yaml",
        eval_interval: int = 15,
    ):
        super().__init__(config, event_bus)
        self.ts = ts_store
        self.cache = cache
        self.eval_interval = eval_interval

        with open(profiles_path) as f:
            self._profiles: dict = yaml.safe_load(f)

        self._cumulative_carbon_umol_m2 = 0.0
        self._last_tick = time.time()
        self._stage_tracker: GrowthStageTracker | None = None

    async def initialise(self) -> None:
        self._stage_tracker = GrowthStageTracker(self.bus)

    def _water_stress_beta(self, soil_moisture: float, stage: str) -> float:
        band = self._profiles.get("soil_moisture", {}).get("by_stage", {}).get(stage)
        if not band:
            return 1.0
        # nominal proxies field capacity, crit_low proxies wilting point.
        return water_stress_factor(soil_moisture, band["crit_low"], band["nominal"])

    async def evaluate(self) -> None:
        now = time.time()
        dt_seconds = now - self._last_tick
        self._last_tick = now
        stage = self._stage_tracker.current_stage if self._stage_tracker else "germination"

        readings = {f: self.ts.get_latest(f) for f in _REQUIRED_FIELDS}
        if any(readings[f] is None for f in _REQUIRED_FIELDS):
            return

        beta = self._water_stress_beta(readings["soil_moisture"], stage)

        # Recalibrated periodically by the L8 Twin Calibration Agent. Vcmax
        # here is the static go-forward baseline (unlike the EKF's own
        # live Vcmax_eff state, which keeps adapting independently) — see
        # reactive/ekf_estimator.py's module docstring for why the two
        # aren't merged into one update path.
        calibrated_vcmax25 = self.cache.get_latest_cached("calibrated_vcmax25") if self.cache else None
        calibrated_bb_slope = self.cache.get_latest_cached("calibrated_bb_slope_m") if self.cache else None

        result = solve_farquhar_ball_berry(
            leaf_temp_c=readings["canopy_temperature"],
            par_umol_m2_s=readings["par"],
            co2_ppm=readings["co2"],
            air_temp_c=readings["air_temperature"],
            relative_humidity_pct=readings["relative_humidity"],
            water_stress_beta=beta,
            vcmax25_override=float(calibrated_vcmax25) if calibrated_vcmax25 is not None else None,
            bb_slope_m_override=float(calibrated_bb_slope) if calibrated_bb_slope is not None else None,
        )
        vpd = vapor_pressure_deficit_kpa(readings["air_temperature"], readings["relative_humidity"])
        transpiration = transpiration_mm_per_hour(result.stomatal_conductance, vpd)

        self._cumulative_carbon_umol_m2 += max(result.net_assimilation, 0.0) * dt_seconds

        # Lower stomatal conductance -> less evaporative cooling -> canopy
        # warmer relative to air. A coarse, uncalibrated proxy signal, not
        # an energy-balance model (see _CANOPY_DELTA_GAIN docstring above).
        predicted_canopy_air_delta = _CANOPY_DELTA_GAIN / max(result.stomatal_conductance, 1e-3)
        residual_canopy_air_delta = predicted_canopy_air_delta - readings["canopy_air_delta"]

        self.ts.write_point(
            measurement="asset_simulation",
            fields={
                "sim_A": result.net_assimilation,
                "sim_gs": result.stomatal_conductance,
                "sim_E": transpiration,
                "sim_water_stress_beta": beta,
                "sim_cumulative_carbon": self._cumulative_carbon_umol_m2,
            },
            tags={"asset_id": self.config.asset_id, "growth_stage": stage},
        )
        self.ts.write_point(
            measurement="asset_residuals",
            fields={"res_canopy_air_delta": residual_canopy_air_delta},
            tags={"asset_id": self.config.asset_id, "growth_stage": stage},
        )

        await self.bus.publish(
            DomainEvent(
                event_type="model.updated",
                source_layer=self.layer_name,
                source_asset=self.config.asset_id,
                payload={
                    "net_assimilation": result.net_assimilation,
                    "stomatal_conductance": result.stomatal_conductance,
                    "transpiration_mm_hr": transpiration,
                    "water_stress_beta": beta,
                },
            )
        )
        self.log.debug(
            "A=%.2f gs=%.4f E=%.3fmm/hr beta=%.2f (iters=%d)",
            result.net_assimilation, result.stomatal_conductance,
            transpiration, beta, result.iterations,
        )

    async def start(self) -> None:
        self._running = True
        self.log.info("BioticPodDT started (interval=%ds)", self.eval_interval)
        while self._running:
            try:
                await self.evaluate()
            except Exception as exc:
                self.log.error("Model runner cycle error: %s", exc)
            await asyncio.sleep(self.eval_interval)
