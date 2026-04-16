"""Headless HMI simulator for the water_plant model.

Connects to the OpenPLC runtime's OPC-UA server as the configured operator,
drives realistic plant traffic: startup sequencing, lead/lag pump rotation,
filter-backwash scheduling, alarm response, setpoint wander, fault drills.

Run after `openplc_client deploy ./models/water_plant`:

    pip install -r requirements.txt
    python hmi_sim.py [--host localhost]
"""

from __future__ import annotations

import argparse
import asyncio
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
ROTATE_PERIOD_S = 45.0       # lead/lag swap cadence
BACKWASH_CHECK_PERIOD_S = 8.0
BACKWASH_DP_THRESHOLD = 70.0
ALARM_POLL_PERIOD_S = 1.5
SETPOINT_WANDER_PERIOD_S = 20.0
FAULT_DRILL_PERIOD_S = 90.0

INTAKE_PUMPS = ("intake_pump_01", "intake_pump_02")
DIST_PUMPS = ("distribution_pump_01", "distribution_pump_02")
FILTERS = ("filter_01", "filter_02", "filter_03", "filter_04")
AGITATORS = ("flash_mixer", "floc_basin_01", "floc_basin_02")

# equipment the drill loop may fault-inject — critical items (all distribution
# pumps, both intake pumps simultaneously) excluded so the plant keeps flowing.
FAULT_CANDIDATES = (
    "intake_screen_fault",
    "flash_mixer_agitator_fault",
    "floc_basin_01_agitator_fault",
    "floc_basin_02_agitator_fault",
    "coagulant_dosing_pump_fault",
)


async def _startup(m) -> None:
    """Bring the plant to nominal operating state."""
    await m.write("emergency_stop", False)
    await m.write("plant_running", True)
    await m.write("intake_screen_cmd", True)
    await m.write("flash_mixer_agitator_cmd", True)
    await m.write("floc_basin_01_agitator_cmd", True)
    await m.write("floc_basin_02_agitator_cmd", True)
    await m.write("coagulant_dosing_pump_run_cmd", True)
    await m.write("chlorine_dosing_pump_run_cmd", True)
    await m.write("intake_pump_01_run_cmd", True)          # lead
    await m.write("intake_pump_02_run_cmd", False)         # lag
    await m.write("distribution_pump_01_run_cmd", True)    # lead
    await m.write("distribution_pump_02_run_cmd", False)   # lag
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
    """Swap lead/lag duty on intake and distribution pump pairs."""
    intake_lead = 0
    dist_lead = 0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=ROTATE_PERIOD_S)
            return
        except asyncio.TimeoutError:
            pass
        intake_lead ^= 1
        await m.write(f"{INTAKE_PUMPS[intake_lead]}_run_cmd", True)
        await m.write(f"{INTAKE_PUMPS[1 - intake_lead]}_run_cmd", False)
        dist_lead ^= 1
        await m.write(f"{DIST_PUMPS[dist_lead]}_run_cmd", True)
        await m.write(f"{DIST_PUMPS[1 - dist_lead]}_run_cmd", False)
        print(
            f"[hmi] lead rotation: intake -> {INTAKE_PUMPS[intake_lead]}, "
            f"distribution -> {DIST_PUMPS[dist_lead]}",
            flush=True,
        )


async def _backwash_loop(m, stop: asyncio.Event) -> None:
    """Round-robin backwash: trigger whichever filter has the highest dP and
    exceeds threshold, or the next one in sequence on a long idle timer."""
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
        # Don't stack backwashes: skip if any filter is already in cycle.
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
        # one-shot: clear cmd shortly so next rising-edge still triggers.
        await asyncio.sleep(0.3)
        await m.write(f"{target}_backwash_cmd", False)
        print(
            f"[hmi] backwash issued to {target} (dP={snap[f'{target}_diff_pressure']:.1f})",
            flush=True,
        )


async def _alarm_loop(m, stop: asyncio.Event) -> None:
    """React to alarms: close upstream on a tank-high, reduce load on
    clearwell-low, raise chlorine setpoint on low residual."""
    while not stop.is_set():
        snap = await m.snapshot(
            "master_alarm",
            "source_reservoir_level_high_alarm", "source_reservoir_level_low_alarm",
            "flash_mixer_level_high_alarm",
            "clearwell_level_low_alarm", "clearwell_level_high_alarm",
            "contact_tank_cl_low_alarm",
            "chlorine_dosing_dose_sp",
            "coagulant_dosing_low_stock_alarm",
            "chlorine_dosing_low_stock_alarm",
        )
        if snap["flash_mixer_level_high_alarm"]:
            await m.write("intake_pump_01_run_cmd", False)
            await m.write("intake_pump_02_run_cmd", False)
            print("[hmi] flash_mixer HIGH -> intake pumps stopped", flush=True)
        if snap["source_reservoir_level_low_alarm"]:
            await m.write("intake_pump_01_run_cmd", False)
            await m.write("intake_pump_02_run_cmd", False)
            print("[hmi] source_reservoir LOW -> intake pumps stopped", flush=True)
        if snap["clearwell_level_low_alarm"]:
            await m.write("distribution_pump_01_run_cmd", False)
            await m.write("distribution_pump_02_run_cmd", False)
            print("[hmi] clearwell LOW -> distribution pumps stopped", flush=True)
        if snap["contact_tank_cl_low_alarm"]:
            new_sp = min(snap["chlorine_dosing_dose_sp"] + 0.1, 3.0)
            if new_sp != snap["chlorine_dosing_dose_sp"]:
                await m.write("chlorine_dosing_dose_sp", new_sp)
                print(f"[hmi] Cl low -> raising chlorine_dosing_dose_sp to {new_sp:.2f}", flush=True)
        if snap["coagulant_dosing_low_stock_alarm"]:
            print("[hmi] coagulant stock LOW (manual refill required)", flush=True)
        if snap["chlorine_dosing_low_stock_alarm"]:
            print("[hmi] chlorine stock LOW (manual refill required)", flush=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=ALARM_POLL_PERIOD_S)
        except asyncio.TimeoutError:
            pass


async def _setpoint_wander_loop(m, stop: asyncio.Event) -> None:
    """Small random walk on dose setpoints to keep OPC-UA writes continuous."""
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


async def _fault_drill_loop(m, stop: asyncio.Event) -> None:
    """Periodically inject then clear a fault on a non-critical item."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=FAULT_DRILL_PERIOD_S)
            return
        except asyncio.TimeoutError:
            pass
        target = random.choice(FAULT_CANDIDATES)
        await m.write(target, True)
        print(f"[hmi] fault drill: asserted {target}", flush=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=15.0)
            return
        except asyncio.TimeoutError:
            pass
        await m.write(target, False)
        print(f"[hmi] fault drill: cleared {target}", flush=True)


async def run(host: str) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    async with connect(MODEL_DIR, host=host) as m:
        print(f"[hmi] connected to {m.endpoint_url}; running water_plant HMI", flush=True)
        await _startup(m)
        await asyncio.gather(
            _status_loop(m, stop),
            _rotate_loop(m, stop),
            _backwash_loop(m, stop),
            _alarm_loop(m, stop),
            _setpoint_wander_loop(m, stop),
            _fault_drill_loop(m, stop),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost",
                        help="Hostname of the runtime (default localhost)")
    args = parser.parse_args()
    asyncio.run(run(args.host))


if __name__ == "__main__":
    main()
