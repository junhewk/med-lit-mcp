"""Native MCP registration for Codex / ChatGPT desktop and pi (their own configuration files)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

import tomlkit

from . import __version__
from .store import atomic_text, file_lock

SERVER_NAME = "med-lit"


class RegistrationConflict(ValueError):
    pass


class ClientConfigurationError(ValueError):
    pass


def codex_config() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve() / "config.toml"


def registration(command: list[str]) -> dict:
    return {"command": command[0], "args": command[1:], "startup_timeout_sec": 180, "tool_timeout_sec": 600}


def register_codex(command: list[str], *, replace: bool = False) -> str:
    """Write one table, retaining unrelated TOML and existing per-tool policy."""
    path = codex_config()
    with file_lock(path.with_suffix(".med-lit.lock")):
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        try:
            document = tomlkit.parse(original)
        except tomlkit.exceptions.ParseError as exc:
            raise ClientConfigurationError("Codex configuration is invalid. Fix it before retrying; the file was not changed.") from exc
        servers = document.setdefault("mcp_servers", tomlkit.table())
        if not isinstance(servers, Mapping):
            raise ClientConfigurationError("The client MCP configuration is invalid; the file was not changed.")
        desired = registration(command)
        existing = servers.get(SERVER_NAME)
        if existing is not None:
            if not isinstance(existing, Mapping):
                raise ClientConfigurationError("The existing med-lit configuration is invalid; the file was not changed.")
            same = existing.get("command") == command[0] and list(existing.get("args", [])) == command[1:]
            conflicting = not same or existing.get("enabled") is False or any(k in existing for k in ("url", "env", "env_vars", "cwd"))
            if conflicting and not replace:
                raise RegistrationConflict("med-lit already has a different registration. Replace it?")
            if conflicting:
                servers[SERVER_NAME] = desired
            else:
                if all(existing.get(k) == v for k, v in desired.items()):
                    return "already registered; restart the client or start a new conversation"
                existing.update(desired)
        else:
            servers[SERVER_NAME] = desired
        if path.exists() and path.read_text(encoding="utf-8") != original:
            raise ClientConfigurationError("Client settings changed during setup. Retry registration.")
        atomic_text(path, tomlkit.dumps(document))
    return "registered; restart the client or start a new conversation"


def pi_config() -> Path:
    """pi's user-level MCP file: mcp.json in its agent directory ($PI_CODING_AGENT_DIR or ~/.pi/agent)."""
    return Path(os.environ.get("PI_CODING_AGENT_DIR") or Path.home() / ".pi" / "agent").expanduser().resolve() / "mcp.json"


def pi_registration(command: list[str]) -> dict:
    # Only med-lit's own entry: its tools are declared directly (a fixed, small set the workflow uses
    # every turn), the scheduled-bot tools stay hidden. pi's model, login and settings are not touched.
    return {
        "command": command[0],
        "args": command[1:],
        "timeout": 600,
        "exposure": "direct",
        "toolExposure": {"bot_*": "hidden"},
        "description": "Staged medical literature review: search, screening, fetch and an evidence-linked wiki",
    }


def register_pi(command: list[str], *, replace: bool = False) -> str:
    """Write the med-lit entry of pi's mcp.json, keeping every other server and setting in the file."""
    path = pi_config()
    with file_lock(path.with_suffix(".med-lit.lock")):
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        try:
            document = json.loads(original) if original.strip() else {}
        except json.JSONDecodeError as exc:
            raise ClientConfigurationError("pi's mcp.json is invalid. Fix it before retrying; the file was not changed.") from exc
        if not isinstance(document, dict) or not isinstance(document.setdefault("mcpServers", {}), dict):
            raise ClientConfigurationError("pi's mcp.json has no valid mcpServers object; the file was not changed.")
        servers = document["mcpServers"]
        desired = pi_registration(command)
        existing = servers.get(SERVER_NAME)
        same = isinstance(existing, dict) and existing.get("command") == command[0] and existing.get("args") == command[1:]
        if same and not replace:
            # Keep the researcher's own changes to the entry (for example exposure set in /mcp).
            return "already registered; start a new pi session (or /reload)"
        if existing is not None and not replace:
            raise RegistrationConflict("med-lit already has a different registration in pi. Replace it?")
        servers[SERVER_NAME] = desired
        if path.exists() and path.read_text(encoding="utf-8") != original:
            raise ClientConfigurationError("pi's mcp.json changed during setup. Retry registration.")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_text(path, json.dumps(document, indent=2) + "\n")
    return (
        "registered in pi's mcp.json; start a new pi session (or /reload). For subagent workers and the "
        f"research policy, install the med-lit pi package: {PI_PACKAGE_HINT}"
    )


PI_PACKAGE_HINT = f"pi install git:github.com/junhewk/med-lit-mcp@v{__version__}"
