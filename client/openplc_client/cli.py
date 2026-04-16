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

from openplc_client.binaries import CLIENT_ROOT, detect_host, ensure_binaries
from openplc_client.packager import zip_staging
from openplc_client.toolchain import build_src_tree
from openplc_client.uploader import RuntimeClient

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
    zip_staging(staging, output_zip)
    size_kb = output_zip.stat().st_size / 1024
    print(f"[build] wrote {output_zip} ({size_kb:.1f} KB)")
    return output_zip


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
