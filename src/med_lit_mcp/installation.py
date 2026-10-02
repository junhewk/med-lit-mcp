"""Validate the portable package shared by installer builds and native setup."""

from __future__ import annotations

from email.parser import BytesParser
from pathlib import Path
import zipfile

from . import __version__


def validate_wheel(path: str | Path) -> Path:
    """Return the resolved wheel path, or reject a different or damaged package."""
    try:
        wheel = Path(path).expanduser().resolve(strict=True)
        if not wheel.is_file() or not wheel.name.endswith("-py3-none-any.whl"):
            raise ValueError("Not a portable wheel")
        with zipfile.ZipFile(wheel) as archive:
            entries = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(entries) != 1:
                raise ValueError("Invalid package metadata")
            metadata = BytesParser().parsebytes(archive.read(entries[0]))
        name = metadata.get("Name", "").lower().replace("_", "-")
        if name != "med-lit-mcp" or metadata.get("Version") != __version__:
            raise ValueError("Different package or version")
        return wheel
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, KeyError) as exc:
        raise ValueError(f"The installer needs a valid portable med-lit {__version__} wheel.") from exc
