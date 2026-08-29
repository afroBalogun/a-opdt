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
        if override and current_stage in override.get("stages", []):
            warning_conditions = override.get("warning", conditions.get("warning", {}))
            critical_conditions = override.get("critical", conditions.get("critical", {}))
        else:
            warning_conditions = conditions.get("warning", {})
            critical_conditions = conditions.get("critical", {})

        if _tier_breached(critical_conditions, readings):
            result[category] = "critical"
        elif _tier_breached(warning_conditions, readings):
            result[category] = "warning"
        else:
            result[category] = None

    return result
