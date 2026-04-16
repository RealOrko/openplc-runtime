# OpenPLC Runtime — Headless Client

Command-line tool that replicates the OpenPLC Editor's compile-and-upload
pipeline. Takes a folder with an IEC 61131-3 `program.st` (plus optional
plugin configs and inline C), runs the same `iec2c` + `xml2st` toolchain the
Editor uses, zips the result in the layout the runtime expects, and posts it
to a running runtime instance.

## Install

Python 3.10+ required. From the repo root:

```bash
cd client
pip install -r requirements.txt
python -m openplc_client setup
```

`setup` downloads `iec2c` (MatIEC) and `xml2st` from the Autonomy-Logic
GitHub releases into `client/bin/<platform>/<arch>/`. Idempotent — re-run
with `--force` to refresh.

## Model folder contract

Any directory can be a model. The minimum is a `program.st`:

```
models/<name>/
  program.st              # REQUIRED — IEC 61131-3 Structured Text
  c_blocks.h              # OPTIONAL — header for inline C/C++ FBs
  c_blocks_code.cpp       # OPTIONAL — inline C/C++ FB implementations
  conf/                   # OPTIONAL — plugin configs (JSON)
    modbus_slave.json
    modbus_master.json
    opcua.json
    s7comm.json
  model.json              # OPTIONAL — per-model CLI defaults (runtime, user, pass)
```

`conf/*.json` files are picked up by the runtime's
`update_plugin_configurations()` on upload; each file's presence flips the
matching row in `plugins.conf` to `enabled=1`, so the corresponding
fieldbus port starts listening.

## Build a zip locally

```bash
python -m openplc_client build ./models/blinky
# -> client/build/blinky.zip
```

## Deploy to a running runtime

With the docker-compose stack from the repo root running:

```bash
docker compose up -d
python -m openplc_client deploy ./models/blinky
```

Defaults assume `https://localhost:8443` with user `openplc` / password
`openplc`. First call to a fresh runtime auto-bootstraps that admin user
(the `/api/create-user` endpoint is open only while the user table is
empty — matches the Editor's first-connect behavior).

Override with flags or a `model.json` next to `program.st`:

```json
{
  "runtime": "https://192.168.1.50:8443",
  "username": "alice",
  "password": "hunter2"
}
```

```bash
python -m openplc_client deploy ./models/blinky \
  --runtime https://192.168.1.50:8443 \
  --username alice --password hunter2
```

## What happens under the hood

1. Stage `client/build/<name>/src/` with `program.st`, the MatIEC `lib/`
   headers, and `c_blocks*` (stubs if not provided).
2. `iec2c -f -p -i -l program.st` → `Config0.c`, `Res0.c`,
   `POUS.{c,h}`, `LOCATED_VARIABLES.h`, `VARIABLES.csv`.
3. `xml2st --generate-debug program.st VARIABLES.csv` → `debug.c`.
4. `xml2st --generate-gluevars LOCATED_VARIABLES.h` → `glueVars.c`.
5. Copy any `<model>/conf/*.json` into `src/conf/`.
6. Zip `src/` → `client/build/<name>.zip`.
7. `POST /api/login` → JWT (or `POST /api/create-user` first on a fresh
   runtime).
8. `POST /api/upload-file` with multipart body.
9. Poll `GET /api/compilation-status` until `SUCCESS` or `FAILED`, streaming
   the runtime's gcc logs.

## Scripting against a deployed model

`openplc_client.model_client` gives you a model-scoped async client that
pre-resolves every OPC-UA variable by `browse_name` — the only lookup key
the runtime's OPC-UA plugin actually honors (see
`docs/OBSERVABILITY.md` for background on that quirk).

```python
import asyncio
from openplc_client.model_client import connect

async def main():
    async with connect("./models/tank_sim", host="localhost") as m:
        # Snapshot the whole model in one shot
        print(await m.snapshot())

        # Or reach individual variables
        level = await m.read("tank_level")
        await m.write("inlet_valve", True)

        # Live polling
        async for snap in m.poll("tank_level", "level_high_alarm", period=0.5):
            print(snap["tank_level"], snap["level_high_alarm"])

asyncio.run(main())
```

Credential precedence: explicit `username=` / `password=` args beat the
"single password user in conf/opcua.json" convention, which beats
Anonymous. For Modbus-only models, use `ModbusModelClient` from the same
module.

## Keeping `conf/opcua.json` in sync

The OPC-UA plugin identifies PLC variables by their position in
`debug.c`'s `debug_vars[]` array (the `index` field). Reordering or adding
ST variables reshuffles those positions and silently invalidates the
config.

`build` warns if it detects a mismatch. To fix:

```bash
python -m openplc_client sync-opcua ./models/tank_sim
```

`sync-opcua` parses the last-built `debug.c`, matches configured
`browse_name`s to IEC symbols' trailing identifiers (case-insensitive),
and rewrites the `index` fields in place.

## Troubleshooting

- **"Binary not found: iec2c"** — run `python -m openplc_client setup` first.
- **TLS / self-signed cert warnings** — suppressed by default; the runtime
  uses a self-signed cert stored at `webserver/certOPENPLC.pem`.
- **"No users found" then login fails** — the bootstrap step only creates
  one user. If you typo the password on first run the runtime is stuck with
  that user; `docker compose down -v` wipes the volume and resets it.
- **Compilation stuck at `UNZIPPING`** — model zip failed safety checks
  (path traversal, disallowed extension, or > 50 MB total). Inspect the
  runtime logs with `docker compose logs -f`.
