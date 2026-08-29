"""
GrowthStageEngine
─────────────────
Tracks the current maize phenological stage by accumulating Growing Degree
Days (GDD) from a simulated temperature record.

In mock mode (no real sensors) the engine advances simulated time at an
accelerated rate defined in config/maize_phenology.yaml so the full crop
cycle can be exercised in a short test window.

All sensor classes call `engine.current_stage` to calibrate their nominal
values to the appropriate growth-stage profile.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

import yaml

log = logging.getLogger(__name__)


@dataclass
class GrowthStage:
    name: str
    das_start: int
    das_end: int
    gdd_start: float
    gdd_end: float
    description: str
    critical_stress_window: bool


@dataclass
class PlantClock:
    """Holds the running simulation state."""
    simulated_das: float = 0.0          # days after sowing (float for sub-day precision)
    cumulative_gdd: float = 0.0
    current_stage: str = "germination"
    is_critical_window: bool = False
    wall_time_start: float = field(default_factory=time.time)


class GrowthStageEngine:
    """
    Manages simulated phenological time for the mock sensor publisher.

    Parameters
    ----------
    phenology_path : str
        Path to config/maize_phenology.yaml
    """

    def __init__(self, phenology_path: str = "config/maize_phenology.yaml"):
        with open(phenology_path) as f:
            cfg = yaml.safe_load(f)

        self._t_base: float = cfg["t_base"]
        self._t_opt: float  = cfg["t_opt"]
        self._t_max: float  = cfg["t_max"]
        self._sim_cfg       = cfg["simulation"]

        self._stages: list[GrowthStage] = [
            GrowthStage(**s) for s in cfg["growth_stages"]
        ]
        self._stage_map: dict[str, GrowthStage] = {s.name: s for s in self._stages}

        self.clock = PlantClock(
            simulated_das=float(self._sim_cfg.get("start_das", 0))
        )
        self._days_per_second: float = self._sim_cfg["days_per_real_second"]
        self._last_tick: float = time.time()
        self._running: bool = False

        # Advance to correct initial stage
        self._sync_stage()

        log.info(
            "GrowthStageEngine ready — stage=%s DAS=%.1f GDD=%.1f",
            self.clock.current_stage,
            self.clock.simulated_das,
            self.clock.cumulative_gdd,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def current_stage(self) -> str:
        return self.clock.current_stage

    @property
    def das(self) -> float:
        return self.clock.simulated_das

    @property
    def gdd(self) -> float:
        return self.clock.cumulative_gdd

    @property
    def is_critical_window(self) -> bool:
        return self.clock.is_critical_window

    @property
    def stage_info(self) -> Optional[GrowthStage]:
        return self._stage_map.get(self.clock.current_stage)

    def stage_progress(self) -> float:
        """Fractional progress through current stage [0.0 – 1.0]."""
        s = self.stage_info
        if s is None:
            return 1.0
        span = s.gdd_end - s.gdd_start
        if span <= 0:
            return 1.0
        return min(1.0, max(0.0, (self.clock.cumulative_gdd - s.gdd_start) / span))

    def diurnal_factor(self) -> float:
        """
        Returns a sinusoidal day/night factor in [0, 1].
        1.0 = solar noon, 0.0 = midnight.
        Based on fractional day within the simulated DAS.
        """
        frac_day = self.clock.simulated_das % 1.0   # 0=midnight, 0.5=noon
        return max(0.0, math.sin(math.pi * frac_day))

    def air_temperature(self) -> float:
        """
        Simulated ambient air temperature with diurnal cycle.
        Mean and range from phenology config.
        """
        mean  = self._sim_cfg["ambient_temp_mean"]
        swing = self._sim_cfg["ambient_temp_diurnal_range"]
        # Peak at solar noon (diurnal_factor=1), trough at midnight
        return mean + (self.diurnal_factor() - 0.5) * swing

    # ──────────────────────────────────────────────────────────────────────────
    # Tick logic
    # ──────────────────────────────────────────────────────────────────────────

    def tick(self) -> None:
        """Advance simulated time by elapsed real time × acceleration factor."""
        now = time.time()
        elapsed_real = now - self._last_tick
        self._last_tick = now

        delta_days = elapsed_real * self._days_per_second
        self.clock.simulated_das += delta_days

        # Accumulate GDD using mean daily temperature for this tick
        t_air = self.air_temperature()
        daily_gdd = self._calc_gdd(t_air)
        # Scale by fraction of day this tick represents
        self.clock.cumulative_gdd += daily_gdd * delta_days

        self._sync_stage()

    def _calc_gdd(self, t_mean: float) -> float:
        """
        Triangular GDD model.
        Returns 0 below t_base, rises linearly to (t_opt - t_base) at t_opt,
        then falls to 0 at t_max.
        """
        if t_mean <= self._t_base or t_mean >= self._t_max:
            return 0.0
        if t_mean <= self._t_opt:
            return t_mean - self._t_base
        # Between t_opt and t_max — linear decline
        return (self._t_max - t_mean) / (self._t_max - self._t_opt) * (self._t_opt - self._t_base)

    def _sync_stage(self) -> None:
        """Update current_stage based on cumulative GDD."""
        for stage in self._stages:
            if self.clock.cumulative_gdd < stage.gdd_end:
                if self.clock.current_stage != stage.name:
                    log.info(
                        "Growth stage transition → %s (DAS=%.1f GDD=%.1f)",
                        stage.name,
                        self.clock.simulated_das,
                        self.clock.cumulative_gdd,
                    )
                self.clock.current_stage = stage.name
                self.clock.is_critical_window = stage.critical_stress_window
                return

        # Past final stage — stay at maturity
        self.clock.current_stage = self._stages[-1].name
        self.clock.is_critical_window = False

    # ──────────────────────────────────────────────────────────────────────────
    # Async background task
    # ──────────────────────────────────────────────────────────────────────────

    async def run(self, interval: float = 1.0) -> None:
        """Background coroutine: tick every `interval` real seconds."""
        self._running = True
        log.info("GrowthStageEngine background task started (tick_interval=%.1fs)", interval)
        while self._running:
            self.tick()
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._running = False
