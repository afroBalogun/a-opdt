"""
MockSensorPublisher
────────────────────
Dyon LayerBase implementation that drives the Physical Asset + Sensing +
Network layers for A-OPDT, using mock data instead of real hardware.

Each publish cycle:
  1. Advances the GrowthStageEngine clock (tick)
  2. Reads from all 6 sensor instances
  3. Merges into a single telemetry dict
  4. Publishes to MQTT topic `dt/{asset_id}/telemetry`
  5. Writes to InfluxDB via the injected TimeSeriesStore

This class fills the role Dyon expects of a network/physical layer —
it follows the same LayerBase contract used by ThresholdRuleEngine and
PIDController, so it slots into AbstractDigitalTwin.build_layers() directly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

from dyon.core.base import LayerBase
from dyon.core.events import DomainEvent

from sensing.growth_stage_engine import GrowthStageEngine
from sensing.sensors import (
    AtmosphericSensor,
    FluorescenceSensor,
    SoilSensor,
    SpectralSensor,
    StressEvent,
    ThermalSensor,
    VOCSensor,
)

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import TimeSeriesStore
    from dyon.network.transport import MQTTTransport

log = logging.getLogger(__name__)


class MockSensorPublisher(LayerBase):
    """
    Physical Asset + Sensing + Network layer (mock implementation).

    Parameters
    ----------
    config : TwinConfig
        Dyon root config (asset_id, mqtt, etc.)
    event_bus : EventBus
        Shared domain event bus.
    ts_store : TimeSeriesStore
        InfluxDB adapter — readings are written here every cycle.
    mqtt_transport : MQTTTransport
        Dyon MQTT wrapper — readings are published here every cycle.
    phenology_path, profiles_path : str
        Paths to the YAML configs driving growth stage + sensor nominal values.
    publish_interval : float
        Seconds between publish cycles (overrides phenology.yaml if set).
    """

    layer_name = "sensing"

    def __init__(
        self,
        config: "TwinConfig",
        event_bus: "EventBus",
        *,
        ts_store: "TimeSeriesStore",
        mqtt_transport: "MQTTTransport",
        cache=None,
        phenology_path: str = "config/maize_phenology.yaml",
        profiles_path: str = "config/sensor_profiles.yaml",
        publish_interval: Optional[float] = None,
        physical_overlay=None,
    ):
        super().__init__(config, event_bus)
        self.ts = ts_store
        self.mqtt = mqtt_transport
        # When set, measured readings from the physical pod replace the
        # simulated values for the fields it covers. None keeps this layer
        # fully mock, which is the default.
        self.physical = physical_overlay
        # The growth-stage clock lives in this process, so anything outside it --
        # the web API in particular -- can only see thermal time if it is cached.
        self.cache = cache

        self.engine = GrowthStageEngine(phenology_path)

        self._sensors = [
            SoilSensor(self.engine, profiles_path),
            SpectralSensor(self.engine, profiles_path),
            ThermalSensor(self.engine, profiles_path),
            FluorescenceSensor(self.engine, profiles_path),
            VOCSensor(self.engine, profiles_path),
            AtmosphericSensor(self.engine, profiles_path),
        ]

        # Pull publish_interval from phenology config unless explicitly overridden
        self._interval = publish_interval or self.engine._sim_cfg.get(
            "publish_interval_seconds", 15
        )

        self._last_stage: str = self.engine.current_stage

    # ──────────────────────────────────────────────────────────────────────────
    # Stress injection passthrough (for demos / testing scenarios)
    # ──────────────────────────────────────────────────────────────────────────

    def inject_stress(self, sensor_field: str, event: StressEvent) -> None:
        """
        Inject a stress event onto any sensor that owns `sensor_field`.
        e.g. publisher.inject_stress("soil_moisture", StressEvent(multiplier=0.4, ramp_steps=20))
        """
        for sensor in self._sensors:
            if sensor_field in sensor.profile_keys:
                sensor.inject_stress(sensor_field, event)
                return
        self.log.warning("No sensor owns field '%s' — stress not injected", sensor_field)

    def clear_all_stress(self) -> None:
        for sensor in self._sensors:
            sensor.clear_stress()

    # ──────────────────────────────────────────────────────────────────────────
    # Core publish cycle
    # ──────────────────────────────────────────────────────────────────────────

    def _collect_readings(self) -> dict[str, float]:
        """Advance the clock once, then poll every sensor for its readings."""
        self.engine.tick()

        telemetry: dict[str, float] = {}
        for sensor in self._sensors:
            telemetry.update(sensor.read())

        return telemetry

    async def _publish_cycle(self) -> None:
        telemetry = self._collect_readings()

        # Overlay hardware readings before anything is stored or published, so
        # InfluxDB, the event bus and MQTT all see the same numbers.
        measured_fields: list[str] = []
        if self.physical is not None:
            telemetry, measured_fields = self.physical.apply(telemetry)

        # Write to InfluxDB
        # InfluxAdapter reads back from the "asset_telemetry" measurement with
        # asset_id as a tag. Writing the asset_id as the measurement name meant
        # every point landed somewhere nothing ever queried.
        # growth_stage is deliberately NOT a tag here. InfluxDB creates a
        # separate series per tag combination, so tagging by stage split this
        # measurement into one series per growth stage - and InfluxAdapter's
        # get_latest() does last(), which returns one row PER SERIES and takes
        # the first. That meant it could return a reading from a stage the crop
        # had already left, minutes or hours stale.
        #
        # It read as correct for as long as every field was simulated and the
        # per-stage values sat close together. The DHT11 is what exposed it: a
        # real 26.8 C alongside a stale 24.1 C from an abandoned stage, with the
        # EMA fed the stale one every cycle so it never converged.
        #
        # Nothing filters by this tag - the stage reaches consumers through the
        # Redis cache and the event bus payloads instead.
        self.ts.write_point(
            measurement="asset_telemetry",
            fields=telemetry,
            tags={"asset_id": self.config.asset_id},
        )

        if self.cache is not None:
            for key, value in (("growth_stage", self.engine.current_stage),
                               ("gdd_accumulated", self.engine.gdd),
                               ("das", self.engine.das)):
                try:
                    self.cache.set_latest(key, value)
                except Exception:
                    pass          # the cache is an optimisation, never a dependency

        # Publish to MQTT
        payload = {
            **telemetry,
            "growth_stage": self.engine.current_stage,
            "das": round(self.engine.das, 2),
            "gdd": round(self.engine.gdd, 2),
            # Names the fields that came from hardware this cycle. Empty means
            # the reading was entirely simulated.
            "measured_fields": measured_fields,
        }
        self.mqtt.publish(self.config.topic_telemetry, payload)

        # Emit domain event so other layers (reactive, intelligent) can react
        await self.bus.publish(
            DomainEvent(
                event_type="telemetry.received",
                source_layer=self.layer_name,
                source_asset=self.config.asset_id,
                payload=payload,
            )
        )

        # Emit a distinct event when the growth stage changes
        if self.engine.current_stage != self._last_stage:
            await self.bus.publish(
                DomainEvent(
                    event_type="growth_stage.changed",
                    source_layer=self.layer_name,
                    source_asset=self.config.asset_id,
                    payload={
                        "from_stage": self._last_stage,
                        "to_stage": self.engine.current_stage,
                        "das": round(self.engine.das, 2),
                        "gdd": round(self.engine.gdd, 2),
                        "critical_window": self.engine.is_critical_window,
                    },
                    severity="info" if not self.engine.is_critical_window else "warning",
                )
            )
            self._last_stage = self.engine.current_stage

        self.log.debug(
            "Published telemetry (stage=%s DAS=%.1f fields=%d)",
            self.engine.current_stage,
            self.engine.das,
            len(telemetry),
        )

    # ──────────────────────────────────────────────────────────────────────────
    # LayerBase contract
    # ──────────────────────────────────────────────────────────────────────────

    async def initialise(self) -> None:
        self.log.info(
            "MockSensorPublisher initialised — %d sensors, interval=%.1fs, start_stage=%s",
            len(self._sensors),
            self._interval,
            self.engine.current_stage,
        )

    async def start(self) -> None:
        self._running = True
        self.log.info("MockSensorPublisher started")
        while self._running:
            try:
                await self._publish_cycle()
            except Exception as exc:
                self.log.error("Publish cycle error: %s", exc)
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        self._running = False
        self.engine.stop()
        self.log.info("MockSensorPublisher stopped")
