"""Headless HMI simulator for the water_plant model.

Connects to the OpenPLC runtime's OPC-UA server as the configured operator,
drives realistic plant traffic: startup sequencing, lead/lag pump rotation,
filter-backwash scheduling, rising-edge alarm response with auto-recovery,
setpoint wander, a scripted incident carousel (distinct faults, operator
intervention, recovery verification, standby window), and chemical-stock
refills.

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
import time
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

# Incident carousel pacing
INCIDENT_WARMUP_S = 45.0                   # let plant settle before first incident
INCIDENT_REACT_DELAY_RANGE = (4.0, 10.0)   # operator sees alarm -> responds
INCIDENT_HOLD_DELAY_RANGE = (8.0, 18.0)    # how long fault is held before clearing
INCIDENT_STANDBY_RANGE = (18.0, 35.0)      # quiet window after full recovery
INCIDENT_ALARM_RISE_TIMEOUT_S = 45.0       # how long to wait for injected alarm
INCIDENT_RECOVERY_TIMEOUT_S = 120.0        # give up and move on if never recovers
INCIDENT_POLL_S = 1.0

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

# Shared operator state; helpers below read from it so rotation and alarm
# loops don't trample each other's pump commands.
_plant_state: dict = {
    "intake_lead": 0,
    "dist_lead": 0,
    "intake_safe": True,
    "dist_safe": True,
    # Name of the currently-running incident, or None. Other loops
    # (rotate) check this to avoid trampling operator actions that a
    # scenario depends on.
    "incident_active": None,
    "backwash_suppressed": False,
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
        if _plant_state.get("incident_active"):
            continue
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
        if _plant_state.get("backwash_suppressed"):
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


def _banner(text: str) -> None:
    print(f"[incident] ===== {text} =====", flush=True)


async def _sleep_or_stop(stop: asyncio.Event, secs: float) -> bool:
    """Sleep up to `secs`; return True if stop triggered while waiting."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=secs)
        return True
    except asyncio.TimeoutError:
        return False


async def _poll_until(m, predicate, var_names, stop: asyncio.Event,
                      timeout: float, poll_s: float = INCIDENT_POLL_S) -> bool:
    """Snapshot `var_names` every poll_s until predicate(snap) or timeout.

    Returns True on predicate hit, False on timeout or stop.
    """
    deadline = time.monotonic() + timeout
    while not stop.is_set():
        snap = await m.snapshot(*var_names)
        if predicate(snap):
            return True
        if time.monotonic() >= deadline:
            return False
        if await _sleep_or_stop(stop, poll_s):
            return False
    return False


async def _force_intake_lead(m, lead_idx: int) -> None:
    _plant_state["intake_lead"] = lead_idx
    await _apply_intake_cmds(m)


async def _force_dist_lead(m, lead_idx: int) -> None:
    _plant_state["dist_lead"] = lead_idx
    await _apply_dist_cmds(m)


# ----------------------------- Scenarios ------------------------------
#
# Each scenario is a single coroutine that runs one incident start-to-end:
#   INJECT -> WAIT_ALARM (optional) -> HOLD -> OPERATOR_RESPONSE ->
#   WAIT_RECOVERY.
# The incident loop wraps each scenario with a STANDBY window that gates
# on master_alarm == FALSE, so the plant visibly returns to nominal
# before the next incident starts.

async def _incident_intake_screen_fault(m, stop) -> None:
    name = "intake_screen_fault"
    _banner(f"START: {name}  (screen debris trip)")
    await m.write("intake_screen_fault", True)
    print("[incident] injected intake_screen_fault; intake outflow will drop", flush=True)
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_HOLD_DELAY_RANGE))
    if stop.is_set():
        return
    print("[incident] operator cycling intake_screen_cmd and clearing fault", flush=True)
    await m.write("intake_screen_cmd", False)
    await asyncio.sleep(0.4)
    await m.write("intake_screen_cmd", True)
    await m.write("intake_screen_fault", False)
    recovered = await _poll_until(
        m,
        lambda s: not s["intake_screen_fault"] and not s["master_alarm"],
        ("intake_screen_fault", "master_alarm"),
        stop, INCIDENT_RECOVERY_TIMEOUT_S,
    )
    _banner(f"{'RECOVERED' if recovered else 'RECOVERY TIMEOUT'}: {name}")


