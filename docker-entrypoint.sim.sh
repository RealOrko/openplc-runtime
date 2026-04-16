#!/bin/sh
# Wait for the openplc runtime to accept TLS on $HOST_IP:$RUNTIME_REST_PORT,
# deploy the configured model, then exec the model's HMI simulator.
#
# Expected environment:
#   HOST_IP             - host IP the runtime is bound to (auto-detected if empty)
#   RUNTIME_REST_PORT   - REST API port (defaults to 18443)
#   MODEL_DIR           - absolute path of model folder inside the container

set -e

: "${RUNTIME_REST_PORT:=18443}"
: "${MODEL_DIR:=/app/client/models/water_plant}"

if [ -z "$HOST_IP" ]; then
    HOST_IP=$(hostname -I | awk '{print $1}')
fi

if [ -z "$HOST_IP" ]; then
    echo "[sim] unable to determine HOST_IP; set it explicitly via env var" >&2
    exit 1
fi

MODEL_NAME=$(basename "$MODEL_DIR")
RUNTIME_URL="https://${HOST_IP}:${RUNTIME_REST_PORT}"

echo "[sim] HOST_IP=${HOST_IP}  RUNTIME=${RUNTIME_URL}  MODEL=${MODEL_NAME}"

# Wait until the runtime's REST port answers a TLS handshake. The runtime
# reports healthy when 8443 (or $RUNTIME_REST_PORT) accepts TLS, but the
# compose depends_on also uses this condition, so this loop usually exits
# on the first iteration.
i=0
until python3 -c "
import ssl, socket
s = socket.create_connection(('${HOST_IP}', ${RUNTIME_REST_PORT}), timeout=5)
ssl._create_unverified_context().wrap_socket(s, server_hostname='${HOST_IP}').close()
" 2>/dev/null; do
    i=$((i+1))
    if [ "$i" -gt 60 ]; then
        echo "[sim] runtime never became reachable after 3 minutes; giving up" >&2
        exit 1
    fi
    echo "[sim] waiting for runtime at ${HOST_IP}:${RUNTIME_REST_PORT}..."
    sleep 3
done

echo "[sim] runtime up; deploying ${MODEL_NAME}"
python3 -m openplc_client deploy "${MODEL_DIR}" --runtime "${RUNTIME_URL}"

# Plugins take ~5 s after upload to finish spinning up the fieldbus sockets.
sleep 5

echo "[sim] starting HMI (host=${HOST_IP})"
exec python3 "${MODEL_DIR}/sim/hmi_sim.py" --host "${HOST_IP}"
