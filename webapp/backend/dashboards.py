"""
Role-scoped dashboard payloads.

Both roles read the same twin state; they differ in what that state is turned
into. The researcher payload exposes every field with its band and provenance so
a value can be judged. The farmer payload answers three questions -- is the crop
in trouble, why, and what should I do today -- and deliberately omits fields that
cannot be acted on.

Provenance is carried explicitly. The twin models nineteen fields, and any of
them may be absent from InfluxDB, in which case the stage nominal from
sensor_profiles.yaml stands in. A nominal is not a measurement, so it is
labelled as such all the way to the client rather than silently blended in.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

Provenance = Literal["measured", "nominal"]

# Which fields belong to which stress story, so the client can group them
# without hard-coding the twin's taxonomy in TypeScript.
FIELD_GROUPS: dict[str, list[str]] = {
    "Soil": ["soil_moisture", "soil_ec", "soil_nitrogen",
             "soil_phosphorus", "soil_potassium"],
    "Canopy": ["canopy_temperature", "canopy_air_delta"],
    "Spectral": ["ndvi", "pri", "red_edge_slope"],
    "Fluorescence": ["fv_fm", "phi_psii"],
    "Volatiles": ["ethylene", "isoprene", "hexenal"],
    "Atmosphere": ["air_temperature", "relative_humidity", "co2", "par"],
}

FIELD_LABELS: dict[str, str] = {
    "soil_moisture": "Soil Moisture", "soil_ec": "Soil EC",
    "soil_nitrogen": "Nitrogen", "soil_phosphorus": "Phosphorus",
    "soil_potassium": "Potassium", "ndvi": "NDVI", "pri": "PRI",
    "red_edge_slope": "Red-Edge Slope", "canopy_temperature": "Canopy Temperature",
    "canopy_air_delta": "Canopy–Air Delta", "fv_fm": "Fv/Fm", "phi_psii": "ΦPSII",
    "ethylene": "Ethylene", "isoprene": "Isoprene", "hexenal": "Hexenal",
    "air_temperature": "Air Temperature", "relative_humidity": "Relative Humidity",
    "co2": "CO₂", "par": "PAR",
}

# What each FSM state means to someone standing in a field, and what to do.
FARMER_GUIDANCE: dict[str, dict[str, str]] = {
    "INITIALISING": {
        "headline": "Starting up",
        "detail": "The twin has not completed its first reading cycle yet.",
        "action": "Check back in a few minutes.",
        "tone": "neutral",
    },
    "HEALTHY": {
        "headline": "The crop is comfortable",
        "detail": "No stress rule is currently breached.",
        "action": "No action needed today. Keep to your normal routine.",
        "tone": "good",
    },
    "WATER_STRESS": {
        "headline": "The crop is short of water",
        "detail": "Canopy temperature and soil moisture indicate the plant is "
                  "struggling to draw enough water.",
        "action": "Irrigate as soon as you can, ideally early morning or evening.",
        "tone": "bad",
    },
    "NUTRIENT_DEFICIT": {
        "headline": "The crop is short of nutrients",
        "detail": "One or more of nitrogen, phosphorus or potassium has fallen "
                  "below the level this growth stage needs.",
        "action": "Plan a top-dressing. Check which nutrient is low before buying "
                  "fertiliser.",
        "tone": "bad",
    },
    "SALINITY_STRESS": {
        "headline": "Salt is building up in the soil",
        "detail": "Soil electrical conductivity is above the safe range, which "
                  "makes it harder for roots to take up water.",
        "action": "Leach the field with clean water if you can, and review your "
                  "fertiliser rate.",
        "tone": "bad",
    },
    "CHLOROPHYLL_STRESS": {
        "headline": "The leaves are under strain",
        "detail": "Photosynthesis indicators have dropped, which can mean heat, "
                  "disease or pest pressure.",
        "action": "Inspect the leaves closely for pests, spots or discolouration.",
        "tone": "bad",
    },
    "MULTI_STRESS": {
        "headline": "The crop is under stress from more than one cause",
        "detail": "Two or more separate stress conditions are active at once.",
        "action": "Deal with water first, then nutrients. Consider asking an "
                  "extension officer to visit.",
        "tone": "critical",
    },
    "INTERVENTION_APPLIED": {
        "headline": "Waiting to see if your action worked",
        "detail": "An intervention was recorded and the twin is watching for the "
                  "readings to recover.",
        "action": "Give it time, and keep watching the readings.",
        "tone": "neutral",
    },
}


class FieldReading(BaseModel):
    field: str
    label: str
    value: Optional[float]
    unit: str
    provenance: Provenance
    status: Literal["ok", "warning", "critical", "unknown"]
    nominal: Optional[float] = None
    warn_low: Optional[float] = None
    warn_high: Optional[float] = None
    crit_low: Optional[float] = None
    crit_high: Optional[float] = None


class ResearcherDashboard(BaseModel):
    #: Minutes since the pod last reported. None means it never has. The
    #: dashboard needs this to tell a live node from one that stopped.
    measured_age_min: Optional[float] = None
    growth_stage: str
    health_score: float
    plant_state: str
    active_categories: dict[str, str]
    groups: dict[str, list[FieldReading]]
    measured_count: int
    total_count: int
    calibrated_vcmax25: Optional[float] = None
    calibrated_bb_slope_m: Optional[float] = None


class FarmerDashboard(BaseModel):
    growth_stage: str
    headline: str
    detail: str
    action: str
    tone: str
    plant_state: str
    health_score: float
    highlights: list[FieldReading]
    measured_count: int
    total_count: int


def _band(profiles: dict, field: str, stage: str) -> dict:
    return profiles.get(field, {}).get("by_stage", {}).get(stage, {}) or {}


def _status(band: dict, value: Optional[float]) -> str:
    """Classify a value against its stage band, mirroring health_score's logic."""
    if value is None:
        return "unknown"
    for key, worse in (("crit_low", True), ("crit_high", False)):
        limit = band.get(key)
        if limit is not None and ((value < limit) if worse else (value > limit)):
            return "critical"
    for key, worse in (("warn_low", True), ("warn_high", False)):
        limit = band.get(key)
        if limit is not None and ((value < limit) if worse else (value > limit)):
            return "warning"
    return "ok"


def build_reading(profiles: dict, field: str, value: Optional[float],
                  stage: str, provenance: Provenance) -> FieldReading:
    band = _band(profiles, field, stage)
    return FieldReading(
        field=field,
        label=FIELD_LABELS.get(field, field.replace("_", " ").title()),
        value=value,
        unit=profiles.get(field, {}).get("unit", ""),
        provenance=provenance,
        status=_status(band, value),
        nominal=band.get("nominal"),
        warn_low=band.get("warn_low"), warn_high=band.get("warn_high"),
        crit_low=band.get("crit_low"), crit_high=band.get("crit_high"),
    )
