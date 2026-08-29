"""L5 — Data Management Layer: smoothing, health scoring, and the EKF
Plant State Estimator.

Two data-quality tracks run side by side here:
  - EMA smoothing + spike flagging for all 19 raw sensor fields (Data
    Quality Agent) — unchanged, cheap, no physiology model required.
  - A genuine Extended Kalman Filter (reactive/ekf_estimator.py) fusing
    the Farquhar/Ball-Berry/Penman-Monteith model with soil_moisture and
    canopy_air_delta observations, producing a filtered soil-moisture
    estimate that supersedes the plain EMA value for health-score
    purposes (soil moisture is the one field where a physically-motivated
    prior meaningfully beats naive smoothing). The other 18 fields have no
    physiology model tying them to a process prior, so they keep using
    EMA smoothing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import yaml

from dyon.core.base import LayerBase
from dyon.core.events import DomainEvent

from reactive.ekf_estimator import EKFForcing, EKFPlantStateEstimator
from reactive.growth_stage_tracker import GrowthStageTracker

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import CacheStore, TimeSeriesStore

log = logging.getLogger(__name__)

_EMA_ALPHA = 0.3
_SPIKE_SIGMA = 2.0
# Weight applied per threshold-band violation when computing the health score.
_CRIT_WEIGHT = 2.0
_WARN_WEIGHT = 1.0
_STATE_CODE_THRESHOLDS = {"healthy": 85.0, "stress": 50.0}  # score >= these -> 0 / 1, else 2

_EKF_FORCING_FIELDS = (
    "par", "air_temperature", "canopy_temperature", "relative_humidity", "co2",
)


def score_field(profiles: dict, field: str, value: float, stage: str) -> float:
    """
    Threshold-band violation weight for one field (0, _WARN_WEIGHT, or
    _CRIT_WEIGHT), against config/sensor_profiles.yaml's per-growth-stage
    bands. Pure function — shared by the live HealthScoreCalculator and
    the webapp's what-if simulator (webapp/backend/app.py) so both use
    identical scoring logic rather than duplicating it.
    """
    band = profiles.get(field, {}).get("by_stage", {}).get(stage)
    if not band:
        return 0.0
    if "crit_low" in band and value < band["crit_low"]:
        return _CRIT_WEIGHT
    if "crit_high" in band and value > band["crit_high"]:
        return _CRIT_WEIGHT
    if "warn_low" in band and value < band["warn_low"]:
        return _WARN_WEIGHT
    if "warn_high" in band and value > band["warn_high"]:
        return _WARN_WEIGHT
    return 0.0


class HealthScoreCalculator(LayerBase):
    layer_name = "data_management"

    def __init__(
        self,
        config: "TwinConfig",
        event_bus: "EventBus",
        *,
        ts_store: "TimeSeriesStore",
        cache: "CacheStore",
        ekf: EKFPlantStateEstimator,
        profiles_path: str = "config/sensor_profiles.yaml",
        eval_interval: int = 60,
    ):
        super().__init__(config, event_bus)
        self.ts = ts_store
        self.cache = cache
        self.eval_interval = eval_interval

        with open(profiles_path) as f:
            self._profiles: dict = yaml.safe_load(f)

        self._ema: dict[str, float] = {}
        self._stage_tracker: GrowthStageTracker | None = None

        # Shared with the L8 escalation protocol, which reads ekf.confidence —
        # constructed once in twin.py so both layers see the same live state.
        self._ekf = ekf
        self._ekf_last_tick = time.time()

    async def initialise(self) -> None:
        self._stage_tracker = GrowthStageTracker(self.bus)

    def _smooth(self, field: str, raw: float) -> tuple[float, bool]:
        previous = self._ema.get(field, raw)
        smoothed = _EMA_ALPHA * raw + (1 - _EMA_ALPHA) * previous
        self._ema[field] = smoothed

        noise_std = self._profiles.get(field, {}).get("noise_std", 0.0)
        is_spike = bool(noise_std) and abs(raw - smoothed) > _SPIKE_SIGMA * noise_std
        return smoothed, is_spike

    def _violation_weight(self, field: str, value: float, stage: str) -> float:
        return score_field(self._profiles, field, value, stage)

    def _run_ekf(self, raw_values: dict[str, float], stage: str) -> None:
        if not all(f in raw_values for f in (*_EKF_FORCING_FIELDS, "soil_moisture", "canopy_air_delta")):
            return

        # Recalibrated periodically by the L8 Twin Calibration Agent.
        calibrated_slope = self.cache.get_latest_cached("calibrated_bb_slope_m")
        if calibrated_slope is not None:
            self._ekf.bb_slope_m = float(calibrated_slope)

        now = time.time()
        dt_hours = (now - self._ekf_last_tick) / 3600.0
        self._ekf_last_tick = now

        band = self._profiles.get("soil_moisture", {}).get("by_stage", {}).get(stage)
        if not band:
            return

        forcing = EKFForcing(
            par_umol_m2_s=raw_values["par"],
            air_temp_c=raw_values["air_temperature"],
            canopy_temp_c=raw_values["canopy_temperature"],
            relative_humidity_pct=raw_values["relative_humidity"],
            co2_ppm=raw_values["co2"],
            stage_field_capacity=band["nominal"],
            stage_wilting_point=band["crit_low"],
            dt_hours=dt_hours,
        )
        self._ekf.step(forcing, raw_values["soil_moisture"], raw_values["canopy_air_delta"])

    async def evaluate(self) -> None:
        stage = self._stage_tracker.current_stage if self._stage_tracker else "germination"

        raw_values: dict[str, float] = {}
        smoothed_values: dict[str, float] = {}
        quality_flags: dict[str, str] = {}
        for field in self.config.field_names:
            raw = self.ts.get_latest(field)
            if raw is None:
                continue
            raw_values[field] = raw
            smoothed, is_spike = self._smooth(field, raw)
            smoothed_values[field] = smoothed
            if is_spike:
                quality_flags[field] = "spike"

        if not smoothed_values:
            return

        self._run_ekf(raw_values, stage)

        # The EKF's soil-moisture estimate (fusing the water-balance prior
        # with the sensor observation) supersedes the plain EMA value for
        # scoring purposes — the one field where a physics-informed filter
        # meaningfully beats naive smoothing.
        scoring_values = dict(smoothed_values)
        scoring_values["soil_moisture"] = self._ekf.soil_moisture

        share = 100.0 / len(scoring_values)
        violations = sum(
            self._violation_weight(f, v, stage) for f, v in scoring_values.items()
        )
        score = max(0.0, 100.0 - share * violations)
        state_code = (
            0 if score >= _STATE_CODE_THRESHOLDS["healthy"]
            else 1 if score >= _STATE_CODE_THRESHOLDS["stress"]
            else 2
        )

        # Note: InfluxAdapter.get_latest() caches by field name only, not by
        # measurement — writing these smoothed values updates the same
        # in-process cache MockSensorPublisher's raw writes use. Other
        # layers' get_latest() calls will see whichever was written most
        # recently (raw or smoothed), not deterministically one or the
        # other. Acceptable for now: the two rarely diverge by much given
        # EMA smoothing; revisit if L4/model_runner also starts writing
        # overlapping field names (e.g. asset_simulation).
        ekf_variances = self._ekf.variances
        processed_fields = {
            **smoothed_values,
            "ekf_soil_moisture": self._ekf.soil_moisture,
            "ekf_vcmax_eff": self._ekf.vcmax_eff,
            "ekf_net_assimilation": self._ekf.net_assimilation,
            "ekf_stomatal_conductance": self._ekf.stomatal_conductance,
            "ekf_transpiration": self._ekf.transpiration,
            "ekf_soil_moisture_variance": ekf_variances["soil_moisture"],
        }
        self.ts.write_point(
            measurement="asset_processed",
            fields=processed_fields,
            tags={"asset_id": self.config.asset_id, "growth_stage": stage},
        )
        self.ts.write_point(
            measurement="asset_health",
            fields={"health_score": score, "state_code": float(state_code)},
            tags={"asset_id": self.config.asset_id, "growth_stage": stage},
        )
        self.cache.set_latest("health_score", score)
        # Read by webapp/backend/app.py's GET /api/current-state to seed the
        # what-if simulator's growth-stage selector with the live value.
        self.cache.set_latest("growth_stage", stage)

        # Feeds the L8 Twin Calibration Agent's rolling buffer — a lighter
        # substitute for querying InfluxDB historical ranges (which would
        # hit the same measurement-blind get_latest() ambiguity noted
        # above), reusing the event bus pattern already established by
        # BioticPodDT's "model.updated" event.
        if all(f in raw_values for f in (*_EKF_FORCING_FIELDS, "soil_moisture")):
            await self.bus.publish(
                DomainEvent(
                    event_type="data_management.cycle_complete",
                    source_layer=self.layer_name,
                    source_asset=self.config.asset_id,
                    payload={
                        "par": raw_values["par"],
                        "air_temperature": raw_values["air_temperature"],
                        "canopy_temperature": raw_values["canopy_temperature"],
                        "relative_humidity": raw_values["relative_humidity"],
                        "co2": raw_values["co2"],
                        "soil_moisture": raw_values["soil_moisture"],
                        "growth_stage": stage,
                        "ekf_net_assimilation": self._ekf.net_assimilation,
                        "ekf_stomatal_conductance": self._ekf.stomatal_conductance,
                    },
                )
            )

        if quality_flags:
            self.log.debug("Sensor spikes flagged: %s", quality_flags)
        self.log.debug("Health score=%.1f state_code=%d", score, state_code)

    async def start(self) -> None:
        self._running = True
        self.log.info(
            "HealthScoreCalculator started (interval=%ds, fields=%d)",
            self.eval_interval,
            len(self.config.field_names),
        )
        while self._running:
            try:
                await self.evaluate()
            except Exception as exc:
                self.log.error("Data management cycle error: %s", exc)
            await asyncio.sleep(self.eval_interval)
