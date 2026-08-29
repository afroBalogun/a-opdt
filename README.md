# A-OPDT — Running the Digital Twin

Quick reference for starting up the whole system: infrastructure, the twin itself, and the What-If web tool. Written so you can follow it without needing to remember how any of the pieces fit together.

## What's in this repo

| Piece | What it is |
|---|---|
| `twin.py` | Entry point for the digital twin itself — wires together all 8 layers and runs forever |
| `sensing/` | L1/L2 — mock maize sensors + MQTT publishing |
| `simulation/` | L4 — the Biotic Pod DT (Farquhar/Ball-Berry/Penman-Monteith physiology model) |
| `reactive/` | L5/L6 — health scoring, the EKF state estimator, the 8-state Plant Health FSM |
| `intelligent/` | L7 — Neo4j knowledge graph + Orchestrator (foundation only, no agents yet) |
| L8 pieces | Twin Calibration Agent + Escalation Protocol, wired into `twin.py` directly |
| `webapp/backend/` | FastAPI service backing the What-If Simulator |
| `webapp/frontend/` | The What-If Simulator itself (Vite + React) |
| `config/*.yaml` | Sensor profiles, stress thresholds, phenology — the twin's tunable knobs |

The digital twin (`twin.py`) and the What-If Simulator (`webapp/`) are **independent** — the twin runs continuously in the background generating live data; the webapp is a separate tool you start whenever you want to explore "what if" scenarios against that live data. You don't need the webapp running for the twin to work, and vice versa (the webapp just shows flat/default values if the twin isn't running).

## Prerequisites (one-time setup)

