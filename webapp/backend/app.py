"""WhatIf simulation API — backs the scientist-facing web frontend.

Doesn't run its own physiology model: it calls the exact same pure
functions the live twin uses (simulation/farquhar_c4.py,
simulation/penman_monteith.py, reactive/stress_rules.py,
reactive/health_score.py::score_field, reactive/health_fsm.py::
bucket_categories_to_state), just with hypothetical inputs from the
frontend instead of live sensor readings. This is a separate process
from twin.py — reads/writes the same InfluxDB/Redis, never touches the
live twin's own state.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from datetime import datetime, timezone

import pandas as pd
import yaml
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from dyon.core.config import TwinConfig
from dyon.data.storage.influx import InfluxAdapter
from dyon.data.storage.mongo import MongoAdapter
from dyon.data.storage.redis_store import RedisAdapter

from webapp.backend import advisory as adv
from webapp.backend import auth as auth_mod
from webapp.backend.dashboards import (
    FARMER_GUIDANCE, FIELD_GROUPS, FIELD_LABELS, FarmerDashboard, FieldReading,
    ResearcherDashboard, build_reading,
)

from reactive.health_fsm import bucket_categories_to_state
from reactive.health_score import score_field
from reactive.stress_rules import evaluate_stress_rules, load_stress_rules
from simulation.farquhar_c4 import (
    solve_farquhar_ball_berry,
    vapor_pressure_deficit_kpa,
    water_stress_factor,
)
from simulation.penman_monteith import transpiration_mm_per_hour

# Duplicated from twin.py's SENSOR_FIELD_NAMES rather than imported, so this
# API doesn't depend on importing the twin's entry-point script.
SENSOR_FIELD_NAMES = [
    "soil_moisture", "soil_ec", "soil_nitrogen", "soil_phosphorus", "soil_potassium",
    "ndvi", "pri", "red_edge_slope",
    "canopy_temperature", "canopy_air_delta",
    "fv_fm", "phi_psii",
    "ethylene", "isoprene", "hexenal",
    "air_temperature", "relative_humidity", "co2", "par",
]

# Same switch the twin uses (A_OPDT_ENV_FILE), so the API and the twin always
# read the same backend. If they disagreed, the dashboard would quietly show
# data from a different database than the one being written.
_ENV_FILE = Path(__file__).resolve().parents[2] / os.getenv("A_OPDT_ENV_FILE", ".env")

# pydantic-settings reads the file into the config object but never os.environ,
# so modules that use plain os.getenv - webapp.backend.auth, which owns its own
# Mongo connection - would not see it. Load it properly as well.
load_dotenv(_ENV_FILE)

config = TwinConfig(_env_file=_ENV_FILE)
ts_store = InfluxAdapter(config)
cache = RedisAdapter(config)
# Event log. Constructed here rather than lazily because MongoClient does not
# connect on construction - the first real operation does - so an unreachable
# database cannot stop the API from starting.
doc_store = MongoAdapter(config)

with open("config/sensor_profiles.yaml") as f:
    PROFILES: dict = yaml.safe_load(f)
STRESS_RULES: dict = load_stress_rules("config/stress_thresholds.yaml")

app = FastAPI(title="A-OPDT WhatIf Simulator")
app.add_middleware(
    CORSMiddleware,
    # Vite picks the next free port when 5173 is taken, so allow the usual few
    # rather than failing with an opaque CORS error the first time it shifts.
    allow_origins=os.getenv(
        "AOPDT_CORS_ORIGINS",
        ",".join(f"http://{h}:{p}" for h in ("localhost", "127.0.0.1")
                 for p in (5173, 5174, 5175)),
    ).split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


class ReadingsSnapshot(BaseModel):
    growth_stage: str
    soil_moisture: float
    soil_ec: float
    soil_nitrogen: float
    soil_phosphorus: float
    soil_potassium: float
    ndvi: float
    pri: float
    red_edge_slope: float
    canopy_temperature: float
    canopy_air_delta: float
    fv_fm: float
    phi_psii: float
    ethylene: float
    isoprene: float
    hexenal: float
    air_temperature: float
    relative_humidity: float
    co2: float
    par: float


class SimulationResult(BaseModel):
    net_assimilation: float
    stomatal_conductance: float
    transpiration_mm_hr: float
    water_stress_beta: float
    health_score: float
    projected_state: str
    active_categories: dict[str, str]


def _fallback_value(field: str, stage: str) -> float:
    """Stage-nominal value, used when InfluxDB has no reading yet (e.g. a
    fresh environment before the twin has published its first cycle)."""
    band = PROFILES.get(field, {}).get("by_stage", {}).get(stage, {})
    return float(band.get("nominal", 0.0))


@app.get("/api/current-state", response_model=ReadingsSnapshot)
def current_state() -> ReadingsSnapshot:
    stage = cache.get_latest_cached("growth_stage") or "germination"
    values = {}
    for field in SENSOR_FIELD_NAMES:
        raw = ts_store.get_latest(field)
        values[field] = raw if raw is not None else _fallback_value(field, stage)
    return ReadingsSnapshot(growth_stage=stage, **values)


@app.post("/api/simulate", response_model=SimulationResult)
def simulate(snapshot: ReadingsSnapshot) -> SimulationResult:
    stage = snapshot.growth_stage
    readings = snapshot.model_dump(exclude={"growth_stage"})

    band = PROFILES.get("soil_moisture", {}).get("by_stage", {}).get(stage)
    beta = (
        water_stress_factor(snapshot.soil_moisture, band["crit_low"], band["nominal"])
        if band else 1.0
    )

    # Reuse the twin's own current calibration (if the L8 Twin Calibration
    # Agent has run yet), so projections reflect the live twin's current
    # understanding rather than always the literature defaults.
    calibrated_vcmax25 = cache.get_latest_cached("calibrated_vcmax25")
    calibrated_bb_slope = cache.get_latest_cached("calibrated_bb_slope_m")

    result = solve_farquhar_ball_berry(
        leaf_temp_c=snapshot.canopy_temperature,
        par_umol_m2_s=snapshot.par,
        co2_ppm=snapshot.co2,
        air_temp_c=snapshot.air_temperature,
        relative_humidity_pct=snapshot.relative_humidity,
        water_stress_beta=beta,
        vcmax25_override=float(calibrated_vcmax25) if calibrated_vcmax25 is not None else None,
        bb_slope_m_override=float(calibrated_bb_slope) if calibrated_bb_slope is not None else None,
    )
    vpd = vapor_pressure_deficit_kpa(snapshot.air_temperature, snapshot.relative_humidity)
    transpiration = transpiration_mm_per_hour(result.stomatal_conductance, vpd)

    category_severities = evaluate_stress_rules(STRESS_RULES, readings, stage)
    health_score = _health_from(stage, readings, category_severities)
    projected_state = bucket_categories_to_state(category_severities)
    active_categories = {c: sev for c, sev in category_severities.items() if sev is not None}

    return SimulationResult(
        net_assimilation=result.net_assimilation,
        stomatal_conductance=result.stomatal_conductance,
        transpiration_mm_hr=transpiration,
        water_stress_beta=beta,
        health_score=health_score,
        projected_state=projected_state,
        active_categories=active_categories,
    )


# ── Accounts ────────────────────────────────────────────────────────────────

@app.post("/api/auth/register", response_model=auth_mod.AuthResponse)
def api_register(req: auth_mod.RegisterRequest) -> auth_mod.AuthResponse:
    return auth_mod.register(req)


@app.post("/api/auth/login", response_model=auth_mod.AuthResponse)
def api_login(req: auth_mod.LoginRequest) -> auth_mod.AuthResponse:
    return auth_mod.login(req)


@app.get("/api/auth/me", response_model=auth_mod.UserOut)
def api_me(user: auth_mod.UserOut = Depends(auth_mod.current_user)) -> auth_mod.UserOut:
    return user


# ── Shared twin state ───────────────────────────────────────────────────────

def _read_twin_state() -> tuple[str, dict[str, float], set[str]]:
    """
    Latest reading per field, plus which of them came from the time series.

    A field absent from InfluxDB falls back to its stage nominal so the models
    still run, but the caller is told which is which -- a nominal standing in
    for an unmeasured field is not evidence about this plant.
    """
    stage = cache.get_latest_cached("growth_stage") or "germination"
    values: dict[str, float] = {}
    measured: set[str] = set()
    for field in SENSOR_FIELD_NAMES:
        raw = ts_store.get_latest(field)
        if raw is None:
            values[field] = _fallback_value(field, stage)
        else:
            values[field] = raw
            measured.add(field)
    return stage, values, measured


#: A plant carrying an active stress cannot be in perfect health, whatever the
#: band scoring says.
#:
#: health_score and the stress categories read two configs that disagree.
#: stress_thresholds.yaml uses one fixed threshold per nutrient
#: (soil_nitrogen_below: 30.0), while sensor_profiles.yaml's warn_low moves with
#: growth stage (25.0 at germination, 30.0 vegetative_early, 35.0
#: vegetative_late). At germination, nitrogen of 27 ppm trips the stress rule
#: but sits inside its band - a nutrient_deficiency warning beside a health
#: score of 100. Later in the season the disagreement reverses.
#:
#: Reconciling the two configs is the real fix and needs an agronomic decision
#: about which thresholds are right. Until then these ceilings keep the headline
#: number consistent with the categories shown next to it.
_STRESS_CEILING = {"warning": 80.0, "critical": 50.0}


def _health_from(stage: str, readings: dict[str, Optional[float]],
                 severities: dict[str, Optional[str]]) -> float:
    """Band-violation score, capped by any active stress category."""
    scored = {f: v for f, v in readings.items() if v is not None}
    if not scored:
        return 0.0
    share = 100.0 / len(scored)
    violations = sum(score_field(PROFILES, f, v, stage) for f, v in scored.items())
    health = max(0.0, 100.0 - share * violations)

    for severity in severities.values():
        ceiling = _STRESS_CEILING.get(severity)
        if ceiling is not None:
            health = min(health, ceiling)
    return health


def _assess(stage: str, readings: dict[str, Optional[float]]) -> tuple[float, str, dict[str, str]]:
    """Health score, FSM state and active stress categories for a reading set."""
    severities = evaluate_stress_rules(STRESS_RULES, readings, stage)
    health = _health_from(stage, readings, severities)
    state = bucket_categories_to_state(severities)
    active = {c: sev for c, sev in severities.items() if sev is not None}
    return health, state, active


# ── Role dashboards ─────────────────────────────────────────────────────────

@app.get("/api/dashboard/researcher", response_model=ResearcherDashboard)
def researcher_dashboard(
    user: auth_mod.UserOut = Depends(auth_mod.require_role("researcher")),
) -> ResearcherDashboard:
    stage, values, measured = _read_twin_state()
    health, state, active = _assess(stage, values)

    groups: dict[str, list[FieldReading]] = {}
    for group, fields in FIELD_GROUPS.items():
        groups[group] = [
            build_reading(PROFILES, f, values[f], stage,
                          "measured" if f in measured else "nominal")
            for f in fields
        ]

    vcmax = cache.get_latest_cached("calibrated_vcmax25")
    bb_slope = cache.get_latest_cached("calibrated_bb_slope_m")

    return ResearcherDashboard(
        growth_stage=stage,
        health_score=health,
        plant_state=state,
        active_categories=active,
        groups=groups,
        measured_count=len(measured),
        total_count=len(SENSOR_FIELD_NAMES),
        calibrated_vcmax25=float(vcmax) if vcmax is not None else None,
        calibrated_bb_slope_m=float(bb_slope) if bb_slope is not None else None,
    )


@app.get("/api/dashboard/farmer", response_model=FarmerDashboard)
def farmer_dashboard(
    user: auth_mod.UserOut = Depends(auth_mod.require_role("farmer")),
) -> FarmerDashboard:
    stage, values, measured = _read_twin_state()
    health, state, active = _assess(stage, values)
    guidance = FARMER_GUIDANCE.get(state, FARMER_GUIDANCE["INITIALISING"])

    # Show the fields a farmer can actually act on, and anything currently out
    # of band, rather than all nineteen.
    actionable = ["soil_moisture", "canopy_air_delta", "soil_nitrogen",
                  "soil_ec", "air_temperature"]
    highlights = [
        build_reading(PROFILES, f, values[f], stage,
                      "measured" if f in measured else "nominal")
        for f in actionable
    ]
    for group in FIELD_GROUPS.values():
        for f in group:
            if f in actionable:
                continue
            reading = build_reading(PROFILES, f, values[f], stage,
                                    "measured" if f in measured else "nominal")
            if reading.status in ("warning", "critical"):
                highlights.append(reading)

    return FarmerDashboard(
        growth_stage=stage,
        headline=guidance["headline"],
        detail=guidance["detail"],
        action=guidance["action"],
        tone=guidance["tone"],
        plant_state=state,
        health_score=health,
        highlights=highlights,
        measured_count=len(measured),
        total_count=len(SENSOR_FIELD_NAMES),
    )


# ── History ─────────────────────────────────────────────────────────────────

WINDOWS = {"1h": 60, "6h": 360, "24h": 1440, "7d": 10080}


@app.get("/api/history")
def history(
    field: str = Query(..., description="Sensor field name"),
    window: str = Query("6h"),
    user: auth_mod.UserOut = Depends(auth_mod.current_user),
):
    """
    Time series for one field, with the growth-stage band alongside so the
    caller can draw the thresholds without a second request.
    """
    if field not in SENSOR_FIELD_NAMES:
        raise HTTPException(404, f"Unknown field '{field}'")
    minutes = WINDOWS.get(window)
    if minutes is None:
        raise HTTPException(400, f"window must be one of {', '.join(WINDOWS)}")

    stage = cache.get_latest_cached("growth_stage") or "germination"
    band = PROFILES.get(field, {}).get("by_stage", {}).get(stage, {}) or {}

    points: list[dict] = []
    try:
        df = ts_store.query_recent(field, minutes=minutes)
        for _, row in df.iterrows():
            ts = row.get("_time")
            points.append({
                "t": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "v": float(row["_value"]),
            })
    except Exception as exc:                      # an empty history is not an error
        logger_msg = f"history query failed for {field}: {exc}"
        print(logger_msg, flush=True)

    return {
        "field": field,
        "label": FIELD_LABELS.get(field, field),
        "unit": PROFILES.get(field, {}).get("unit", ""),
        "window": window,
        "points": points,
        "band": {k: band.get(k) for k in
                 ("nominal", "warn_low", "warn_high", "crit_low", "crit_high")},
    }


@app.get("/api/history/fields")
def history_fields(user: auth_mod.UserOut = Depends(auth_mod.current_user)):
    """Field list with labels and groups, so the client need not hard-code them."""
    return {
        "windows": list(WINDOWS),
        "groups": {g: [{"field": f, "label": FIELD_LABELS.get(f, f),
                        "unit": PROFILES.get(f, {}).get("unit", "")} for f in fields]
                   for g, fields in FIELD_GROUPS.items()},
    }


# ── Escalations ─────────────────────────────────────────────────────────────

@app.get("/api/escalations")
def escalations(
    limit: int = Query(20, ge=1, le=100),
    user: auth_mod.UserOut = Depends(auth_mod.require_role("researcher")),
):
    """
    Cases the twin has referred for human review.

    The escalation protocol writes these when a state change cannot be resolved
    by forward simulation alone. They are the researcher's actual queue, and
    until now they only ever reached a log file.
    """
    raw = cache.get_latest_cached("escalation_log")
    items: list[dict] = []
    if isinstance(raw, str) and raw:
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            items = []
    elif isinstance(raw, list):
        items = raw

    items = sorted(items, key=lambda e: e.get("at", ""), reverse=True)[:limit]
    return {"escalations": items, "count": len(items)}


# ── Event log ───────────────────────────────────────────────────────────────

EVENT_TYPES = (
    "state_change",
    "mas_escalation_response",
    "escalation_resolved_by_simulation",
    "twin_calibration",
)


@app.get("/api/events")
def events(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    event_type: str | None = Query(None),
    user: auth_mod.UserOut = Depends(auth_mod.require_role("researcher")),
):
    """
    The twin's own record of what it did and why.

    Everything here is reasoning rather than measurement: FSM transitions, the
    agent system's answers when a transition could not be explained, escalations
    closed by forward simulation, and the calibration agent refitting the model
    against observed behaviour. It has existed in MongoDB all along and had no
    way to be read.

    `available` distinguishes an empty log from an unreachable database. The
    storage adapter returns [] for both, and rendering "no events yet" over a
    dead connection is the kind of quiet failure this project keeps tripping on.
    """
    try:
        # No public health check on the adapter, and a ping is the only way to
        # tell "empty log" from "cannot reach the database".
        doc_store._client.admin.command("ping")   # noqa: SLF001
        available = True
        detail = None
    except Exception as exc:                        # noqa: BLE001 - reported, not raised
        return {
            "events": [], "count": 0, "available": False,
            "detail": f"Event store unreachable: {type(exc).__name__}",
        }

    # The storage adapter offers a limit but no skip, and the log only grows -
    # 149 events after a day - so paging has to be done against the collection
    # directly. Same reasoning as the ping above: no public API for it yet.
    query: dict = {"asset_id": config.asset_id}
    if event_type:
        query["event_type"] = event_type

    collection = doc_store._events                       # noqa: SLF001
    total = collection.count_documents(query)
    items = list(
        collection.find(query, {"_id": 0})
                  .sort("timestamp", -1)
                  .skip(offset)
                  .limit(limit)
    )

    # timestamp is a datetime from the driver; the response model is untyped so
    # it would serialise inconsistently. Normalise to ISO-8601 here.
    for item in items:
        stamp = item.get("timestamp")
        if hasattr(stamp, "isoformat"):
            item["timestamp"] = stamp.isoformat()
        elif stamp is not None:
            item["timestamp"] = str(stamp)

    return {
        "events": items,
        "count": len(items),
        # `total` is what the client needs to know there is more to fetch; a
        # short page is not proof of the end when a filter is applied.
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(items) < total,
        "available": available,
        "detail": detail,
        "types": list(EVENT_TYPES),
    }


# ── Farmer decision support ─────────────────────────────────────────────────

def _transpiration_now(stage: str, values: dict[str, float]) -> float | None:
    """Current crop water use, from the twin's own physiology model."""
    try:
        band = PROFILES.get("soil_moisture", {}).get("by_stage", {}).get(stage)
        beta = (water_stress_factor(values["soil_moisture"],
                                    band["crit_low"], band["nominal"])
                if band else 1.0)
        result = solve_farquhar_ball_berry(
            leaf_temp_c=values["canopy_temperature"],
            par_umol_m2_s=values["par"],
            co2_ppm=values["co2"],
            air_temp_c=values["air_temperature"],
            relative_humidity_pct=values["relative_humidity"],
            water_stress_beta=beta,
        )
        vpd = vapor_pressure_deficit_kpa(values["air_temperature"],
                                         values["relative_humidity"])
        return transpiration_mm_per_hour(result.stomatal_conductance, vpd)
    except Exception:
        return None


