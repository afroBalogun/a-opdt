"""
PhysicalSensorOverlay
─────────────────────
Subscribes to the pod node's MQTT topic and replaces the matching simulated
readings with measured ones.

A-OPDT's sensing layer is `MockSensorPublisher`: six simulated sensors driven by
a growth-stage clock. The physical pod measures three of the fields those
sensors invent. Rather than replace the layer, this overlays the measured values
on top of the simulated telemetry each cycle, so the twenty-odd fields nothing
measures keep coming from the model and the pipeline below is untouched.

    ESP32 --HTTP--> pod_bridge --:1883--> pod/pod_01/telemetry --> this

The bridge (`~/sensors/tools/pod_bridge.py`) owns the vocabulary translation and
already publishes this topic in A-OPDT's field names. It has been publishing all
along; nothing subscribed until now.

Staleness
─────────
A reading older than `max_age_s` is dropped and the simulated value stands. A
dead node must degrade to simulation, never freeze the twin on its last reading.

Provenance
──────────
Each cycle reports which fields were measured, including fields derived purely
from measured inputs (see _derive), so `measured_fields` on the
outgoing payload names them and nothing downstream has to guess whether a number
came from hardware. An empty list means the cycle was entirely simulated.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.network.transport import MQTTTransport

log = logging.getLogger(__name__)

DEFAULT_TOPIC = "pod/pod_01/telemetry"

# The node uploads every 10 s; tolerate a few missed reports before falling back.
DEFAULT_MAX_AGE_S = 60.0

# Fields the pod actually measures, with the physically sane range for each.
# A value outside its range is a sensor fault, not a reading, and must not
# reach the twin.
MEASURABLE_FIELDS: dict[str, tuple[float, float]] = {
    "soil_moisture":      (0.0, 100.0),
    "air_temperature":    (0.0, 50.0),     # DHT11 operating envelope
    "relative_humidity":  (20.0, 90.0),    # DHT11 operating envelope
    # MLX90614 aimed at canopy. Its own range is -70..380 C, but a leaf outside
    # -10..60 means the sensor is pointed at something that is not a plant -
    # sky reads far colder, sunlit metal far hotter - and a real temperature of
    # the wrong object is worse than no reading.
    "canopy_temperature": (-10.0, 60.0),
}


class PhysicalSensorOverlay:
    """Caches the pod's latest reading and overlays it on simulated telemetry.

    Parameters
    ----------
    mqtt_transport : MQTTTransport
        Reuses the twin's existing connection rather than opening a second one.
    topic : str
        Topic `pod_bridge.py` publishes to.
    max_age_s : float
        Readings older than this are ignored.
    """

    def __init__(
        self,
        mqtt_transport: "MQTTTransport",
        topic: str = DEFAULT_TOPIC,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        cache=None,
    ):
        self._mqtt = mqtt_transport
        self._topic = topic
        self._max_age = max_age_s
        # Optional: where the measured-field list is published so the API can
        # tell an observation from a stage nominal. None disables it, and the
        # reader then treats every field as nominal.
        self._cache = cache

        self._lock = threading.Lock()
        self._values: dict[str, float] = {}
        self._received_at: float = 0.0
        self._accepted = 0
        self._rejected = 0
        self._last_live: bool | None = None

    # ──────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe. The transport handles reconnects and re-subscribes."""
        self._mqtt.subscribe(self._topic, self._on_reading)
        log.info("physical overlay subscribed to '%s'", self._topic)

    # ──────────────────────────────────────────────────────────────────────
    # Consumer interface
    # ──────────────────────────────────────────────────────────────────────

    def apply(self, telemetry: dict[str, float]) -> tuple[dict[str, float], list[str]]:
        """Overlay the newest measured values onto `telemetry`.

        Returns the merged dict and the sorted names of the fields that came
        from hardware. `telemetry` is not mutated.
        """
        merged = dict(telemetry)

        with self._lock:
            fresh = (
                bool(self._values)
                and (time.time() - self._received_at) <= self._max_age
            )
            values = dict(self._values) if fresh else {}

        self._note_transition(bool(values))

        if not values:
            # Publish the empty list rather than returning early. Leaving the
            # previous one in place would keep asserting those fields were
            # measured after the node went quiet - the same stale-claim bug
            # this whole change exists to remove.
            self._publish_provenance([])
            return merged, []

        # Only overlay fields the simulator actually produces. A measured field
        # the twin has no slot for is dropped rather than invented into the
        # payload, where nothing downstream would know how to read it.
        measured = []
        for field, value in values.items():
            if field in merged:
                merged[field] = value
                measured.append(field)
            else:
                log.debug("measured field '%s' has no simulated counterpart", field)

        measured += self._derive(merged, measured)
        measured = sorted(measured)
        self._publish_provenance(measured)
        return merged, measured

    def _publish_provenance(self, measured: list[str]) -> None:
        """
        Record which fields came from hardware, where a reader can find it.

        The list has always been returned from apply(), and nothing kept it.
        The dashboard therefore inferred provenance from "is there a value in
        InfluxDB" - and since the mock publisher writes all nineteen fields
        there, every one of them read as measured. Publishing the list to the
        cache is what lets the API answer the question honestly.

        Best effort: a cache that is down must not stop a reading being
        overlaid. The reader treats a missing key as "nothing is measured",
        which errs toward calling a value nominal rather than observed.
        """
        if self._cache is None:
            return
        try:
            self._cache.set_latest("measured_fields", ",".join(measured))
        except Exception as exc:                      # noqa: BLE001
            log.debug("could not cache measured_fields: %s", exc)

    @staticmethod
    def _derive(merged: dict[str, float], measured: list[str]) -> list[str]:
        """Recompute fields that are fully determined by measured inputs.

        A field the simulator invents does not stop being invented just because
        its inputs became real. canopy_air_delta is the case that matters:
        sensor_profiles defines it as Tc - Ta, and stress_thresholds fires the
        drought rule on it at 1.2 warn / 2.8 crit. With the MLX90614 and the
        DHT11 both live, leaving it simulated means the drought rule reasons
        over an invented number while both its ingredients sit measured beside
        it - which is how a twin ends up confidently wrong.

        Derived, not sensed - but nothing simulated contributes to it, so it is
        reported as measured. Same treatment VPD gets when it is computed from
        a measured temperature and humidity.
        """
        derived: list[str] = []

        if ("canopy_temperature" in measured and "air_temperature" in measured
                and "canopy_air_delta" in merged):
            merged["canopy_air_delta"] = round(
                merged["canopy_temperature"] - merged["air_temperature"], 4)
            derived.append("canopy_air_delta")

        return derived

    @property
    def is_live(self) -> bool:
        with self._lock:
            return (
                bool(self._values)
                and (time.time() - self._received_at) <= self._max_age
            )

    @property
    def stats(self) -> dict[str, int]:
        return {"accepted": self._accepted, "rejected": self._rejected}

    # ──────────────────────────────────────────────────────────────────────
    # MQTT callback
    # ──────────────────────────────────────────────────────────────────────

    def _on_reading(self, payload: dict) -> None:
        """Handle one bridge message. The transport has already parsed JSON."""
        if not isinstance(payload, dict):
            self._rejected += 1
            log.warning("payload on '%s' is not an object", self._topic)
            return

        values: dict[str, float] = {}
        for field, (lo, hi) in MEASURABLE_FIELDS.items():
            raw = payload.get(field)
            if raw is None:
                continue          # the node does not measure it this cycle
            try:
                value = float(raw)
            except (TypeError, ValueError):
                log.warning("field '%s' is not numeric: %r", field, raw)
                continue
            if value != value:    # NaN — a failed sensor read
                log.warning("field '%s' is NaN", field)
                continue
            if not lo <= value <= hi:
                log.warning("field '%s' = %.2f outside sane range %.0f-%.0f",
                            field, value, lo, hi)
                continue
            values[field] = value

        if not values:
            self._rejected += 1
            return

        with self._lock:
            self._values = values
            # Arrival time, not the node's timestamp: the bridge forwards
            # immediately, and a node with an unsynced clock would otherwise
            # look permanently stale or permanently fresh.
            self._received_at = time.time()
        self._accepted += 1

    # ──────────────────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────────────────

    def _note_transition(self, live: bool) -> None:
        """Log only when liveness changes, not every cycle."""
        if self._last_live == live:
            return
        self._last_live = live
        if live:
            log.info("pod node live — %s are measured",
                     ", ".join(sorted(self._values)))
        else:
            log.warning("pod node stale — falling back to simulated readings")
