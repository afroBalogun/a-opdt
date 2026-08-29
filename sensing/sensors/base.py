"""
BaseSensor
──────────
Abstract base for all A-OPDT mock sensors.

Each sensor:
  1. Reads its nominal value and noise params from sensor_profiles.yaml
     for the current growth stage.
  2. Applies Gaussian noise + optional stress injection.
  3. Returns a dict of field_name → float readings ready for MQTT publish.

Stress injection allows the mock publisher to simulate realistic stress
scenarios (e.g. drought onset, heat spike at anthesis) without real hardware.
"""

from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import yaml

from sensing.growth_stage_engine import GrowthStageEngine

log = logging.getLogger(__name__)


@dataclass
class StressEvent:
    """
    Describes an active stress being injected into a sensor reading.

    multiplier : float
        Applied to the nominal value. < 1.0 reduces it (e.g. drought drops
        soil moisture), > 1.0 increases it (e.g. heat raises canopy temp).
    additive : float
        Added after multiplicative scaling (useful for delta/ratio fields).
    ramp_steps : int
        How many readings to ramp from nominal to full stress (gradual onset).
    current_step : int
        Internal counter — do not set manually.
    """
    multiplier: float = 1.0
    additive: float = 0.0
    ramp_steps: int = 10
    current_step: int = field(default=0, init=False)

    def scale(self) -> tuple[float, float]:
        """Return (effective_multiplier, effective_additive) for current ramp step."""
        if self.ramp_steps <= 1:
            return self.multiplier, self.additive
        progress = min(1.0, self.current_step / self.ramp_steps)
        eff_mult = 1.0 + (self.multiplier - 1.0) * progress
        eff_add  = self.additive * progress
        self.current_step += 1
        return eff_mult, eff_add


class BaseSensor(ABC):
    """
    Abstract base for all mock sensors.

    Subclasses must implement `read()` which returns a dict of
    field_name → float. All fields in this dict will be published to MQTT
    and written to InfluxDB.

    Parameters
    ----------
    engine : GrowthStageEngine
        Shared phenology clock — sensors use it for stage-aware nominals
        and diurnal modulation.
    profiles_path : str
        Path to config/sensor_profiles.yaml
    """

    #: Subclasses declare which top-level keys in sensor_profiles.yaml they own
    profile_keys: list[str] = []

    def __init__(
        self,
        engine: GrowthStageEngine,
        profiles_path: str = "config/sensor_profiles.yaml",
    ):
        self.engine = engine
        self.log = logging.getLogger(f"a_opdt.sensor.{self.__class__.__name__}")
        self._stress_events: dict[str, StressEvent] = {}

        with open(profiles_path) as f:
            all_profiles = yaml.safe_load(f)

        # Extract only this sensor's profiles
        self._profiles: dict[str, dict] = {
            k: all_profiles[k]
            for k in self.profile_keys
            if k in all_profiles
        }

        if not self._profiles:
            self.log.warning(
                "No profiles found for keys %s in %s",
                self.profile_keys,
                profiles_path,
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Core interface
    # ──────────────────────────────────────────────────────────────────────────

    @abstractmethod
    def read(self) -> dict[str, float]:
        """
        Return current sensor readings as {field_name: value}.
        Must be implemented by every sensor subclass.
        """
        ...

    # ──────────────────────────────────────────────────────────────────────────
    # Stress injection API
    # ──────────────────────────────────────────────────────────────────────────

    def inject_stress(self, field: str, event: StressEvent) -> None:
        """
        Inject a stress event onto a specific field.
        The mock publisher calls this to simulate drought, heat spike, etc.
        """
        self._stress_events[field] = event
        self.log.info("Stress injected on field '%s': mult=%.2f add=%.2f", field, event.multiplier, event.additive)

    def clear_stress(self, field: Optional[str] = None) -> None:
        """Remove a specific stress event, or all if field is None."""
        if field:
            self._stress_events.pop(field, None)
        else:
            self._stress_events.clear()

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers for subclasses
    # ──────────────────────────────────────────────────────────────────────────

    def _sample(self, field: str, diurnal_scale: float = 0.0) -> float:
        """
        Generate a single noisy reading for `field` at the current growth stage.

        Parameters
        ----------
        field : str
            Key in sensor_profiles.yaml.
        diurnal_scale : float
            If > 0, the diurnal factor (0–1) is multiplied by this and added
            to the nominal before noise. Use for light/temp-driven sensors.
        """
        profile = self._profiles.get(field)
        if profile is None:
            self.log.warning("No profile for field '%s'", field)
            return 0.0

        stage = self.engine.current_stage
        stage_params = profile["by_stage"].get(stage)
        if stage_params is None:
            # Fall back to first available stage
            stage_params = next(iter(profile["by_stage"].values()))

        nominal: float   = float(stage_params["nominal"])
        noise_std: float = float(profile.get("noise_std", 0.01))

        # Diurnal modulation (e.g. PAR, air temperature, canopy temperature)
        if diurnal_scale != 0.0:
            nominal += diurnal_scale * self.engine.diurnal_factor()

        # Apply stress event if active
        if field in self._stress_events:
            mult, add = self._stress_events[field].scale()
            nominal = nominal * mult + add

        # Gaussian noise
        value = random.gauss(nominal, noise_std)
        return round(value, 4)

    def _clamp(self, value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))
