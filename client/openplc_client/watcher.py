"""Model-driven live watch: reads a model's conf/*.json and polls the
matching plugin (OPC-UA preferred, Modbus fallback) so you can see variable
values change in the terminal.

Optional dependencies — imported lazily so the rest of openplc_client works
without them:
    asyncua   (for OPC-UA)
    pymodbus  (for Modbus)
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

POLL_INTERVAL_S = 1.0


def _format_row(name: str, value: Any, datatype: str | None = None) -> str:
    if isinstance(value, float):
        rendered = f"{value:10.3f}"
    elif isinstance(value, bool):
        rendered = "True " if value else "False"
    elif value is None:
        rendered = "?"
    else:
        rendered = str(value)
    type_str = f" [{datatype}]" if datatype else ""
    return f"  {name:<24s} {rendered}{type_str}"


def _load_opcua_config(model_dir: Path) -> dict | None:
    path = model_dir / "conf" / "opcua.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    if isinstance(data, list):
        if not data:
            return None
        data = data[0].get("config", data[0])
    return data


def _load_modbus_config(model_dir: Path) -> dict | None:
    path = model_dir / "conf" / "modbus_slave.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())


async def _watch_opcua(model_dir: Path, runtime_host: str) -> int:
    try:
        from asyncua import Client
    except ImportError:
        print("ERROR: asyncua is required for OPC-UA watch. Install with "
              "`pip install asyncua` or run from the model's sim/ folder.")
        return 2

    cfg = _load_opcua_config(model_dir)
    if cfg is None:
        return 1

    endpoint_url = cfg["server"]["endpoint_url"]
    # Rewrite 0.0.0.0 in the config to the host we're actually connecting to
    parsed = urlparse(endpoint_url)
    if parsed.hostname in ("0.0.0.0", None):
        endpoint_url = endpoint_url.replace("0.0.0.0", runtime_host, 1)

    namespace_uri = cfg["address_space"]["namespace_uri"]
    variables = cfg["address_space"]["variables"]

    print(f"[watch] connecting to {endpoint_url}")
    client = Client(url=endpoint_url)
    await client.connect()
    try:
        ns = await client.get_namespace_index(namespace_uri)
        node_map = {}
        for var in variables:
            # The OPC-UA plugin places every variable as a direct child of
            # Objects, keyed by browse_name. The dotted node_id is a label
            # only, not a hierarchy.
            node = await client.nodes.objects.get_child([f"{ns}:{var['browse_name']}"])
            node_map[var["browse_name"]] = (node, var["datatype"])

        print(f"[watch] {len(node_map)} variables; Ctrl-C to stop")
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass

        while not stop.is_set():
            print(f"--- {time.strftime('%H:%M:%S')} ---")
            for name, (node, dtype) in node_map.items():
                try:
                    value = await node.read_value()
                except Exception as e:
                    value = f"<err: {e}>"
                print(_format_row(name, value, dtype))
            try:
                await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
    finally:
        await client.disconnect()
    return 0


def _watch_modbus(model_dir: Path, runtime_host: str) -> int:
    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError:
        print("ERROR: pymodbus is required for Modbus watch. Install with "
              "`pip install pymodbus`.")
        return 2

    cfg = _load_modbus_config(model_dir)
    if cfg is None:
        return 1

    port = int(cfg.get("network_configuration", {}).get("port", 5020))
    coils = int(cfg.get("coils", {}).get("qx_bits", 16))
    holding = int(cfg.get("holding_registers", {}).get("qw_count", 16))

    client = ModbusTcpClient(runtime_host, port=port, timeout=2)
    if not client.connect():
        print(f"ERROR: cannot connect to Modbus at {runtime_host}:{port}")
        return 1
    print(f"[watch] connected to modbus {runtime_host}:{port}; Ctrl-C to stop")
    try:
        while True:
            print(f"--- {time.strftime('%H:%M:%S')} ---")
            if coils > 0:
                resp = client.read_coils(0, count=coils)
                if not resp.isError():
                    bits = " ".join("1" if b else "0" for b in resp.bits[:coils])
                    print(f"  coils[0..{coils-1}]       {bits}")
            if holding > 0:
                resp = client.read_holding_registers(0, count=holding)
                if not resp.isError():
                    vals = " ".join(f"{v:5d}" for v in resp.registers[:holding])
                    print(f"  holding[0..{holding-1}]   {vals}")
            time.sleep(POLL_INTERVAL_S)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
    return 0


def watch_model(model_dir: Path, runtime_url: str, prefer: str = "auto") -> int:
    parsed = urlparse(runtime_url)
    host = parsed.hostname or "localhost"

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
