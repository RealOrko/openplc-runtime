(*
  water_plant - 20-unit water-treatment plant simulation.

  Single PLC, single program.st, kinematic per-tick integration (matches the
  tank_sim pattern scaled up). Process flow:

      Source_Reservoir --> Intake_Screen --> Intake_Pump_01/02
              --> Flash_Mixer (with Coagulant_Dosing)
              --> Floc_Basin_01/02
              --> Clarifier_01/02
              --> Filter_01/02 (from Clarifier_01), Filter_03/04 (from Clarifier_02)
              --> Contact_Tank (with Chlorine_Dosing)
              --> Clearwell --> Distribution_Pump_01/02 --> grid

  All located variables live in one VAR_GLOBAL block at CONFIGURATION scope
  to keep MatIEC v4.0.11's parser happy (mixing AT and non-AT in one VAR
  block fails to compile).

  MainTask runs at 20 ms; all rate literals below assume that cadence.
*)

PROGRAM Main
VAR_EXTERNAL
    (* --- Supervision --- *)
    heartbeat                               : BOOL;
    plant_running                           : BOOL;
    master_alarm                            : BOOL;
    emergency_stop                          : BOOL;
    (* --- Source Reservoir --- *)
    source_reservoir_level_high_alarm       : BOOL;
    source_reservoir_level_low_alarm        : BOOL;
    (* --- Intake Screen --- *)
    intake_screen_cmd                       : BOOL;
    intake_screen_status                    : BOOL;
    intake_screen_fault                     : BOOL;
    (* --- Intake Pump 01 --- *)
    intake_pump_01_run_cmd                  : BOOL;
    intake_pump_01_run_fb                   : BOOL;
    intake_pump_01_fault                    : BOOL;
    (* --- Intake Pump 02 --- *)
    intake_pump_02_run_cmd                  : BOOL;
    intake_pump_02_run_fb                   : BOOL;
    intake_pump_02_fault                    : BOOL;
    (* --- Flash Mixer --- *)
    flash_mixer_level_high_alarm            : BOOL;
    flash_mixer_level_low_alarm             : BOOL;
    flash_mixer_agitator_cmd                : BOOL;
    flash_mixer_agitator_fault              : BOOL;
    (* --- Coagulant Dosing --- *)
    coagulant_dosing_low_stock_alarm        : BOOL;
    coagulant_dosing_pump_run_cmd           : BOOL;
    coagulant_dosing_pump_run_fb            : BOOL;
    coagulant_dosing_pump_fault             : BOOL;
    (* --- Floc Basin 01 --- *)
    floc_basin_01_level_high_alarm          : BOOL;
    floc_basin_01_level_low_alarm           : BOOL;
    floc_basin_01_agitator_cmd              : BOOL;
    floc_basin_01_agitator_fault            : BOOL;
    (* --- Floc Basin 02 --- *)
    floc_basin_02_level_high_alarm          : BOOL;
    floc_basin_02_level_low_alarm           : BOOL;
    floc_basin_02_agitator_cmd              : BOOL;
    floc_basin_02_agitator_fault            : BOOL;
    (* --- Clarifiers --- *)
    clarifier_01_level_high_alarm           : BOOL;
    clarifier_01_level_low_alarm            : BOOL;
    clarifier_02_level_high_alarm           : BOOL;
    clarifier_02_level_low_alarm            : BOOL;
    (* --- Filters --- *)
    filter_01_level_high_alarm              : BOOL;
    filter_01_level_low_alarm               : BOOL;
    filter_01_backwash_cmd                  : BOOL;
    filter_01_backwash_active               : BOOL;
    filter_02_level_high_alarm              : BOOL;
    filter_02_level_low_alarm               : BOOL;
    filter_02_backwash_cmd                  : BOOL;
    filter_02_backwash_active               : BOOL;
    filter_03_level_high_alarm              : BOOL;
    filter_03_level_low_alarm               : BOOL;
    filter_03_backwash_cmd                  : BOOL;
    filter_03_backwash_active               : BOOL;
    filter_04_level_high_alarm              : BOOL;
    filter_04_level_low_alarm               : BOOL;
    filter_04_backwash_cmd                  : BOOL;
    filter_04_backwash_active               : BOOL;
    (* --- Chlorine Dosing --- *)
    chlorine_dosing_low_stock_alarm         : BOOL;
    chlorine_dosing_pump_run_cmd            : BOOL;
    chlorine_dosing_pump_run_fb             : BOOL;
    chlorine_dosing_pump_fault              : BOOL;
    (* --- Contact Tank --- *)
    contact_tank_level_high_alarm           : BOOL;
    contact_tank_level_low_alarm            : BOOL;
    contact_tank_cl_low_alarm               : BOOL;
    (* --- Clearwell --- *)
    clearwell_level_high_alarm              : BOOL;
    clearwell_level_low_alarm               : BOOL;
    (* --- Distribution Pumps --- *)
    distribution_pump_01_run_cmd            : BOOL;
    distribution_pump_01_run_fb             : BOOL;
    distribution_pump_01_fault              : BOOL;
    distribution_pump_02_run_cmd            : BOOL;
    distribution_pump_02_run_fb             : BOOL;
    distribution_pump_02_fault              : BOOL;

    (* --- Plant-wide REAL/DINT --- *)
    total_inflow                            : REAL;
    total_outflow                           : REAL;
    alarm_count                             : DINT;
    (* --- Source Reservoir --- *)
    source_reservoir_level                  : REAL;
    source_reservoir_inflow                 : REAL;
    source_reservoir_outflow                : REAL;
    (* --- Intake Screen --- *)
    intake_screen_diff_pressure             : REAL;
    (* --- Intake Pump 01 --- *)
    intake_pump_01_discharge_pressure       : REAL;
    intake_pump_01_run_hours                : DINT;
    intake_pump_01_start_count              : DINT;
    (* --- Intake Pump 02 --- *)
    intake_pump_02_discharge_pressure       : REAL;
    intake_pump_02_run_hours                : DINT;
    intake_pump_02_start_count              : DINT;
    (* --- Flash Mixer --- *)
    flash_mixer_level                       : REAL;
    flash_mixer_inflow                      : REAL;
    flash_mixer_outflow                     : REAL;
    (* --- Coagulant Dosing --- *)
    coagulant_dosing_dose_sp                : REAL;
    coagulant_dosing_dose_rate              : REAL;
    coagulant_dosing_stock_level            : REAL;
    (* --- Floc Basin 01 --- *)
    floc_basin_01_level                     : REAL;
    floc_basin_01_inflow                    : REAL;
    floc_basin_01_outflow                   : REAL;
    (* --- Floc Basin 02 --- *)
    floc_basin_02_level                     : REAL;
    floc_basin_02_inflow                    : REAL;
    floc_basin_02_outflow                   : REAL;
    (* --- Clarifier 01 --- *)
    clarifier_01_level                      : REAL;
    clarifier_01_inflow                     : REAL;
    clarifier_01_outflow                    : REAL;
    (* --- Clarifier 02 --- *)
    clarifier_02_level                      : REAL;
    clarifier_02_inflow                     : REAL;
    clarifier_02_outflow                    : REAL;
    (* --- Filter 01 --- *)
    filter_01_level                         : REAL;
    filter_01_inflow                        : REAL;
    filter_01_outflow                       : REAL;
    filter_01_diff_pressure                 : REAL;
    filter_01_loading_hours                 : DINT;
    filter_01_backwash_count                : DINT;
    (* --- Filter 02 --- *)
    filter_02_level                         : REAL;
    filter_02_inflow                        : REAL;
    filter_02_outflow                       : REAL;
    filter_02_diff_pressure                 : REAL;
    filter_02_loading_hours                 : DINT;
    filter_02_backwash_count                : DINT;
    (* --- Filter 03 --- *)
    filter_03_level                         : REAL;
    filter_03_inflow                        : REAL;
    filter_03_outflow                       : REAL;
    filter_03_diff_pressure                 : REAL;
    filter_03_loading_hours                 : DINT;
    filter_03_backwash_count                : DINT;
    (* --- Filter 04 --- *)
    filter_04_level                         : REAL;
    filter_04_inflow                        : REAL;
    filter_04_outflow                       : REAL;
    filter_04_diff_pressure                 : REAL;
    filter_04_loading_hours                 : DINT;
    filter_04_backwash_count                : DINT;
    (* --- Chlorine Dosing --- *)
    chlorine_dosing_dose_sp                 : REAL;
    chlorine_dosing_dose_rate               : REAL;
    chlorine_dosing_stock_level             : REAL;
    (* --- Contact Tank --- *)
    contact_tank_level                      : REAL;
    contact_tank_inflow                     : REAL;
    contact_tank_outflow                    : REAL;
    contact_tank_cl_residual                : REAL;
    (* --- Clearwell --- *)
    clearwell_level                         : REAL;
    clearwell_inflow                        : REAL;
    clearwell_outflow                       : REAL;
    (* --- Distribution Pump 01 --- *)
    distribution_pump_01_discharge_pressure : REAL;
    distribution_pump_01_run_hours          : DINT;
    distribution_pump_01_start_count        : DINT;
    (* --- Distribution Pump 02 --- *)
    distribution_pump_02_discharge_pressure : REAL;
    distribution_pump_02_run_hours          : DINT;
    distribution_pump_02_start_count        : DINT;