@app.get("/api/farmer/irrigation", response_model=adv.IrrigationAdvice)
def farmer_irrigation(
    user: auth_mod.UserOut = Depends(auth_mod.require_role("farmer")),
) -> adv.IrrigationAdvice:
    stage, values, measured = _read_twin_state()
    band = PROFILES.get("soil_moisture", {}).get("by_stage", {}).get(stage, {}) or {}
    return adv.irrigation_advice(
        soil_moisture=values["soil_moisture"],
        band=band,
        transpiration_mm_hr=_transpiration_now(stage, values),
        all_measured=len(measured) == len(SENSOR_FIELD_NAMES),
    )


@app.get("/api/farmer/projection", response_model=adv.Projection)
def farmer_projection(
    hours: int = Query(48, ge=6, le=168),
    user: auth_mod.UserOut = Depends(auth_mod.current_user),
) -> adv.Projection:
    """
    Where the crop heads if nothing is done.

    Rather than inventing a forecast, this extrapolates each field along its
    recent trend and re-runs the twin's own scoring and stress rules on the
    result, so the projected state is produced by exactly the logic that
    classifies the present one.
    """
    stage, values, _ = _read_twin_state()
    now_health, now_state, _ = _assess(stage, values)

    # InfluxDB timestamps are real time, but the mock sensing layer advances the
    # crop's clock far faster: days_per_real_second: 0.1 means one real hour is
    # 360 crop days. A slope measured per real hour therefore cannot be applied
    # over a horizon expressed in crop hours without converting first -- doing so
    # overstated every trend by that same factor.
    with open("config/maize_phenology.yaml") as fh:
        sim_cfg = (yaml.safe_load(fh) or {}).get("simulation") or {}
    days_per_real_second = float(sim_cfg.get("days_per_real_second") or 0.0)
    crop_hours_per_real_hour = days_per_real_second * 24 * 3600
    horizon_real_hours = (hours / crop_hours_per_real_hour
                          if crop_hours_per_real_hour > 0 else float(hours))

    projected = dict(values)
    trend_confidence: list[float] = []
    for field in SENSOR_FIELD_NAMES:
        try:
            df = ts_store.query_recent(field, minutes=360)
        except Exception:
            continue
        if len(df) < 6:
            continue

        series = df["_value"].astype(float)
        # Derive the slope from the actual timestamps. Assuming a publish cadence
        # was wrong by orders of magnitude and produced impossible projections --
        # a soil moisture "falling 190%" is not a forecast, it is a bug.
        times = pd.to_datetime(df["_time"])
        span_hours = (times.iloc[-1] - times.iloc[0]).total_seconds() / 3600.0
        if span_hours <= 0:
            continue
        slope_per_hour = (series.iloc[-1] - series.iloc[0]) / span_hours

        raw = float(series.iloc[-1] + slope_per_hour * horizon_real_hours)

        # Clamp to what the field can physically be. A linear trend extended far
        # enough always leaves the plausible range; reporting that as a forecast
        # would discredit every other number on the page.
        band = PROFILES.get(field, {}).get("by_stage", {}).get(stage, {}) or {}
        floor = band.get("crit_low")
        ceiling = band.get("crit_high")
        lo = floor * 0.5 if floor is not None else min(0.0, series.min() * 0.5)
        hi = ceiling * 1.5 if ceiling is not None else series.max() * 1.5
        if hi <= lo:
            lo, hi = min(lo, hi), max(lo, hi)
        projected[field] = float(min(max(raw, lo), hi))

        # A steadier series supports a longer extrapolation.
        spread = float(series.std() or 0.0)
        mean = abs(float(series.mean())) or 1.0
        trend_confidence.append(max(0.0, 1.0 - min(1.0, spread / mean)))

    future_health, future_state, _ = _assess(stage, projected)
    confidence = (sum(trend_confidence) / len(trend_confidence)) if trend_confidence else 0.0
    # A six-hour window of real observation cannot support an arbitrarily long
    # extrapolation. Decay confidence as the horizon outruns the evidence.
    confidence *= min(1.0, 6.0 / max(horizon_real_hours, 6.0))

    return adv.describe_projection(
        now_readings=values, future_readings=projected,
        now_state=now_state, future_state=future_state,
        now_health=now_health, future_health=future_health,
        horizon_hours=hours, confidence=confidence, labels=FIELD_LABELS,
    )


