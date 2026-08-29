"""Shared growth-stage awareness for L5/L6 layers.

The Sensing layer (MockSensorPublisher) owns the one true GrowthStageEngine
clock and broadcasts the current stage on every "telemetry.received" event.
Rather than each downstream layer running its own (redundant, driftable)
clock, they track the broadcast stage via this tiny subscriber.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.core.events import DomainEvent, EventBus


class GrowthStageTracker:
    def __init__(self, event_bus: "EventBus", default: str = "germination"):
        self.current_stage = default
        event_bus.subscribe("telemetry.received", self._on_telemetry)

    async def _on_telemetry(self, event: "DomainEvent") -> None:
        stage = event.payload.get("growth_stage")
        if stage:
            self.current_stage = stage
