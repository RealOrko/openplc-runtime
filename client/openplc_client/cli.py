"""Entrypoint for `python -m openplc_client`.

Subcommands:
    setup                  one-time download of iec2c + xml2st
    build <model>          compile the model folder locally; emit build/<name>.zip
    deploy <model>         build + upload + poll to a running runtime
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import asyncio
import time
from urllib.parse import urlparse

from openplc_client.binaries import CLIENT_ROOT, detect_host, ensure_binaries
from openplc_client.opcua_gen import compute_index_changes, sync_opcua_json
from openplc_client.packager import zip_staging
from openplc_client.toolchain import build_src_tree
from openplc_client.uploader import RuntimeClient
from openplc_client.watcher import watch_model

DEFAULT_RUNTIME_URL = "https://localhost:8443"
DEFAULT_USERNAME = "openplc"
DEFAULT_PASSWORD = "openplc"

BUILD_ROOT = CLIENT_ROOT / "build"


def _model_overrides(model_dir: Path) -> dict:
    """Optional model.json in the model folder can override CLI defaults
    (runtime URL, credentials). CLI flags take precedence over model.json."""
    cfg_path = model_dir / "model.json"
    if cfg_path.is_file():
        try:
            return json.loads(cfg_path.read_text())
        except json.JSONDecodeError as e:
            raise SystemExit(f"Invalid model.json in {model_dir}: {e}")
    return {}


def _resolve_model(arg: str) -> Path:
    p = Path(arg).expanduser().resolve()
    if not p.is_dir():
        raise SystemExit(f"Model folder not found: {p}")
    if not (p / "program.st").is_file():
        raise SystemExit(f"Model folder must contain program.st: {p}")
    return p


def _build(model_dir: Path) -> Path:
    target = ensure_binaries()
    staging = BUILD_ROOT / model_dir.name / "src"
    output_zip = BUILD_ROOT / f"{model_dir.name}.zip"
    print(f"[build] model={model_dir.name} staging={staging}")
    build_src_tree(model_dir, staging, target)
    _warn_stale_opcua_indices(model_dir, staging)
    zip_staging(staging, output_zip)
    size_kb = output_zip.stat().st_size / 1024
    print(f"[build] wrote {output_zip} ({size_kb:.1f} KB)")
    return output_zip


def _warn_stale_opcua_indices(model_dir: Path, staging: Path) -> None:
    """If the model has an opcua.json whose indices don't match the
    freshly-generated debug.c, print a warning."""
    if not (model_dir / "conf" / "opcua.json").is_file():
        return
    try:
        result = compute_index_changes(model_dir, build_dir=staging)
    except (FileNotFoundError, ValueError):
        return

    if result.updated:
        print("[build] WARNING: conf/opcua.json indices are stale:")
        for name, (old, new) in result.updated.items():
            print(f"         {name}: index {old} -> {new}")
        print("         Run `python -m openplc_client sync-opcua "
              f"{model_dir}` to rewrite the config.")
    if result.missing_from_debug:
        print("[build] WARNING: these variables in conf/opcua.json are not "
              "in the compiled debug.c:")
        for name in result.missing_from_debug:
            print(f"         {name}")


def _cmd_setup(args: argparse.Namespace) -> int:
    ensure_binaries(force=args.force)
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    model_dir = _resolve_model(args.model)
    _build(model_dir)
    return 0


def _cmd_deploy(args: argparse.Namespace) -> int:
    model_dir = _resolve_model(args.model)
    overrides = _model_overrides(model_dir)

    runtime_url = args.runtime or overrides.get("runtime") or DEFAULT_RUNTIME_URL
    username = args.username or overrides.get("username") or DEFAULT_USERNAME
    password = args.password or overrides.get("password") or DEFAULT_PASSWORD

    zip_path = _build(model_dir)

    print(f"[deploy] runtime={runtime_url} user={username}")
    client = RuntimeClient(base_url=runtime_url, username=username, password=password)
    client.ensure_authenticated()
    client.upload_zip(zip_path)
    client.poll_compilation()
    return 0


def _make_client(args: argparse.Namespace) -> RuntimeClient:
    runtime_url = args.runtime or DEFAULT_RUNTIME_URL
    username = args.username or DEFAULT_USERNAME
    password = args.password or DEFAULT_PASSWORD
    client = RuntimeClient(base_url=runtime_url, username=username, password=password)
    client.ensure_authenticated()
    return client


def _cmd_status(args: argparse.Namespace) -> int:
    client = _make_client(args)
    status = client.plc_status(include_stats=True)
    compile_status = client.compilation_status()

    plc_state = status.get("status", "?")
    print(f"runtime : {args.runtime or DEFAULT_RUNTIME_URL}")
    print(f"plc     : {plc_state}")
    stats = status.get("timing_stats")
    if stats:
        print("timing  :")
        for k, v in stats.items():
            print(f"  {k:<28s} {v}")

    print(f"build   : {compile_status.get('status', '?')} "
          f"(exit_code={compile_status.get('exit_code')})")
    logs = compile_status.get("logs", [])
    if logs:
        print("  last 5 build log lines:")
        for line in logs[-5:]:
            print(f"    {line.rstrip()}")
    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    client = _make_client(args)
    since = 0
    if args.follow:
        print("[logs] following runtime logs; Ctrl-C to stop")
        try:
            while True:
                entries = client.runtime_logs(since_id=since, level=args.level)
                for e in entries:
                    since = max(since, int(e.get("id", since)))
                    _print_log_entry(e)
                time.sleep(1.0)
        except KeyboardInterrupt:
            print()
            return 0
    else:
        for e in client.runtime_logs(level=args.level):
            _print_log_entry(e)
        return 0


def _print_log_entry(e: dict) -> None:
    if not isinstance(e, dict):
        print(str(e))
        return
    level = e.get("level", "?")
    ts = e.get("timestamp") or e.get("time") or ""
    msg = e.get("message") or e.get("msg") or ""
    print(f"{ts} [{level}] {msg}")


def _cmd_start(args: argparse.Namespace) -> int:
    client = _make_client(args)
    print(client.start_plc())
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    client = _make_client(args)
    print(client.stop_plc())
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    model_dir = _resolve_model(args.model)
    runtime_url = args.runtime or DEFAULT_RUNTIME_URL
    return watch_model(model_dir, runtime_url, prefer=args.via)


def _cmd_browse(args: argparse.Namespace) -> int:
    """One-shot browse of a model's OPC-UA variables with current values."""
    from openplc_client.model_client import connect

    model_dir = _resolve_model(args.model)
    host = urlparse(args.runtime or DEFAULT_RUNTIME_URL).hostname or "localhost"

    async def go() -> int:
        async with connect(model_dir, host=host) as m:
            print(f"endpoint: {m.endpoint_url}")
            print(f"{'name':<24s} {'type':<8s} value")
            print(f"{'-'*24} {'-'*8} -----")
            snap = await m.snapshot()
            for name, value in snap.items():
                dtype = m[name].datatype
                if isinstance(value, float):
                    rendered = f"{value:.3f}"
                else:
                    rendered = str(value)
                print(f"{name:<24s} {dtype:<8s} {rendered}")
            return 0

    return asyncio.run(go())


