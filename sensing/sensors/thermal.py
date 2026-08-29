"""ThermalSensor — canopy temperature, canopy-air delta (Tc - Ta).

Canopy temperature is diurnally modulated: peaks near solar noon, troughs
at night, tracking the ambient air temperature cycle from GrowthStageEngine
but offset by the plant's transpirational cooling (captured in canopy_air_delta).
"""

from __future__ import annotations

from sensing.sensors.base import BaseSensor


class ThermalSensor(BaseSensor):
    profile_keys = ["canopy_temperature", "canopy_air_delta"]

    #: Amplitude of diurnal swing applied on top of stage nominal (°C)
    _DIURNAL_AMPLITUDE = 4.0

    def read(self) -> dict[str, float]:
        canopy_temp = self._sample(
            "canopy_temperature", diurnal_scale=self._DIURNAL_AMPLITUDE
        )
        canopy_air_delta = self._sample("canopy_air_delta")

        return {
            "canopy_temperature": self._clamp(canopy_temp, -5.0, 55.0),
            "canopy_air_delta":   self._clamp(canopy_air_delta, -10.0, 15.0),
        }
