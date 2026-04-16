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

# Exposed ports:
#   8443 - REST API / Editor connection (always on)
#   5020 - Modbus TCP Slave (when modbus_slave plugin is enabled)
#   4840 - OPC UA Server (when opcua plugin is enabled)
EXPOSE 8443 5020 4840

# Liveness probe: a successful TLS handshake on 8443 means the runtime is up.
# /api/ping requires JWT auth so it is not suitable for an unauthenticated probe.
HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD python3 -c "import ssl,socket; s=socket.create_connection(('127.0.0.1',8443),timeout=5); ssl._create_unverified_context().wrap_socket(s,server_hostname='localhost').close()" || exit 1

# Default execution - Start OpenPLC Runtime
CMD ["bash", "./start_openplc.sh"]
