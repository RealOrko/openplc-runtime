"""Subprocess lifecycle manager for the single sim child.

One instance per orchestrator process. Safe for concurrent API calls
because all state-mutating operations take a single asyncio.Lock.

The sim child runs with these env vars:
    MODEL_DIR   -> /data/current    (so the sim can load conf/opcua.json)
    PLC_HOST    -> hostname parsed from RUNTIME_URL
    RUNTIME_URL -> full runtime base URL (rarely needed by sims)
    PYTHONPATH  -> inherited (image sets it so openplc_client imports work)

Stdout + stderr are merged and streamed into:
    - a bounded deque for `?tail=N`
    - a set of asyncio.Queue subscribers for SSE streaming
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SimStatus:
    running: bool
    pid: int | None
    sim_filename: str | None


class SimProcess:
    def __init__(
        self,
        *,
        log_buffer_lines: int,
        shutdown_grace_s: float,
    ) -> None:
        self._lock = asyncio.Lock()
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._buffer: deque[str] = deque(maxlen=log_buffer_lines)
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._shutdown_grace_s = shutdown_grace_s
        self._current_filename: str | None = None

    # ---- public API ----

    def status(self) -> SimStatus:
        proc = self._proc
        running = proc is not None and proc.returncode is None
        return SimStatus(
            running=running,
            pid=proc.pid if running else None,
            sim_filename=self._current_filename if running else None,
        )

    async def start(
        self,
        script_path: Path,
        *,
        env_overrides: dict[str, str],
        display_filename: str | None,
    ) -> SimStatus:
        """Stop any running child, then launch `script_path` as a subprocess.
        Returns post-launch status."""
        async with self._lock:
            await self._stop_locked()
            if not script_path.is_file():
                raise FileNotFoundError(f"sim script not found: {script_path}")

            env = os.environ.copy()
            env.update(env_overrides)

            self._proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-u",                       # unbuffered stdout
                str(script_path),
                cwd=str(script_path.parent.parent),   # /data/current
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._current_filename = display_filename
            self._buffer.clear()
            self._reader_task = asyncio.create_task(
                self._read_loop(self._proc),
                name="sim-log-reader",
            )
            return SimStatus(
                running=True,
                pid=self._proc.pid,
                sim_filename=display_filename,
            )

    async def stop(self) -> SimStatus:
        async with self._lock:
            await self._stop_locked()
            return SimStatus(running=False, pid=None, sim_filename=None)

    def tail(self, n: int) -> list[str]:
        if n <= 0:
            return []
        if n >= len(self._buffer):
            return list(self._buffer)
        return list(self._buffer)[-n:]

    @contextlib.asynccontextmanager
    async def subscribe(self):
        """Yields an asyncio.Queue[str] that receives every new log line.
        Caller is responsible for iterating quickly; if the queue fills we
        drop oldest messages to keep up."""
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        # Seed with current tail so the subscriber sees recent history
        for line in list(self._buffer):
            if q.full():
                break
            q.put_nowait(line)
        self._subscribers.add(q)
        try:
            yield q
        finally:
            self._subscribers.discard(q)

    # ---- internal ----

    async def _stop_locked(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=self._shutdown_grace_s)
            except asyncio.TimeoutError:
                proc.kill()
                with contextlib.suppress(ProcessLookupError):
                    await proc.wait()
        if self._reader_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        self._proc = None
        self._reader_task = None
        self._current_filename = None

    async def _read_loop(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        try:
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
                self._buffer.append(line)
                # Fan out to subscribers; drop on full to avoid blocking
                # the reader on a slow consumer.
                for q in list(self._subscribers):
                    if q.full():
                        with contextlib.suppress(asyncio.QueueEmpty):
                            q.get_nowait()
                    q.put_nowait(line)
        finally:
            await proc.wait()
            # Propagate exit to stdout of orchestrator for kubectl logs
            print(
                f"[sim-orchestrator] sim child exited rc={proc.returncode}",
                flush=True,
            )

    async def aclose(self) -> None:
        """Called on app shutdown. Best-effort reap."""
        with contextlib.suppress(Exception):
            await self.stop()
