"""Zips the staged src/ tree into a program.zip suitable for POST /api/upload-file."""

from __future__ import annotations

import zipfile
from pathlib import Path


def zip_staging(staging_dir: Path, output_zip: Path) -> Path:
    """Recursively zip staging_dir's contents into output_zip. Files live at
    the zip root (no wrapper directory) because the runtime's safe_extract()
    strips a single root if present but we prefer to be explicit."""

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        output_zip.unlink()

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(staging_dir.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=path.relative_to(staging_dir).as_posix())

    return output_zip