async def _incident_flash_mixer_agitator(m, stop) -> None:
    name = "flash_mixer_agitator_trip"
    _banner(f"START: {name}")
    await m.write("flash_mixer_agitator_fault", True)
    print("[incident] injected flash_mixer_agitator_fault; mixer outflow halts", flush=True)
    # Wait for the level alarm to cascade (alarm_loop will stop intake for us)
    await _poll_until(
        m, lambda s: s["flash_mixer_level_high_alarm"],
        ("flash_mixer_level_high_alarm",),
        stop, INCIDENT_ALARM_RISE_TIMEOUT_S,
    )
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_HOLD_DELAY_RANGE))
    if stop.is_set():
        return
    print("[incident] operator clearing flash_mixer_agitator_fault", flush=True)
    await m.write("flash_mixer_agitator_fault", False)
    recovered = await _poll_until(
        m,
        lambda s: not s["flash_mixer_level_high_alarm"] and not s["master_alarm"],
        ("flash_mixer_level_high_alarm", "master_alarm"),
        stop, INCIDENT_RECOVERY_TIMEOUT_S,
    )
    _banner(f"{'RECOVERED' if recovered else 'RECOVERY TIMEOUT'}: {name}")


async def _incident_coagulant_pump_trip(m, stop) -> None:
    name = "coagulant_pump_trip"
    _banner(f"START: {name}")
    await m.write("coagulant_dosing_pump_fault", True)
    print("[incident] injected coagulant_dosing_pump_fault; dosing halts", flush=True)
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_HOLD_DELAY_RANGE))
    if stop.is_set():
        return
    print("[incident] operator clearing coagulant_dosing_pump_fault", flush=True)
    await m.write("coagulant_dosing_pump_fault", False)
    recovered = await _poll_until(
        m,
        lambda s: (not s["coagulant_dosing_pump_fault"]
                   and s["coagulant_dosing_dose_rate"] > 0.01
                   and not s["master_alarm"]),
        ("coagulant_dosing_pump_fault", "coagulant_dosing_dose_rate", "master_alarm"),
        stop, INCIDENT_RECOVERY_TIMEOUT_S,
    )
    _banner(f"{'RECOVERED' if recovered else 'RECOVERY TIMEOUT'}: {name}")


async def _incident_floc_basin_agitator(m, stop) -> None:
    basin = random.choice(("floc_basin_01", "floc_basin_02"))
    name = f"{basin}_agitator_trip"
    _banner(f"START: {name}")
    await m.write(f"{basin}_agitator_fault", True)
    print(f"[incident] injected {basin}_agitator_fault; basin outflow halts", flush=True)
    await _poll_until(
        m, lambda s: s[f"{basin}_level_high_alarm"],
        (f"{basin}_level_high_alarm",),
        stop, INCIDENT_ALARM_RISE_TIMEOUT_S,
    )
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_HOLD_DELAY_RANGE))
    if stop.is_set():
        return
    print(f"[incident] operator clearing {basin}_agitator_fault", flush=True)
    await m.write(f"{basin}_agitator_fault", False)
    recovered = await _poll_until(
        m,
        lambda s: (not s[f"{basin}_level_high_alarm"]
                   and not s["master_alarm"]),
        (f"{basin}_level_high_alarm", "master_alarm"),
        stop, INCIDENT_RECOVERY_TIMEOUT_S,
    )
    _banner(f"{'RECOVERED' if recovered else 'RECOVERY TIMEOUT'}: {name}")


