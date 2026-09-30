"""Environment settings, read at call time so the MCP client's env block is authoritative."""

from __future__ import annotations

import os
from pathlib import Path


def user_path(value: str, base: Path | None = None) -> Path:
    """A folder setting as an absolute path: expands $VARS and ~; relative means under base or home.

    MCP clients start servers from arbitrary working directories (Claude Desktop uses the extension's
    install folder) and may pass defaults such as "${HOME}/med-lit" unexpanded.
    """
    path = Path(os.path.expandvars(value.strip())).expanduser()
    return path if path.is_absolute() else (base or Path.home()) / path


def state_dir() -> Path:
    """Where the registry of projects lives (not the projects themselves)."""
    configured = os.environ.get("MED_LIT_STATE_DIR", "").strip()
    if configured:
        return user_path(configured)
    return user_path(os.environ.get("XDG_DATA_HOME") or "~/.local/share") / "med-lit-mcp"


def projects_dir() -> Path:
    """Default parent folder for new projects."""
    return user_path(os.environ.get("MED_LIT_PROJECTS_DIR", "").strip() or "~/med-lit")


def ncbi_email(*, required: bool = False) -> str | None:
    value = os.environ.get("NCBI_EMAIL", "").strip() or None
    if required and not value:
        raise ValueError(
            "NCBI_EMAIL is not set. Add it to this MCP server's environment "
            "(hermes mcp add ... --env NCBI_EMAIL=you@example.org; "
            "claude mcp add ... -e NCBI_EMAIL=you@example.org)"
        )
    return value


def ncbi_api_key() -> str | None:
    return os.environ.get("NCBI_API_KEY", "").strip() or None


STAGES = ("search", "screening", "fetch", "wiki")


def enabled_stages() -> tuple[str, ...]:
    """MED_LIT_STAGES (e.g. 'fetch') exposes that stage and the stages it depends on."""
    raw = os.environ.get("MED_LIT_STAGES", "").strip()
    if not raw:
        return STAGES
    wanted = {part.strip().lower() for part in raw.split(",") if part.strip()}
    unknown = sorted(wanted - set(STAGES))
    if unknown:
        raise ValueError(f"Unknown MED_LIT_STAGES {', '.join(unknown)}; use {', '.join(STAGES)}")
    last = max(STAGES.index(stage) for stage in wanted)
    return STAGES[: last + 1]
