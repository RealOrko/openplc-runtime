"""Sim log access.

GET /model/sim/logs?tail=200       -> JSON list of last N buffered lines
GET /model/sim/logs?stream=true    -> text/event-stream (SSE) tail
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/model/sim")


@router.get("/logs")
async def get_logs(
    request: Request,
    tail: int = Query(200, ge=1, le=5000),
    stream: bool = Query(False),
):
    sim = request.app.state.sim

    if not stream:
        return {"lines": sim.tail(tail)}

    async def event_source():
        async with sim.subscribe() as q:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    line = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {line}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
