#!/bin/sh
# Orchestrator entrypoint. Auto-resolves RUNTIME_URL for dev (docker host
# networking) and execs uvicorn.
#
# In Kubernetes the operator sets RUNTIME_URL explicitly (usually a Service
# DNS name like https://openplc-runtime.default.svc.cluster.local:18443)
# and this script leaves it alone.
#
# Fallback chain when RUNTIME_URL is unset:
#   $HOST_IP           -> explicit host IP
#   hostname -I first  -> docker-compose with host networking
#   127.0.0.1          -> last resort; likely won't work for OPC-UA

set -e

: "${RUNTIME_REST_PORT:=18443}"

if [ -z "$RUNTIME_URL" ]; then
    if [ -z "$HOST_IP" ]; then
        HOST_IP=$(hostname -I | awk '{print $1}')
    fi
    if [ -z "$HOST_IP" ]; then
        HOST_IP=127.0.0.1
    fi
    export RUNTIME_URL="https://${HOST_IP}:${RUNTIME_REST_PORT}"
fi

echo "[orchestrator] RUNTIME_URL=${RUNTIME_URL}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
