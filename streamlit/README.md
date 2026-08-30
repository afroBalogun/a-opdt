# A-OPDT on Streamlit Community Cloud

A thin client over the webapp backend, in the same shape as merged-asdt's
scientist portal. Every number comes from an endpoint the React dashboard
already uses, so the two cannot disagree.

## Deploy

share.streamlit.io → **New app** → from `afroBalogun/a-opdt`:

| Field | Value |
|---|---|
| Branch | `main` |
| Main file path | `streamlit/app_researcher.py` |
| Requirements | `streamlit/requirements.txt` (detected automatically) |

Then **Settings → Secrets**:

```toml
AOPDT_API_URL = "https://<your-tunnel>.trycloudflare.com"
```

That is the only secret it needs. The backend holds the databases and the LLM
key; none of that belongs in a client. Anything else in there is unused.

## Why no CORS setting

Streamlit calls the API from its **server**, not the browser, so the requests
are not cross-origin and `AOPDT_CORS_ORIGINS` does not apply. The React
frontend does need it; this does not. That is one reason this route is less
fragile than hosting the SPA.

## What must be running for it to show anything

| What | Why |
|---|---|
| `webapp-backend` on :8500 | the API this client calls |
| `cloudflared` → :8500 | makes it reachable from Streamlit Cloud |
| `twin.py` | writes readings the API reads |
| a-opdt InfluxDB, Mongo, Redis | storage behind the API |

The tunnel hostname changes on every `cloudflared` restart. When it does,
update `AOPDT_API_URL` and the app reboots itself.

## Known problem this client cannot fix

The dashboard reports **19 of 19 parameters measured**. Only about four are:
air temperature, humidity, canopy temperature, and the derived canopy-air
delta. `MockSensorPublisher` writes all nineteen simulated fields into
InfluxDB, and `_read_twin_state()` infers provenance from "is there a value in
the database", so simulated values are labelled `measured`.

The real signal exists — `physical_overlay.apply()` returns the list of fields
that genuinely came from hardware — but nothing carries it to the API. Until
that is wired through, treat the MEASURED chips on this dashboard as "present
in the store", not "observed by an instrument".

merged-asdt does not have this bug: its ingestor caches `measured_fields` and
the portal reads it.
