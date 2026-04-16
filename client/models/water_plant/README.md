# water_plant

A 20-unit water-treatment plant simulation that exposes 137 variables over
OPC UA (and Modbus, as a fallback) in a plant-structured folder hierarchy —
a SCADA-scale observability exercise built on the same pattern as
`tank_sim`.

## Process flow

```
Source_Reservoir -> Intake_Screen -> Intake_Pump_01/02
    -> Flash_Mixer (+ Coagulant_Dosing)
    -> Floc_Basin_01/02
    -> Clarifier_01/02
    -> Filter_01/02 (from Clarifier_01), Filter_03/04 (from Clarifier_02)
    -> Contact_Tank (+ Chlorine_Dosing)
    -> Clearwell -> Distribution_Pump_01/02 -> grid
```

All dynamics are kinematic (per-tick increments, clamped) running at a
20 ms scan cycle. There are no first-order time constants or noise —
matches tank_sim's style, scaled up.

## Units (20)

| Stage | Unit | OPC-UA folder |
|---|---|---|
| Intake | Source_Reservoir | `Plant/Intake/Source_Reservoir` |
| Intake | Intake_Screen | `Plant/Intake/Intake_Screen` |
| Intake | Intake_Pump_01 | `Plant/Intake/Intake_Pump_01` |
| Intake | Intake_Pump_02 | `Plant/Intake/Intake_Pump_02` |
| Coagulation | Flash_Mixer | `Plant/Coagulation/Flash_Mixer` |
| Coagulation | Coagulant_Dosing | `Plant/Coagulation/Coagulant_Dosing` |
| Coagulation | Floc_Basin_01 | `Plant/Coagulation/Floc_Basin_01` |
| Coagulation | Floc_Basin_02 | `Plant/Coagulation/Floc_Basin_02` |
| Sedimentation | Clarifier_01 | `Plant/Sedimentation/Clarifier_01` |
| Sedimentation | Clarifier_02 | `Plant/Sedimentation/Clarifier_02` |
| Filtration | Filter_01..04 | `Plant/Filtration/Filter_0N` |
| Disinfection | Chlorine_Dosing | `Plant/Disinfection/Chlorine_Dosing` |
| Disinfection | Contact_Tank | `Plant/Disinfection/Contact_Tank` |
| Distribution | Clearwell | `Plant/Distribution/Clearwell` |
| Distribution | Distribution_Pump_01 | `Plant/Distribution/Distribution_Pump_01` |
| Distribution | Distribution_Pump_02 | `Plant/Distribution/Distribution_Pump_02` |
| Supervision | Plant_Master | `Plant/Master` |

## Archetypes

The 137 variables compose from six reusable archetypes:

- **Tank/Basin**: `level`, `level_high_alarm`, `level_low_alarm`, `inflow`, `outflow`, optional `agitator_cmd`/`agitator_fault`
- **Pump**: `run_cmd`, `run_fb`, `fault`, `discharge_pressure`, `run_hours`, `start_count`
- **Valve/Screen**: `cmd`, `status`, `fault`, `diff_pressure`
- **Filter**: tank + `diff_pressure`, `backwash_cmd`, `backwash_active`, `loading_hours`, `backwash_count`
- **Dosing**: `dose_sp`, `dose_rate`, `stock_level`, `low_stock_alarm` + pump archetype
- **Plant master**: `heartbeat`, `plant_running`, `master_alarm`, `alarm_count`, `emergency_stop`, `total_inflow`, `total_outflow`

## Variables (summary)

- 66 BOOLs mapped to `%QX0.0..%QX8.1`
- 54 REALs + 17 DINTs mapped to `%MD0..%MD70`

`conf/opcua.json` lists all 137 with dotted `node_id` paths that are expanded
to a FolderType hierarchy by the runtime's OPC-UA plugin (see
`../../docs/OBSERVABILITY.md#how-the-runtimes-opc-ua-plugin-lays-out-nodes`).

## Deploy

```bash
cd client
python -m openplc_client deploy ./models/water_plant
```

That uploads the compiled program, auto-enables the `opcua` and
`modbus_slave` plugins via `conf/*.json`, and restarts the PLC.

If any ST variables get reordered later, re-sync OPC-UA indices:

```bash
python -m openplc_client sync-opcua ./models/water_plant
```

## Drive traffic

```bash
cd models/water_plant/sim
pip install -r requirements.txt
python hmi_sim.py
```

The simulator:
- Startup: sets `plant_running`, starts intake/distribution lead pumps,
  agitators, dosing pumps.
- **Status loop** (3 s): prints a one-line plant summary.
- **Rotation loop** (45 s): swaps lead/lag duty on intake and distribution
  pump pairs.
- **Backwash loop** (8 s): triggers the filter with highest diff-pressure
  when it exceeds 70 kPa, or rotates through filters on a long idle cycle.
  Won't stack backwashes while any filter is in cycle.
- **Alarm loop** (1.5 s): reacts to tank-level and low-chlorine alarms by
  stopping the responsible pumps or bumping the chlorine setpoint.
- **Setpoint wander** (20 s): random-walks coagulant and chlorine
  setpoints within safe bands so OPC-UA writes remain continuous.
- **Fault drill** (90 s): injects and clears a fault on a random
  non-critical piece of equipment.

## Observe with the CLI

```bash
python -m openplc_client status
python -m openplc_client logs --follow
python -m openplc_client watch ./models/water_plant
python -m openplc_client browse ./models/water_plant
```

For an OPC-UA GUI browser (UaExpert or similar), point it at
`opc.tcp://localhost:4840/openplc/water_plant` with None/None security and
Anonymous auth (or operator/operator for writes).

See `../../docs/OBSERVABILITY.md` for the full toolkit.
