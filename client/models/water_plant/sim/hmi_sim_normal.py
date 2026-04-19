"""Headless HMI simulator for the water_plant model (normal operation).

Connects to the OpenPLC runtime's OPC-UA server as the configured operator
and drives realistic plant traffic: startup sequencing, lead/lag pump
rotation, filter-backwash scheduling, rising-edge alarm response with
auto-recovery, setpoint wander, and chemical-stock refills.

This variant does NOT inject any faults: no `*_fault` flags are written and
no scripted incident carousel runs. The plant is allowed to run at
nominal. Alarms may still fire from natural drift (e.g. low chemical
stock), and the HMI will react and refill as it does in the fault-driven
variant.

Run after `openplc_client deploy ./models/water_plant`:

    pip install -r requirements.txt
    python hmi_sim_normal.py [--host localhost]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import signal
import sys
from pathlib import Path

_CLIENT_ROOT = Path(__file__).resolve().parents[3]
if str(_CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_CLIENT_ROOT))

from openplc_client.model_client import connect  # noqa: E402

MODEL_DIR = Path(__file__).resolve().parents[1]

STATUS_PERIOD_S = 3.0
ROTATE_PERIOD_S = 45.0
BACKWASH_CHECK_PERIOD_S = 8.0
BACKWASH_DP_THRESHOLD = 70.0
ALARM_POLL_PERIOD_S = 1.5
SETPOINT_WANDER_PERIOD_S = 20.0
REFILL_CHECK_PERIOD_S = 5.0
REFILL_DELAY_S = 10.0

INTAKE_PUMPS = ("intake_pump_01", "intake_pump_02")
DIST_PUMPS = ("distribution_pump_01", "distribution_pump_02")
FILTERS = ("filter_01", "filter_02", "filter_03", "filter_04")

# Alarms that, when active, gate the intake/distribution pump groups off.
INTAKE_STOP_ALARMS = ("source_reservoir_level_low_alarm", "flash_mixer_level_high_alarm")
DIST_STOP_ALARMS = ("clearwell_level_low_alarm",)

# Other alarms we edge-detect for telemetry and reactions.
OTHER_ALARMS = (
    "contact_tank_cl_low_alarm",
    "coagulant_dosing_low_stock_alarm",
    "chlorine_dosing_low_stock_alarm",
    "source_reservoir_level_high_alarm",
    "flash_mixer_level_low_alarm",
    "clearwell_level_high_alarm",
)

ALL_WATCHED_ALARMS = INTAKE_STOP_ALARMS + DIST_STOP_ALARMS + OTHER_ALARMS

_plant_state: dict = {
    "intake_lead": 0,
    "dist_lead": 0,
    "intake_safe": True,
    "dist_safe": True,
}


async def _apply_intake_cmds(m) -> None:
    lead = _plant_state["intake_lead"]
    safe = _plant_state["intake_safe"]
    await m.write(f"{INTAKE_PUMPS[lead]}_run_cmd", safe)
    await m.write(f"{INTAKE_PUMPS[1 - lead]}_run_cmd", False)


async def _apply_dist_cmds(m) -> None:
    lead = _plant_state["dist_lead"]
    safe = _plant_state["dist_safe"]
    await m.write(f"{DIST_PUMPS[lead]}_run_cmd", safe)
    await m.write(f"{DIST_PUMPS[1 - lead]}_run_cmd", False)


async def _startup(m) -> None:
    await m.write("emergency_stop", False)
    await m.write("plant_running", True)
    await m.write("intake_screen_cmd", True)
    await m.write("flash_mixer_agitator_cmd", True)
    await m.write("floc_basin_01_agitator_cmd", True)
    await m.write("floc_basin_02_agitator_cmd", True)
    await m.write("coagulant_dosing_pump_run_cmd", True)
    await m.write("chlorine_dosing_pump_run_cmd", True)
    await _apply_intake_cmds(m)
    await _apply_dist_cmds(m)
    print("[hmi] startup complete: plant running, lead pumps online", flush=True)


async def _status_loop(m, stop: asyncio.Event) -> None:
    while not stop.is_set():
        snap = await m.snapshot(
            "heartbeat", "plant_running", "master_alarm", "alarm_count",
            "total_inflow", "total_outflow",
            "source_reservoir_level", "flash_mixer_level", "clearwell_level",
            "contact_tank_cl_residual",
            "intake_pump_01_run_fb", "intake_pump_02_run_fb",
            "distribution_pump_01_run_fb", "distribution_pump_02_run_fb",
            "filter_01_diff_pressure", "filter_02_diff_pressure",
            "filter_03_diff_pressure", "filter_04_diff_pressure",
        )
        intake_on = sum(1 for k in ("intake_pump_01_run_fb", "intake_pump_02_run_fb") if snap[k])
        dist_on = sum(1 for k in ("distribution_pump_01_run_fb", "distribution_pump_02_run_fb") if snap[k])
        max_dp = max(snap[f"filter_0{i}_diff_pressure"] for i in range(1, 5))
        print(
            f"[hmi] hb={'O' if snap['heartbeat'] else '.'}  "
            f"run={'Y' if snap['plant_running'] else 'N'}  "
            f"src={snap['source_reservoir_level']:5.1f}  "
            f"fm={snap['flash_mixer_level']:5.1f}  "
            f"cw={snap['clearwell_level']:5.1f}  "
            f"cl={snap['contact_tank_cl_residual']:4.2f}  "
            f"in_pumps={intake_on}/2  "
            f"out_pumps={dist_on}/2  "
            f"maxFilt_dP={max_dp:5.1f}  "
            f"flowIn={snap['total_inflow']:4.2f}  flowOut={snap['total_outflow']:4.2f}  "
            f"alarm={'!' if snap['master_alarm'] else '-'}{int(snap['alarm_count']):<4d}",
            flush=True,
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=STATUS_PERIOD_S)
        except asyncio.TimeoutError:
            pass


async def _rotate_loop(m, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=ROTATE_PERIOD_S)
            return
        except asyncio.TimeoutError:
            pass
        _plant_state["intake_lead"] ^= 1
        _plant_state["dist_lead"] ^= 1
        await _apply_intake_cmds(m)
        await _apply_dist_cmds(m)
        print(
            f"[hmi] lead rotation: intake -> {INTAKE_PUMPS[_plant_state['intake_lead']]}, "
            f"distribution -> {DIST_PUMPS[_plant_state['dist_lead']]}",
            flush=True,
        )


async def _backwash_loop(m, stop: asyncio.Event) -> None:
    rr = 0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=BACKWASH_CHECK_PERIOD_S)
            return
        except asyncio.TimeoutError:
            pass
        snap = await m.snapshot(
            *[f"{f}_diff_pressure" for f in FILTERS],
            *[f"{f}_backwash_active" for f in FILTERS],
        )
        if any(snap[f"{f}_backwash_active"] for f in FILTERS):
            continue
        candidates = [
            (f, snap[f"{f}_diff_pressure"])
            for f in FILTERS
            if snap[f"{f}_diff_pressure"] >= BACKWASH_DP_THRESHOLD
        ]
        if candidates:
            target = max(candidates, key=lambda x: x[1])[0]
        else:
            target = FILTERS[rr % len(FILTERS)]
            rr += 1
        await m.write(f"{target}_backwash_cmd", True)
        await asyncio.sleep(0.3)
        await m.write(f"{target}_backwash_cmd", False)
        print(
            f"[hmi] backwash issued to {target} (dP={snap[f'{target}_diff_pressure']:.1f})",
            flush=True,
        )


async def _on_alarm_rise(m, name: str, snap: dict) -> None:
    if name in INTAKE_STOP_ALARMS:
        if _plant_state["intake_safe"]:
            _plant_state["intake_safe"] = False
            await _apply_intake_cmds(m)
            print(f"[hmi] {name} -> intake pumps stopped", flush=True)
    elif name in DIST_STOP_ALARMS:
        if _plant_state["dist_safe"]:
            _plant_state["dist_safe"] = False
            await _apply_dist_cmds(m)
            print(f"[hmi] {name} -> distribution pumps stopped", flush=True)
    elif name == "contact_tank_cl_low_alarm":
        new_sp = min(snap["chlorine_dosing_dose_sp"] + 0.1, 3.0)
        if new_sp != snap["chlorine_dosing_dose_sp"]:
            await m.write("chlorine_dosing_dose_sp", new_sp)
            print(f"[hmi] Cl low -> raising chlorine_dosing_dose_sp to {new_sp:.2f}", flush=True)
    elif name == "coagulant_dosing_low_stock_alarm":
        print("[hmi] coagulant stock LOW", flush=True)
    elif name == "chlorine_dosing_low_stock_alarm":
        print("[hmi] chlorine stock LOW", flush=True)
    else:
        print(f"[hmi] alarm raised: {name}", flush=True)


async def _on_alarm_fall(m, name: str, snap: dict) -> None:
    if name in INTAKE_STOP_ALARMS:
        if not any(snap[a] for a in INTAKE_STOP_ALARMS):
            _plant_state["intake_safe"] = True
            await _apply_intake_cmds(m)
            print(f"[hmi] {name} cleared -> intake lead re-enabled", flush=True)
    elif name in DIST_STOP_ALARMS:
        if not any(snap[a] for a in DIST_STOP_ALARMS):
            _plant_state["dist_safe"] = True
            await _apply_dist_cmds(m)
            print(f"[hmi] {name} cleared -> distribution lead re-enabled", flush=True)
    else:
        print(f"[hmi] alarm cleared: {name}", flush=True)


async def _alarm_loop(m, stop: asyncio.Event) -> None:
    prev: dict = {}
    while not stop.is_set():
        snap = await m.snapshot(*ALL_WATCHED_ALARMS, "chlorine_dosing_dose_sp")
        for name in ALL_WATCHED_ALARMS:
            now = bool(snap[name])
            was = prev.get(name)
            if was is None:
                prev[name] = now
                continue
            if now and not was:
                await _on_alarm_rise(m, name, snap)
            elif was and not now:
                await _on_alarm_fall(m, name, snap)
            prev[name] = now
        try:
            await asyncio.wait_for(stop.wait(), timeout=ALARM_POLL_PERIOD_S)
        except asyncio.TimeoutError:
            pass


async def _setpoint_wander_loop(m, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=SETPOINT_WANDER_PERIOD_S)
            return
        except asyncio.TimeoutError:
            pass
        coag = await m.read("coagulant_dosing_dose_sp")
        new_coag = max(2.0, min(10.0, coag + random.uniform(-0.3, 0.3)))
        await m.write("coagulant_dosing_dose_sp", new_coag)
        cl = await m.read("chlorine_dosing_dose_sp")
        new_cl = max(0.8, min(2.5, cl + random.uniform(-0.08, 0.08)))
        await m.write("chlorine_dosing_dose_sp", new_cl)


async def _refill_tank(m, stock_name: str, label: str) -> None:
    await asyncio.sleep(REFILL_DELAY_S)
    await m.write(stock_name, 100.0)
    print(f"[hmi] {label} tanker refill complete", flush=True)


async def _refill_loop(m, stop: asyncio.Event) -> None:
    prev_coag = False
    prev_cl = False
    pending_coag: asyncio.Task | None = None
    pending_cl: asyncio.Task | None = None
    while not stop.is_set():
        snap = await m.snapshot(
            "coagulant_dosing_low_stock_alarm", "chlorine_dosing_low_stock_alarm"
        )
        if snap["coagulant_dosing_low_stock_alarm"] and not prev_coag:
            if pending_coag is None or pending_coag.done():
                print(f"[hmi] scheduling coagulant refill in {REFILL_DELAY_S:.0f} s", flush=True)
                pending_coag = asyncio.create_task(
                    _refill_tank(m, "coagulant_dosing_stock_level", "coagulant")
                )
        if snap["chlorine_dosing_low_stock_alarm"] and not prev_cl:
            if pending_cl is None or pending_cl.done():
                print(f"[hmi] scheduling chlorine refill in {REFILL_DELAY_S:.0f} s", flush=True)
                pending_cl = asyncio.create_task(
                    _refill_tank(m, "chlorine_dosing_stock_level", "chlorine")
                )
        prev_coag = snap["coagulant_dosing_low_stock_alarm"]
        prev_cl = snap["chlorine_dosing_low_stock_alarm"]
        try:
            await asyncio.wait_for(stop.wait(), timeout=REFILL_CHECK_PERIOD_S)
        except asyncio.TimeoutError:
            pass


async def run(host: str) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    async with connect(MODEL_DIR, host=host) as m:
        print(f"[hmi] connected to {m.endpoint_url}; running water_plant HMI (normal)", flush=True)
        await _startup(m)
        await asyncio.gather(
            _status_loop(m, stop),
            _rotate_loop(m, stop),
            _backwash_loop(m, stop),
            _alarm_loop(m, stop),
            _setpoint_wander_loop(m, stop),
            _refill_loop(m, stop),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("PLC_HOST", "localhost"),
                        help="Hostname of the runtime (default $PLC_HOST or localhost)")
    args = parser.parse_args()
    asyncio.run(run(args.host))


if __name__ == "__main__":
    main()
