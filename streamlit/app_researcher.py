"""
A-OPDT researcher portal — Streamlit client over the twin's REST API.

A thin client, deliberately. Every number here comes from an endpoint the React
dashboard already uses, so the two cannot disagree about the state of the
plant. Nothing is computed locally and nothing is cached beyond a rerun.

Hosting: Streamlit Community Cloud builds this from the repo. It needs one
secret, AOPDT_API_URL, pointing at a publicly reachable webapp backend. The
backend is where the databases and the LLM key live; none of that belongs here.
"""
from __future__ import annotations

import os
from datetime import datetime

import pandas as pd
import requests
import streamlit as st

API_BASE = os.getenv("AOPDT_API_URL", "http://localhost:8500").rstrip("/")

#: A reading older than this is not "now". The pod uploads every ten seconds,
#: so anything past a few minutes means it stopped rather than slowed.
LIVE_WITHIN_MIN = 5

st.set_page_config(page_title="A-OPDT Researcher", page_icon="🌱", layout="wide")


# ── Transport ───────────────────────────────────────────────────────────────

def _headers() -> dict:
    token = st.session_state.get("token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def api_get(path: str, params: dict | None = None, timeout: int = 20):
    try:
        r = requests.get(f"{API_BASE}{path}", headers=_headers(),
                         params=params, timeout=timeout)
        if r.status_code == 401:
            return {"_error": "Session expired. Sign in again."}
        return r.json() if r.ok else {"_error": r.text[:200]}
    except requests.RequestException as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}


def api_post(path: str, data: dict | None = None, timeout: int = 20):
    # The advisor runs a tool-calling loop against a cloud model, so it gets a
    # far longer budget than a REST read. Sharing one default made the slow
    # path fail silently.
    try:
        r = requests.post(f"{API_BASE}{path}", headers=_headers(),
                          json=data, timeout=timeout)
        return r.json() if r.ok else {"_error": r.text[:300]}
    except requests.RequestException as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}


# ── Auth ────────────────────────────────────────────────────────────────────