END_VAR

VAR
    heartbeat_ticks               : INT := 0;

    (* edge-detect latches for start-count rising edges *)
    intake_pump_01_fb_last        : BOOL := FALSE;
    intake_pump_02_fb_last        : BOOL := FALSE;
    distribution_pump_01_fb_last  : BOOL := FALSE;
    distribution_pump_02_fb_last  : BOOL := FALSE;

    (* edge-detect latches for backwash triggers *)
    filter_01_backwash_cmd_last   : BOOL := FALSE;
    filter_02_backwash_cmd_last   : BOOL := FALSE;
    filter_03_backwash_cmd_last   : BOOL := FALSE;
    filter_04_backwash_cmd_last   : BOOL := FALSE;

    (* backwash countdown timers, in 20 ms ticks (1500 = 30 s) *)
    filter_01_backwash_timer      : INT := 0;
    filter_02_backwash_timer      : INT := 0;
    filter_03_backwash_timer      : INT := 0;
    filter_04_backwash_timer      : INT := 0;

    (* screen cmd edge for diff_pressure reset *)
    intake_screen_cmd_last        : BOOL := FALSE;

    (* alarm rising-edge latch *)
    master_alarm_last             : BOOL := FALSE;

    (* aggregation scratch *)
    any_alarm                     : BOOL := FALSE;
    flash_mixer_agit_ok           : BOOL := FALSE;
    floc_basin_01_agit_ok         : BOOL := FALSE;
    floc_basin_02_agit_ok         : BOOL := FALSE;
END_VAR

(* ===== HEARTBEAT (toggles every 25 ticks = ~500 ms) ===== *)
heartbeat_ticks := heartbeat_ticks + 1;
IF heartbeat_ticks >= 25 THEN
    heartbeat_ticks := 0;
    heartbeat := NOT heartbeat;
END_IF;

(* ===== PERMISSIVES ===== *)
IF emergency_stop THEN
    plant_running := FALSE;
END_IF;

(* ============================================================ *)
(* ===== STAGE 1: INTAKE                                   ===== *)
(* ============================================================ *)

(* --- Intake Screen --- *)
intake_screen_status := intake_screen_cmd AND NOT intake_screen_fault;
IF intake_screen_cmd_last AND NOT intake_screen_cmd THEN
    (* falling edge = screen cycled = cleaning *)
    intake_screen_diff_pressure := 5.0;
END_IF;
intake_screen_cmd_last := intake_screen_cmd;

(* --- Intake Pump 01 --- *)
intake_pump_01_run_fb := intake_pump_01_run_cmd AND plant_running AND NOT intake_pump_01_fault;
IF intake_pump_01_run_fb THEN
    IF intake_pump_01_discharge_pressure < 350.0 THEN
        intake_pump_01_discharge_pressure := intake_pump_01_discharge_pressure + 25.0;
        IF intake_pump_01_discharge_pressure > 350.0 THEN
            intake_pump_01_discharge_pressure := 350.0;
        END_IF;
    END_IF;
    intake_pump_01_run_hours := intake_pump_01_run_hours + 1;
ELSE
    IF intake_pump_01_discharge_pressure > 0.0 THEN
        intake_pump_01_discharge_pressure := intake_pump_01_discharge_pressure - 25.0;
        IF intake_pump_01_discharge_pressure < 0.0 THEN
            intake_pump_01_discharge_pressure := 0.0;
        END_IF;
    END_IF;
END_IF;
IF intake_pump_01_run_fb AND NOT intake_pump_01_fb_last THEN
    intake_pump_01_start_count := intake_pump_01_start_count + 1;
END_IF;
intake_pump_01_fb_last := intake_pump_01_run_fb;

