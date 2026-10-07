"""Evaluates config/stress_thresholds.yaml against current sensor readings.

Each stress category lists one or more "*_below"/"*_above" conditions per
tier (warning/critical). A tier fires only when ALL of its conditions are
breached simultaneously — requiring multi-sensor corroboration before
raising an alert, consistent with the framework paper's emphasis on fused,
multi-modal stress detection over single-sensor triggers.
"""

from __future__ import annotations

import yaml

Severity = str  # "critical" | "warning" | None


def load_stress_rules(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)["rules"]


def _tier_breached(conditions: dict, readings: dict[str, float | None]) -> bool:
    if not conditions:
        return False
    for key, threshold in conditions.items():
        if key.endswith("_below"):
            field = key[: -len("_below")]
        elif key.endswith("_above"):
            field = key[: -len("_above")]
        else:
            continue
        value = readings.get(field)
        if value is None:
            return False
        if key.endswith("_below") and not (value < threshold):
            return False
        if key.endswith("_above") and not (value > threshold):
            return False
    return True


def evaluate_stress_rules(
    rules: dict,
    readings: dict[str, float | None],
    current_stage: str,
) -> dict[str, Severity]:
    """Return {category_name: "critical" | "warning" | None} for every rule."""
    result: dict[str, Severity] = {}

    for category, spec in rules.items():
        conditions = spec["conditions"]
        override = spec.get("critical_stage_override")
        warning_conditions = dict(conditions.get("warning", {}))
        critical_conditions = dict(conditions.get("critical", {}))
        if override and current_stage in override.get("stages", []):
            # An override tightens the thresholds it names and keeps every
            # other condition of the tier. Replacing the tier wholesale
            # silently dropped corroborating fields (e.g. heat stress at
            # anthesis fired on canopy temperature alone, without isoprene),
            # contradicting the multi-sensor corroboration rule above.
            # A rule that really wants a single-condition tier during a
            # stage sets `mode: replace` on its override.
            if override.get("mode", "merge") == "replace":
                warning_conditions = dict(override.get("warning", warning_conditions))
                critical_conditions = dict(override.get("critical", critical_conditions))
            else:
                warning_conditions.update(override.get("warning", {}))
                critical_conditions.update(override.get("critical", {}))

        if _tier_breached(critical_conditions, readings):
            result[category] = "critical"
        elif _tier_breached(warning_conditions, readings):
            result[category] = "warning"
        else:
            result[category] = None

    return result
