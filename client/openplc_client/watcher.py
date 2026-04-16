"""Model-driven live watch. OPC-UA goes through model_client (which hides
the browse_name-vs-node_id gotcha); Modbus stays as a raw coil/register
dump since the IEC→Modbus address mapping isn't encoded in conf/.

Optional dependencies imported lazily:
    asyncua   (OPC-UA)
    pymodbus  (Modbus)
"""

from __future__ import annotations

import asyncio
import signal
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openplc_client.model_client import (
    ModbusModelClient,
    connect,
)

POLL_INTERVAL_S = 1.0


def _format_value(value: Any, datatype: str | None = None) -> str:
    if isinstance(value, float):
        return f"{value:10.3f}"
    if isinstance(value, bool):
        return "True " if value else "False"
    if value is None:
        return "?"
    return str(value)


async def _watch_opcua(model_dir: Path, host: str) -> int:
    try:
        import asyncua  # noqa: F401
    except ImportError:
        print("ERROR: asyncua is required for OPC-UA watch. Install with "
              "`pip install asyncua`.")
        return 2

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    async with connect(model_dir, host=host) as m:
        print(f"[watch] connected to {m.endpoint_url}; "
              f"{len(m.variables)} variables; Ctrl-C to stop")
        while not stop.is_set():
            print(f"--- {time.strftime('%H:%M:%S')} ---")
            snap = await m.snapshot()
            for name, value in snap.items():
                dtype = m[name].datatype
                print(f"  {name:<24s} {_format_value(value, dtype)} [{dtype}]")
            try:
                await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
    return 0


def _watch_modbus(model_dir: Path, host: str) -> int:
    try:
        import pymodbus  # noqa: F401
    except ImportError:
        print("ERROR: pymodbus is required for Modbus watch. Install with "
              "`pip install pymodbus`.")
        return 2

    try:
        m = ModbusModelClient(model_dir, host=host)
        m.connect()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 1
    except ConnectionError as e:
        print(f"ERROR: {e}")
        return 1

    print(f"[watch] connected to modbus {host}; Ctrl-C to stop")
    try:
        while True:
            print(f"--- {time.strftime('%H:%M:%S')} ---")
            try:
                coils = m.read_coils()
                bits = " ".join("1" if b else "0" for b in coils)
                print(f"  coils[0..{len(coils)-1}]     {bits}")
            except RuntimeError as e:
                print(f"  coils read error: {e}")
            try:
                regs = m.read_holding()
                vals = " ".join(f"{v:5d}" for v in regs)
                print(f"  holding[0..{len(regs)-1}]   {vals}")
            except RuntimeError as e:
                print(f"  holding read error: {e}")
            time.sleep(POLL_INTERVAL_S)
    except KeyboardInterrupt:
        pass
    finally:
        m.close()
    return 0


def watch_model(model_dir: Path, runtime_url: str, prefer: str = "auto") -> int:
    host = urlparse(runtime_url).hostname or "localhost"

    has_opcua = (model_dir / "conf" / "opcua.json").is_file()
    has_modbus = (model_dir / "conf" / "modbus_slave.json").is_file()

    if prefer == "opcua" or (prefer == "auto" and has_opcua):
        if not has_opcua:
            print("ERROR: no conf/opcua.json in model folder")
            return 1
        return asyncio.run(_watch_opcua(model_dir, host))
    if prefer == "modbus" or (prefer == "auto" and has_modbus):
        if not has_modbus:
            print("ERROR: no conf/modbus_slave.json in model folder")
            return 1
        return _watch_modbus(model_dir, host)

    print("ERROR: model has neither conf/opcua.json nor conf/modbus_slave.json")
    return 1