(* --- Intake Pump 02 --- *)
intake_pump_02_run_fb := intake_pump_02_run_cmd AND plant_running AND NOT intake_pump_02_fault;
IF intake_pump_02_run_fb THEN
    IF intake_pump_02_discharge_pressure < 350.0 THEN
        intake_pump_02_discharge_pressure := intake_pump_02_discharge_pressure + 25.0;
        IF intake_pump_02_discharge_pressure > 350.0 THEN
            intake_pump_02_discharge_pressure := 350.0;
        END_IF;
    END_IF;
    intake_pump_02_run_hours := intake_pump_02_run_hours + 1;
ELSE
    IF intake_pump_02_discharge_pressure > 0.0 THEN
        intake_pump_02_discharge_pressure := intake_pump_02_discharge_pressure - 25.0;
        IF intake_pump_02_discharge_pressure < 0.0 THEN
            intake_pump_02_discharge_pressure := 0.0;
        END_IF;
    END_IF;
END_IF;
IF intake_pump_02_run_fb AND NOT intake_pump_02_fb_last THEN
    intake_pump_02_start_count := intake_pump_02_start_count + 1;
END_IF;
intake_pump_02_fb_last := intake_pump_02_run_fb;

(* --- Source Reservoir (river inflow + pump draw) --- *)
source_reservoir_inflow := 0.05;
(* Overflow weir: once the reservoir reaches 95 %, river inflow spills
   to waste so the level can't climb further. Without this, an intake
   stop during an incident would pin the reservoir at 100 %, and with
   one lead pump the draw exactly matches the river inflow -- the
   high-level alarm would latch and never clear. *)
IF source_reservoir_level > 95.0 THEN
    source_reservoir_inflow := 0.0;
END_IF;
source_reservoir_outflow := 0.0;
IF intake_screen_status THEN
    IF intake_pump_01_run_fb THEN
        source_reservoir_outflow := source_reservoir_outflow + 0.05;
    END_IF;
    IF intake_pump_02_run_fb THEN
        source_reservoir_outflow := source_reservoir_outflow + 0.05;
    END_IF;
END_IF;
source_reservoir_level := source_reservoir_level + source_reservoir_inflow - source_reservoir_outflow;
IF source_reservoir_level > 100.0 THEN source_reservoir_level := 100.0; END_IF;
IF source_reservoir_level < 0.0 THEN source_reservoir_level := 0.0; END_IF;
(* Threshold raised to 97 so brief intake pauses during incidents
   (when the river inflow keeps filling the reservoir unattended) don't
   instantly latch the plant into high-level alarm. *)
source_reservoir_level_high_alarm := source_reservoir_level > 97.0;
source_reservoir_level_low_alarm  := source_reservoir_level < 15.0;

(* --- Intake screen diff_pressure rises with intake flow --- *)
IF intake_screen_status AND source_reservoir_outflow > 0.0 THEN
    intake_screen_diff_pressure := intake_screen_diff_pressure + 0.05;
    IF intake_screen_diff_pressure > 60.0 THEN
        intake_screen_diff_pressure := 60.0;
    END_IF;
END_IF;

(* ============================================================ *)
(* ===== STAGE 2: COAGULATION + FLOCCULATION              ===== *)
(* ============================================================ *)

(* --- Coagulant Dosing --- *)
coagulant_dosing_pump_run_fb :=
    coagulant_dosing_pump_run_cmd AND
    plant_running AND
    NOT coagulant_dosing_pump_fault AND
    (coagulant_dosing_stock_level > 0.5);
IF coagulant_dosing_pump_run_fb THEN
    coagulant_dosing_dose_rate := coagulant_dosing_dose_sp;
    (* 5x slower than the original 0.002: 13-minute natural refill cycle
       instead of 160 s, so low_stock_alarm doesn't keep master_alarm
       perma-latched in the background. *)
    coagulant_dosing_stock_level :=
        coagulant_dosing_stock_level - coagulant_dosing_dose_sp * 0.0004;
    IF coagulant_dosing_stock_level < 0.0 THEN
        coagulant_dosing_stock_level := 0.0;
    END_IF;
ELSE
    coagulant_dosing_dose_rate := 0.0;
END_IF;
coagulant_dosing_low_stock_alarm := coagulant_dosing_stock_level < 20.0;

(* --- Flash Mixer --- *)
flash_mixer_agit_ok := flash_mixer_agitator_cmd AND NOT flash_mixer_agitator_fault;
flash_mixer_inflow := 0.0;
IF intake_pump_01_run_fb THEN flash_mixer_inflow := flash_mixer_inflow + 0.05; END_IF;
IF intake_pump_02_run_fb THEN flash_mixer_inflow := flash_mixer_inflow + 0.05; END_IF;
IF flash_mixer_level > 12.0 AND flash_mixer_agit_ok THEN
    flash_mixer_outflow := 0.05;
ELSE
    flash_mixer_outflow := 0.0;
END_IF;
flash_mixer_level := flash_mixer_level + flash_mixer_inflow - flash_mixer_outflow;
IF flash_mixer_level > 100.0 THEN flash_mixer_level := 100.0; END_IF;
IF flash_mixer_level < 0.0 THEN flash_mixer_level := 0.0; END_IF;
flash_mixer_level_high_alarm := flash_mixer_level > 90.0;
flash_mixer_level_low_alarm  := flash_mixer_level < 10.0;

(* --- Floc Basin 01 (takes half of flash mixer outflow) --- *)
floc_basin_01_agit_ok := floc_basin_01_agitator_cmd AND NOT floc_basin_01_agitator_fault;
floc_basin_01_inflow := flash_mixer_outflow * 0.5;
(* Auto-isolation: cut this basin's feed once it's above the high-level
   alarm threshold. Without this, an agitator fault halts outflow while
   inflow keeps arriving -> basin pins at 100 %, and after the fault
   clears inflow/outflow rebalance at 100 % so the alarm never clears.
   With the cut, level drops past the alarm threshold before inflow
   resumes. *)
IF floc_basin_01_level > 90.0 THEN
    floc_basin_01_inflow := 0.0;
END_IF;
IF floc_basin_01_level > 12.0 AND floc_basin_01_agit_ok THEN
    floc_basin_01_outflow := 0.025;
ELSE
    floc_basin_01_outflow := 0.0;
END_IF;
floc_basin_01_level := floc_basin_01_level + floc_basin_01_inflow - floc_basin_01_outflow;
IF floc_basin_01_level > 100.0 THEN floc_basin_01_level := 100.0; END_IF;
IF floc_basin_01_level < 0.0 THEN floc_basin_01_level := 0.0; END_IF;
floc_basin_01_level_high_alarm := floc_basin_01_level > 90.0;
floc_basin_01_level_low_alarm  := floc_basin_01_level < 10.0;

