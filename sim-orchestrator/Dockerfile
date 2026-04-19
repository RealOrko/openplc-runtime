# syntax=docker/dockerfile:1
#
# OpenPLC sim orchestrator. FastAPI service that accepts PLC bundles
# (program.st + conf/opcua.json) plus an optional sim script, deploys the
# bundle to a running OpenPLC runtime via openplc_client, and manages the
# sim as a subprocess inside this container.
#
# Runtime expectations:
#   RUNTIME_URL         base URL of the OpenPLC REST API (required)
#   RUNTIME_USERNAME    runtime login (default openplc)
#   RUNTIME_PASSWORD    runtime password (default openplc)
#   DATA_DIR            persistent dir for the loaded bundle (default /data)
#   PORT                uvicorn listen port (default 8000)

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        iproute2 \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# openplc_client package: used by the orchestrator to build + upload
# bundles to the runtime, and by spawned sim children to speak OPC-UA.
COPY client/ /app/client/

# Install deps: openplc_client's own + asyncua (for sim children) + FastAPI
# stack (for the orchestrator).
COPY sim-orchestrator/requirements.txt /app/orchestrator-requirements.txt
RUN pip install --no-cache-dir -r /app/client/requirements.txt asyncua \
    && pip install --no-cache-dir -r /app/orchestrator-requirements.txt \
    && cd /app/client && python -m openplc_client setup

COPY sim-orchestrator/app /app/app
COPY sim-orchestrator/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

WORKDIR /app

ENV PYTHONPATH=/app:/app/client \
    DATA_DIR=/data \
    PORT=8000

ENTRYPOINT ["/app/entrypoint.sh"]