async def _incident_chlorine_pump_trip(m, stop) -> None:
    name = "chlorine_pump_trip"
    _banner(f"START: {name}  (Cl residual will decay)")
    await m.write("chlorine_dosing_pump_fault", True)
    print("[incident] injected chlorine_dosing_pump_fault", flush=True)
    # Residual decays slowly: give extra time for cl_low alarm to rise.
    await _poll_until(
        m, lambda s: s["contact_tank_cl_low_alarm"],
        ("contact_tank_cl_low_alarm",),
        stop, INCIDENT_ALARM_RISE_TIMEOUT_S + 60.0,
    )
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_REACT_DELAY_RANGE))
    if stop.is_set():
        return
    print("[incident] operator clearing chlorine_dosing_pump_fault", flush=True)
    await m.write("chlorine_dosing_pump_fault", False)
    # Recovery takes a while: Cl residual has to climb above 0.5 again.
    recovered = await _poll_until(
        m,
        lambda s: (not s["contact_tank_cl_low_alarm"]
                   and not s["chlorine_dosing_pump_fault"]),
        ("contact_tank_cl_low_alarm", "chlorine_dosing_pump_fault"),
        stop, INCIDENT_RECOVERY_TIMEOUT_S + 120.0,
    )
    _banner(f"{'RECOVERED' if recovered else 'RECOVERY TIMEOUT'}: {name}")


async def _incident_distribution_pump_trip(m, stop) -> None:
    idx = random.choice((0, 1))
    pump = DIST_PUMPS[idx]
    name = f"{pump}_trip"
    _banner(f"START: {name}")
    await _force_dist_lead(m, idx)          # make sure the faulted pump is the lead
    await m.write(f"{pump}_fault", True)
    print(f"[incident] injected {pump}_fault", flush=True)
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_REACT_DELAY_RANGE))
    if stop.is_set():
        return
    other = DIST_PUMPS[1 - idx]
    print(f"[incident] operator rotating distribution lead to {other}", flush=True)
    await _force_dist_lead(m, 1 - idx)
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_HOLD_DELAY_RANGE))
    if stop.is_set():
        return
    print(f"[incident] operator clearing {pump}_fault", flush=True)
    await m.write(f"{pump}_fault", False)
    recovered = await _poll_until(
        m,
        lambda s: not s[f"{pump}_fault"] and not s["master_alarm"],
        (f"{pump}_fault", "master_alarm"),
        stop, INCIDENT_RECOVERY_TIMEOUT_S,
    )
    _banner(f"{'RECOVERED' if recovered else 'RECOVERY TIMEOUT'}: {name}")


async def _incident_intake_pump_trip(m, stop) -> None:
    # Trip the lead intake pump; operator switches to standby then restores.
    idx = _plant_state["intake_lead"]
    pump = INTAKE_PUMPS[idx]
    name = f"{pump}_trip"
    _banner(f"START: {name}")
    await _force_intake_lead(m, idx)
    await m.write(f"{pump}_fault", True)
    print(f"[incident] injected {pump}_fault; intake flow will halve", flush=True)
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_REACT_DELAY_RANGE))
    if stop.is_set():
        return
    other = INTAKE_PUMPS[1 - idx]
    print(f"[incident] operator switching intake lead to {other}", flush=True)
    await _force_intake_lead(m, 1 - idx)
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_HOLD_DELAY_RANGE))
    if stop.is_set():
        return
    print(f"[incident] operator clearing {pump}_fault", flush=True)
    await m.write(f"{pump}_fault", False)
    recovered = await _poll_until(
        m,
        lambda s: not s[f"{pump}_fault"] and not s["master_alarm"],
        (f"{pump}_fault", "master_alarm"),
        stop, INCIDENT_RECOVERY_TIMEOUT_S,
    )
    _banner(f"{'RECOVERED' if recovered else 'RECOVERY TIMEOUT'}: {name}")


