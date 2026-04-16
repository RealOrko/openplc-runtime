"""Unified, model-driven client for talking to a deployed PLC program.

Reads a model folder's `conf/` and pre-resolves every variable so callers
can write natural code like:

    async with connect("./models/tank_sim") as m:
        level = await m["tank_level"].read()
        await m["inlet_valve"].write(True)

        async for snapshot in m.poll("tank_level", "level_high_alarm", period=0.5):
            print(snapshot)

Hides the OPC-UA quirks (browse_name-as-lookup-key, namespace resolution,
Anonymous-vs-Username auth). Also has a Modbus path for models without
OPC-UA configured, backed by pymodbus.

Optional dependencies are imported lazily so the rest of openplc_client
works without them installed:
    asyncua   (OPC-UA path)
    pymodbus  (Modbus path)
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Iterable
from urllib.parse import urlparse


# ---------- config loading -------------------------------------------------

@dataclass
class _OpcuaConfig:
    endpoint_url: str
    namespace_uri: str
    variables: list[dict]
    users: list[dict]


def _load_opcua_config(model_dir: Path) -> _OpcuaConfig | None:
    path = model_dir / "conf" / "opcua.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text())
    if isinstance(raw, list):
        if not raw:
            return None
        raw = raw[0].get("config", raw[0])
    return _OpcuaConfig(
        endpoint_url=raw["server"]["endpoint_url"],
        namespace_uri=raw["address_space"]["namespace_uri"],
        variables=list(raw["address_space"].get("variables", [])),
        users=list(raw.get("users", [])),
    )


def _load_modbus_config(model_dir: Path) -> dict | None:
    path = model_dir / "conf" / "modbus_slave.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def _pick_default_credentials(cfg: _OpcuaConfig) -> tuple[str | None, str | None]:
    """If the config lists exactly one password user, guess credentials as
    (username, username) — matches the tank_sim demo convention. Otherwise
    return (None, None) and fall back to Anonymous."""
    password_users = [u for u in cfg.users if u.get("type") == "password"]
    if len(password_users) == 1:
        name = password_users[0].get("username")
        return (name, name) if name else (None, None)
    return (None, None)


# ---------- OPC-UA implementation ------------------------------------------

class _OpcuaVariable:
    """Pre-resolved variable handle. Read/write values by their OPC-UA type."""

    _TYPE_MAP = {
        "BOOL": "Boolean",
        "BYTE": "Byte",
        "SINT": "SByte",
        "INT": "Int16",
        "DINT": "Int32",
        "LINT": "Int64",
        "USINT": "Byte",
        "UINT": "UInt16",
        "UDINT": "UInt32",
        "ULINT": "UInt64",
        "REAL": "Float",
        "LREAL": "Double",
        "STRING": "String",
    }

    def __init__(self, browse_name: str, datatype: str, node: Any) -> None:
        self.browse_name = browse_name
        self.datatype = datatype.upper()
        self._node = node

    async def read(self) -> Any:
        return await self._node.read_value()

    async def write(self, value: Any) -> None:
        from asyncua import ua  # local import to keep top-level optional

        variant_name = self._TYPE_MAP.get(self.datatype)
        if variant_name is None:
            raise ValueError(f"Unsupported datatype for write: {self.datatype}")
        variant_type = getattr(ua.VariantType, variant_name)
        coerced = _coerce_value(self.datatype, value)
        await self._node.write_value(ua.DataValue(ua.Variant(coerced, variant_type)))


def _coerce_value(datatype: str, value: Any) -> Any:
    dt = datatype.upper()
    if dt == "BOOL":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if dt in ("REAL", "LREAL"):
        return float(value)
    if dt in ("SINT", "INT", "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT", "BYTE"):
        return int(value)
    return value


class OpcuaModelClient:
    """OPC-UA-backed model client. Pre-resolves every configured variable so
    callers can reach them by browse_name without dealing with the plugin's
    flat-namespace quirk."""

    def __init__(self, config: _OpcuaConfig, username: str | None, password: str | None) -> None:
        self._config = config
        self._username = username
        self._password = password
        self._client: Any = None
        self._ns_idx: int | None = None
        self._variables: dict[str, _OpcuaVariable] = {}
        self._resolved_endpoint: str | None = None

    @property
    def endpoint_url(self) -> str:
        return self._resolved_endpoint or self._config.endpoint_url

    @property
    def variables(self) -> dict[str, _OpcuaVariable]:
        return self._variables

    def __getitem__(self, name: str) -> _OpcuaVariable:
        try:
            return self._variables[name]
        except KeyError as e:
            raise KeyError(
                f"Variable '{name}' is not in this model's conf/opcua.json. "
                f"Known: {sorted(self._variables)}"
            ) from e

    def __contains__(self, name: str) -> bool:
        return name in self._variables

    def __iter__(self):
        return iter(self._variables)

    async def read(self, name: str) -> Any:
        return await self[name].read()

    async def write(self, name: str, value: Any) -> None:
        await self[name].write(value)

    async def connect(self, host_override: str | None = None) -> None:
        from asyncua import Client

        endpoint = self._config.endpoint_url
        parsed = urlparse(endpoint)
        # 0.0.0.0 in the runtime's config means "listen on all interfaces";
        # a client actually reaching it needs a real host. Rewrite if needed.
        if parsed.hostname in ("0.0.0.0", None) or host_override:
            replacement = host_override or "localhost"
            endpoint = endpoint.replace(parsed.hostname or "0.0.0.0", replacement, 1)
        self._resolved_endpoint = endpoint

        self._client = Client(url=endpoint)
        if self._username and self._password:
            self._client.set_user(self._username)
            self._client.set_password(self._password)
        await self._client.connect()

        self._ns_idx = await self._client.get_namespace_index(self._config.namespace_uri)

        for var in self._config.variables:
            browse_name = var["browse_name"]
            node = await self._resolve_variable_node(var)
            self._variables[browse_name] = _OpcuaVariable(
                browse_name=browse_name,
                datatype=var["datatype"],
                node=node,
            )

    async def _resolve_variable_node(self, var: dict) -> Any:
        """Walk the dotted `node_id` prefix as a FolderType path under
        Objects, then attach to the leaf via browse_name. Backward
        compatible with the old flat layout: if the prefix walk fails (no
        such folder), fall back to looking up the leaf directly under
        Objects."""
        browse_name = var["browse_name"]
        node_id = var.get("node_id", "") or ""
        segments = [s for s in node_id.split(".") if s]
        prefix = segments[:-1]

        # Walk the prefix folders, then attach the leaf by browse_name.
        try:
            node = self._client.nodes.objects
            for segment in prefix:
                node = await node.get_child([f"{self._ns_idx}:{segment}"])
            return await node.get_child([f"{self._ns_idx}:{browse_name}"])
        except Exception:
            # Fallback: older runtime builds placed every variable directly
            # under Objects keyed by browse_name only.
            return await self._client.nodes.objects.get_child(
                [f"{self._ns_idx}:{browse_name}"]
            )

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

    async def snapshot(self, *names: str) -> dict[str, Any]:
        """Read a set of variables atomically (best-effort) into a dict."""
        selected = names or tuple(self._variables)
        values = await asyncio.gather(*(self[n].read() for n in selected))
        return dict(zip(selected, values))

    async def poll(
        self,
        *names: str,
        period: float = 1.0,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield a snapshot dict of the named variables every `period` seconds."""
        while True:
            yield await self.snapshot(*names)
            await asyncio.sleep(period)


