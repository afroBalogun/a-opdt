"""
Tool-calling advisor over the twin's live state.

The dashboards already show numbers and the models already turn them into
irrigation depths and stage forecasts. What was missing was anything that could
be *asked* a question and go and look. This is that: an LLM that decides which
of the twin's own accessors to call, reads the results, and answers from them.

It invents no science. Every tool returns the same values the dashboards and
advisory models produce; the model chooses what to fetch and how to explain it.

Provenance is the thing this must not get wrong
-----------------------------------------------
Four of the nineteen fields are measured on this hardware - air temperature and
humidity from the DHT11, canopy temperature from the MLX90614, and the derived
canopy-air delta. The rest are growth-stage nominals standing in for sensors
that do not exist. A nominal is not an observation, and advice resting on one
is advice about a number nobody measured.

So every reading a tool returns is labelled, the system prompt says what that
label means, and the model is told to say so when it leans on a nominal. This
is the same discipline the ingest path already keeps with `measured_fields`.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

MAX_ITERATIONS = 6

# Set per request by ask(); the tool closures read it. Tools take no twin
# handles as arguments because the model should not be inventing those.
_ctx: dict[str, Any] = {}


def _state() -> tuple[str, dict, set]:
    return _ctx["read_twin_state"]()


@tool
def get_twin_state(query: str = "") -> str:
    """Growth stage, health score, plant state, and any active stress categories.

    Call this first for 'how is the crop doing' style questions.
    """
    stage, values, measured = _state()
    health, plant_state, active = _ctx["assess"](stage, values)
    return json.dumps({
        "growth_stage": stage,
        "health_score": round(health, 1),
        "plant_state": plant_state,
        "active_stress_categories": active or "none",
        "fields_measured": f"{len(measured)} of {len(values)}",
        "note": ("Fields not in the measured set are growth-stage nominals, "
                 "not observations."),
    })


@tool
def get_readings(fields: str = "") -> str:
    """Current value, band and provenance for sensor fields.

    Pass a comma-separated list of field names, or leave empty for all.
    'provenance' is 'measured' (a real sensor) or 'nominal' (a stand-in).
    """
    stage, values, measured = _state()
    wanted = [f.strip() for f in fields.split(",") if f.strip()] or list(values)
    out = {}
    for f in wanted:
        if f not in values:
            out[f] = "unknown field"
            continue
        band = _ctx["band"](f, stage)
        out[f] = {
            "value": values[f],
            "provenance": "measured" if f in measured else "nominal",
            "nominal": band.get("nominal"),
            "warn_low": band.get("warn_low"), "warn_high": band.get("warn_high"),
            "crit_low": band.get("crit_low"), "crit_high": band.get("crit_high"),
        }
    return json.dumps(out)


@tool
def get_irrigation_advice(query: str = "") -> str:
    """How much water to apply, in mm, derived from soil moisture and transpiration."""
    advice = _ctx["irrigation"]()
    return advice.model_dump_json()


@tool
def get_stage_forecast(query: str = "") -> str:
    """Where the crop is in its life cycle and roughly when the next stage arrives."""
    forecast = _ctx["stage_forecast"]()
    return forecast.model_dump_json()


TOOLS = [get_twin_state, get_readings, get_irrigation_advice, get_stage_forecast]

SYSTEM_PROMPT = """You are an agronomy advisor attached to a maize digital twin.

Answer only from the tools. Call them before answering - do not guess a reading,
and do not answer from general knowledge about maize when a tool can tell you
what this plant is actually doing.

Provenance matters more than precision here. Each reading is labelled
'measured' or 'nominal'. A nominal is a growth-stage default standing in for a
sensor that does not exist on this node - it describes a typical plant at this
stage, not this one. When your answer depends on a nominal, say so plainly, in
one short clause. Never present a nominal as an observation.

Only four fields are measured on this hardware: air_temperature,
relative_humidity, canopy_temperature and canopy_air_delta. Soil nutrients,
NDVI, PRI, fluorescence and the volatiles are all nominals.

Be concise and concrete. Give a number and a next action where the tools
support one. If the tools do not support an answer, say what is missing rather
than filling the gap.
"""


def ask(question: str, context: dict[str, Callable], history: Optional[list] = None
        ) -> tuple[str, list]:
    """Run the tool loop for one question. Returns (answer, new_history)."""
    global _ctx
    _ctx = context

    llm = context["build_llm"]().bind_tools(TOOLS)
    tool_map = {t.name: t for t in TOOLS}

    messages: list = [("system", SYSTEM_PROMPT)]
    if history:
        messages += history
    messages.append(("human", question))

    for _ in range(MAX_ITERATIONS):
        response = llm.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            return response.content, messages[1:]

        for call in response.tool_calls:
            name = call["name"]
            try:
                result = tool_map[name].invoke(call["args"]) if name in tool_map \
                    else f"unknown tool: {name}"
            except Exception as exc:                      # a tool fault is data,
                result = f"tool error: {exc}"             # not a reason to die
            messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

    # Out of iterations: say so rather than presenting a partial answer as final.
    return ("I could not settle on an answer within the tool-call budget. "
            "Try a narrower question."), messages[1:]
