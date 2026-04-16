# tank_sim

A tank/pump process simulation that exposes seven variables over OPC UA
(and Modbus, as a fallback) so you have something realistic to observe
and poke at.

## Variables

| IEC name | Location | Type | Direction | Meaning |
|---|---|---|---|---|
| `heartbeat` | `%QX0.0` | BOOL | PLC → HMI | toggles every ~500 ms |
| `inlet_valve` | `%QX0.1` | BOOL | HMI → PLC | open = filling |
| `outlet_pump` | `%QX0.2` | BOOL | HMI → PLC | on = draining |
| `level_high_alarm` | `%QX0.3` | BOOL | PLC → HMI | `tank_level` > 80 |
| `level_low_alarm` | `%QX0.4` | BOOL | PLC → HMI | `tank_level` < 20 |
| `tank_level` | `%MD0` | REAL | PLC → HMI | 0.0–100.0 % |
| `pump_run_count` | `%MD1` | DINT | PLC → HMI | rising-edge count |

## Deploy

```bash
cd client
python -m openplc_client deploy ./models/tank_sim
```

That uploads the compiled program, auto-enables the `opcua` and
`modbus_slave` plugins (via `conf/*.json`), and restarts the PLC.

## Drive traffic

```bash
cd models/tank_sim/sim
pip install -r requirements.txt
python hmi_sim.py
```

The simulator connects to `opc.tcp://localhost:4840/openplc/tank`,
prints the tank state every 2 s, toggles the inlet valve every 10 s and
the outlet pump every 15 s, and force-closes the valve on a high alarm.

## Observe with the CLI

```bash
python -m openplc_client status
python -m openplc_client logs --follow
python -m openplc_client watch ./models/tank_sim
```

See `client/docs/OBSERVABILITY.md` for the full toolkit.
