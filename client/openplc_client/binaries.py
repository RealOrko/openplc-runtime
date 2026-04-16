"""Downloads xml2st and iec2c (MatIEC) from the Autonomy-Logic GitHub releases.

The editor pins the same versions in its binary-versions.json; we keep them in
sync manually. First-run cost is ~a few MB per platform.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

XML2ST_VERSION = "v4.0.3"
XML2ST_REPO = "Autonomy-Logic/xml2st"
MATIEC_VERSION = "v4.0.11"
MATIEC_REPO = "Autonomy-Logic/matiec"

CLIENT_ROOT = Path(__file__).resolve().parent.parent
BIN_ROOT = CLIENT_ROOT / "bin"
MATIEC_LIB_DIR = BIN_ROOT / "matiec-lib"
CACHE_FILE_NAME = ".binary-metadata.json"


@dataclass(frozen=True)
class HostTarget:
    platform: str  # "linux" | "darwin" | "win32"
    arch: str      # "x64" | "arm64"

    @property
    def bin_dir(self) -> Path:
        return BIN_ROOT / self.platform / self.arch

    @property
    def iec2c_path(self) -> Path:
        exe = "iec2c.exe" if self.platform == "win32" else "iec2c"
        return self.bin_dir / exe

    @property
    def xml2st_path(self) -> Path:
        if self.platform == "darwin":
            return self.bin_dir / "xml2st" / "xml2st"
        exe = "xml2st.exe" if self.platform == "win32" else "xml2st"
        return self.bin_dir / exe


def detect_host() -> HostTarget:
    sys_plat = sys.platform
    if sys_plat.startswith("linux"):
        plat = "linux"
    elif sys_plat == "darwin":
        plat = "darwin"
    elif sys_plat in ("win32", "cygwin"):
        plat = "win32"
    else:
        raise RuntimeError(f"Unsupported platform: {sys_plat}")

    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        raise RuntimeError(f"Unsupported architecture: {machine}")

    return HostTarget(platform=plat, arch=arch)


def _cache_file(target: HostTarget) -> Path:
    return target.bin_dir / CACHE_FILE_NAME


def _read_cache(target: HostTarget) -> dict:
    try:
        return json.loads(_cache_file(target).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_cache(target: HostTarget) -> None:
    target.bin_dir.mkdir(parents=True, exist_ok=True)
    _cache_file(target).write_text(
        json.dumps(
            {"xml2st": XML2ST_VERSION, "matiec": MATIEC_VERSION,
             "platform": target.platform, "arch": target.arch},
            indent=2,
        )
    )


def _download(url: str, dest: Path) -> None:
    print(f"  downloading {url}", flush=True)
    with urllib.request.urlopen(url) as resp:
        dest.write_bytes(resp.read())


def _extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(dest)


def _chmod_exec(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _ext(target: HostTarget) -> str:
    return "zip" if target.platform == "win32" else "tar.gz"


def _download_xml2st(target: HostTarget) -> None:
    url = (
        f"https://github.com/{XML2ST_REPO}/releases/download/"
        f"{XML2ST_VERSION}/xml2st-{target.platform}-{target.arch}.{_ext(target)}"
    )
    with tempfile.TemporaryDirectory(prefix="xml2st-", dir=BIN_ROOT) as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / f"xml2st.{_ext(target)}"
        _download(url, archive)

        extracted = tmp_path / "extracted"
        _extract(archive, extracted)
        src_dir = extracted / "xml2st"

        target.bin_dir.mkdir(parents=True, exist_ok=True)
        if target.platform == "darwin":
            dest = target.bin_dir / "xml2st"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src_dir, dest, symlinks=True)
            _chmod_exec(dest / "xml2st")
        else:
            exe = "xml2st.exe" if target.platform == "win32" else "xml2st"
            dest_file = target.bin_dir / exe
            if dest_file.exists():
                dest_file.unlink()
            shutil.copyfile(src_dir / exe, dest_file)
            if target.platform != "win32":
                _chmod_exec(dest_file)


def _download_matiec(target: HostTarget) -> None:
    url = (
        f"https://github.com/{MATIEC_REPO}/releases/download/"
        f"{MATIEC_VERSION}/matiec-{target.platform}-{target.arch}.{_ext(target)}"
    )
    with tempfile.TemporaryDirectory(prefix="matiec-", dir=BIN_ROOT) as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / f"matiec.{_ext(target)}"
        _download(url, archive)

        extracted = tmp_path / "extracted"
        _extract(archive, extracted)
        src_dir = extracted / "matiec"

        target.bin_dir.mkdir(parents=True, exist_ok=True)
        exe = "iec2c.exe" if target.platform == "win32" else "iec2c"
        shutil.copyfile(src_dir / exe, target.bin_dir / exe)
        if target.platform != "win32":
            _chmod_exec(target.bin_dir / exe)

        iec2iec = "iec2iec.exe" if target.platform == "win32" else "iec2iec"
        iec2iec_src = src_dir / iec2iec
        if iec2iec_src.exists():
            shutil.copyfile(iec2iec_src, target.bin_dir / iec2iec)
            if target.platform != "win32":
                _chmod_exec(target.bin_dir / iec2iec)

        lib_src = src_dir / "lib"
        if lib_src.exists():
            if MATIEC_LIB_DIR.exists():
                shutil.rmtree(MATIEC_LIB_DIR)
            shutil.copytree(lib_src, MATIEC_LIB_DIR)


def ensure_binaries(force: bool = False) -> HostTarget:
    """Idempotent one-time setup. Returns the resolved host target."""
    BIN_ROOT.mkdir(parents=True, exist_ok=True)
    target = detect_host()
    cache = {} if force else _read_cache(target)

    need_xml2st = force or not target.xml2st_path.exists() or cache.get("xml2st") != XML2ST_VERSION
    need_matiec = (
        force
        or not target.iec2c_path.exists()
        or not MATIEC_LIB_DIR.exists()
        or cache.get("matiec") != MATIEC_VERSION
    )

    if not need_xml2st and not need_matiec:
        print(f"[setup] binaries already present for {target.platform}-{target.arch}")
        return target

    print(f"[setup] host={target.platform}-{target.arch}")
    if need_xml2st:
        _download_xml2st(target)
        print(f"[setup] xml2st {XML2ST_VERSION} installed")
    if need_matiec:
        _download_matiec(target)
        print(f"[setup] matiec {MATIEC_VERSION} installed")

    _write_cache(target)
    return target
