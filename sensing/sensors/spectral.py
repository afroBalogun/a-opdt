"""SpectralSensor — NDVI, PRI, red-edge slope (hyperspectral camera proxy)."""

from __future__ import annotations

from sensing.sensors.base import BaseSensor


class SpectralSensor(BaseSensor):
    profile_keys = ["ndvi", "pri", "red_edge_slope"]

    def read(self) -> dict[str, float]:
        return {
            "ndvi":            self._clamp(self._sample("ndvi"), -1.0, 1.0),
            "pri":             self._clamp(self._sample("pri"), -1.0, 1.0),
            "red_edge_slope":  self._clamp(self._sample("red_edge_slope"), 0.0, 1.0),
        }