def _cmd_poke(args: argparse.Namespace) -> int:
    """One-shot write to a single OPC-UA variable."""
    from openplc_client.model_client import connect

    model_dir = _resolve_model(args.model)
    host = urlparse(args.runtime or DEFAULT_RUNTIME_URL).hostname or "localhost"

    async def go() -> int:
        async with connect(model_dir, host=host) as m:
            if args.name not in m:
                print(f"ERROR: '{args.name}' is not in conf/opcua.json. "
                      f"Known: {sorted(m.variables)}")
                return 1
            before = await m.read(args.name)
            await m.write(args.name, args.value)
            after = await m.read(args.name)
            print(f"{args.name}: {before} -> {after}")
            return 0

    return asyncio.run(go())


def _cmd_sync_opcua(args: argparse.Namespace) -> int:
    """Rewrite conf/opcua.json's `index` fields from the model's last
    compiled debug.c."""
    model_dir = _resolve_model(args.model)
    staging = BUILD_ROOT / model_dir.name / "src"
    if not (staging / "debug.c").is_file():
        print(f"[sync-opcua] no build output at {staging}. "
              f"Running build first...")
        _build(model_dir)

    try:
        result = sync_opcua_json(model_dir, build_dir=staging)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}")
        return 1

    if not result.updated:
        print("[sync-opcua] already in sync — no changes written")
    else:
        print(f"[sync-opcua] rewrote conf/opcua.json ({len(result.updated)} "
              "index change(s)):")
        for name, (old, new) in result.updated.items():
            print(f"  {name}: {old} -> {new}")

    if result.missing_from_debug:
        print("[sync-opcua] these configured variables are NOT in debug.c "
              "(left unchanged):")
        for name in result.missing_from_debug:
            print(f"  {name}")
    if result.extra_in_debug:
        print("[sync-opcua] these debug.c symbols are NOT exposed via OPC-UA:")
        for name in result.extra_in_debug:
            print(f"  {name}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m openplc_client",
        description="Headless IEC 61131-3 compiler and uploader for OpenPLC Runtime v4",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="Download iec2c and xml2st binaries")
    p_setup.add_argument("--force", action="store_true", help="Re-download even if cached")
    p_setup.set_defaults(func=_cmd_setup)

    p_build = sub.add_parser("build", help="Compile a model folder to a program.zip")
    p_build.add_argument("model", help="Path to a model folder (e.g. ./models/blinky)")
    p_build.set_defaults(func=_cmd_build)

    p_deploy = sub.add_parser("deploy", help="Compile and upload a model to a running runtime")
    p_deploy.add_argument("model", help="Path to a model folder (e.g. ./models/blinky)")
    p_deploy.add_argument("--runtime", help=f"Runtime base URL (default {DEFAULT_RUNTIME_URL})")
    p_deploy.add_argument("--username", help=f"Runtime username (default {DEFAULT_USERNAME})")
    p_deploy.add_argument("--password", help=f"Runtime password (default {DEFAULT_PASSWORD})")
    p_deploy.set_defaults(func=_cmd_deploy)

    def _add_auth(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--runtime", help=f"Runtime base URL (default {DEFAULT_RUNTIME_URL})")
        sp.add_argument("--username", help=f"Runtime username (default {DEFAULT_USERNAME})")
        sp.add_argument("--password", help=f"Runtime password (default {DEFAULT_PASSWORD})")

    p_status = sub.add_parser("status", help="Print PLC runtime state and last build status")
    _add_auth(p_status)
    p_status.set_defaults(func=_cmd_status)

    p_logs = sub.add_parser("logs", help="Dump runtime log buffer")
    _add_auth(p_logs)
    p_logs.add_argument("--follow", "-f", action="store_true", help="Tail new log entries")
    p_logs.add_argument("--level", help="Filter by level (e.g. error, warn, info)")
    p_logs.set_defaults(func=_cmd_logs)

    p_start = sub.add_parser("start", help="Start the PLC program")
    _add_auth(p_start)
    p_start.set_defaults(func=_cmd_start)

    p_stop = sub.add_parser("stop", help="Stop the PLC program")
    _add_auth(p_stop)
    p_stop.set_defaults(func=_cmd_stop)

    p_watch = sub.add_parser("watch", help="Live view of a model's variables (OPC-UA or Modbus)")
    p_watch.add_argument("model", help="Path to a model folder")
    p_watch.add_argument("--via", choices=["auto", "opcua", "modbus"], default="auto",
                         help="Protocol to use (default auto — OPC-UA if configured)")
    p_watch.add_argument("--runtime", help=f"Runtime base URL (default {DEFAULT_RUNTIME_URL})")
    p_watch.set_defaults(func=_cmd_watch)

    p_browse = sub.add_parser("browse", help="One-shot read of every OPC-UA variable in a model")
    p_browse.add_argument("model", help="Path to a model folder")
    p_browse.add_argument("--runtime", help=f"Runtime base URL (default {DEFAULT_RUNTIME_URL})")
    p_browse.set_defaults(func=_cmd_browse)

    p_poke = sub.add_parser("poke", help="One-shot write to an OPC-UA variable")
    p_poke.add_argument("model", help="Path to a model folder")
    p_poke.add_argument("name", help="Variable browse_name (e.g. inlet_valve)")
    p_poke.add_argument("value", help="Value to write (true/false, int, float)")
    p_poke.add_argument("--runtime", help=f"Runtime base URL (default {DEFAULT_RUNTIME_URL})")
    p_poke.set_defaults(func=_cmd_poke)

    p_sync = sub.add_parser("sync-opcua",
                            help="Rewrite conf/opcua.json indices to match the current debug.c")
    p_sync.add_argument("model", help="Path to a model folder")
    p_sync.set_defaults(func=_cmd_sync_opcua)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except SystemExit:
        raise
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
