"""Headless HMI simulator for the tank_sim model.

Connects to the OpenPLC runtime's OPC-UA server as the configured operator,
watches the tank state, periodically drives the inlet valve and outlet pump
to simulate operator activity, and force-closes the valve if the high-level
alarm fires.

Run after `openplc_client deploy ./models/tank_sim`:

    pip install -r requirements.txt
    python hmi_sim.py [--host localhost]
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

# Make the client package importable when running this script directly from
# the sim/ folder inside the model.
_CLIENT_ROOT = Path(__file__).resolve().parents[3]
if str(_CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_CLIENT_ROOT))

from openplc_client.model_client import connect  # noqa: E402

MODEL_DIR = Path(__file__).resolve().parents[1]

PRINT_INTERVAL_S = 1.7          # avoid aliasing with the ~500 ms heartbeat
VALVE_TOGGLE_S = 10.0
PUMP_TOGGLE_S = 15.0


async def _print_loop(m, stop: asyncio.Event) -> None:
    while not stop.is_set():
        snap = await m.snapshot(
            "heartbeat", "inlet_valve", "outlet_pump",
            "level_high_alarm", "level_low_alarm",
            "tank_level", "pump_run_count",
        )
        alarms = []
        if snap["level_high_alarm"]:
            alarms.append("HIGH")
        if snap["level_low_alarm"]:
            alarms.append("LOW")
        print(
            f"[hmi] hb={'O' if snap['heartbeat'] else '.'}  "
            f"level={snap['tank_level']:5.1f}%  "
            f"valve={'open ' if snap['inlet_valve'] else 'shut '}  "
            f"pump={'on ' if snap['outlet_pump'] else 'off'}  "
            f"runs={snap['pump_run_count']:<4d}  "
            f"alarm={','.join(alarms) if alarms else '-'}",
            flush=True,
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=PRINT_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


async def _valve_loop(m, stop: asyncio.Event) -> None:
    open_valve = False
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=VALVE_TOGGLE_S)
            return
        except asyncio.TimeoutError:
            pass

        if await m.read("level_high_alarm"):
            if open_valve:
                open_valve = False
                await m.write("inlet_valve", False)
                print("[hmi] high alarm: forcing inlet valve CLOSED", flush=True)
            continue

        open_valve = not open_valve
        await m.write("inlet_valve", open_valve)
        print(f"[hmi] inlet_valve -> {'OPEN' if open_valve else 'SHUT'}", flush=True)


async def _pump_loop(m, stop: asyncio.Event) -> None:
    pump_on = False
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=PUMP_TOGGLE_S)
            return
        except asyncio.TimeoutError:
            pass
        pump_on = not pump_on
        await m.write("outlet_pump", pump_on)
        print(f"[hmi] outlet_pump -> {'ON' if pump_on else 'OFF'}", flush=True)


async def run(host: str) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    async with connect(MODEL_DIR, host=host) as m:
        print(f"[hmi] connected to {m.endpoint_url}; "
              f"driving valve/pump to simulate operator activity", flush=True)
        await asyncio.gather(
            _print_loop(m, stop),
            _valve_loop(m, stop),
            _pump_loop(m, stop),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost",
                        help="Hostname of the runtime (default localhost)")
    args = parser.parse_args()
    asyncio.run(run(args.host))


if __name__ == "__main__":
    main()
