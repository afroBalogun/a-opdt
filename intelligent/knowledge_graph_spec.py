"""L7 foundation: builds a maize-specific Neo4j KnowledgeGraphSpec.

Dyon's KnowledgeGraph/KnowledgeGraphSpec (dyon/intelligent/knowledge_graph.py)
uses an industrial-asset vocabulary — Component / FailureMode / Symptom /
MaintenanceAction — reused here for the plant domain rather than
reimplementing an equivalent schema:

    Component          -> physiological subsystem (soil water balance, canopy
                           thermal regulation, photosystem, root zone, pest
                           defense)
    FailureMode         -> one of config/stress_thresholds.yaml's 7 stress
                           categories
    Symptom             -> the category's single most diagnostic trigger
                           field/threshold (the full multi-condition logic
                           stays authoritative in reactive/stress_rules.py —
                           this is a simpler, queryable explanatory layer,
                           not a duplicate of the FSM's precise evaluation)
    MaintenanceAction   -> the corresponding farmer-facing intervention

Severity labels follow the architecture doc's Orchestrator Priority Queue
(section 12.2: water=1/critical, heat=2/critical, salinity=3/high,
nutrient=4/medium, pest=5/low); frost and photosystem_stress aren't ranked
there, so frost is placed at critical (irreversible cold damage) and
photosystem_stress at high (photoinhibition severity) as reasoned defaults.
"""

from __future__ import annotations

import yaml

from dyon.intelligent.knowledge_graph import (
    FailureMode,
    KnowledgeGraphSpec,
    SymptomMapping,
)

_COMPONENTS = [
    "soil_water_balance",
    "canopy_thermal_regulation",
    "photosystem",
    "root_zone",
    "pest_pathogen_defense",
]

# category -> (component, severity, maintenance_action, primary_field, direction)
_CATEGORY_SPEC = {
    "drought":              ("soil_water_balance",       "critical", "irrigate"),
    "heat_stress":          ("canopy_thermal_regulation", "critical", "shade_or_mist_cooling"),
    "frost":                ("canopy_thermal_regulation", "critical", "frost_protection_cover"),
    "photosystem_stress":   ("photosystem",               "high",     "reduce_light_exposure"),
    "salinity":             ("root_zone",                 "high",     "leaching_irrigation"),
    "nutrient_deficiency":  ("root_zone",                 "medium",   "organic_fertilizer_application"),
    "pest_pressure":        ("pest_pathogen_defense",     "low",      "biocontrol_application"),
}


def _primary_condition(critical_conditions: dict) -> tuple[str, float, str]:
    """First critical-tier condition in the category -> (field, threshold, direction)."""
    key, threshold = next(iter(critical_conditions.items()))
    if key.endswith("_below"):
        return key[: -len("_below")], threshold, "low"
    return key[: -len("_above")], threshold, "high"


def build_maize_kg_spec(stress_rules_path: str = "config/stress_thresholds.yaml") -> KnowledgeGraphSpec:
    with open(stress_rules_path) as f:
        rules = yaml.safe_load(f)["rules"]

    failure_modes: list[FailureMode] = []
    symptom_mappings: list[SymptomMapping] = []

    for category, spec in rules.items():
        component, severity, action = _CATEGORY_SPEC[category]
        field, threshold, direction = _primary_condition(spec["conditions"]["critical"])

        failure_modes.append(
            FailureMode(
                name=category,
                severity=severity,
                maintenance_actions=[action],
                affected_components=[component],
            )
        )
        symptom_mappings.append(
            SymptomMapping(
                symptom_name=f"{category}_critical",
                sensor_field=field,
                threshold=threshold,
                failure_modes=[category],
                direction=direction,
            )
        )

    return KnowledgeGraphSpec(
        components=_COMPONENTS,
        failure_modes=failure_modes,
        symptom_mappings=symptom_mappings,
    )