(* --- Floc Basin 02 (takes the other half) --- *)
floc_basin_02_agit_ok := floc_basin_02_agitator_cmd AND NOT floc_basin_02_agitator_fault;
floc_basin_02_inflow := flash_mixer_outflow * 0.5;
(* Auto-isolation: see Floc Basin 01 note. *)
IF floc_basin_02_level > 90.0 THEN
    floc_basin_02_inflow := 0.0;
END_IF;
IF floc_basin_02_level > 12.0 AND floc_basin_02_agit_ok THEN
    floc_basin_02_outflow := 0.025;
ELSE
    floc_basin_02_outflow := 0.0;
END_IF;
floc_basin_02_level := floc_basin_02_level + floc_basin_02_inflow - floc_basin_02_outflow;
IF floc_basin_02_level > 100.0 THEN floc_basin_02_level := 100.0; END_IF;
IF floc_basin_02_level < 0.0 THEN floc_basin_02_level := 0.0; END_IF;
floc_basin_02_level_high_alarm := floc_basin_02_level > 90.0;
floc_basin_02_level_low_alarm  := floc_basin_02_level < 10.0;

(* ============================================================ *)
(* ===== STAGE 3: SEDIMENTATION                           ===== *)
(* ============================================================ *)

(* --- Clarifier 01 (from Floc Basin 01) --- *)
clarifier_01_inflow := floc_basin_01_outflow;
IF clarifier_01_level > 12.0 THEN
    clarifier_01_outflow := 0.025;
ELSE
    clarifier_01_outflow := 0.0;
END_IF;
clarifier_01_level := clarifier_01_level + clarifier_01_inflow - clarifier_01_outflow;
IF clarifier_01_level > 100.0 THEN clarifier_01_level := 100.0; END_IF;
IF clarifier_01_level < 0.0 THEN clarifier_01_level := 0.0; END_IF;
clarifier_01_level_high_alarm := clarifier_01_level > 90.0;
clarifier_01_level_low_alarm  := clarifier_01_level < 10.0;

(* --- Clarifier 02 (from Floc Basin 02) --- *)
clarifier_02_inflow := floc_basin_02_outflow;
IF clarifier_02_level > 12.0 THEN
    clarifier_02_outflow := 0.025;
ELSE
    clarifier_02_outflow := 0.0;
END_IF;
clarifier_02_level := clarifier_02_level + clarifier_02_inflow - clarifier_02_outflow;
IF clarifier_02_level > 100.0 THEN clarifier_02_level := 100.0; END_IF;
IF clarifier_02_level < 0.0 THEN clarifier_02_level := 0.0; END_IF;
clarifier_02_level_high_alarm := clarifier_02_level > 90.0;
clarifier_02_level_low_alarm  := clarifier_02_level < 10.0;

(* ============================================================ *)
(* ===== STAGE 4: FILTRATION                              ===== *)
(* ============================================================ *)

(* --- Filter 01 (from Clarifier 01, half share) --- *)
IF filter_01_backwash_cmd AND NOT filter_01_backwash_cmd_last THEN
    filter_01_backwash_timer  := 1500;
    filter_01_backwash_active := TRUE;
    filter_01_backwash_count  := filter_01_backwash_count + 1;
END_IF;
filter_01_backwash_cmd_last := filter_01_backwash_cmd;
IF filter_01_backwash_timer > 0 THEN
    filter_01_backwash_timer := filter_01_backwash_timer - 1;
    IF filter_01_backwash_timer = 0 THEN
        filter_01_backwash_active := FALSE;
        filter_01_loading_hours  := 0;
        filter_01_diff_pressure  := 5.0;
    END_IF;
END_IF;
IF filter_01_backwash_active THEN
    filter_01_inflow  := 0.0;
    filter_01_outflow := 0.0;
ELSE
    filter_01_inflow := clarifier_01_outflow * 0.5;
    IF filter_01_level > 12.0 THEN
        filter_01_outflow := 0.0125;
    ELSE
        filter_01_outflow := 0.0;
    END_IF;
END_IF;
filter_01_level := filter_01_level + filter_01_inflow - filter_01_outflow;
IF filter_01_level > 100.0 THEN filter_01_level := 100.0; END_IF;
IF filter_01_level < 0.0 THEN filter_01_level := 0.0; END_IF;
IF filter_01_outflow > 0.0 THEN
    filter_01_diff_pressure := filter_01_diff_pressure + 0.005;
    IF filter_01_diff_pressure > 90.0 THEN
        filter_01_diff_pressure := 90.0;
    END_IF;
    filter_01_loading_hours := filter_01_loading_hours + 1;
END_IF;
filter_01_level_high_alarm := filter_01_level > 90.0;
filter_01_level_low_alarm  := filter_01_level < 10.0;

(* --- Filter 02 (from Clarifier 01, other half) --- *)
IF filter_02_backwash_cmd AND NOT filter_02_backwash_cmd_last THEN
    filter_02_backwash_timer  := 1500;
    filter_02_backwash_active := TRUE;
    filter_02_backwash_count  := filter_02_backwash_count + 1;
END_IF;
filter_02_backwash_cmd_last := filter_02_backwash_cmd;
IF filter_02_backwash_timer > 0 THEN
    filter_02_backwash_timer := filter_02_backwash_timer - 1;
    IF filter_02_backwash_timer = 0 THEN
        filter_02_backwash_active := FALSE;
        filter_02_loading_hours  := 0;
        filter_02_diff_pressure  := 5.0;
    END_IF;
END_IF;
IF filter_02_backwash_active THEN
    filter_02_inflow  := 0.0;
    filter_02_outflow := 0.0;
ELSE
    filter_02_inflow := clarifier_01_outflow * 0.5;
    IF filter_02_level > 12.0 THEN
        filter_02_outflow := 0.0125;
    ELSE
        filter_02_outflow := 0.0;
    END_IF;
END_IF;
filter_02_level := filter_02_level + filter_02_inflow - filter_02_outflow;
IF filter_02_level > 100.0 THEN filter_02_level := 100.0; END_IF;
IF filter_02_level < 0.0 THEN filter_02_level := 0.0; END_IF;
IF filter_02_outflow > 0.0 THEN
    filter_02_diff_pressure := filter_02_diff_pressure + 0.005;
    IF filter_02_diff_pressure > 90.0 THEN
        filter_02_diff_pressure := 90.0;
    END_IF;
    filter_02_loading_hours := filter_02_loading_hours + 1;
