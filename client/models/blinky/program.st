(*
  Blinky — toggles %QX0.0 roughly every 500 ms.
  Smoke-test for the headless client. Counts scan cycles of the 20 ms
  MainTask and flips the coil every 25 ticks.
*)

PROGRAM Main
VAR_EXTERNAL
    led : BOOL;
END_VAR
VAR
    counter : INT := 0;
END_VAR

counter := counter + 1;
IF counter >= 25 THEN
    counter := 0;
    led := NOT led;
END_IF;

END_PROGRAM

CONFIGURATION Config0

    VAR_GLOBAL
        led AT %QX0.0 : BOOL := FALSE;
    END_VAR

    RESOURCE Res0 ON PLC
        TASK MainTask(INTERVAL := T#20ms, PRIORITY := 0);
        PROGRAM MainInst WITH MainTask : Main;
    END_RESOURCE

END_CONFIGURATION
