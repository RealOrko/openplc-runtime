# Observability toolkit

Everything you can run from a terminal to see what's happening with the
runtime or the PLC program.

## `openplc_client` subcommands

All of these authenticate the same way `deploy` does — first call on a fresh
runtime auto-creates the `openplc/openplc` admin. Override with
`--runtime`, `--username`, `--password` where needed.

| Command | What you get |
|---|---|
| `status` | PLC state (`RUNNING`/`STOPPED`/`EMPTY`), optional timing stats, last build outcome + tail of build logs |
| `logs` | Runtime log buffer (structured JSON from the Python logger) |
| `logs --follow` | Same, polling for new entries every second |
| `logs --level error` | Filter by log level (`error`, `warn`, `info`, `debug`) |
| `start` / `stop` | Start or stop the PLC program via the REST API |
| `watch <model>` | Live table of the model's variables polled via OPC-UA or Modbus |
| `watch <model> --via modbus` | Force Modbus even if `conf/opcua.json` exists |
| `browse <model>` | One-shot read of every OPC-UA variable with current value |
| `poke <model> <name> <value>` | One-shot write (accepts `true`/`false`, int, float) |
| `sync-opcua <model>` | Rewrite `conf/opcua.json` `index` fields from the last-built `debug.c` |

Examples:

```bash
cd client

python -m openplc_client status
python -m openplc_client logs --follow --level warn
python -m openplc_client watch ./models/tank_sim                # OPC-UA
python -m openplc_client watch ./models/blinky --via modbus     # Modbus
python -m openplc_client browse ./models/tank_sim               # one-shot dump
python -m openplc_client poke ./models/tank_sim inlet_valve true
python -m openplc_client sync-opcua ./models/tank_sim           # fix stale indices
python -m openplc_client stop
python -m openplc_client start
```

`watch` auto-detects which plugin is configured. OPC-UA needs `asyncua`
installed; Modbus needs `pymodbus`. Neither is in the core client
`requirements.txt` — install on demand when you first reach for it.

## Container-level

```bash
# Tail the runtime's stdout/stderr (the Python webserver + gcc output)
docker compose logs -f openplc-runtime

# Last N lines only
docker compose logs --tail=50 openplc-runtime

# Verify which .so is loaded (named after the build timestamp)
docker exec openplc-runtime ls -lh build/libplc_*.so

# Inspect the plugin enablement state
docker exec openplc-runtime cat plugins.conf

# Confirm the runtime's Unix sockets exist
docker exec openplc-runtime ls -la /run/runtime

# Check published ports
docker port openplc-runtime
```

## Ad-hoc Modbus probe

```bash
pip install pymodbus

python -c "
from pymodbus.client import ModbusTcpClient
c = ModbusTcpClient('localhost', port=5020)
c.connect()
print('coils 0-7  :', c.read_coils(0, count=8).bits[:8])
print('holding 0-7:', c.read_holding_registers(0, count=8).registers)
c.close()
"
```

Memory-mapped types in OpenPLC's Modbus slave:

- Coils 0..N → `%QX0.0, %QX0.1, ...` (bool outputs)
- Discrete inputs → `%IX*`
- Holding registers → `%QW*, %MW*, %MD*, %ML*` (ordered per `modbus_slave.json`)
- Input registers → `%IW*`

## Ad-hoc OPC-UA browse

```bash
pip install asyncua

# Walk the address space
python -m asyncua.tools.uals -u opc.tcp://localhost:4840/openplc/tank

# Read a single node
python -m asyncua.tools.uaread -u opc.tcp://localhost:4840/openplc/tank \
    --nodeid "ns=2;s=PLC.Tank.tank_level"
```

For a GUI browser, [UaExpert](https://www.unified-automation.com/products/development-tools/uaexpert.html)
is the standard free choice. Point it at the same endpoint with "None/None"
security and Anonymous auth.

## The tank_sim observation workflow

Terminal 1 — deploy and let the HMI simulator drive the process:

```bash
cd client
python -m openplc_client deploy ./models/tank_sim
cd models/tank_sim/sim
pip install -r requirements.txt
python hmi_sim.py
```

Terminal 2 — watch variables changing live:

```bash
cd client
python -m openplc_client watch ./models/tank_sim
```

Terminal 3 — tail runtime-internal logs (compile output, plugin lifecycle,
errors from the scan cycle):

```bash
cd client
python -m openplc_client logs --follow
```

Terminal 4 — container-level logs (anything the runtime prints to stdout,
including C runtime crashes that never make it to the REST log buffer):

```bash
cd /home/gavin/code/openplc-runtime
docker compose logs -f openplc-runtime
```

## How the runtime's OPC-UA plugin lays out nodes

Worth knowing if you write your own OPC-UA clients against the runtime.

The plugin places **every variable as a direct child of `Objects`**, keyed
by `browse_name`. The dotted `node_id` in `conf/opcua.json` (e.g.
`"PLC.Tank.heartbeat"`) is treated as a label, not a path — it doesn't
create a folder hierarchy. Only the leaf matters for resolution.

That means a raw asyncua lookup looks like this — **not** a dotted walk:

```python
ns = await client.get_namespace_index("urn:openplc:tank_sim")
node = await client.nodes.objects.get_child([f"{ns}:heartbeat"])
```

`openplc_client.model_client.connect()` hides this entirely. Prefer it
over writing resolution logic by hand:

```python
async with connect("./models/tank_sim") as m:
    value = await m.read("heartbeat")
```

## Gotchas

- **JWT expiry** — the runtime issues JWTs with a fixed lifetime; if
  `status` starts returning 401 after a long-running session, re-run the
  command and the client will log in again.
- **OPC-UA `index` drift** — `conf/opcua.json` references debug variables
  by position in `debug.c`'s `debug_vars[]`. Reordering or adding ST
  variables silently invalidates the config. `build` warns on mismatch;
  `sync-opcua` rewrites the indices in place from the last built
  `debug.c`.
- **"status: EMPTY"** — the runtime is up but no `.so` has been loaded.
  Run `deploy` to push a program.
- **`watch` hangs on connect** — the plugin needs time to spin up after a
  deploy; wait ~5 s after `runtime build SUCCESS` before running `watch`.
