"""SoilSensor — moisture (TDR), EC (salinity), N/P/K ion-selective probes."""

from __future__ import annotations

from sensing.sensors.base import BaseSensor


class SoilSensor(BaseSensor):
    profile_keys = [
        "soil_moisture",
        "soil_ec",
        "soil_nitrogen",
        "soil_phosphorus",
        "soil_potassium",
    ]

    def read(self) -> dict[str, float]:
        moisture = self._clamp(self._sample("soil_moisture"), 0.0, 0.5)
        return {
            "soil_moisture":   moisture,
            "soil_ec":         self._clamp(self._sample("soil_ec"), 0.0, 10.0),
            "soil_nitrogen":   self._clamp(self._sample("soil_nitrogen"), 0.0, 200.0),
            "soil_phosphorus": self._clamp(self._sample("soil_phosphorus"), 0.0, 100.0),
            "soil_potassium":  self._clamp(self._sample("soil_potassium"), 0.0, 400.0),
        }