END_IF;
filter_02_level_high_alarm := filter_02_level > 90.0;
filter_02_level_low_alarm  := filter_02_level < 10.0;

(* --- Filter 03 (from Clarifier 02, half share) --- *)
IF filter_03_backwash_cmd AND NOT filter_03_backwash_cmd_last THEN
    filter_03_backwash_timer  := 1500;
    filter_03_backwash_active := TRUE;
    filter_03_backwash_count  := filter_03_backwash_count + 1;
END_IF;
filter_03_backwash_cmd_last := filter_03_backwash_cmd;
IF filter_03_backwash_timer > 0 THEN
    filter_03_backwash_timer := filter_03_backwash_timer - 1;
    IF filter_03_backwash_timer = 0 THEN
        filter_03_backwash_active := FALSE;
        filter_03_loading_hours  := 0;
        filter_03_diff_pressure  := 5.0;
    END_IF;
END_IF;
IF filter_03_backwash_active THEN
    filter_03_inflow  := 0.0;
    filter_03_outflow := 0.0;
ELSE
    filter_03_inflow := clarifier_02_outflow * 0.5;
    IF filter_03_level > 12.0 THEN
        filter_03_outflow := 0.0125;
    ELSE
        filter_03_outflow := 0.0;
    END_IF;
END_IF;
filter_03_level := filter_03_level + filter_03_inflow - filter_03_outflow;
IF filter_03_level > 100.0 THEN filter_03_level := 100.0; END_IF;
IF filter_03_level < 0.0 THEN filter_03_level := 0.0; END_IF;
IF filter_03_outflow > 0.0 THEN
    filter_03_diff_pressure := filter_03_diff_pressure + 0.005;
    IF filter_03_diff_pressure > 90.0 THEN
        filter_03_diff_pressure := 90.0;
    END_IF;
    filter_03_loading_hours := filter_03_loading_hours + 1;
END_IF;
filter_03_level_high_alarm := filter_03_level > 90.0;
filter_03_level_low_alarm  := filter_03_level < 10.0;

(* --- Filter 04 (from Clarifier 02, other half) --- *)
IF filter_04_backwash_cmd AND NOT filter_04_backwash_cmd_last THEN
    filter_04_backwash_timer  := 1500;
    filter_04_backwash_active := TRUE;
    filter_04_backwash_count  := filter_04_backwash_count + 1;
END_IF;
filter_04_backwash_cmd_last := filter_04_backwash_cmd;
IF filter_04_backwash_timer > 0 THEN
    filter_04_backwash_timer := filter_04_backwash_timer - 1;
    IF filter_04_backwash_timer = 0 THEN
        filter_04_backwash_active := FALSE;
        filter_04_loading_hours  := 0;
        filter_04_diff_pressure  := 5.0;
    END_IF;
END_IF;
IF filter_04_backwash_active THEN
    filter_04_inflow  := 0.0;
    filter_04_outflow := 0.0;
ELSE
    filter_04_inflow := clarifier_02_outflow * 0.5;
    IF filter_04_level > 12.0 THEN
        filter_04_outflow := 0.0125;
    ELSE
        filter_04_outflow := 0.0;
    END_IF;
END_IF;
filter_04_level := filter_04_level + filter_04_inflow - filter_04_outflow;
IF filter_04_level > 100.0 THEN filter_04_level := 100.0; END_IF;
IF filter_04_level < 0.0 THEN filter_04_level := 0.0; END_IF;
IF filter_04_outflow > 0.0 THEN
    filter_04_diff_pressure := filter_04_diff_pressure + 0.005;
    IF filter_04_diff_pressure > 90.0 THEN
        filter_04_diff_pressure := 90.0;
    END_IF;
    filter_04_loading_hours := filter_04_loading_hours + 1;
END_IF;
filter_04_level_high_alarm := filter_04_level > 90.0;
filter_04_level_low_alarm  := filter_04_level < 10.0;

(* ============================================================ *)
(* ===== STAGE 5: DISINFECTION                            ===== *)
(* ============================================================ *)

(* --- Chlorine Dosing --- *)
chlorine_dosing_pump_run_fb :=
    chlorine_dosing_pump_run_cmd AND
    plant_running AND
    NOT chlorine_dosing_pump_fault AND
    (chlorine_dosing_stock_level > 0.5);
IF chlorine_dosing_pump_run_fb THEN
    chlorine_dosing_dose_rate := chlorine_dosing_dose_sp;
    chlorine_dosing_stock_level :=
        chlorine_dosing_stock_level - chlorine_dosing_dose_sp * 0.0005;
    IF chlorine_dosing_stock_level < 0.0 THEN
        chlorine_dosing_stock_level := 0.0;
    END_IF;
ELSE
    chlorine_dosing_dose_rate := 0.0;
END_IF;
chlorine_dosing_low_stock_alarm := chlorine_dosing_stock_level < 20.0;

(* --- Contact Tank --- *)
contact_tank_inflow :=
    filter_01_outflow + filter_02_outflow + filter_03_outflow + filter_04_outflow;
IF contact_tank_level > 12.0 THEN
    contact_tank_outflow := 0.05;
ELSE
    contact_tank_outflow := 0.0;
END_IF;
contact_tank_level := contact_tank_level + contact_tank_inflow - contact_tank_outflow;
IF contact_tank_level > 100.0 THEN contact_tank_level := 100.0; END_IF;
IF contact_tank_level < 0.0 THEN contact_tank_level := 0.0; END_IF;
contact_tank_level_high_alarm := contact_tank_level > 90.0;
contact_tank_level_low_alarm  := contact_tank_level < 10.0;

(* --- Chlorine residual tracks dose rate, decays without dosing --- *)
IF chlorine_dosing_pump_run_fb AND contact_tank_inflow > 0.0 THEN
    IF contact_tank_cl_residual < chlorine_dosing_dose_rate THEN
        contact_tank_cl_residual := contact_tank_cl_residual + 0.01;
        IF contact_tank_cl_residual > chlorine_dosing_dose_rate THEN
            contact_tank_cl_residual := chlorine_dosing_dose_rate;
        END_IF;
    ELSIF contact_tank_cl_residual > chlorine_dosing_dose_rate THEN
        contact_tank_cl_residual := contact_tank_cl_residual - 0.01;
        IF contact_tank_cl_residual < chlorine_dosing_dose_rate THEN
            contact_tank_cl_residual := chlorine_dosing_dose_rate;
        END_IF;
    END_IF;
