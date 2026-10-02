"""Shared native Codex / ChatGPT desktop MCP registration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import tomlkit

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
