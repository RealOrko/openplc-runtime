"""Keep conf/opcua.json `index` fields in sync with debug.c.

After `iec2c` runs, the `debug_vars[]` array in debug.c lists the PLC
variables in declaration order. The OPC-UA plugin uses those positions as
the `index` field in the JSON config — so reordering ST variables silently
invalidates a config without auto-sync.

This module parses debug.c and rewrites conf/opcua.json in place, matching
by `browse_name` against the IEC symbol's tail component (case-insensitive).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_DEBUG_ARRAY_RE = re.compile(
    r"debug_vars\[\]\s*=\s*\{(.*?)\};", re.DOTALL
)
_ENTRY_RE = re.compile(r"&\(([^)]+)\)")


@dataclass
class IndexResult:
    updated: dict[str, tuple[int, int]]   # browse_name -> (old, new)
    unchanged: list[str]
    missing_from_debug: list[str]          # configured but not in debug.c
    extra_in_debug: list[str]              # in debug.c but not configured


def _tail_symbol(iec_symbol: str) -> str:
    """Map an IEC symbol like `CONFIG0__TANK_LEVEL` or
    `RES0__MAININST.PUMP_LAST` to its lowercase tail identifier."""
    # Split first on '.', take rightmost component
    tail = iec_symbol.rsplit(".", 1)[-1]
    # Then split on '__' (MatIEC's scope separator), take rightmost
    tail = tail.rsplit("__", 1)[-1]
    return tail.lower()


def parse_debug_vars(debug_c_path: Path) -> list[str]:
    """Return the list of IEC symbols (tail component, lowercased) in the
    order they appear in debug.c's `debug_vars[]` array — that order IS the
    debug-variable index."""
    text = debug_c_path.read_text()
    m = _DEBUG_ARRAY_RE.search(text)
    if not m:
        raise ValueError(f"Could not find debug_vars[] in {debug_c_path}")
    block = m.group(1)
    symbols = _ENTRY_RE.findall(block)
    return [_tail_symbol(s) for s in symbols]


def _load_opcua_json(path: Path) -> tuple[list | dict, dict]:
    """Return (outer, config_dict) — outer is the full JSON doc (may be a
    wrapping list), config_dict is the nested plugin-config to mutate."""
    data = json.loads(path.read_text())
    if isinstance(data, list):
        if not data:
            raise ValueError(f"{path} is an empty list")
        cfg = data[0].setdefault("config", {})
    elif isinstance(data, dict) and "config" in data:
        cfg = data["config"]
    else:
        cfg = data
    return data, cfg


def _write_opcua_json(path: Path, outer: list | dict) -> None:
    path.write_text(json.dumps(outer, indent=2) + "\n")


def compute_index_changes(
    model_dir: Path,
    build_dir: Path | None = None,
) -> IndexResult:
    """Read the model's conf/opcua.json and the freshly-built debug.c and
    return the diff in terms of `index` field updates (no writes)."""
    conf_path = model_dir / "conf" / "opcua.json"
    if not conf_path.is_file():
        raise FileNotFoundError(f"No conf/opcua.json in {model_dir}")

    build_dir = build_dir or model_dir.parents[0].parent / "build" / model_dir.name / "src"
    # Accept either the staged src/ or its parent build/<name>/ layout
    candidates = [
        build_dir / "debug.c",
        build_dir / "src" / "debug.c",
    ]
    debug_c = next((p for p in candidates if p.is_file()), None)
    if debug_c is None:
        raise FileNotFoundError(
            f"No debug.c found near {build_dir}. Run `openplc_client build` first."
        )

    debug_order = parse_debug_vars(debug_c)  # lowercase tail symbols
    debug_index = {name: i for i, name in enumerate(debug_order)}

    _, cfg = _load_opcua_json(conf_path)
    variables = cfg.get("address_space", {}).get("variables", [])

    updated: dict[str, tuple[int, int]] = {}
    unchanged: list[str] = []
    missing: list[str] = []
    for var in variables:
        browse = var.get("browse_name", "")
        key = browse.lower()
        if key not in debug_index:
            missing.append(browse)
            continue
        new_idx = debug_index[key]
        old_idx = var.get("index", -1)
        if old_idx != new_idx:
            updated[browse] = (old_idx, new_idx)
        else:
            unchanged.append(browse)

    configured = {v.get("browse_name", "").lower() for v in variables}
    extra = [d for d in debug_order if d not in configured]

    return IndexResult(
        updated=updated,
        unchanged=unchanged,
        missing_from_debug=missing,
        extra_in_debug=extra,
    )


def sync_opcua_json(model_dir: Path, build_dir: Path | None = None) -> IndexResult:
    """Apply the index changes in place and return the diff summary."""
    result = compute_index_changes(model_dir, build_dir)
    if not result.updated:
        return result

    conf_path = model_dir / "conf" / "opcua.json"
    outer, cfg = _load_opcua_json(conf_path)
    variables = cfg.get("address_space", {}).get("variables", [])

    # Parse debug.c once more for the actual indices
    build_dir = build_dir or model_dir.parents[0].parent / "build" / model_dir.name / "src"
    candidates = [build_dir / "debug.c", build_dir / "src" / "debug.c"]
    debug_c = next(p for p in candidates if p.is_file())
    debug_order = parse_debug_vars(debug_c)
    debug_index = {name: i for i, name in enumerate(debug_order)}

    for var in variables:
        browse = var.get("browse_name", "")
        key = browse.lower()
        if key in debug_index:
            var["index"] = debug_index[key]

    _write_opcua_json(conf_path, outer)
    return result