async def _incident_dual_filter_high_dp(m, stop) -> None:
    # Suppress automatic backwash so two filters foul naturally; operator then
    # manually backwashes each in sequence.
    pair = random.choice((("filter_01", "filter_02"), ("filter_03", "filter_04")))
    name = f"dual_high_dp_{pair[0]}_{pair[1]}"
    _banner(f"START: {name}  (backwash suppressed; filters fouling naturally)")
    print(f"[incident] suppressing backwash loop to let {pair[0]} and {pair[1]} foul", flush=True)
    _plant_state["backwash_suppressed"] = True
    hit = await _poll_until(
        m,
        lambda s: (s[f"{pair[0]}_diff_pressure"] >= BACKWASH_DP_THRESHOLD
                   and s[f"{pair[1]}_diff_pressure"] >= BACKWASH_DP_THRESHOLD),
        (f"{pair[0]}_diff_pressure", f"{pair[1]}_diff_pressure"),
        stop, INCIDENT_ALARM_RISE_TIMEOUT_S * 6,
    )
    _plant_state["backwash_suppressed"] = False
    if not hit:
        _banner(f"RECOVERY TIMEOUT (dP never reached threshold): {name}")
        return
    for f in pair:
        print(f"[incident] operator issuing backwash to {f}", flush=True)
        await m.write(f"{f}_backwash_cmd", True)
        await asyncio.sleep(0.3)
        await m.write(f"{f}_backwash_cmd", False)
        await _sleep_or_stop(stop, 35.0)
        if stop.is_set():
            return
    recovered = await _poll_until(
        m,
        lambda s: not s["master_alarm"],
        ("master_alarm",),
        stop, INCIDENT_RECOVERY_TIMEOUT_S,
    )
    _banner(f"{'RECOVERED' if recovered else 'RECOVERY TIMEOUT'}: {name}")


async def _incident_emergency_stop(m, stop) -> None:
    # Operator triggers e-stop then restarts after a short inspection window.
    name = "emergency_stop"
    _banner(f"START: {name}  (e-stop drill)")
    print("[incident] operator pressing emergency stop", flush=True)
    await m.write("emergency_stop", True)
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_HOLD_DELAY_RANGE))
    if stop.is_set():
        await m.write("emergency_stop", False)
        await m.write("plant_running", True)
        return
    print("[incident] inspection complete; releasing e-stop and restarting plant", flush=True)
    await m.write("emergency_stop", False)
    await asyncio.sleep(1.0)
    await m.write("plant_running", True)
    await _apply_intake_cmds(m)
    await _apply_dist_cmds(m)
    recovered = await _poll_until(
        m,
        lambda s: s["plant_running"] and not s["master_alarm"],
        ("plant_running", "master_alarm"),
        stop, INCIDENT_RECOVERY_TIMEOUT_S,
    )
    _banner(f"{'RECOVERED' if recovered else 'RECOVERY TIMEOUT'}: {name}")


async def _incident_clarifier_starve(m, stop) -> None:
    # Both floc basin agitators on one train trip simultaneously -> clarifier
    # starved -> low-level alarm cascade.
    basin_pair = ("floc_basin_01", "floc_basin_02")
    name = "dual_floc_basin_trip"
    _banner(f"START: {name}  (both basins trip; clarifiers starved)")
    for b in basin_pair:
        await m.write(f"{b}_agitator_fault", True)
    print("[incident] both floc basin agitators faulted", flush=True)
    await _poll_until(
        m,
        lambda s: s["floc_basin_01_level_high_alarm"] or s["floc_basin_02_level_high_alarm"],
        ("floc_basin_01_level_high_alarm", "floc_basin_02_level_high_alarm"),
        stop, INCIDENT_ALARM_RISE_TIMEOUT_S,
    )
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_HOLD_DELAY_RANGE))
    if stop.is_set():
        for b in basin_pair:
            await m.write(f"{b}_agitator_fault", False)
        return
    print("[incident] operator clearing both floc basin faults", flush=True)
    for b in basin_pair:
        await m.write(f"{b}_agitator_fault", False)
    recovered = await _poll_until(
        m,
        lambda s: (not s["floc_basin_01_level_high_alarm"]
                   and not s["floc_basin_02_level_high_alarm"]
                   and not s["master_alarm"]),
        ("floc_basin_01_level_high_alarm", "floc_basin_02_level_high_alarm", "master_alarm"),
        stop, INCIDENT_RECOVERY_TIMEOUT_S,
    )
    _banner(f"{'RECOVERED' if recovered else 'RECOVERY TIMEOUT'}: {name}")