# ---------- Modbus implementation -----------------------------------------

class ModbusModelClient:
    """Simple Modbus-TCP client for models without OPC-UA. Variables are
    addressed by raw Modbus type + offset since the model's IEC→Modbus
    mapping isn't encoded in any config file.

    Example:
        m = ModbusModelClient(model_dir, host="localhost")
        m.connect()
        m.read_coils(0, 8)
        m.close()
    """

    def __init__(self, model_dir: Path, host: str = "localhost") -> None:
        cfg = _load_modbus_config(model_dir)
        if cfg is None:
            raise FileNotFoundError(f"No conf/modbus_slave.json in {model_dir}")
        self._host = host
        self._port = int(cfg.get("network_configuration", {}).get("port", 5020))
        self._coils = int(cfg.get("coils", {}).get("qx_bits", 16))
        self._holding = int(cfg.get("holding_registers", {}).get("qw_count", 16))
        self._client: Any = None

    def connect(self) -> None:
        from pymodbus.client import ModbusTcpClient

        self._client = ModbusTcpClient(self._host, port=self._port, timeout=2)
        if not self._client.connect():
            raise ConnectionError(f"Cannot reach modbus at {self._host}:{self._port}")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def read_coils(self, offset: int = 0, count: int | None = None) -> list[bool]:
        resp = self._client.read_coils(offset, count=count or self._coils)
        if resp.isError():
            raise RuntimeError(f"read_coils({offset}): {resp}")
        return [bool(b) for b in resp.bits[: (count or self._coils)]]

    def read_holding(self, offset: int = 0, count: int | None = None) -> list[int]:
        resp = self._client.read_holding_registers(offset, count=count or self._holding)
        if resp.isError():
            raise RuntimeError(f"read_holding({offset}): {resp}")
        return list(resp.registers[: (count or self._holding)])

    def write_coil(self, offset: int, value: bool) -> None:
        resp = self._client.write_coil(offset, bool(value))
        if resp.isError():
            raise RuntimeError(f"write_coil({offset}): {resp}")


# ---------- public entrypoint ---------------------------------------------

@asynccontextmanager
async def connect(
    model_dir: Path | str,
    *,
    host: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> AsyncIterator[OpcuaModelClient]:
    """Open an OPC-UA-backed model client scoped to a `async with` block.

    Credential precedence: explicit args > single-password-user convention
    from conf/opcua.json > Anonymous.
    """
    model_dir = Path(model_dir)
    cfg = _load_opcua_config(model_dir)
    if cfg is None:
        raise FileNotFoundError(
            f"No conf/opcua.json in {model_dir}. Use ModbusModelClient for "
            "Modbus-only models."
        )

    if username is None and password is None:
        username, password = _pick_default_credentials(cfg)

    client = OpcuaModelClient(cfg, username=username, password=password)
    await client.connect(host_override=host)
    try:
        yield client
    finally:
        await client.close()


def variable_names(model_dir: Path | str) -> list[str]:
    """Return the browse_names of every variable in the model's
    conf/opcua.json. Cheap — no connection required."""
    cfg = _load_opcua_config(Path(model_dir))
    if cfg is None:
        return []
    return [v["browse_name"] for v in cfg.variables]
