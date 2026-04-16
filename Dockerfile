# syntax=docker/dockerfile:1

FROM debian:bookworm-slim

WORKDIR /workdir

# Copy source code
COPY . .

# Setup runtime directory and permissions
RUN mkdir -p /var/run/runtime && \
    chmod +x install.sh scripts/* start_openplc.sh

# Clean any existing build artifacts to ensure clean Docker build
RUN rm -rf build/ venvs/ .venv/ 2>/dev/null || true

# Run installation script
RUN ./install.sh

# Clean up apt cache to reduce image size (Docker-specific optimization)
RUN rm -rf /var/lib/apt/lists/*

# Exposed ports (informational — under host networking these bind directly):
#   REST API   — port from $RUNTIME_REST_PORT (default 8443)
#   Modbus TCP — port from conf/modbus_slave.json (per model)
#   OPC-UA     — port from conf/opcua.json endpoint_url (per model)
# The docker-compose.yml sets RUNTIME_REST_PORT=18443 and ships a water_plant
# model that binds OPC-UA on 14840 and Modbus on 15020.
EXPOSE 8443 18443 4840 14840 5020 15020

# Liveness probe: a successful TLS handshake on the REST port means the
# runtime is up. Resolves host via `hostname -I` under host networking so it
# hits the same interface external clients see (no 127.0.0.1).
HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD python3 -c "import os,ssl,socket,subprocess; h=os.environ.get('HOST_IP') or subprocess.check_output(['hostname','-I']).decode().split()[0]; p=int(os.environ.get('RUNTIME_REST_PORT','8443')); s=socket.create_connection((h,p),timeout=5); ssl._create_unverified_context().wrap_socket(s,server_hostname=h).close()" || exit 1

# Default execution - Start OpenPLC Runtime
CMD ["bash", "./start_openplc.sh"]