- **Docker Desktop** — running, with the containers already defined in `docker-compose.yml`.
- **Python environment** — this project is built on the [`dyon`](https://github.com/lazy-monster/dyon) digital twin framework (published on PyPI; requires Python 3.11+). Create a venv in this repo and install it:

  ```bash
  python3.11 -m venv .venv
  .venv/bin/pip install dyon
  ```

  Earlier revisions imported the framework as `dt_forge`, which was the name before it was renamed to `dyon`. The old name still resolves through a compatibility shim that emits a `DeprecationWarning`, but this repo now imports `dyon` directly and does not depend on that shim.
- **Node.js** — for the frontend. `webapp/frontend/package.json` pins Vite to a stable v5 release; don't let `npm install` drift it to the latest major version — a newer Vite defaults to an experimental native bundler that doesn't have prebuilt binaries for this machine's Node version and will fail to start.

## Every time: starting it all up

Run these from the `a-opdt` repo root, in order.

### 1. Start the infrastructure

```bash
docker compose up -d
```

Brings up 10 containers: Mosquitto (MQTT), InfluxDB, MongoDB, Redis, the 5-container Eclipse Ditto stack, and Neo4j. First run pulls images and takes a few minutes; after that it's seconds.

If containers show as "Exited" (e.g. after the machine slept or restarted), the same command restarts them — check with:

```bash
docker ps -a --format "{{.Names}}: {{.Status}}"
```

### 2. Start the twin

```bash
.venv/bin/python twin.py
```

Leave this running in its own terminal tab. Within a few seconds you should see log lines like:

```
FSM state: INITIALISING → HEALTHY
Knowledge graph built: 5 components, 7 failure modes
MAS started with 0 agents
```

No errors, no tracebacks — if you see any, something in the infrastructure step above isn't up yet (give it a few more seconds and restart the twin).

### 3. (Optional) Start the What-If Simulator

Only needed if you want to explore scenarios. Two more processes, each in its own terminal tab:

```bash
# Backend — from the a-opdt root
.venv/bin/uvicorn webapp.backend.app:app --reload --port 8500
```

```bash
# Frontend — from webapp/frontend/
npm install   # only needed the first time, or after pulling changes to package.json
npm run dev
```

Open the URL Vite prints (defaults to **http://localhost:5173**).

## Checking things are actually working

- **Twin logs** — should show a health score and FSM state update every ~60 seconds, no `ERROR` lines.
- **Ditto (live canonical twin state)**:
  ```bash
  curl -u ditto:ditto http://localhost:8080/api/2/things/org.aopdt:maize_farm_001
  ```
- **InfluxDB UI** — http://localhost:8086 (login: `admin` / `password`, per `docker-compose.yml`).
- **Neo4j Browser** — http://localhost:7474 (login: `neo4j` / whatever's in `.env`'s `DT_NEO4J__PASSWORD`).
- **What-If Simulator** — http://localhost:5173, sliders should load prefilled with live values from the twin (not zeros/defaults).

## Shutting down

- Stop the twin, backend, and frontend with `Ctrl+C` in each terminal.
- Containers are fine to leave running (`restart: unless-stopped`), or stop them with:
  ```bash
  docker compose down
  ```
  (add `-v` only if you actually want to wipe stored data — InfluxDB history, Mongo events, Neo4j graph, calibration state all live in those volumes).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Twin crashes on startup with connection errors | Docker containers aren't up yet | `docker compose up -d`, wait ~10s, retry |
| Old `twin.py` process still running from a previous session | Background process survived a restart | `ps aux \| grep twin.py`, `kill <pid>` |
| Containers show `Exited` after machine sleep/restart | Normal — Docker doesn't always restart everything cleanly | `docker compose up -d` again |
| `npm run dev` fails with a "Cannot find native binding" / rolldown error | `package.json` got un-pinned to the latest Vite | Check `webapp/frontend/package.json` has `"vite": "^5.4.19"`; if not, fix it, then `rm -rf node_modules package-lock.json && npm install` |
| What-If Simulator sliders show all zeros | Twin isn't running, so InfluxDB has no data yet | Start `twin.py` first, then reload the page |
| L7's Orchestrator/agents don't do anything | Expected — no individual TwinAgents are wired up yet (needs a real LLM API key; `.env`'s `DT_LLM__API_KEY` is still a placeholder) | Not a bug — see the implementation report for what's deferred and why |

## Where to go deeper

- The full implementation report (architecture walkthrough, verification evidence, design tradeoffs) — the Artifact generated earlier in this project's history.
- `config/stress_thresholds.yaml` and `config/sensor_profiles.yaml` — the two files most worth reading to understand what the reactive layer and health scoring actually check.
- The architecture design doc (supervisor-revised 8-layer spec) and framework paper live outside this repo, in `~/Downloads` — see the project's saved memory for exact filenames if needed.

## Web application — landing page, accounts and dashboards

`webapp/` now serves the whole front door, not just the What-If Simulator.

| Piece | What it is |
|---|---|
| Landing page | Public. Explains the twin and routes to sign-up or sign-in. |
| Accounts | Email + password, roles `researcher` and `farmer`. JWT, 12 h expiry. |
| Researcher dashboard | All 19 modelled fields grouped by domain, each against its growth-stage band and labelled measured or nominal, plus stress categories, health score, L8 calibration and the What-If Simulator. |
| Farmer dashboard | Plain-language state, cause and recommended action, with only the actionable readings plus anything out of band. |

Both roles read the same twin state. They differ in what that state is turned
into — a researcher needs every field and its provenance, a farmer needs to know
what to do today. The API returns different payloads per role rather than one
payload the client filters, so a farmer's browser never receives fields the view
is not entitled to.

### Running it

```bash
docker compose up -d mongodb          # accounts live here
AOPDT_SECRET_KEY=<something-stable> \
AOPDT_MONGO_URI="mongodb://admin:password@localhost:27017/?authSource=admin" \
  .venv/bin/python -m uvicorn webapp.backend.app:app --port 8500

cd webapp/frontend && npm install && npm run dev
```

`AOPDT_SECRET_KEY` matters: without it a key is generated per process, so every
restart invalidates issued tokens and signs everyone out.

### On provenance

The twin models 19 fields; any of them may have no sensor behind it. Where
InfluxDB has no reading, the stage nominal from `config/sensor_profiles.yaml`
stands in so the physiology models still run — but that value is tagged
`nominal` all the way to the browser, and both dashboards carry a standing
notice of how many fields are actually measured. A nominal is an assumption,
not an observation of this plant, and the interface never lets the two look
alike.

### What each role gets

**Researcher**

| | |
|---|---|
| Time-series explorer | Any of the 19 fields over 1 h / 6 h / 24 h / 7 d, with the growth-stage warning and critical bands drawn behind the trace. |
| Escalation inbox | Cases the escalation protocol referred for human review, with the forward-simulation confidence. Previously these only reached a log file. |
| Field grid | All 19 parameters by domain, each against its stage band and labelled measured or nominal. |
| Calibration | Vcmax25 and Ball-Berry slope as fitted by the L8 agent. |
| What-if simulator | Unchanged, now a panel rather than the whole app. |

**Farmer**

| | |
|---|---|
| Irrigation depth | A number of millimetres and a time of day, from Penman-Monteith transpiration against the root-zone deficit. Also reports daily crop water use and days of water remaining. |
| If you do nothing | Each field extrapolated along its recent trend, re-scored by the twin's own rules, with a confidence that decays as the horizon outruns the evidence. |
| Growth stage | Where the crop is in thermal time and roughly when the next stage arrives. |
| Intervention log | Record what you did; the twin judges after six hours whether the readings responded. |

### Two bugs this work uncovered

`sensing/mock_publisher.py` called `self.ts.write(...)`, but `InfluxAdapter`
exposes `write_point`. Every publish cycle raised, so nothing was ever stored.
Once fixed, it wrote `measurement=asset_id` while the adapter reads from
`measurement="asset_telemetry"` with `asset_id` as a tag, so points landed
where nothing queried. Both are corrected; the dashboards now report 19 of 19
parameters measured instead of falling back to stage nominals.

### A note on projected values

InfluxDB timestamps are real time, but `days_per_real_second: 0.1` means the
crop's clock runs 8,640 times faster — one real hour is 360 crop days. A trend
measured per real hour cannot be applied over a horizon expressed in crop hours
without converting between the two, and the projection endpoint does so
explicitly. Extrapolated values are additionally clamped to the physically
plausible range for each field, because a linear trend extended far enough
always leaves it.
