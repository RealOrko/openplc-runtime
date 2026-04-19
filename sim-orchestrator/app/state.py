"""Filesystem-backed state for the currently-loaded model.

Layout under `DATA_DIR` (PVC-mounted at /data in K8s, named volume in
docker-compose):

    current/
      program.st
      conf/opcua.json
      sim/current.py          (optional)
      meta.json               {"deployed_at": iso8601, "sim_filename": ...}
    staging/                  scratch directory for uploads + build output

The orchestrator keeps no in-memory source of truth about the loaded
model — on every request it reads `current/` fresh. Pod restarts thus
recover the last-loaded bundle automatically (the StatefulSet's PVC
preserves `current/` across restarts).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


CURRENT = "current"
STAGING = "staging"
META_FILE = "meta.json"
PROGRAM_FILE = "program.st"
OPCUA_FILE = "conf/opcua.json"
SIM_FILE = "sim/current.py"


@dataclass
class ModelMeta:
    deployed_at: str
    sim_filename: str | None  # original upload filename, for operator display


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.current_dir = data_dir / CURRENT
        self.staging_dir = data_dir / STAGING
        data_dir.mkdir(parents=True, exist_ok=True)

    # ---- current model accessors ----

    def bundle_present(self) -> bool:
        return (self.current_dir / PROGRAM_FILE).is_file() and \
               (self.current_dir / OPCUA_FILE).is_file()

    def sim_present(self) -> bool:
        return (self.current_dir / SIM_FILE).is_file()

    def program_path(self) -> Path:
        return self.current_dir / PROGRAM_FILE

    def opcua_path(self) -> Path:
        return self.current_dir / OPCUA_FILE

    def sim_path(self) -> Path:
        return self.current_dir / SIM_FILE

    def meta(self) -> ModelMeta | None:
        p = self.current_dir / META_FILE
        if not p.is_file():
            return None
        raw = json.loads(p.read_text())
        return ModelMeta(
            deployed_at=raw.get("deployed_at", ""),
            sim_filename=raw.get("sim_filename"),
        )

    # ---- mutation ----

    def reset_staging(self) -> Path:
        """Empty + recreate the staging directory."""
        if self.staging_dir.exists():
            shutil.rmtree(self.staging_dir)
        self.staging_dir.mkdir(parents=True)
        return self.staging_dir

    def promote_staging_to_current(self, sim_filename: str | None) -> None:
        """Atomically swap staging/ into current/. If a previous current/
        exists it is removed first. `sim_filename` records the operator's
        original upload name for display."""
        if self.current_dir.exists():
            # Delete in place — we already validated that staging has the
            # required files. No partial-state window except during the
            # rmtree + rename below, which is ~ms.
            shutil.rmtree(self.current_dir)
        self._write_meta(sim_filename)
        self.staging_dir.rename(self.current_dir)

    def _write_meta(self, sim_filename: str | None) -> None:
        meta = {
            "deployed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sim_filename": sim_filename,
        }
        (self.staging_dir / META_FILE).write_text(json.dumps(meta, indent=2))

    def clear_current(self) -> None:
        if self.current_dir.exists():
            shutil.rmtree(self.current_dir)

    def replace_sim(self, sim_bytes: bytes, sim_filename: str) -> None:
        """Overwrite just the sim script and update meta.json. Leaves the
        deployed PLC bundle alone."""
        sim_dir = self.current_dir / "sim"
        sim_dir.mkdir(parents=True, exist_ok=True)
        (sim_dir / "current.py").write_bytes(sim_bytes)
        existing = self.meta()
        meta = {
            "deployed_at": existing.deployed_at if existing else
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sim_filename": sim_filename,
        }
        (self.current_dir / META_FILE).write_text(json.dumps(meta, indent=2))

    def remove_sim(self) -> None:
        sim_file = self.current_dir / SIM_FILE
        if sim_file.is_file():
            sim_file.unlink()
        existing = self.meta()
        if existing:
            meta = {"deployed_at": existing.deployed_at, "sim_filename": None}
            (self.current_dir / META_FILE).write_text(json.dumps(meta, indent=2))
