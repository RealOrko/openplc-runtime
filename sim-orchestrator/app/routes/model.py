"""Model lifecycle endpoints.

POST   /model        deploy PLC + (optional) start sim
GET    /model        current state
DELETE /model        stop sim + stop PLC + clear persisted bundle
PUT    /model/sim    swap sim script (PLC untouched)
DELETE /model/sim    stop sim (PLC untouched)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app import runtime_client
from app.state import Store

router = APIRouter(prefix="/model")


def _store(request: Request) -> Store:
    return request.app.state.store


def _settings(request: Request):
    return request.app.state.settings


def _sim(request: Request):
    return request.app.state.sim


def _sim_env(settings) -> dict[str, str]:
    return {
        "MODEL_DIR": str(settings.data_dir / "current"),
        "PLC_HOST": settings.plc_host,
        "RUNTIME_URL": settings.runtime_url,
    }


async def _launch_sim_if_present(store: Store, settings, sim) -> None:
    if not store.sim_present():
        return
    meta = store.meta()
    display = meta.sim_filename if meta else None
    await sim.start(
        store.sim_path(),
        env_overrides=_sim_env(settings),
        display_filename=display,
    )


# --------------------------- POST /model -----------------------------------

@router.post("")
async def create_or_replace_model(
    request: Request,
    program_st: UploadFile = File(...),
    opcua_json: UploadFile = File(...),
    sim_py: UploadFile | None = File(None),
) -> dict:
    store = _store(request)
    settings = _settings(request)
    sim = _sim(request)

    # Kill any running sim immediately — we're about to replace the PLC.
    await sim.stop()

    staging = store.reset_staging()
    (staging / "conf").mkdir(parents=True, exist_ok=True)
    (staging / "sim").mkdir(parents=True, exist_ok=True)

    (staging / "program.st").write_bytes(await program_st.read())
    (staging / "conf" / "opcua.json").write_bytes(await opcua_json.read())

    sim_filename: str | None = None
    if sim_py is not None:
        sim_filename = sim_py.filename
        (staging / "sim" / "current.py").write_bytes(await sim_py.read())

    # Build + upload + poll. Uses a scratch dir outside staging so a
    # failed deploy doesn't pollute staging before promote.
    build_work = store.data_dir / "build-work"
    if build_work.exists():
        shutil.rmtree(build_work)
    build_work.mkdir(parents=True)

    try:
        await runtime_client.deploy(
            program_st=staging / "program.st",
            opcua_json=staging / "conf" / "opcua.json",
            work_dir=build_work,
            runtime_url=settings.runtime_url,
            username=settings.runtime_username,
            password=settings.runtime_password,
        )
    except Exception as e:
        shutil.rmtree(build_work, ignore_errors=True)
        raise HTTPException(status_code=502,
                            detail=f"Deploy to runtime failed: {e}") from e

    shutil.rmtree(build_work, ignore_errors=True)
    store.promote_staging_to_current(sim_filename=sim_filename)

    sim_status = None
    if sim_py is not None:
        sim_status = await sim.start(
            store.sim_path(),
            env_overrides=_sim_env(settings),
            display_filename=sim_filename,
        )

    return {
        "deployed": True,
        "sim_running": bool(sim_status and sim_status.running),
        "sim_pid": sim_status.pid if sim_status else None,
        "sim_filename": sim_filename,
    }


# --------------------------- GET /model ------------------------------------

@router.get("")
async def get_model(request: Request) -> dict:
    store = _store(request)
    sim = _sim(request)
    meta = store.meta()
    status = sim.status()
    return {
        "bundle_present": store.bundle_present(),
        "sim_present": store.sim_present(),
        "sim_running": status.running,
        "sim_pid": status.pid,
        "sim_filename": status.sim_filename or (meta.sim_filename if meta else None),
        "deployed_at": meta.deployed_at if meta else None,
    }


# --------------------------- DELETE /model ---------------------------------

@router.delete("")
async def delete_model(request: Request) -> dict:
    store = _store(request)
    settings = _settings(request)
    sim = _sim(request)

    await sim.stop()
    stopped_runtime = False
    try:
        await runtime_client.stop_plc(
            runtime_url=settings.runtime_url,
            username=settings.runtime_username,
            password=settings.runtime_password,
        )
        stopped_runtime = True
    except Exception:
        # If the runtime is already down or unreachable, we still want to
        # clear our persisted bundle so POST /model starts from clean.
        stopped_runtime = False

    store.clear_current()
    return {"cleared": True, "plc_stopped": stopped_runtime}


# --------------------------- PUT /model/sim --------------------------------

@router.put("/sim")
async def replace_sim(
    request: Request,
    sim_py: UploadFile = File(...),
) -> dict:
    store = _store(request)
    settings = _settings(request)
    sim = _sim(request)

    if not store.bundle_present():
        raise HTTPException(
            status_code=409,
            detail="No model deployed. POST /model first.",
        )

    sim_bytes = await sim_py.read()
    store.replace_sim(sim_bytes, sim_filename=sim_py.filename or "sim.py")

    status = await sim.start(
        store.sim_path(),
        env_overrides=_sim_env(settings),
        display_filename=sim_py.filename,
    )
    return {
        "sim_running": status.running,
        "sim_pid": status.pid,
        "sim_filename": sim_py.filename,
    }


# --------------------------- DELETE /model/sim -----------------------------

@router.delete("/sim")
async def stop_sim(request: Request) -> dict:
    store = _store(request)
    sim = _sim(request)
    await sim.stop()
    store.remove_sim()
    return {"sim_running": False}
