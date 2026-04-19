"""Liveness + readiness endpoints for K8s probes.

/healthz — 200 always (process is up)
/readyz  — 200 iff the runtime is reachable (so we're useful, not just alive)
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.runtime_client import runtime_reachable

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict:
    settings = request.app.state.settings
    if await runtime_reachable(settings.runtime_url):
        return {"status": "ready", "runtime": settings.runtime_url}
    response.status_code = 503
    return {"status": "runtime-unreachable", "runtime": settings.runtime_url}