def sign_in_view() -> None:
    st.title("🌱 A-OPDT")
    st.caption(f"Backend: `{API_BASE}`")

    tab_in, tab_up = st.tabs(["Sign in", "Create account"])

    with tab_in:
        with st.form("signin"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Sign in", use_container_width=True):
                res = api_post("/api/auth/login",
                               {"email": email, "password": password})
                if res.get("_error"):
                    st.error(res["_error"])
                else:
                    st.session_state.token = res.get("token")
                    st.session_state.user = res.get("user", {})
                    st.rerun()

    with tab_up:
        with st.form("signup"):
            name = st.text_input("Name")
            email = st.text_input("Email", key="su_email")
            password = st.text_input("Password (8+ characters)", type="password",
                                     key="su_pw")
            role = st.selectbox("Role", ["researcher", "farmer"])
            if st.form_submit_button("Create account", use_container_width=True):
                res = api_post("/api/auth/register", {
                    "name": name, "email": email,
                    "password": password, "role": role,
                })
                if res.get("_error"):
                    st.error(res["_error"])
                else:
                    st.session_state.token = res.get("token")
                    st.session_state.user = res.get("user", {})
                    st.rerun()


# ── Dashboard ───────────────────────────────────────────────────────────────

def _provenance_chip(provenance: str) -> str:
    if provenance == "measured":
        return ("<span style='background:#14532d;color:#fff;padding:1px 7px;"
                "border-radius:9px;font-size:0.68rem'>MEASURED</span>")
    return ("<span style='background:#3f3f46;color:#d4d4d8;padding:1px 7px;"
            "border-radius:9px;font-size:0.68rem'>NOMINAL</span>")


def dashboard_view(data: dict) -> None:
    measured = data.get("measured_count", 0)
    total = data.get("total_count", 0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Health score", f"{data.get('health_score', 0):.1f}")
    c2.metric("Plant state", str(data.get("plant_state", "—")).replace("_", " ").title())
    c3.metric("Growth stage", str(data.get("growth_stage", "—")).replace("_", " ").title())
    c4.metric("Instrumented", f"{measured} of {total}")

    # The count is the honest headline. A dashboard that shows nineteen numbers
    # without saying how many came from an instrument invites every one of them
    # to be read as an observation.
    if total and measured == 0:
        st.warning(
            "No sensor data has reached the twin. Every value below is the "
            "growth-stage nominal — a starting assumption, not an observation "
            "of this plant."
        )
    elif measured < total:
        st.info(
            f"{measured} of {total} parameters come from instruments. The rest "
            "are growth-stage nominals and are labelled below."
        )

    active = data.get("active_categories") or {}
    if active:
        st.error("Active stress: " + ", ".join(
            f"**{k.replace('_', ' ')}** ({v})" for k, v in active.items()))

    for group, items in (data.get("groups") or {}).items():
        st.subheader(group.replace("_", " ").title())
        cols = st.columns(min(4, max(1, len(items))))
        for i, item in enumerate(items):
            with cols[i % len(cols)]:
                val = item.get("value")
                st.metric(
                    item.get("label", item.get("field", "—")),
                    f"{val:.2f} {item.get('unit', '')}".strip() if isinstance(val, (int, float)) else "—",
                )
                st.markdown(_provenance_chip(item.get("provenance", "nominal")),
                            unsafe_allow_html=True)
                status = item.get("status")
                if status and status != "ok":
                    st.caption(f"status: {status}")


# ── History ─────────────────────────────────────────────────────────────────

def history_view() -> None:
    meta = api_get("/api/history/fields")
    if meta.get("_error"):
        st.error(meta["_error"]); return

    # The endpoint returns {windows: [...], groups: {"Soil": [{field,label,unit}]}}
    # rather than a flat list, and keeps the display label with each field.
    labels: dict[str, str] = {}
    for group, items in (meta.get("groups") or {}).items():
        for item in items:
            labels[item["field"]] = f"{item.get('label', item['field'])} · {group}"
    if not labels:
        st.info("No fields available."); return

    windows = meta.get("windows") or ["1h", "6h", "24h", "7d"]
    col_f, col_w = st.columns([3, 1])
    field = col_f.selectbox("Field", list(labels), format_func=lambda f: labels[f])
    window = col_w.selectbox("Window", windows, index=min(1, len(windows) - 1))

    series = api_get("/api/history", {"field": field, "window": window})
    if series.get("_error"):
        st.error(series["_error"]); return

    points = series.get("points") or []
    if not points:
        st.info("No readings in this window yet.")
        st.caption("Either nothing has reported, or the twin is not writing to "
                   "the store this API reads.")
        return

    # Points are {t, v}; the band alongside them is the growth-stage threshold
    # set, drawn so a value can be read against what counts as normal.
    df = pd.DataFrame(points).rename(columns={"t": "time", "v": series.get("label", field)})
    df["time"] = pd.to_datetime(df["time"])
    st.line_chart(df.set_index("time"))

    band = series.get("band") or {}
    if band:
        st.caption(" · ".join(
            f"{k.replace('_', ' ')}: {v}" for k, v in band.items() if v is not None))
    st.caption(f"{len(points)} readings · unit {series.get('unit', '—')}")


# ── Advisor ─────────────────────────────────────────────────────────────────

def advisor_view() -> None:
    st.subheader("Ask the twin")
    st.caption(
        "The agent reads the twin's own accessors — state, readings, irrigation "
        "depth, stage forecast — and answers from them. Fields with no "
        "instrument are reported as nominals."
    )

    if "chat" not in st.session_state:
        st.session_state.chat = []

    suggestions = [
        "How is the crop doing right now?",
        "Should I irrigate today?",
        "Which readings come from real sensors?",
        "When does the next growth stage start?",
    ]

    pending = None
    if not st.session_state.chat:
        cols = st.columns(len(suggestions))
        for col, text in zip(cols, suggestions):
            if col.button(text, use_container_width=True, key=f"sugg_{text[:14]}"):
                pending = text

    for turn in st.session_state.chat:
        with st.chat_message(turn["role"], avatar="🌱" if turn["role"] == "assistant" else None):
            st.markdown(turn["content"])
            if turn.get("at"):
                st.caption(f"answered {turn['at']}")

    typed = st.chat_input("Ask about the crop, irrigation, or a reading...")
    question = pending or typed
    if not question:
        if st.session_state.chat and st.button("Clear conversation"):
            st.session_state.chat = []
            st.rerun()
        return

    st.session_state.chat.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant", avatar="🌱"):
        with st.spinner("Reading the twin's current state..."):
            res = api_post("/api/advisor/ask", {"question": question}, timeout=240)
        answer = (f"The advisor did not answer: {res['_error']}"
                  if res.get("_error") else res.get("answer", "No answer returned."))
        st.markdown(answer)
        st.session_state.chat.append({
            "role": "assistant", "content": answer,
            "at": datetime.now().strftime("%H:%M:%S"),
        })


# ── Shell ───────────────────────────────────────────────────────────────────

def main() -> None:
    if not st.session_state.get("token"):
        sign_in_view()
        return

    user = st.session_state.get("user", {})
    with st.sidebar:
        st.markdown(f"### 🌱 {user.get('name', 'User')}")
        st.caption(f"{user.get('email', '')} — {str(user.get('role', '')).title()}")
        st.divider()
        view = st.radio("View", ["Dashboard", "History", "Ask the Twin"],
                        label_visibility="collapsed")
        st.divider()
        if st.button("Refresh", use_container_width=True):
            st.rerun()
        if st.button("Sign out", use_container_width=True):
            st.session_state.clear()
            st.rerun()
        st.caption(f"API: {API_BASE}")

    role = user.get("role", "researcher")
    endpoint = "/api/dashboard/researcher" if role == "researcher" else "/api/dashboard/farmer"

    if view == "Dashboard":
        data = api_get(endpoint)
        if data.get("_error"):
            st.error(data["_error"])
            st.caption("If this says the backend is unreachable, the tunnel or "
                       "the API behind AOPDT_API_URL has stopped.")
            return
        dashboard_view(data)
    elif view == "History":
        history_view()
    else:
        advisor_view()


main()
