"""L5 EKF Plant State Estimator: fuses the Farquhar/Ball-Berry/PM model with
soil-moisture and canopy-air-delta sensor observations into a 5-state
plant-soil vector: [SW, Vcmax_eff, A, gs, E].

Design notes (read before touching the numbers):

- SW and Vcmax_eff are the only components with genuine independent
  dynamics: SW follows a simple water-balance draw-down from transpiration;
  Vcmax_eff is a slow random walk standing in for the L8 Twin Calibration
  Agent's future recalibration. A, gs, E are near-instantaneous algebraic
  responses to [SW, Vcmax_eff] and current environmental forcing via the
  coupled Farquhar-BB model. They're still carried as state components —
  per the architecture doc's literal "estimate latent states: A, gs, E,
  SW, Vcmax" — but their process noise represents model-structural
  uncertainty rather than an independent physical stochastic driver.

- The transition and observation Jacobians (F, H) are computed by
  numerical (finite-difference) differentiation of the actual nonlinear
  functions, not hand-derived analytically. Step sizes are scaled to each
  state component's magnitude, since SW (~0.1-0.4) and Vcmax (~60) differ
  by orders of magnitude.

- Observations are two channels: soil_moisture (direct noisy observation
  of SW) and canopy_air_delta (Tc - Ta). The canopy channel is modelled
  CWSI-style in simulation/canopy_temperature.py: the stage's well-watered
  nominal when conductance is at its unstressed value, rising towards the
  stage's dry limit as conductance falls. An earlier k/gs proxy could only
  be positive while the sensors report Tc - Ta < 0 for a well-watered crop,
  which pinned confidence at zero in nominal operation.

- Either observation may be missing. The update then uses whichever
  channels are present; with none, the filter propagates the model
  prediction alone and its covariance grows. This is the model-substitution
  behaviour NFR3 asks for, instead of skipping the cycle.

- Process noise Q is hand-set (model-structural uncertainty terms).
  Observation noise R comes from config/sensor_profiles.yaml's noise_std.

- Confidence and the gate latch. `confidence` maps the EMA-smoothed NIS to
  [0, 1]. Innovation statistics alone cannot tell a slowly biased sensor
  from a changed plant: after a confidence collapse the filter absorbs a
  constant bias into its state within a few dozen cycles and confidence
  recovers while the estimate is wrong. So a collapse latches the gate:
  once confidence has sat at zero for _LATCH_CYCLES consecutive updates
  (after a warm-up), `gate_confidence` reports 0 until a human clears the
  latch with clear_latch(). The autonomy gate reads `gate_confidence`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from simulation.canopy_temperature import (
    DEFAULT_DELTA_DRY,
    DEFAULT_DELTA_WW,
    expected_canopy_air_delta,
)
from simulation.farquhar_c4 import (
    BB_SLOPE_M as _BB_SLOPE_M_DEFAULT,
    VCMAX25 as _VCMAX25_DEFAULT,
    solve_farquhar_ball_berry,
    vapor_pressure_deficit_kpa,
    water_stress_factor,
)
from simulation.penman_monteith import transpiration_mm_per_hour

_N_STATE = 5  # [SW, Vcmax_eff, A, gs, E]
_ROOT_ZONE_DEPTH_MM = 300.0  # ~30cm, matches the framework paper's soil probe depth

_REL_STEP = 1e-4
_ABS_STEP_FLOOR = 1e-6

_N_OBS_CHANNELS = 2        # soil_moisture, canopy_air_delta
_NIS_EMA_ALPHA = 0.2
_NIS_TOLERANCE_FACTOR = 3.0  # confidence hits 0 at (1 + factor)x the expected NIS

_LATCH_WARMUP_UPDATES = 20   # broad initial priors settle first
_LATCH_CYCLES = 3            # consecutive zero-confidence updates that latch the gate
_CLEAR_GRACE_UPDATES = 20    # after a human clears the gate, updates allowed to recover
_RECOVERED_CONFIDENCE = 0.60


@dataclass
class EKFForcing:
    par_umol_m2_s: float
    air_temp_c: float
    canopy_temp_c: float
    relative_humidity_pct: float
    co2_ppm: float
    stage_field_capacity: float   # sensor_profiles.yaml soil_moisture.by_stage[stage].nominal
    stage_wilting_point: float    # sensor_profiles.yaml soil_moisture.by_stage[stage].crit_low
    dt_hours: float
    stage_delta_ww: float = DEFAULT_DELTA_WW    # canopy_air_delta.by_stage[stage].nominal
    stage_delta_dry: float = DEFAULT_DELTA_DRY  # canopy_air_delta.by_stage[stage].crit_high


def confidence_from_nis(nis: float, dof: float) -> float:
    """Map a (smoothed) NIS with `dof` degrees of freedom to [0, 1]."""
    excess = max(0.0, nis - dof)
    return float(max(0.0, 1.0 - excess / (_NIS_TOLERANCE_FACTOR * dof)))


class EKFPlantStateEstimator:
    def __init__(
        self,
        *,
        soil_moisture_noise_std: float = 0.005,
        canopy_air_delta_noise_std: float = 0.10,
        initial_soil_moisture: float = 0.28,
    ):
        self.x = np.array(
            [initial_soil_moisture, _VCMAX25_DEFAULT, 0.0, 0.05, 0.0], dtype=float
        )
        # Broad, uninformative initial priors — expected to tighten within
        # the first several cycles as the filter converges.
        self.P = np.diag([0.01, 100.0, 100.0, 0.05, 1.0])
        self.Q = np.diag([1e-6, 1e-2, 0.5, 1e-4, 1e-4])
        self.R = np.diag([soil_moisture_noise_std ** 2, canopy_air_delta_noise_std ** 2])

        # Recalibrated periodically by the L8 Twin Calibration Agent. Vcmax_eff
        # is a live filter state, so calibration only touches the Ball-Berry
        # slope here, to avoid two mechanisms fighting over the same variable.
        self.bb_slope_m = _BB_SLOPE_M_DEFAULT

        # Innovation-based self-consistency (NIS), smoothed by an EMA because a
        # single step's NIS is chi-squared distributed with mean equal to the
        # number of observed channels even under perfect tracking.
        self.last_mahalanobis_distance: float = 0.0
        self._nis_ema: float = float(_N_OBS_CHANNELS)
        self._dof_ema: float = float(_N_OBS_CHANNELS)
        self.last_observed_channels: int = 0

        # Gate latch (see module docstring).
        self._updates = 0
        self._zero_streak = 0
        self.gate_latched: bool = False
        self.latch_count: int = 0
        self._awaiting_recovery = False
        self._recovery_wait = 0

    # ── Models ──────────────────────────────────────────────────────────────

    def predict_next(self, x: np.ndarray, forcing: EKFForcing) -> np.ndarray:
        """Public wrapper around the process model (no state mutation)."""
        return self._transition(x, forcing)

    def observe(self, x: np.ndarray, forcing: EKFForcing) -> np.ndarray:
        """Public wrapper around the observation model: [soil_moisture, canopy_air_delta]."""
        return self._observe(x, forcing)

    def _solve(self, forcing: EKFForcing, beta: float, vcmax25: float):
        return solve_farquhar_ball_berry(
            leaf_temp_c=forcing.canopy_temp_c,
            par_umol_m2_s=forcing.par_umol_m2_s,
            co2_ppm=forcing.co2_ppm,
            air_temp_c=forcing.air_temp_c,
            relative_humidity_pct=forcing.relative_humidity_pct,
            water_stress_beta=beta,
            vcmax25_override=vcmax25,
            bb_slope_m_override=self.bb_slope_m,
        )

    def _transition(self, x: np.ndarray, forcing: EKFForcing) -> np.ndarray:
        sw, vcmax, _a, _gs, e_prev = x
        sw_next = sw - (e_prev * forcing.dt_hours) / _ROOT_ZONE_DEPTH_MM
        vcmax_next = vcmax  # random walk; Q carries the drift uncertainty

        beta = water_stress_factor(sw_next, forcing.stage_wilting_point, forcing.stage_field_capacity)
        result = self._solve(forcing, beta, vcmax_next)
        vpd = vapor_pressure_deficit_kpa(forcing.air_temp_c, forcing.relative_humidity_pct)
        e_next = transpiration_mm_per_hour(result.stomatal_conductance, vpd)

        return np.array([sw_next, vcmax_next, result.net_assimilation, result.stomatal_conductance, e_next])

    def _observe(self, x: np.ndarray, forcing: EKFForcing) -> np.ndarray:
        sw, vcmax, _a, gs, _e = x
        gs_potential = self._solve(forcing, 1.0, vcmax).stomatal_conductance
        delta = expected_canopy_air_delta(gs, gs_potential, forcing.stage_delta_ww, forcing.stage_delta_dry)
        return np.array([sw, delta])

    @staticmethod
    def _numerical_jacobian(fn, x: np.ndarray) -> np.ndarray:
        n_out = len(fn(x))
        jac = np.zeros((n_out, len(x)))
        for i in range(len(x)):
            step = max(_ABS_STEP_FLOOR, abs(x[i]) * _REL_STEP)
            dx = np.zeros(len(x))
            dx[i] = step
            jac[:, i] = (fn(x + dx) - fn(x - dx)) / (2 * step)
        return jac

    # ── Filter step ─────────────────────────────────────────────────────────

    def step(
        self,
        forcing: EKFForcing,
        soil_moisture_obs: float | None = None,
        canopy_air_delta_obs: float | None = None,
    ) -> None:
        """Predict, then update with whichever observations are present.

        With no observation at all the prediction stands and P grows: the
        estimate degrades honestly instead of the cycle being skipped.
        """
        x_pred = self._transition(self.x, forcing)
        f_jacobian = self._numerical_jacobian(lambda xx: self._transition(xx, forcing), self.x)
        p_pred = f_jacobian @ self.P @ f_jacobian.T + self.Q

        observed = [
            i for i, v in enumerate((soil_moisture_obs, canopy_air_delta_obs))
            if v is not None and np.isfinite(v)
        ]
        self.last_observed_channels = len(observed)
        if not observed:
            self.x, self.P = x_pred, p_pred
            return

        z_full = np.array([
            soil_moisture_obs if soil_moisture_obs is not None else np.nan,
            canopy_air_delta_obs if canopy_air_delta_obs is not None else np.nan,
        ])
        idx = np.array(observed)
        h_full = self._numerical_jacobian(lambda xx: self._observe(xx, forcing), x_pred)
        h_jacobian = h_full[idx, :]
        innovation = z_full[idx] - self._observe(x_pred, forcing)[idx]
        r = self.R[np.ix_(idx, idx)]
        s = h_jacobian @ p_pred @ h_jacobian.T + r
        s_inv = np.linalg.inv(s)
        kalman_gain = p_pred @ h_jacobian.T @ s_inv

        self.x = x_pred + kalman_gain @ innovation
        self.P = (np.eye(_N_STATE) - kalman_gain @ h_jacobian) @ p_pred

        nis = float(innovation.T @ s_inv @ innovation)
        self.last_mahalanobis_distance = float(np.sqrt(max(0.0, nis)))
        # Smooth NIS and its expected value together, so a cycle with one
        # channel missing is judged against one degree of freedom, not two.
        self._nis_ema = _NIS_EMA_ALPHA * nis + (1 - _NIS_EMA_ALPHA) * self._nis_ema
        self._dof_ema = _NIS_EMA_ALPHA * len(observed) + (1 - _NIS_EMA_ALPHA) * self._dof_ema

        self._updates += 1
        self._update_latch()

    def _update_latch(self) -> None:
        if self._updates <= _LATCH_WARMUP_UPDATES or self.gate_latched:
            return
        if self._awaiting_recovery:
            # Just cleared: the smoothed NIS needs a few cycles to decay even
            # once the fault is fixed. Wait for confidence to recover, and
            # re-latch only if it has not done so within the grace period.
            if self.confidence >= _RECOVERED_CONFIDENCE:
                self._awaiting_recovery = False
            else:
                self._recovery_wait += 1
                if self._recovery_wait >= _CLEAR_GRACE_UPDATES:
                    self._latch()
            return
        self._zero_streak = self._zero_streak + 1 if self.confidence <= 0.0 else 0
        if self._zero_streak >= _LATCH_CYCLES:
            self._latch()

    def _latch(self) -> None:
        self.gate_latched = True
        self.latch_count += 1
        self._awaiting_recovery = False

    def clear_latch(self) -> None:
        """Human acknowledgement: re-arm the gate.

        Confidence then has _CLEAR_GRACE_UPDATES updates to recover above
        _RECOVERED_CONFIDENCE; if it does not, the gate latches again.
        """
        self.gate_latched = False
        self._zero_streak = 0
        self._awaiting_recovery = True
        self._recovery_wait = 0

    # ── Read-outs ───────────────────────────────────────────────────────────

    @property
    def soil_moisture(self) -> float:
        return float(self.x[0])

    @property
    def vcmax_eff(self) -> float:
        return float(self.x[1])

    @property
    def net_assimilation(self) -> float:
        return float(self.x[2])

    @property
    def stomatal_conductance(self) -> float:
        return float(self.x[3])

    @property
    def transpiration(self) -> float:
        return float(self.x[4])

    @property
    def confidence(self) -> float:
        """
        Filter self-consistency in [0, 1] from the EMA-smoothed NIS: 1.0 while
        the smoothed NIS is at or below its expected value, falling linearly to
        0 at (1 + _NIS_TOLERANCE_FACTOR)x that value. It measures whether the
        model explains the observations, not whether the plant is healthy.
        """
        return confidence_from_nis(self._nis_ema, max(1.0, self._dof_ema))

    @property
    def gate_confidence(self) -> float:
        """What the autonomy gate reads: 0 while latched, else `confidence`."""
        return 0.0 if self.gate_latched else self.confidence

    @property
    def variances(self) -> dict[str, float]:
        return {
            "soil_moisture": float(self.P[0, 0]),
            "vcmax_eff": float(self.P[1, 1]),
            "net_assimilation": float(self.P[2, 2]),
            "stomatal_conductance": float(self.P[3, 3]),
            "transpiration": float(self.P[4, 4]),
        }