ELSE
    IF contact_tank_cl_residual > 0.0 THEN
        contact_tank_cl_residual := contact_tank_cl_residual - 0.002;
        IF contact_tank_cl_residual < 0.0 THEN
            contact_tank_cl_residual := 0.0;
        END_IF;
    END_IF;
END_IF;
contact_tank_cl_low_alarm := contact_tank_cl_residual < 0.5;

(* ============================================================ *)
(* ===== STAGE 6: DISTRIBUTION                            ===== *)
(* ============================================================ *)

(* --- Distribution Pump 01 --- *)
distribution_pump_01_run_fb :=
    distribution_pump_01_run_cmd AND plant_running AND NOT distribution_pump_01_fault;
IF distribution_pump_01_run_fb THEN
    IF distribution_pump_01_discharge_pressure < 500.0 THEN
        distribution_pump_01_discharge_pressure :=
            distribution_pump_01_discharge_pressure + 25.0;
        IF distribution_pump_01_discharge_pressure > 500.0 THEN
            distribution_pump_01_discharge_pressure := 500.0;
        END_IF;
    END_IF;
    distribution_pump_01_run_hours := distribution_pump_01_run_hours + 1;
ELSE
    IF distribution_pump_01_discharge_pressure > 0.0 THEN
        distribution_pump_01_discharge_pressure :=
            distribution_pump_01_discharge_pressure - 25.0;
        IF distribution_pump_01_discharge_pressure < 0.0 THEN
            distribution_pump_01_discharge_pressure := 0.0;
        END_IF;
    END_IF;
END_IF;
IF distribution_pump_01_run_fb AND NOT distribution_pump_01_fb_last THEN
    distribution_pump_01_start_count := distribution_pump_01_start_count + 1;
END_IF;
distribution_pump_01_fb_last := distribution_pump_01_run_fb;

(* --- Distribution Pump 02 --- *)
distribution_pump_02_run_fb :=
    distribution_pump_02_run_cmd AND plant_running AND NOT distribution_pump_02_fault;
IF distribution_pump_02_run_fb THEN
    IF distribution_pump_02_discharge_pressure < 500.0 THEN
        distribution_pump_02_discharge_pressure :=
            distribution_pump_02_discharge_pressure + 25.0;
        IF distribution_pump_02_discharge_pressure > 500.0 THEN
            distribution_pump_02_discharge_pressure := 500.0;
        END_IF;
    END_IF;
    distribution_pump_02_run_hours := distribution_pump_02_run_hours + 1;
ELSE
    IF distribution_pump_02_discharge_pressure > 0.0 THEN
        distribution_pump_02_discharge_pressure :=
            distribution_pump_02_discharge_pressure - 25.0;
        IF distribution_pump_02_discharge_pressure < 0.0 THEN
            distribution_pump_02_discharge_pressure := 0.0;
        END_IF;
    END_IF;
END_IF;
IF distribution_pump_02_run_fb AND NOT distribution_pump_02_fb_last THEN
    distribution_pump_02_start_count := distribution_pump_02_start_count + 1;
END_IF;
distribution_pump_02_fb_last := distribution_pump_02_run_fb;

(* --- Clearwell --- *)
clearwell_inflow := contact_tank_outflow;
clearwell_outflow := 0.0;
IF clearwell_level > 12.0 THEN
    IF distribution_pump_01_run_fb THEN
        clearwell_outflow := clearwell_outflow + 0.05;
    END_IF;
    IF distribution_pump_02_run_fb THEN
        clearwell_outflow := clearwell_outflow + 0.05;
    END_IF;
END_IF;
clearwell_level := clearwell_level + clearwell_inflow - clearwell_outflow;
IF clearwell_level > 100.0 THEN clearwell_level := 100.0; END_IF;
IF clearwell_level < 0.0 THEN clearwell_level := 0.0; END_IF;
clearwell_level_high_alarm := clearwell_level > 90.0;
clearwell_level_low_alarm  := clearwell_level < 10.0;

(* ============================================================ *)
(* ===== STAGE 7: PLANT SUPERVISION                       ===== *)
(* ============================================================ *)

total_inflow := 0.0;
IF intake_pump_01_run_fb THEN total_inflow := total_inflow + 0.05; END_IF;
IF intake_pump_02_run_fb THEN total_inflow := total_inflow + 0.05; END_IF;

total_outflow := 0.0;
IF distribution_pump_01_run_fb THEN total_outflow := total_outflow + 0.05; END_IF;
IF distribution_pump_02_run_fb THEN total_outflow := total_outflow + 0.05; END_IF;

any_alarm :=
    source_reservoir_level_high_alarm OR source_reservoir_level_low_alarm OR
    flash_mixer_level_high_alarm OR flash_mixer_level_low_alarm OR
    coagulant_dosing_low_stock_alarm OR
    floc_basin_01_level_high_alarm OR floc_basin_01_level_low_alarm OR
    floc_basin_02_level_high_alarm OR floc_basin_02_level_low_alarm OR
    clarifier_01_level_high_alarm OR clarifier_01_level_low_alarm OR
    clarifier_02_level_high_alarm OR clarifier_02_level_low_alarm OR
    filter_01_level_high_alarm OR filter_01_level_low_alarm OR
    filter_02_level_high_alarm OR filter_02_level_low_alarm OR
    filter_03_level_high_alarm OR filter_03_level_low_alarm OR
    filter_04_level_high_alarm OR filter_04_level_low_alarm OR
    chlorine_dosing_low_stock_alarm OR
    contact_tank_level_high_alarm OR contact_tank_level_low_alarm OR
    contact_tank_cl_low_alarm OR
    clearwell_level_high_alarm OR clearwell_level_low_alarm;
master_alarm := any_alarm;
IF master_alarm AND NOT master_alarm_last THEN
    alarm_count := alarm_count + 1;
END_IF;
master_alarm_last := master_alarm;

END_PROGRAM


