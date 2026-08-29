"""VOCSensor — electronic-nose proxy for ethylene, isoprene, hexenal.

These are stress-signalling volatiles:
  - ethylene  : general stress hormone (drought, wounding, senescence)
  - isoprene  : heat-stress marker, rises steeply above ~35°C
  - hexenal   : green-leaf volatile, spikes on pest/mechanical damage
"""

from __future__ import annotations

from sensing.sensors.base import BaseSensor


class VOCSensor(BaseSensor):
    profile_keys = ["ethylene", "isoprene", "hexenal"]

    def read(self) -> dict[str, float]:
        return {
            "ethylene": self._clamp(self._sample("ethylene"), 0.0, 50.0),
            "isoprene": self._clamp(self._sample("isoprene"), 0.0, 100.0),
            "hexenal":  self._clamp(self._sample("hexenal"), 0.0, 50.0),
        }
