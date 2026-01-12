(*
 * OPC-UA Data Type Test Program
 *
 * This program declares variables of all supported data types
 * for testing OPC-UA plugin bidirectional synchronization.
 *
 * Data Types Tested:
 *   - Simple: BOOL, BYTE, INT, DINT, LINT, REAL, STRING
 *   - Structures: Custom TYPE with mixed fields
 *   - Arrays: Fixed-size arrays of various types
 *)

(* ============================================ *)
(* TYPE DEFINITIONS - Structures                *)
(* ============================================ *)

TYPE
    (* Simple structure with basic types *)
    SensorData : STRUCT
        sensor_id : INT;
        value : REAL;
        is_valid : BOOL;
    END_STRUCT;

    (* Structure for coordinate/position data *)
    Position3D : STRUCT
        x : REAL;
        y : REAL;
        z : REAL;
    END_STRUCT;

    (* Complex structure with multiple field types *)
    DeviceStatus : STRUCT
        device_name : STRING;
        error_code : DINT;
        temperature : REAL;
        is_online : BOOL;
        uptime_seconds : LINT;
    END_STRUCT;
END_TYPE

(* ============================================ *)
(* PROGRAM - Main test program                  *)
(* ============================================ *)

PROGRAM OpcuaDataTypeTest
VAR
    (* ---------------------------------------- *)
    (* SIMPLE VARIABLES - All basic data types  *)
    (* ---------------------------------------- *)

    (* Boolean - 1 bit logical value *)
    simple_bool : BOOL := FALSE;
    simple_bool_true : BOOL := TRUE;

    (* Byte - 8-bit unsigned integer (0 to 255) *)
    simple_byte : BYTE := 0;
    simple_byte_max : BYTE := 255;

    (* Int - 16-bit signed integer (-32768 to 32767) *)
    simple_int : INT := 0;
    simple_int_negative : INT := -100;
    simple_int_max : INT := 32767;
    simple_int_min : INT := -32768;

    (* DInt - 32-bit signed integer *)
    simple_dint : DINT := 0;
    simple_dint_large : DINT := 100000;
    simple_dint_negative : DINT := -100000;

    (* LInt - 64-bit signed integer *)
    simple_lint : LINT := 0;
    simple_lint_large : LINT := 1000000000;

    (* Real/Float - 32-bit floating point *)
    simple_real : REAL := 0.0;
    simple_real_pi : REAL := 3.14159;
    simple_real_negative : REAL := -273.15;

    (* String - Variable length text *)
    simple_string : STRING := '';
    simple_string_hello : STRING := 'Hello OPC-UA';

    (* ---------------------------------------- *)
    (* STRUCTURE INSTANCES                       *)
    (* ---------------------------------------- *)

    (* Sensor data structure instance *)
    sensor1 : SensorData := (sensor_id := 1, value := 0.0, is_valid := FALSE);
    sensor2 : SensorData := (sensor_id := 2, value := 25.5, is_valid := TRUE);

    (* Position structure instance *)
    robot_position : Position3D := (x := 0.0, y := 0.0, z := 0.0);
    target_position : Position3D := (x := 100.0, y := 50.0, z := 25.0);

    (* Device status structure instance *)
    plc_status : DeviceStatus := (
        device_name := 'OpenPLC',
        error_code := 0,
        temperature := 45.5,
        is_online := TRUE,
        uptime_seconds := 0
    );

    (* ---------------------------------------- *)
    (* ARRAY VARIABLES                           *)
    (* ---------------------------------------- *)

    (* Array of booleans - digital inputs simulation *)
    bool_array : ARRAY[0..7] OF BOOL := [FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE];

    (* Array of integers - analog values *)
    int_array : ARRAY[0..4] OF INT := [0, 0, 0, 0, 0];

    (* Array of reals - sensor readings *)
    real_array : ARRAY[0..3] OF REAL := [0.0, 0.0, 0.0, 0.0];

    (* Array of DInts - counters *)
    dint_array : ARRAY[0..2] OF DINT := [0, 0, 0];

    (* ---------------------------------------- *)
    (* WORKING VARIABLES (for logic)             *)
    (* ---------------------------------------- *)

    (* Counter for uptime tracking *)
    cycle_counter : DINT := 0;

    (* Toggle for testing write operations *)
    toggle_output : BOOL := FALSE;

END_VAR

(* ============================================ *)
(* PROGRAM LOGIC                                *)
(* ============================================ *)

(* Increment cycle counter each scan *)
cycle_counter := cycle_counter + 1;

(* Update PLC uptime in status structure (assuming 100ms cycle) *)
IF cycle_counter >= 10 THEN
    plc_status.uptime_seconds := plc_status.uptime_seconds + 1;
    cycle_counter := 0;
END_IF;

(* Toggle output every second for visual feedback *)
IF plc_status.uptime_seconds MOD 2 = 0 THEN
    toggle_output := TRUE;
ELSE
    toggle_output := FALSE;
END_IF;

(* Update sensor1 validity based on value range *)
IF sensor1.value >= -100.0 AND sensor1.value <= 100.0 THEN
    sensor1.is_valid := TRUE;
ELSE
    sensor1.is_valid := FALSE;
END_IF;

(* Copy first bool_array element to simple_bool for sync testing *)
simple_bool := bool_array[0];

(* Calculate average of real_array and store in simple_real *)
simple_real := (real_array[0] + real_array[1] + real_array[2] + real_array[3]) / 4.0;

END_PROGRAM