@app.get("/api/farmer/stage", response_model=adv.StageForecast)
def farmer_stage(
    user: auth_mod.UserOut = Depends(auth_mod.current_user),
) -> adv.StageForecast:
    stage = cache.get_latest_cached("growth_stage") or "germination"
    gdd = cache.get_latest_cached("gdd_accumulated")
    with open("config/maize_phenology.yaml") as f:
        phenology = yaml.safe_load(f)

    t_base = float(phenology.get("t_base", 8.0))
    air = ts_store.get_latest("air_temperature")
    mean_daily_gdd = max(0.0, float(air) - t_base) if air is not None else None

    return adv.stage_forecast(
        phenology, stage,
        float(gdd) if gdd is not None else None,
        mean_daily_gdd,
    )


# ── Interventions ───────────────────────────────────────────────────────────

@app.post("/api/farmer/intervention", response_model=adv.InterventionOut)
def log_intervention(
    req: adv.InterventionIn,
    user: auth_mod.UserOut = Depends(auth_mod.require_role("farmer")),
) -> adv.InterventionOut:
    """
    Record something the farmer did, and capture the twin's state at that moment
    so the effect can be judged later.
    """
    stage, values, _ = _read_twin_state()
    health, state, _ = _assess(stage, values)

    doc = {
        "id": uuid.uuid4().hex,
        "user_id": user.user_id,
        "kind": req.kind,
        "note": req.note,
        "amount": req.amount,
        "unit": req.unit,
        "logged_at": datetime.now(timezone.utc),
        "state_at_logging": state,
        "health_at_logging": health,
        "readings_at_logging": values,
        "outcome": "pending",
        "outcome_detail": "The twin is watching for the readings to respond.",
    }
    auth_mod._users_collection().database.interventions.insert_one(dict(doc))
    doc.pop("readings_at_logging", None)
    doc.pop("user_id", None)
    return adv.InterventionOut(**doc)


