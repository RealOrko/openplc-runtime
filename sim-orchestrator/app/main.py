"""FastAPI app factory + lifecycle.

Responsibilities:
  - Load settings from env.
  - Wire the persistent Store and the SimProcess manager onto app.state.
  - On startup: if a bundle is already on the PVC, optimistically relaunch
    the sim (sim will log-and-exit if the runtime has lost the PLC, and
    the operator will re-POST /model to recover).
  - On shutdown: reap the sim child cleanly.
"""

from __future__ import annotations

import contextlib

from fastapi import FastAPI

from app.config import load_settings
from app.routes import health, logs, model
from app.sim_process import SimProcess
from app.state import Store


def create_app() -> FastAPI:
    settings = load_settings()
    store = Store(settings.data_dir)
    sim = SimProcess(
        log_buffer_lines=settings.log_buffer_lines,
        shutdown_grace_s=settings.sim_shutdown_grace_s,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.store = store
        app.state.sim = sim
        await _resume_if_present(store, settings, sim)
        try:
            yield
        finally:
            await sim.aclose()

    app = FastAPI(
        title="OpenPLC sim orchestrator",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(model.router)
    app.include_router(logs.router)
    return app


async def _resume_if_present(store: Store, settings, sim: SimProcess) -> None:
    if not store.bundle_present():
        print("[sim-orchestrator] no persisted bundle; starting empty",
              flush=True)
        return
    print(
        f"[sim-orchestrator] found persisted bundle at {store.current_dir}",
        flush=True,
    )
    if not store.sim_present():
        print("[sim-orchestrator] no sim in bundle; idling", flush=True)
        return
    meta = store.meta()
    try:
        status = await sim.start(
            store.sim_path(),
            env_overrides={
                "MODEL_DIR": str(settings.data_dir / "current"),
                "PLC_HOST": settings.plc_host,
                "RUNTIME_URL": settings.runtime_url,
            },
            display_filename=meta.sim_filename if meta else None,
        )
        print(
            f"[sim-orchestrator] resumed sim pid={status.pid} "
            f"filename={status.sim_filename}",
            flush=True,
        )
    except Exception as e:
        print(f"[sim-orchestrator] sim resume failed: {e!r}", flush=True)


app = create_app()
