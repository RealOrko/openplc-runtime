"""Environment-driven configuration for the sim orchestrator.

Every knob is read from env vars so the same image runs in docker-compose
(bound via service `environment:`) and in Kubernetes (bound via a
ConfigMap/Secret). No file-based config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Settings:
    runtime_url: str
    runtime_username: str
    runtime_password: str
    data_dir: Path
    port: int
    log_buffer_lines: int
    sim_shutdown_grace_s: float

    @property
    def plc_host(self) -> str:
        """Hostname extracted from runtime_url — the address the sim child
        should dial for OPC-UA/Modbus."""
        host = urlparse(self.runtime_url).hostname
        if not host:
            raise ValueError(f"RUNTIME_URL has no hostname: {self.runtime_url}")
        return host


def load_settings() -> Settings:
    runtime_url = os.environ.get("RUNTIME_URL")
    if not runtime_url:
        raise RuntimeError("RUNTIME_URL env var is required")

    return Settings(
        runtime_url=runtime_url.rstrip("/"),
        runtime_username=os.environ.get("RUNTIME_USERNAME", "openplc"),
        runtime_password=os.environ.get("RUNTIME_PASSWORD", "openplc"),
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
        port=int(os.environ.get("PORT", "8000")),
        log_buffer_lines=int(os.environ.get("LOG_BUFFER_LINES", "1000")),
        sim_shutdown_grace_s=float(os.environ.get("SIM_SHUTDOWN_GRACE_S", "10")),
    )