CONFIGURATION Config0

    VAR_GLOBAL
        (* ---- Supervision (BOOL) ---- *)
        heartbeat                               AT %QX0.0 : BOOL := FALSE;
        plant_running                           AT %QX0.1 : BOOL := FALSE;
        master_alarm                            AT %QX0.2 : BOOL := FALSE;
        emergency_stop                          AT %QX0.3 : BOOL := FALSE;
        (* ---- Source Reservoir (BOOL) ---- *)
        source_reservoir_level_high_alarm       AT %QX0.4 : BOOL := FALSE;
        source_reservoir_level_low_alarm        AT %QX0.5 : BOOL := FALSE;
        (* ---- Intake Screen (BOOL) ---- *)
        intake_screen_cmd                       AT %QX0.6 : BOOL := FALSE;
        intake_screen_status                    AT %QX0.7 : BOOL := FALSE;
        intake_screen_fault                     AT %QX1.0 : BOOL := FALSE;
        (* ---- Intake Pump 01 (BOOL) ---- *)
        intake_pump_01_run_cmd                  AT %QX1.1 : BOOL := FALSE;
        intake_pump_01_run_fb                   AT %QX1.2 : BOOL := FALSE;
        intake_pump_01_fault                    AT %QX1.3 : BOOL := FALSE;
        (* ---- Intake Pump 02 (BOOL) ---- *)
        intake_pump_02_run_cmd                  AT %QX1.4 : BOOL := FALSE;
        intake_pump_02_run_fb                   AT %QX1.5 : BOOL := FALSE;
        intake_pump_02_fault                    AT %QX1.6 : BOOL := FALSE;
        (* ---- Flash Mixer (BOOL) ---- *)
        flash_mixer_level_high_alarm            AT %QX1.7 : BOOL := FALSE;
        flash_mixer_level_low_alarm             AT %QX2.0 : BOOL := FALSE;
        flash_mixer_agitator_cmd                AT %QX2.1 : BOOL := FALSE;
        flash_mixer_agitator_fault              AT %QX2.2 : BOOL := FALSE;
        (* ---- Coagulant Dosing (BOOL) ---- *)
        coagulant_dosing_low_stock_alarm        AT %QX2.3 : BOOL := FALSE;
        coagulant_dosing_pump_run_cmd           AT %QX2.4 : BOOL := FALSE;
        coagulant_dosing_pump_run_fb            AT %QX2.5 : BOOL := FALSE;
        coagulant_dosing_pump_fault             AT %QX2.6 : BOOL := FALSE;
        (* ---- Floc Basin 01 (BOOL) ---- *)
        floc_basin_01_level_high_alarm          AT %QX2.7 : BOOL := FALSE;
        floc_basin_01_level_low_alarm           AT %QX3.0 : BOOL := FALSE;
        floc_basin_01_agitator_cmd              AT %QX3.1 : BOOL := FALSE;
        floc_basin_01_agitator_fault            AT %QX3.2 : BOOL := FALSE;
        (* ---- Floc Basin 02 (BOOL) ---- *)
        floc_basin_02_level_high_alarm          AT %QX3.3 : BOOL := FALSE;
        floc_basin_02_level_low_alarm           AT %QX3.4 : BOOL := FALSE;
        floc_basin_02_agitator_cmd              AT %QX3.5 : BOOL := FALSE;
        floc_basin_02_agitator_fault            AT %QX3.6 : BOOL := FALSE;
        (* ---- Clarifiers (BOOL) ---- *)
        clarifier_01_level_high_alarm           AT %QX3.7 : BOOL := FALSE;
        clarifier_01_level_low_alarm            AT %QX4.0 : BOOL := FALSE;
        clarifier_02_level_high_alarm           AT %QX4.1 : BOOL := FALSE;
        clarifier_02_level_low_alarm            AT %QX4.2 : BOOL := FALSE;
        (* ---- Filters (BOOL) ---- *)
        filter_01_level_high_alarm              AT %QX4.3 : BOOL := FALSE;
        filter_01_level_low_alarm               AT %QX4.4 : BOOL := FALSE;
        filter_01_backwash_cmd                  AT %QX4.5 : BOOL := FALSE;
        filter_01_backwash_active               AT %QX4.6 : BOOL := FALSE;
        filter_02_level_high_alarm              AT %QX4.7 : BOOL := FALSE;
        filter_02_level_low_alarm               AT %QX5.0 : BOOL := FALSE;
        filter_02_backwash_cmd                  AT %QX5.1 : BOOL := FALSE;
        filter_02_backwash_active               AT %QX5.2 : BOOL := FALSE;
        filter_03_level_high_alarm              AT %QX5.3 : BOOL := FALSE;
        filter_03_level_low_alarm               AT %QX5.4 : BOOL := FALSE;
        filter_03_backwash_cmd                  AT %QX5.5 : BOOL := FALSE;
        filter_03_backwash_active               AT %QX5.6 : BOOL := FALSE;
        filter_04_level_high_alarm              AT %QX5.7 : BOOL := FALSE;
        filter_04_level_low_alarm               AT %QX6.0 : BOOL := FALSE;
        filter_04_backwash_cmd                  AT %QX6.1 : BOOL := FALSE;
        filter_04_backwash_active               AT %QX6.2 : BOOL := FALSE;
        (* ---- Chlorine Dosing (BOOL) ---- *)
        chlorine_dosing_low_stock_alarm         AT %QX6.3 : BOOL := FALSE;
        chlorine_dosing_pump_run_cmd            AT %QX6.4 : BOOL := FALSE;
        chlorine_dosing_pump_run_fb             AT %QX6.5 : BOOL := FALSE;
        chlorine_dosing_pump_fault              AT %QX6.6 : BOOL := FALSE;
        (* ---- Contact Tank (BOOL) ---- *)
        contact_tank_level_high_alarm           AT %QX6.7 : BOOL := FALSE;
        contact_tank_level_low_alarm            AT %QX7.0 : BOOL := FALSE;
        contact_tank_cl_low_alarm               AT %QX7.1 : BOOL := TRUE;  (* starts before dosing active *)
        (* ---- Clearwell (BOOL) ---- *)
        clearwell_level_high_alarm              AT %QX7.2 : BOOL := FALSE;
        clearwell_level_low_alarm               AT %QX7.3 : BOOL := FALSE;
        (* ---- Distribution Pumps (BOOL) ---- *)
        distribution_pump_01_run_cmd            AT %QX7.4 : BOOL := FALSE;
        distribution_pump_01_run_fb             AT %QX7.5 : BOOL := FALSE;
        distribution_pump_01_fault              AT %QX7.6 : BOOL := FALSE;
        distribution_pump_02_run_cmd            AT %QX7.7 : BOOL := FALSE;
        distribution_pump_02_run_fb             AT %QX8.0 : BOOL := FALSE;
        distribution_pump_02_fault              AT %QX8.1 : BOOL := FALSE;

        (* ---- Plant-wide (REAL/DINT) ---- *)
        total_inflow                            AT %MD0   : REAL := 0.0;
        total_outflow                           AT %MD1   : REAL := 0.0;
        alarm_count                             AT %MD2   : DINT := 0;
        (* ---- Source Reservoir (REAL) ---- *)
        source_reservoir_level                  AT %MD3   : REAL := 75.0;
        source_reservoir_inflow                 AT %MD4   : REAL := 0.0;
        source_reservoir_outflow                AT %MD5   : REAL := 0.0;
        (* ---- Intake Screen (REAL) ---- *)
        intake_screen_diff_pressure             AT %MD6   : REAL := 5.0;
        (* ---- Intake Pump 01 (REAL/DINT) ---- *)
        intake_pump_01_discharge_pressure       AT %MD7   : REAL := 0.0;
        intake_pump_01_run_hours                AT %MD8   : DINT := 0;
        intake_pump_01_start_count              AT %MD9   : DINT := 0;
        (* ---- Intake Pump 02 (REAL/DINT) ---- *)
        intake_pump_02_discharge_pressure       AT %MD10  : REAL := 0.0;
        intake_pump_02_run_hours                AT %MD11  : DINT := 0;
        intake_pump_02_start_count              AT %MD12  : DINT := 0;
        (* ---- Flash Mixer (REAL) ---- *)
        flash_mixer_level                       AT %MD13  : REAL := 50.0;
        flash_mixer_inflow                      AT %MD14  : REAL := 0.0;
        flash_mixer_outflow                     AT %MD15  : REAL := 0.0;
        (* ---- Coagulant Dosing (REAL) ---- *)
        coagulant_dosing_dose_sp                AT %MD16  : REAL := 5.0;
        coagulant_dosing_dose_rate              AT %MD17  : REAL := 0.0;
        coagulant_dosing_stock_level            AT %MD18  : REAL := 100.0;
        (* ---- Floc Basin 01 (REAL) ---- *)
        floc_basin_01_level                     AT %MD19  : REAL := 50.0;
        floc_basin_01_inflow                    AT %MD20  : REAL := 0.0;
        floc_basin_01_outflow                   AT %MD21  : REAL := 0.0;
        (* ---- Floc Basin 02 (REAL) ---- *)
        floc_basin_02_level                     AT %MD22  : REAL := 50.0;
        floc_basin_02_inflow                    AT %MD23  : REAL := 0.0;
        floc_basin_02_outflow                   AT %MD24  : REAL := 0.0;
        (* ---- Clarifier 01 (REAL) ---- *)
        clarifier_01_level                      AT %MD25  : REAL := 50.0;
        clarifier_01_inflow                     AT %MD26  : REAL := 0.0;
        clarifier_01_outflow                    AT %MD27  : REAL := 0.0;
        (* ---- Clarifier 02 (REAL) ---- *)
        clarifier_02_level                      AT %MD28  : REAL := 50.0;
        clarifier_02_inflow                     AT %MD29  : REAL := 0.0;
        clarifier_02_outflow                    AT %MD30  : REAL := 0.0;
        (* ---- Filter 01 (REAL/DINT) ---- *)
        filter_01_level                         AT %MD31  : REAL := 40.0;
        filter_01_inflow                        AT %MD32  : REAL := 0.0;
        filter_01_outflow                       AT %MD33  : REAL := 0.0;
        filter_01_diff_pressure                 AT %MD34  : REAL := 5.0;
        filter_01_loading_hours                 AT %MD35  : DINT := 0;
        filter_01_backwash_count                AT %MD36  : DINT := 0;
        (* ---- Filter 02 (REAL/DINT) ---- *)
        filter_02_level                         AT %MD37  : REAL := 40.0;
        filter_02_inflow                        AT %MD38  : REAL := 0.0;
        filter_02_outflow                       AT %MD39  : REAL := 0.0;
        filter_02_diff_pressure                 AT %MD40  : REAL := 5.0;
        filter_02_loading_hours                 AT %MD41  : DINT := 0;
        filter_02_backwash_count                AT %MD42  : DINT := 0;
        (* ---- Filter 03 (REAL/DINT) ---- *)
        filter_03_level                         AT %MD43  : REAL := 40.0;
        filter_03_inflow                        AT %MD44  : REAL := 0.0;
        filter_03_outflow                       AT %MD45  : REAL := 0.0;
        filter_03_diff_pressure                 AT %MD46  : REAL := 5.0;
        filter_03_loading_hours                 AT %MD47  : DINT := 0;
        filter_03_backwash_count                AT %MD48  : DINT := 0;
        (* ---- Filter 04 (REAL/DINT) ---- *)
        filter_04_level                         AT %MD49  : REAL := 40.0;
        filter_04_inflow                        AT %MD50  : REAL := 0.0;
        filter_04_outflow                       AT %MD51  : REAL := 0.0;
        filter_04_diff_pressure                 AT %MD52  : REAL := 5.0;
        filter_04_loading_hours                 AT %MD53  : DINT := 0;
        filter_04_backwash_count                AT %MD54  : DINT := 0;
        (* ---- Chlorine Dosing (REAL) ---- *)
        chlorine_dosing_dose_sp                 AT %MD55  : REAL := 1.2;
        chlorine_dosing_dose_rate               AT %MD56  : REAL := 0.0;
        chlorine_dosing_stock_level             AT %MD57  : REAL := 100.0;
        (* ---- Contact Tank (REAL) ---- *)
        contact_tank_level                      AT %MD58  : REAL := 60.0;
        contact_tank_inflow                     AT %MD59  : REAL := 0.0;
        contact_tank_outflow                    AT %MD60  : REAL := 0.0;
        contact_tank_cl_residual                AT %MD61  : REAL := 0.0;
        (* ---- Clearwell (REAL) ---- *)
        clearwell_level                         AT %MD62  : REAL := 55.0;
        clearwell_inflow                        AT %MD63  : REAL := 0.0;
        clearwell_outflow                       AT %MD64  : REAL := 0.0;
        (* ---- Distribution Pump 01 (REAL/DINT) ---- *)
        distribution_pump_01_discharge_pressure AT %MD65  : REAL := 0.0;
        distribution_pump_01_run_hours          AT %MD66  : DINT := 0;
        distribution_pump_01_start_count        AT %MD67  : DINT := 0;
        (* ---- Distribution Pump 02 (REAL/DINT) ---- *)
        distribution_pump_02_discharge_pressure AT %MD68  : REAL := 0.0;
        distribution_pump_02_run_hours          AT %MD69  : DINT := 0;
        distribution_pump_02_start_count        AT %MD70  : DINT := 0;
    END_VAR

    RESOURCE Res0 ON PLC
        TASK MainTask(INTERVAL := T#20ms, PRIORITY := 0);
        PROGRAM MainInst WITH MainTask : Main;
    END_RESOURCE

END_CONFIGURATION
