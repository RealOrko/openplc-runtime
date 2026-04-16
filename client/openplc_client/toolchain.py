"""Replicates the OpenPLC Editor's IEC -> C pipeline:
    iec2c -f -p -i -l program.st
    xml2st --generate-debug program.st VARIABLES.csv
    xml2st --generate-gluevars LOCATED_VARIABLES.h

The output of this module is a populated staging folder laid out exactly as
core/generated/ in the runtime — ready to be zipped and uploaded.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from openplc_client.binaries import MATIEC_LIB_DIR, HostTarget

C_BLOCKS_HEADER_STUB = """#pragma once
// Generated stub: the model provided no c_blocks.h. The runtime's
// c_blocks_code.cpp is compiled against this header; keeping it empty is
// safe when no inline C/C++ function blocks are used.
"""

C_BLOCKS_CODE_STUB = """// Generated stub: the model provided no c_blocks_code.cpp.
// The runtime's compile.sh requires this file to exist, but it can be empty
// when no inline C/C++ function blocks are declared in the IEC program.
#include "c_blocks.h"
"""


def _run(cmd: list[str], cwd: Path) -> None:
    print(f"  $ {' '.join(str(c) for c in cmd)}", flush=True)
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"Binary not found: {cmd[0]} ({e})") from e

    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, end="")
        raise RuntimeError(f"{Path(cmd[0]).name} exited with code {result.returncode}")


def build_src_tree(
    model_dir: Path,
    staging_dir: Path,
    target: HostTarget,
) -> Path:
    """Compile the model into a staging tree matching the runtime's
    core/generated/ layout. Returns the path to that tree."""

    program_st = model_dir / "program.st"
    if not program_st.is_file():
        raise FileNotFoundError(
            f"Model folder must contain program.st: {model_dir}"
        )

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    # 1. Stage program.st
    shutil.copyfile(program_st, staging_dir / "program.st")

    # 2. Stage MatIEC library headers (iec_std_lib.h et al.)
    if not MATIEC_LIB_DIR.exists():
        raise FileNotFoundError(
            f"MatIEC library headers missing at {MATIEC_LIB_DIR}. "
            "Run `python -m openplc_client setup` first."
        )
    shutil.copytree(MATIEC_LIB_DIR, staging_dir / "lib")

    # 3. Stage user C code blocks, or generate stubs
    user_cblocks_h = model_dir / "c_blocks.h"
    user_cblocks_cpp = model_dir / "c_blocks_code.cpp"
    if user_cblocks_h.is_file():
        shutil.copyfile(user_cblocks_h, staging_dir / "c_blocks.h")
    else:
        (staging_dir / "c_blocks.h").write_text(C_BLOCKS_HEADER_STUB)
    if user_cblocks_cpp.is_file():
        shutil.copyfile(user_cblocks_cpp, staging_dir / "c_blocks_code.cpp")
    else:
        (staging_dir / "c_blocks_code.cpp").write_text(C_BLOCKS_CODE_STUB)

    # 4. ST -> C (Config0.c, Res0.c, POUS.{c,h}, LOCATED_VARIABLES.h, VARIABLES.csv)
    print("[compile] iec2c: ST -> C")
    _run(
        [str(target.iec2c_path), "-f", "-p", "-i", "-l", "program.st"],
        cwd=staging_dir,
    )

    # 5. Generate debug.c (xml2st reads program.st + VARIABLES.csv)
    variables_csv = staging_dir / "VARIABLES.csv"
    if not variables_csv.is_file():
        raise RuntimeError(
            "iec2c did not produce VARIABLES.csv — check the ST source"
        )
    print("[compile] xml2st --generate-debug")
    _run(
        [str(target.xml2st_path), "--generate-debug", "program.st", "VARIABLES.csv"],
        cwd=staging_dir,
    )

    # 6. Generate glueVars.c (xml2st reads LOCATED_VARIABLES.h)
    located = staging_dir / "LOCATED_VARIABLES.h"
    if not located.is_file():
        raise RuntimeError(
            "iec2c did not produce LOCATED_VARIABLES.h — unexpected"
        )
    print("[compile] xml2st --generate-gluevars")
    _run(
        [str(target.xml2st_path), "--generate-gluevars", "LOCATED_VARIABLES.h"],
        cwd=staging_dir,
    )

    # 7. Copy user-supplied plugin configs (optional).
    # The runtime's update_plugin_configurations() scans conf/*.json on upload
    # and auto-enables the matching plugin in plugins.conf.
    user_conf = model_dir / "conf"
    if user_conf.is_dir():
        dest_conf = staging_dir / "conf"
        dest_conf.mkdir(exist_ok=True)
        for json_file in user_conf.glob("*.json"):
            shutil.copyfile(json_file, dest_conf / json_file.name)
            print(f"[compile] staged plugin config: {json_file.name}")

    # 8. Verify the runtime's required files are all present
    required = ["Config0.c", "Res0.c", "debug.c", "glueVars.c", "c_blocks_code.cpp"]
    missing = [f for f in required if not (staging_dir / f).is_file()]
    if missing:
        raise RuntimeError(
            f"Compiled tree is missing required files: {missing}"
        )
    if not (staging_dir / "lib").is_dir():
        raise RuntimeError("Compiled tree is missing lib/ directory")

    return staging_dir
