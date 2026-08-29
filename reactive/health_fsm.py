"""L6 — Reactive Control Layer: the 8-state Plant Health State Machine.

States and mapping (per the A-OPDT architecture design, section 10.1),
with the 7 fine-grained stress categories from stress_thresholds.yaml
bucketed into the 4 named stress states:

    drought, heat_stress, frost        -> WATER_STRESS
    nutrient_deficiency                -> NUTRIENT_DEFICIT
    salinity                           -> SALINITY_STRESS
    photosystem_stress, pest_pressure  -> CHLOROPHYLL_STRESS

Two or more of those *state buckets* breached at once escalates to
MULTI_STRESS, matching "2+ stress domains simultaneously breached" in the
design doc. INTERVENTION_APPLIED is not auto-triggered (no L7/L8 actuation
exists yet) — it's exposed via mark_intervention_applied() for future
wiring, and holds until readings clear rather than being pre-empted by the
next stress evaluation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dyon.reactive.fsm_engine import MultiStateFSMRuleEngine

from reactive.growth_stage_tracker import GrowthStageTracker
from reactive.stress_rules import evaluate_stress_rules, load_stress_rules

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import CacheStore, DocumentStore, TimeSeriesStore

CATEGORY_TO_STATE = {
    "drought": "WATER_STRESS",
    "heat_stress": "WATER_STRESS",
    "frost": "WATER_STRESS",
    "nutrient_deficiency": "NUTRIENT_DEFICIT",
    "salinity": "SALINITY_STRESS",
    "photosystem_stress": "CHLOROPHYLL_STRESS",
    "pest_pressure": "CHLOROPHYLL_STRESS",
}


def bucket_categories_to_state(category_severities: dict[str, str | None]) -> str:
    """
    Map {category: "critical"|"warning"|None} (evaluate_stress_rules'
    output) to one of the FSM's named states. Pure function — shared by
    the live PlantHealthFSM and the webapp's what-if simulator
    (webapp/backend/app.py) so both use identical bucketing logic.
    """
    critical_states = {
        CATEGORY_TO_STATE[c] for c, sev in category_severities.items() if sev == "critical"
    }
    warning_states = {
        CATEGORY_TO_STATE[c] for c, sev in category_severities.items() if sev == "warning"
    }
    active_states = critical_states or warning_states
    if len(active_states) >= 2:
        return "MULTI_STRESS"
    if len(active_states) == 1:
        return next(iter(active_states))
    return "HEALTHY"


class PlantHealthFSM(MultiStateFSMRuleEngine):
    layer_name = "reactive"

    _states = [
        "INITIALISING",
        "HEALTHY",
        "WATER_STRESS",
        "NUTRIENT_DEFICIT",
        "SALINITY_STRESS",
        "CHLOROPHYLL_STRESS",
        "MULTI_STRESS",
        "INTERVENTION_APPLIED",
    ]
    _initial_state = "INITIALISING"
    _severity_map = {
        "WATER_STRESS": "warning",
        "NUTRIENT_DEFICIT": "warning",
        "SALINITY_STRESS": "warning",
        "CHLOROPHYLL_STRESS": "warning",
        "MULTI_STRESS": "critical",
    }

    def __init__(
        self,
        config: "TwinConfig",
        event_bus: "EventBus",
        *,
        ts_store: "TimeSeriesStore",
        cache: "CacheStore",
        doc_store: "DocumentStore",
        stress_rules_path: str = "config/stress_thresholds.yaml",
        eval_interval: int = 60,
    ):
        super().__init__(
            config,
            event_bus,
            ts_store=ts_store,
            cache=cache,
            doc_store=doc_store,
            eval_interval=eval_interval,
        )
        self._rules = load_stress_rules(stress_rules_path)
        self._stage_tracker: GrowthStageTracker | None = None

    async def initialise(self) -> None:
        self._stage_tracker = GrowthStageTracker(self.bus)

    def mark_intervention_applied(self) -> None:
        """For future L7/L8 use: record that a prescribed action was dispatched."""
        self._transition_to("INTERVENTION_APPLIED")

    def compute_desired_state(self, readings: dict[str, float | None]) -> str | None:
        stage = self._stage_tracker.current_stage if self._stage_tracker else "germination"
        result = evaluate_stress_rules(self._rules, readings, stage)

        any_active = any(sev is not None for sev in result.values())
        if self.state == "INTERVENTION_APPLIED" and any_active:  # type: ignore[attr-defined]
            return None  # hold until recovery, don't get pre-empted by ongoing stress

        return bucket_categories_to_state(result)