async def _incident_coagulant_overdose(m, stop) -> None:
    # Setpoint accidentally ramped to maximum; operator detects and corrects.
    name = "coagulant_overdose"
    _banner(f"START: {name}  (dose SP spiked to max)")
    original_sp = await m.read("coagulant_dosing_dose_sp")
    await m.write("coagulant_dosing_dose_sp", 18.0)
    print(f"[incident] coagulant dose SP spiked to 18.0 (was {original_sp:.1f})", flush=True)
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_HOLD_DELAY_RANGE))
    if stop.is_set():
        await m.write("coagulant_dosing_dose_sp", original_sp)
        return
    corrected = max(2.0, min(10.0, original_sp))
    print(f"[incident] operator correcting coagulant SP to {corrected:.1f}", flush=True)
    await m.write("coagulant_dosing_dose_sp", corrected)
    recovered = await _poll_until(
        m,
        lambda s: not s["master_alarm"],
        ("master_alarm",),
        stop, INCIDENT_RECOVERY_TIMEOUT_S,
    )
    _banner(f"{'RECOVERED' if recovered else 'RECOVERY TIMEOUT'}: {name}")


async def _incident_clearwell_low(m, stop) -> None:
    # Drain clearwell to near-empty by stopping distribution lead for too long.
    # Alarm fires; operator brings standby distribution pump online.
    name = "clearwell_low_level"
    _banner(f"START: {name}  (demand surge drains clearwell)")
    lead = _plant_state["dist_lead"]
    other = 1 - lead
    _plant_state["dist_safe"] = False
    await _apply_dist_cmds(m)
    # Also run standby to maximise draw
    await m.write(f"{DIST_PUMPS[other]}_run_cmd", True)
    print("[incident] both distribution pumps running to drain clearwell", flush=True)
    hit = await _poll_until(
        m, lambda s: s["clearwell_level_low_alarm"],
        ("clearwell_level_low_alarm",),
        stop, INCIDENT_ALARM_RISE_TIMEOUT_S + 30.0,
    )
    if not hit:
        _plant_state["dist_safe"] = True
        await _apply_dist_cmds(m)
        _banner(f"RECOVERY TIMEOUT (alarm never fired): {name}")
        return
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_REACT_DELAY_RANGE))
    if stop.is_set():
        _plant_state["dist_safe"] = True
        await _apply_dist_cmds(m)
        return
    print("[incident] operator stopping distribution pumps to let clearwell recover", flush=True)
    await m.write(f"{DIST_PUMPS[other]}_run_cmd", False)
    _plant_state["dist_safe"] = True
    await _apply_dist_cmds(m)
    recovered = await _poll_until(
        m,
        lambda s: not s["clearwell_level_low_alarm"] and not s["master_alarm"],
        ("clearwell_level_low_alarm", "master_alarm"),
        stop, INCIDENT_RECOVERY_TIMEOUT_S,
    )
    _banner(f"{'RECOVERED' if recovered else 'RECOVERY TIMEOUT'}: {name}")


async def _incident_chlorine_overdose(m, stop) -> None:
    # Chlorine SP accidentally set too high; operator must detect and reduce.
    name = "chlorine_overdose"
    _banner(f"START: {name}  (Cl dose SP spiked)")
    original_sp = await m.read("chlorine_dosing_dose_sp")
    await m.write("chlorine_dosing_dose_sp", 4.5)
    print(f"[incident] chlorine dose SP spiked to 4.5 mg/L (was {original_sp:.2f})", flush=True)
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_HOLD_DELAY_RANGE))
    if stop.is_set():
        await m.write("chlorine_dosing_dose_sp", original_sp)
        return
    corrected = max(0.8, min(2.5, original_sp))
    print(f"[incident] operator correcting chlorine SP to {corrected:.2f}", flush=True)
    await m.write("chlorine_dosing_dose_sp", corrected)
    recovered = await _poll_until(
        m,
        lambda s: not s["master_alarm"],
        ("master_alarm",),
        stop, INCIDENT_RECOVERY_TIMEOUT_S,
    )
    _banner(f"{'RECOVERED' if recovered else 'RECOVERY TIMEOUT'}: {name}")


