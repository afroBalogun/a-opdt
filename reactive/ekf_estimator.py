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
  functions, not hand-derived analytically. Standard practice for an EKF
  wrapped around a model as algebraically involved as the coupled
  Farquhar/Ball-Berry stack, and avoids an error-prone manual derivation.
  Step sizes are scaled to each state component's magnitude rather than
  one fixed absolute epsilon, since SW (~0.1-0.4) and Vcmax (~60) differ
  by orders of magnitude.

- Observations are deliberately limited to two channels: soil_moisture
  (direct noisy observation of SW) and canopy_air_delta (a stomatal-
  closure proxy for gs, using the same proxy relationship as
  simulation/biotic_pod_dt.py's residual calculation, for consistency).
  Fusing all AS7265x/7-in-1 channels the doc mentions would need an
  observation model for each one — deferred; two well-justified channels
  keep this auditable.

- Process noise Q is hand-set (model-structural uncertainty terms).
  Observation noise R comes directly from config/sensor_profiles.yaml's
  noise_std for soil_moisture and canopy_air_delta, so the filter's trust
  in each sensor matches its documented characterisation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

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
_CANOPY_DELTA_GAIN = 0.05    # same proxy constant as simulation/biotic_pod_dt.py

_REL_STEP = 1e-4
_ABS_STEP_FLOOR = 1e-6

_OBSERVATION_DOF = 2       # soil_moisture, canopy_air_delta
_NIS_EMA_ALPHA = 0.2
_NIS_TOLERANCE_FACTOR = 3.0  # confidence hits 0 at (1 + factor)x the expected NIS


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

        # Recalibrated periodically by the L8 Twin Calibration Agent (Bayesian
        # optimisation against buffered EKF residuals) — Vcmax_eff is instead
        # a live filter state (see module docstring), so calibration only
        # touches the Ball-Berry slope here to avoid two mechanisms fighting
        # over the same variable.
        self.bb_slope_m = _BB_SLOPE_M_DEFAULT

        # Innovation-based filter self-consistency (NIS: Normalized
        # Innovation Squared), refreshed each step() — the standard Kalman
        # filter "am I still tracking reality" diagnostic, reused by the L8
        # escalation protocol as an honest confidence signal rather than an
        # invented one. Smoothed via EMA since a single step's Mahalanobis
        # distance is noisy by construction even under perfectly nominal
        # tracking (d^2 ~ chi-squared with _OBSERVATION_DOF degrees of
        # freedom, mean _OBSERVATION_DOF, not 0) — instantaneous values
        # would make "confidence" look poor even when nothing is wrong.
        self.last_mahalanobis_distance: float = 0.0
        self._nis_ema: float = float(_OBSERVATION_DOF)

    def predict_next(self, x: np.ndarray, forcing: EKFForcing) -> np.ndarray:
        """Public wrapper around the process model, for the L8 escalation
        protocol's forward-simulation rollouts (no state mutation)."""
        return self._transition(x, forcing)

    def _transition(self, x: np.ndarray, forcing: EKFForcing) -> np.ndarray:
        sw, vcmax, _a, _gs, e_prev = x
        sw_next = sw - (e_prev * forcing.dt_hours) / _ROOT_ZONE_DEPTH_MM
        vcmax_next = vcmax  # random walk; Q carries the drift uncertainty

        beta = water_stress_factor(sw_next, forcing.stage_wilting_point, forcing.stage_field_capacity)
        result = solve_farquhar_ball_berry(
            leaf_temp_c=forcing.canopy_temp_c,
            par_umol_m2_s=forcing.par_umol_m2_s,
            co2_ppm=forcing.co2_ppm,
            air_temp_c=forcing.air_temp_c,
            relative_humidity_pct=forcing.relative_humidity_pct,
            water_stress_beta=beta,
            vcmax25_override=vcmax_next,
            bb_slope_m_override=self.bb_slope_m,
        )
        vpd = vapor_pressure_deficit_kpa(forcing.air_temp_c, forcing.relative_humidity_pct)
        e_next = transpiration_mm_per_hour(result.stomatal_conductance, vpd)

        return np.array([sw_next, vcmax_next, result.net_assimilation, result.stomatal_conductance, e_next])

    @staticmethod
    def _observe(x: np.ndarray) -> np.ndarray:
        sw, _vcmax, _a, gs, _e = x
        predicted_canopy_air_delta = _CANOPY_DELTA_GAIN / max(gs, 1e-3)
        return np.array([sw, predicted_canopy_air_delta])

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

    def step(self, forcing: EKFForcing, soil_moisture_obs: float, canopy_air_delta_obs: float) -> None:
        # Predict
        x_pred = self._transition(self.x, forcing)
        f_jacobian = self._numerical_jacobian(lambda xx: self._transition(xx, forcing), self.x)
        p_pred = f_jacobian @ self.P @ f_jacobian.T + self.Q

        # Update
        z = np.array([soil_moisture_obs, canopy_air_delta_obs])
        h_jacobian = self._numerical_jacobian(self._observe, x_pred)
        innovation = z - self._observe(x_pred)
        s = h_jacobian @ p_pred @ h_jacobian.T + self.R
        s_inv = np.linalg.inv(s)
        kalman_gain = p_pred @ h_jacobian.T @ s_inv

        self.x = x_pred + kalman_gain @ innovation
        self.P = (np.eye(_N_STATE) - kalman_gain @ h_jacobian) @ p_pred

        nis = float(innovation.T @ s_inv @ innovation)
        self.last_mahalanobis_distance = float(np.sqrt(max(0.0, nis)))
        self._nis_ema = _NIS_EMA_ALPHA * nis + (1 - _NIS_EMA_ALPHA) * self._nis_ema

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
        Filter self-consistency in [0, 1], derived from the EMA-smoothed
        Normalized Innovation Squared (NIS): 1.0 when the smoothed NIS sits
        at or below its chi-squared expected value (_OBSERVATION_DOF) —
        i.e. the model and sensors agree as well as nominal noise would
        predict — falling linearly to 0 once the smoothed NIS reaches
        (1 + _NIS_TOLERANCE_FACTOR)x that expected value, meaning the
        model has been persistently diverging from observations. An
        honest "is the model still tracking reality" signal, not an
        invented number.
        """
        excess = max(0.0, self._nis_ema - _OBSERVATION_DOF)
        return float(max(0.0, 1.0 - excess / (_NIS_TOLERANCE_FACTOR * _OBSERVATION_DOF)))

    @property
    def variances(self) -> dict[str, float]:
        return {
            "soil_moisture": float(self.P[0, 0]),
            "vcmax_eff": float(self.P[1, 1]),
            "net_assimilation": float(self.P[2, 2]),
            "stomatal_conductance": float(self.P[3, 3]),
            "transpiration": float(self.P[4, 4]),
        }
