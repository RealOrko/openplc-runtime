"""Thin wrapper around openplc_client's build + deploy flow, callable from
async handlers.

The underlying openplc_client.RuntimeClient is synchronous (requests-based),
so every entry point runs in a threadpool via asyncio.to_thread() to avoid
blocking the uvicorn event loop.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import httpx

from openplc_client.binaries import ensure_binaries
from openplc_client.packager import zip_staging
from openplc_client.toolchain import build_src_tree
from openplc_client.uploader import RuntimeClient


def _sync_deploy(
    program_st: Path,
    opcua_json: Path,
    work_dir: Path,
    runtime_url: str,
    username: str,
    password: str,
) -> None:
    """Stage a minimal model layout under work_dir, compile it, zip it, and
    upload to the runtime. Blocking — call via asyncio.to_thread()."""
    model_dir = work_dir / "model"
    src_dir = work_dir / "src"
    zip_path = work_dir / "program.zip"

    if model_dir.exists():
        shutil.rmtree(model_dir)
    (model_dir / "conf").mkdir(parents=True)
    shutil.copy(program_st, model_dir / "program.st")
    shutil.copy(opcua_json, model_dir / "conf" / "opcua.json")

    target = ensure_binaries()
    build_src_tree(model_dir, src_dir, target)
    zip_staging(src_dir, zip_path)

    client = RuntimeClient(
        base_url=runtime_url,
        username=username,
        password=password,
    )
    client.ensure_authenticated()
    client.upload_zip(zip_path)
    client.poll_compilation()


async def deploy(
    program_st: Path,
    opcua_json: Path,
    work_dir: Path,
    runtime_url: str,
    username: str,
    password: str,
) -> None:
    await asyncio.to_thread(
        _sync_deploy,
        program_st, opcua_json, work_dir, runtime_url, username, password,
    )


def _sync_stop_plc(runtime_url: str, username: str, password: str) -> None:
    client = RuntimeClient(
        base_url=runtime_url, username=username, password=password
    )
    client.ensure_authenticated()
    client.stop_plc()


async def stop_plc(runtime_url: str, username: str, password: str) -> None:
    await asyncio.to_thread(_sync_stop_plc, runtime_url, username, password)


async def runtime_reachable(runtime_url: str) -> bool:
    """Readiness probe target — does the runtime's REST port answer TLS?
    We deliberately don't require a valid cert (self-signed in dev) or auth
    (the probe runs anonymously)."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=3.0) as c:
            resp = await c.get(f"{runtime_url.rstrip('/')}/api/get-users-info")
            return resp.status_code < 500
    except httpx.HTTPError:
        return False
