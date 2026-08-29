"""
Decision support built on the twin's own models.

Nothing here invents new science. Irrigation depth comes from the same
Penman-Monteith transpiration the simulation layer runs; the projection is the
EKF's process model rolled forward; the growth-stage forecast uses the thermal
time thresholds in config/maize_phenology.yaml. The value added is turning them
into a quantity and a date a farmer can act on.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

# Rooting depth over which the soil moisture probe is taken to be representative.
# Maize in early vegetative stages draws mostly from the top 300 mm.
ROOT_ZONE_MM = 300.0

# Irrigation efficiency for smallholder surface application. Applying exactly
# the deficit under-delivers, because not all of it reaches the root zone.
APPLICATION_EFFICIENCY = 0.75

# Refill when the profile has dried to this fraction of the way from the
# critical point up to field capacity, the standard "refill point" heuristic.
REFILL_FRACTION = 0.5


class IrrigationAdvice(BaseModel):
    should_irrigate: bool
    depth_mm: Optional[float] = Field(
        default=None, description="Gross depth to apply, including application losses")
    deficit_mm: Optional[float] = Field(
        default=None, description="Water missing from the root zone right now")
    daily_use_mm: Optional[float] = Field(
        default=None, description="Modelled crop water use per day at current conditions")
    days_of_water_left: Optional[float] = None
    best_time: str
    reason: str
    confidence: Literal["measured", "partly-modelled"]


class Projection(BaseModel):
    """Where the crop is heading if nothing is done."""
    horizon_hours: int
    projected_state: str
    projected_health: float
    changes: list[str]
    confidence: float
    summary: str


class StageForecast(BaseModel):
    current_stage: str
    next_stage: Optional[str]
    gdd_accumulated: Optional[float]
    gdd_to_next: Optional[float]
    days_to_next: Optional[float]
    summary: str


class InterventionIn(BaseModel):
    kind: Literal["irrigation", "fertiliser", "lime", "pest_treatment", "other"]
    note: str = Field(default="", max_length=400)
    amount: Optional[float] = None
    unit: Optional[str] = Field(default=None, max_length=20)


class InterventionOut(BaseModel):
    id: str
    kind: str
    note: str
    amount: Optional[float]
    unit: Optional[str]
    logged_at: datetime
    state_at_logging: str
    health_at_logging: float
    # Filled in once the twin has had a chance to respond.
    outcome: Literal["pending", "improved", "unchanged", "worsened"]
    outcome_detail: str


def irrigation_advice(
    soil_moisture: float,
    band: dict,
    transpiration_mm_hr: Optional[float],
    all_measured: bool,
) -> IrrigationAdvice:
    """
    Convert a moisture reading into a depth of water.

    `band` is the growth-stage band for soil_moisture from sensor_profiles.yaml:
    its nominal is treated as the target (field capacity for practical purposes)
    and crit_low as the point below which the crop is in trouble.
    """
    target = band.get("nominal")
    crit = band.get("crit_low")
    warn = band.get("warn_low")

    if target is None or crit is None:
        return IrrigationAdvice(
            should_irrigate=False, best_time="—",
            reason="No irrigation thresholds are defined for this growth stage.",
            confidence="partly-modelled",
        )

    # Volumetric water content is a fraction, so a deficit over the root zone
    # converts to millimetres by multiplying by the depth.
    deficit_mm = max(0.0, (target - soil_moisture) * ROOT_ZONE_MM)
    refill_point = crit + REFILL_FRACTION * (target - crit)

    daily_use = round(transpiration_mm_hr * 24, 2) if transpiration_mm_hr else None
    days_left = None
    if daily_use and daily_use > 0:
        available_mm = max(0.0, (soil_moisture - crit) * ROOT_ZONE_MM)
        days_left = round(available_mm / daily_use, 1)

    confidence = "measured" if all_measured else "partly-modelled"

    if soil_moisture <= crit:
        return IrrigationAdvice(
            should_irrigate=True,
            depth_mm=round(deficit_mm / APPLICATION_EFFICIENCY, 1),
            deficit_mm=round(deficit_mm, 1),
            daily_use_mm=daily_use, days_of_water_left=days_left,
            best_time="As soon as possible",
            reason=(f"Soil moisture is at {soil_moisture:.3f}, below the critical "
                    f"level of {crit:.3f} for this stage. The crop is already losing yield."),
            confidence=confidence,
        )

    if soil_moisture <= refill_point:
        return IrrigationAdvice(
            should_irrigate=True,
            depth_mm=round(deficit_mm / APPLICATION_EFFICIENCY, 1),
            deficit_mm=round(deficit_mm, 1),
            daily_use_mm=daily_use, days_of_water_left=days_left,
            best_time="Early morning, before 9am",
            reason=(f"Soil moisture is at {soil_moisture:.3f}, past the refill point "
                    f"of {refill_point:.3f}. Watering now avoids stress rather than "
                    f"recovering from it."),
            confidence=confidence,
        )

    headroom = f"about {days_left} days of water left" if days_left else "the profile is well supplied"
    return IrrigationAdvice(
        should_irrigate=False,
        deficit_mm=round(deficit_mm, 1),
        daily_use_mm=daily_use, days_of_water_left=days_left,
        best_time="Not needed yet",
        reason=(f"Soil moisture is at {soil_moisture:.3f}, above the refill point of "
                f"{refill_point:.3f} — {headroom}."
                + (f" Warning level is {warn:.3f}." if warn is not None else "")),
        confidence=confidence,
    )


def describe_projection(
    now_readings: dict[str, float],
    future_readings: dict[str, float],
    now_state: str,
    future_state: str,
    now_health: float,
    future_health: float,
    horizon_hours: int,
    confidence: float,
    labels: dict[str, str],
) -> Projection:
    """Put a forward simulation into words a farmer can act on."""
    changes: list[str] = []
    for field, future in future_readings.items():
        current = now_readings.get(field)
        if current is None or current == 0:
            continue
        delta = (future - current) / abs(current)
        if abs(delta) >= 0.08:      # below this the movement is not worth reporting
            direction = "rises" if delta > 0 else "falls"
            changes.append(f"{labels.get(field, field)} {direction} "
                           f"{abs(delta) * 100:.0f}%")

    if future_state != now_state:
        summary = (f"If nothing changes, the crop moves from "
                   f"{now_state.replace('_', ' ').lower()} to "
                   f"{future_state.replace('_', ' ').lower()} within about "
                   f"{horizon_hours} hours.")
    elif future_health < now_health - 5:
        summary = (f"If nothing changes, condition declines over the next "
                   f"{horizon_hours} hours but stays within "
                   f"{now_state.replace('_', ' ').lower()}.")
    else:
        summary = (f"If nothing changes, the crop stays roughly as it is over the "
                   f"next {horizon_hours} hours.")

    return Projection(
        horizon_hours=horizon_hours,
        projected_state=future_state,
        projected_health=round(future_health, 1),
        changes=changes[:4],
        confidence=round(confidence, 2),
        summary=summary,
    )


def stage_forecast(phenology: dict, stage: str, gdd: Optional[float],
                   mean_daily_gdd: Optional[float]) -> StageForecast:
    """Where the crop is in its life, and roughly when the next stage arrives."""
    # growth_stages is an ordered list of dicts keyed by "name", not a mapping.
    raw = phenology.get("growth_stages") or []
    stages = {entry["name"]: entry for entry in raw if isinstance(entry, dict)}
    order = [entry["name"] for entry in raw if isinstance(entry, dict)]

    current = stages.get(stage, {})
    gdd_end = current.get("gdd_end")

    next_stage = None
    if stage in order:
        idx = order.index(stage)
        if idx + 1 < len(order):
            next_stage = order[idx + 1]

    gdd_to_next = None
    days = None
    if gdd is not None and gdd_end is not None:
        gdd_to_next = max(0.0, float(gdd_end) - float(gdd))
        if mean_daily_gdd and mean_daily_gdd > 0:
            days = round(gdd_to_next / mean_daily_gdd, 1)

    pretty = lambda s: s.replace("_", " ").title() if s else None
    if next_stage and days is not None:
        summary = (f"{pretty(stage)} now. {pretty(next_stage)} expected in about "
                   f"{days:.0f} days at the current rate of heat accumulation.")
    elif next_stage:
        summary = f"{pretty(stage)} now. {pretty(next_stage)} is next."
    else:
        summary = f"{pretty(stage)} — the final stage."

    return StageForecast(
        current_stage=stage, next_stage=next_stage,
        gdd_accumulated=round(gdd, 1) if gdd is not None else None,
        gdd_to_next=round(gdd_to_next, 1) if gdd_to_next is not None else None,
        days_to_next=days, summary=summary,
    )