# How long to wait before judging whether an intervention worked. Soil moisture
# responds within hours; nutrients take days. This is the shortest defensible
# window for the fastest case.
_OUTCOME_AFTER_HOURS = 6


@app.get("/api/farmer/interventions")
def list_interventions(
    user: auth_mod.UserOut = Depends(auth_mod.require_role("farmer")),
):
    """
    Past actions, each judged against what the twin saw afterwards.

    This is the loop that makes the record worth keeping: an action with no
    verification is a diary entry, not evidence that anything improved.
    """
    collection = auth_mod._users_collection().database.interventions
    stage, values, _ = _read_twin_state()
    health_now, state_now, _ = _assess(stage, values)
    now = datetime.now(timezone.utc)

    out = []
    for doc in collection.find({"user_id": user.user_id}).sort("logged_at", -1).limit(25):
        logged = doc["logged_at"]
        if logged.tzinfo is None:
            logged = logged.replace(tzinfo=timezone.utc)
        hours = (now - logged).total_seconds() / 3600.0

        if hours < _OUTCOME_AFTER_HOURS:
            outcome, detail = "pending", (
                f"Too early to tell — checking again "
                f"{_OUTCOME_AFTER_HOURS - hours:.0f} hours from now.")
        else:
            delta = health_now - doc.get("health_at_logging", health_now)
            if delta > 3:
                outcome = "improved"
                detail = (f"Condition improved by {delta:.0f} points since you "
                          f"logged this.")
            elif delta < -3:
                outcome = "worsened"
                detail = (f"Condition fell {abs(delta):.0f} points since you logged "
                          f"this. Something else may be wrong.")
            else:
                outcome = "unchanged"
                detail = "No clear change in condition since you logged this."
            if doc.get("state_at_logging") != state_now:
                detail += (f" The twin moved from "
                           f"{doc['state_at_logging'].replace('_', ' ').lower()} to "
                           f"{state_now.replace('_', ' ').lower()}.")

        out.append({
            "id": doc["id"], "kind": doc["kind"], "note": doc.get("note", ""),
            "amount": doc.get("amount"), "unit": doc.get("unit"),
            "logged_at": logged.isoformat(),
            "state_at_logging": doc.get("state_at_logging", "UNKNOWN"),
            "health_at_logging": doc.get("health_at_logging", 0.0),
            "outcome": outcome, "outcome_detail": detail,
        })
    return {"interventions": out, "count": len(out)}
