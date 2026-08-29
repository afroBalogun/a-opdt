"""AtmosphericSensor — air temperature, relative humidity, CO2, PAR.

air_temperature is sourced directly from GrowthStageEngine's diurnal model
(shared ground truth with ThermalSensor's canopy readings). The remaining
fields use their own diurnal-modulated profiles.
"""

from __future__ import annotations

from sensing.sensors.base import BaseSensor


class AtmosphericSensor(BaseSensor):
    profile_keys = ["air_temperature", "relative_humidity", "co2", "par"]

    _RH_DIURNAL_AMPLITUDE  = -10.0   # RH drops at midday
    _PAR_DIURNAL_AMPLITUDE = 400.0   # PAR peaks at solar noon, ~0 at night

    def read(self) -> dict[str, float]:
        # Use the engine's own diurnal air temperature as ground truth
        air_temp = self.engine.air_temperature()

        rh  = self._sample("relative_humidity", diurnal_scale=self._RH_DIURNAL_AMPLITUDE)
        co2 = self._sample("co2")
        par = self._sample("par", diurnal_scale=self._PAR_DIURNAL_AMPLITUDE)

        return {
            "air_temperature":   round(air_temp, 4),
            "relative_humidity": self._clamp(rh, 0.0, 100.0),
            "co2":               self._clamp(co2, 250.0, 600.0),
            "par":               self._clamp(par, 0.0, 2200.0),
        }