async def _incident_source_reservoir_low(m, stop) -> None:
    # Simulate a drought/supply interruption by zeroing river inflow indirectly:
    # trip both intake pumps so reservoir level climbs first, then restores.
    # Actually simulates the reverse: both intake pumps left running drain the
    # reservoir; operator stops one to conserve.
    name = "source_reservoir_low"
    _banner(f"START: {name}  (raw water supply reduced)")
    lead = _plant_state["intake_lead"]
    other = 1 - lead
    # Run both pumps to draw reservoir down faster
    await m.write(f"{INTAKE_PUMPS[other]}_run_cmd", True)
    print("[incident] both intake pumps running to deplete source reservoir", flush=True)
    hit = await _poll_until(
        m, lambda s: s["source_reservoir_level_low_alarm"],
        ("source_reservoir_level_low_alarm",),
        stop, INCIDENT_ALARM_RISE_TIMEOUT_S + 60.0,
    )
    if not hit:
        await m.write(f"{INTAKE_PUMPS[other]}_run_cmd", False)
        _banner(f"RECOVERY TIMEOUT (alarm never fired): {name}")
        return
    await _sleep_or_stop(stop, random.uniform(*INCIDENT_REACT_DELAY_RANGE))
    if stop.is_set():
        await m.write(f"{INTAKE_PUMPS[other]}_run_cmd", False)
        return
    print("[incident] operator stopping standby intake pump to conserve source", flush=True)
    await m.write(f"{INTAKE_PUMPS[other]}_run_cmd", False)
    recovered = await _poll_until(
        m,
        lambda s: not s["source_reservoir_level_low_alarm"] and not s["master_alarm"],
        ("source_reservoir_level_low_alarm", "master_alarm"),
        stop, INCIDENT_RECOVERY_TIMEOUT_S + 60.0,
    )
    _banner(f"{'RECOVERED' if recovered else 'RECOVERY TIMEOUT'}: {name}")


INCIDENTS = (
    _incident_intake_screen_fault,
    _incident_flash_mixer_agitator,
    _incident_coagulant_pump_trip,
    _incident_floc_basin_agitator,
    _incident_chlorine_pump_trip,
    _incident_distribution_pump_trip,
    _incident_intake_pump_trip,
    _incident_dual_filter_high_dp,
    _incident_emergency_stop,
    _incident_clarifier_starve,
    _incident_coagulant_overdose,
    _incident_clearwell_low,
    _incident_chlorine_overdose,
    _incident_source_reservoir_low,
)


async def _incident_loop(m, stop: asyncio.Event) -> None:
    # Warmup: let the plant settle and Cl residual climb above 0.5 so
    # master_alarm is actually FALSE before we start causing trouble.
    if await _sleep_or_stop(stop, INCIDENT_WARMUP_S):
        return
    order = list(INCIDENTS)
    while not stop.is_set():
        random.shuffle(order)
        for scenario in order:
            if stop.is_set():
                return
            _plant_state["incident_active"] = scenario.__name__
            try:
                await scenario(m, stop)
            except Exception as exc:  # pragma: no cover - sim best-effort
                print(f"[incident] error in {scenario.__name__}: {exc!r}", flush=True)
            finally:
                _plant_state["incident_active"] = None
            if stop.is_set():
                return
            # Standby: wait until master_alarm is clear, then sit idle so
            # the operator can see the plant back at baseline.
            settled = await _poll_until(
                m, lambda s: not s["master_alarm"], ("master_alarm",),
                stop, timeout=45.0,
            )
            if not settled:
                print("[incident] master_alarm still latched; advancing anyway", flush=True)
            standby = random.uniform(*INCIDENT_STANDBY_RANGE)
            _banner(f"STANDBY {standby:.0f}s  plant nominal")
            if await _sleep_or_stop(stop, standby):
                return


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
        print(f"[hmi] connected to {m.endpoint_url}; running water_plant HMI", flush=True)
        await _startup(m)
        await asyncio.gather(
            _status_loop(m, stop),
            _rotate_loop(m, stop),
            _backwash_loop(m, stop),
            _alarm_loop(m, stop),
            _setpoint_wander_loop(m, stop),
            _incident_loop(m, stop),
            _refill_loop(m, stop),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost",
                        help="Hostname of the runtime (default localhost)")
    args = parser.parse_args()
    asyncio.run(run(args.host))


if __name__ == "__main__":
    main()
