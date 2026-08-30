# A-OPDT webapp backend (FastAPI).
#
# This service was never in docker-compose.yml - it only ran by hand with
# uvicorn on the host. Containerising it is what makes the webapp deployable.
#
# dyon is a real PyPI package (0.11.0), so no vendoring is needed.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY docker/requirements-webapp.txt /tmp/requirements.txt
# --timeout/--retries: PyPI reads time out on slow links, and a droplet
# build should not fail on one stalled download.
RUN pip install --no-cache-dir --timeout 120 --retries 5 -r /tmp/requirements.txt

# app.py opens "config/sensor_profiles.yaml" by relative path, so the working
# directory has to be the repo root - not webapp/.
COPY config/     /app/config/
COPY reactive/   /app/reactive/
COPY sensing/    /app/sensing/
COPY simulation/ /app/simulation/
COPY webapp/     /app/webapp/

EXPOSE 8500
CMD ["uvicorn", "webapp.backend.app:app", "--host", "0.0.0.0", "--port", "8500"]
