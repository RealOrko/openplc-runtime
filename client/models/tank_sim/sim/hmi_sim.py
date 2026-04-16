"""Headless HMI simulator for the tank_sim model.

Connects to the OpenPLC runtime's OPC-UA server (Anonymous auth), watches
the tank state, periodically drives the inlet valve and outlet pump to
simulate operator activity, and force-closes the valve if the high-level
alarm fires.

Run after `openplc_client deploy ./models/tank_sim`:

    pip install -r requirements.txt
    python hmi_sim.py [--endpoint opc.tcp://localhost:4840/openplc/tank]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
from dataclasses import dataclass

from asyncua import Client, ua

DEFAULT_ENDPOINT = "opc.tcp://localhost:4840/openplc/tank"
NAMESPACE = "urn:openplc:tank_sim"
DEFAULT_USERNAME = "operator"
DEFAULT_PASSWORD = "operator"

PRINT_INTERVAL_S = 1.7          # avoid aliasing with the ~500 ms heartbeat
VALVE_TOGGLE_S = 10.0
PUMP_TOGGLE_S = 15.0


@dataclass
class TankNodes:
    heartbeat: object
    inlet_valve: object
    outlet_pump: object
    level_high_alarm: object
    level_low_alarm: object
    tank_level: object
    pump_run_count: object


async def _resolve_nodes(client: Client) -> TankNodes:
    """The OPC-UA plugin flattens every configured variable as a direct
    child of `Objects`, keyed by browse_name — the dotted node_id in the
    JSON is only a label, not a hierarchy."""
    ns = await client.get_namespace_index(NAMESPACE)
    objects = client.nodes.objects

    async def find(browse_name: str) -> object:
        return await objects.get_child([f"{ns}:{browse_name}"])

    return TankNodes(
        heartbeat=await find("heartbeat"),
        inlet_valve=await find("inlet_valve"),
        outlet_pump=await find("outlet_pump"),
        level_high_alarm=await find("level_high_alarm"),
        level_low_alarm=await find("level_low_alarm"),
        tank_level=await find("tank_level"),
        pump_run_count=await find("pump_run_count"),
    )


async def _write_bool(node, value: bool) -> None:
    await node.write_value(ua.DataValue(ua.Variant(value, ua.VariantType.Boolean)))


async def _print_loop(nodes: TankNodes, stop: asyncio.Event) -> None:
    while not stop.is_set():
        level = await nodes.tank_level.read_value()
        valve = await nodes.inlet_valve.read_value()
        pump = await nodes.outlet_pump.read_value()
        high = await nodes.level_high_alarm.read_value()
        low = await nodes.level_low_alarm.read_value()
        count = await nodes.pump_run_count.read_value()
        hb = await nodes.heartbeat.read_value()

        alarms = []
        if high:
            alarms.append("HIGH")
        if low:
            alarms.append("LOW")
        alarm_str = ",".join(alarms) if alarms else "-"
        hb_str = "O" if hb else "."

        print(
            f"[hmi] hb={hb_str}  level={level:5.1f}%  "
            f"valve={'open ' if valve else 'shut '}  "
            f"pump={'on ' if pump else 'off'}  "
            f"runs={count:<4d}  alarm={alarm_str}",
            flush=True,
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=PRINT_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


async def _valve_loop(nodes: TankNodes, stop: asyncio.Event) -> None:
    open_valve = False
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=VALVE_TOGGLE_S)
            return
        except asyncio.TimeoutError:
            pass

        # Safety: never open the valve while the high alarm is active
        high = await nodes.level_high_alarm.read_value()
        if high:
            if open_valve:
                open_valve = False
                await _write_bool(nodes.inlet_valve, False)
                print("[hmi] high alarm: forcing inlet valve CLOSED", flush=True)
            continue

        open_valve = not open_valve
        await _write_bool(nodes.inlet_valve, open_valve)
        print(f"[hmi] inlet_valve -> {'OPEN' if open_valve else 'SHUT'}", flush=True)


async def _pump_loop(nodes: TankNodes, stop: asyncio.Event) -> None:
    pump_on = False
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=PUMP_TOGGLE_S)
            return
        except asyncio.TimeoutError:
            pass
        pump_on = not pump_on
        await _write_bool(nodes.outlet_pump, pump_on)
        print(f"[hmi] outlet_pump -> {'ON' if pump_on else 'OFF'}", flush=True)


async def run(endpoint: str, username: str, password: str) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    print(f"[hmi] connecting to {endpoint} as '{username}'")
    client = Client(url=endpoint)
    client.set_user(username)
    client.set_password(password)
    await client.connect()
    try:
        nodes = await _resolve_nodes(client)
        print("[hmi] connected; driving valve/pump to simulate operator activity")
        await asyncio.gather(
            _print_loop(nodes, stop),
            _valve_loop(nodes, stop),
            _pump_loop(nodes, stop),
        )
    finally:
        await client.disconnect()
        print("[hmi] disconnected")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                        help=f"OPC-UA endpoint URL (default {DEFAULT_ENDPOINT})")
    parser.add_argument("--username", default=DEFAULT_USERNAME,
                        help=f"OPC-UA username (default {DEFAULT_USERNAME})")
    parser.add_argument("--password", default=DEFAULT_PASSWORD,
                        help="OPC-UA password")
    args = parser.parse_args()
    asyncio.run(run(args.endpoint, args.username, args.password))


if __name__ == "__main__":
    main()
