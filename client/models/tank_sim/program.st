(*
  tank_sim — a minimal tank/pump process simulation for the headless client.

  A 100-unit tank fills when the inlet valve is open and drains when the
  outlet pump runs. High / low alarms fire at the level thresholds. A
  heartbeat toggles once per second so observers can tell the PLC is alive.
  The pump_run_count increments on each rising edge of outlet_pump so the
  HMI simulator has a monotonic counter to watch.

  MainTask runs at 20 ms; scaling below assumes that cadence.
*)

PROGRAM Main
VAR_EXTERNAL
    heartbeat        : BOOL;
    inlet_valve      : BOOL;
    outlet_pump      : BOOL;
    level_high_alarm : BOOL;
    level_low_alarm  : BOOL;
    tank_level       : REAL;
    pump_run_count   : DINT;
END_VAR
VAR
    heartbeat_ticks  : INT := 0;
    pump_last        : BOOL := FALSE;
END_VAR

(* --- heartbeat: toggle every 25 ticks ~= 500 ms --- *)
heartbeat_ticks := heartbeat_ticks + 1;
IF heartbeat_ticks >= 25 THEN
    heartbeat_ticks := 0;
    heartbeat := NOT heartbeat;
END_IF;

(* --- tank level integration --- *)
IF inlet_valve THEN
    tank_level := tank_level + 0.1;
END_IF;
IF outlet_pump THEN
    tank_level := tank_level - 0.2;
END_IF;
IF tank_level > 100.0 THEN
    tank_level := 100.0;
END_IF;
IF tank_level < 0.0 THEN
    tank_level := 0.0;
END_IF;

(* --- alarms --- *)
level_high_alarm := tank_level > 80.0;
level_low_alarm  := tank_level < 20.0;

(* --- pump run counter: rising-edge on outlet_pump --- *)
IF outlet_pump AND NOT pump_last THEN
    pump_run_count := pump_run_count + 1;
END_IF;
pump_last := outlet_pump;

END_PROGRAM

CONFIGURATION Config0

    VAR_GLOBAL
        heartbeat        AT %QX0.0 : BOOL := FALSE;
        inlet_valve      AT %QX0.1 : BOOL := FALSE;
        outlet_pump      AT %QX0.2 : BOOL := FALSE;
        level_high_alarm AT %QX0.3 : BOOL := FALSE;
        level_low_alarm  AT %QX0.4 : BOOL := TRUE;   (* starts empty *)
        tank_level       AT %MD0   : REAL := 0.0;
        pump_run_count   AT %MD1   : DINT := 0;
    END_VAR

    RESOURCE Res0 ON PLC
        TASK MainTask(INTERVAL := T#20ms, PRIORITY := 0);
        PROGRAM MainInst WITH MainTask : Main;
    END_RESOURCE

END_CONFIGURATION
