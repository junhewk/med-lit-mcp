"""Configuration status for the model; private values stay in the local settings form."""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
import threading
import time
from typing import Any

from . import clients, keys, projects, settings
from .config import ncbi_email

_lock = threading.Lock()
_form: Any = None


def require_interactive() -> None:
    if projects.scope() == "bot":
        raise ValueError("Scheduled bots cannot change configuration. Use a normal researcher chat.")


def status() -> dict[str, Any]:
    """No email address, keys (including suffixes), folder or private session URL in this result."""
    keys.load_into_environ()
    configured = bool(ncbi_email())
    result: dict[str, Any] = {
        "configured": configured,
        "email_configured": configured,
        "providers": {p.name: bool(os.environ.get(p.env, "").strip()) for p in keys.PROVIDERS},
        "next": "Start a review." if configured else "Open the private form with med_lit_settings(action='open').",
        "privacy": "Enter email and API keys only in the private form or your own terminal, never in chat.",
    }
    with _lock:
        if _form is not None:
            result["form"] = (
                "saved" if _form.succeeded else "unavailable" if getattr(_form, "browser_failed", False)
                else "closed" if _form.finished.is_set() else "open"
            )
            if result["form"] == "unavailable":
                result["command"] = "uvx med-lit-mcp setup --terminal --no-register"
    return result


def close_form() -> None:
    with _lock:
        if _form is not None:
            _form.finished.set()


atexit.register(close_form)


def _open_browser(url: str) -> bool:
    # Browser helpers can write to stdout. Isolate them so they cannot corrupt MCP's stdio stream
    # or leak the private URL through captured tool output.
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import sys, webbrowser; sys.exit(0 if webbrowser.open(sys.argv[1]) else 1)", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def open_form() -> dict[str, Any]:
    """Launch the form without exposing its bearer URL to the model or the chat transcript."""
    from .setup_ui import SetupServer

    global _form
    require_interactive()
    with _lock:
        if _form is not None and not _form.finished.is_set():
            return {"form": "open", "message": "The private settings form is already open in your browser."}
        client = os.environ.get("MED_LIT_CLIENT", "agent")
        server = SetupServer([], register=False, client=client if client in clients.CLIENT_NAMES else "agent")
        server.browser_failed = False
        server.timeout = 0.5
        _form = server

    def serve() -> None:
        try:
            while not server.finished.is_set():
                server.handle_request()
                if not server.busy.locked() and time.monotonic() - server.last_activity > 900:
                    server.finished.set()
        finally:
            server.server_close()

    threading.Thread(target=serve, daemon=True, name="med-lit-private-settings").start()
    browser_done = threading.Event()
    opened = []

    def launch() -> None:
        try:
            success = _open_browser(f"{server.origin}/#{server.token}")
            opened.append(success)
            if not success:
                server.browser_failed = True
                server.finished.set()
        finally:
            browser_done.set()

    threading.Thread(target=launch, daemon=True, name="med-lit-open-settings").start()
    browser_done.wait(2)
    if browser_done.is_set() and not opened[0]:
        server.finished.set()
        return {
            "form": "unavailable",
            "message": "A browser could not be opened on the machine running med-lit. Run the following in your own terminal there.",
            "command": "uvx med-lit-mcp setup --terminal --no-register",
        }
    return {
        "form": "open",
        "message": "Complete the private settings form in your browser, then call med_lit_settings(action='status'). Do not read, capture or automate the form or credential files.",
    }


def defaults(mode: str, changes: dict[str, Any] | None = None) -> dict[str, Any]:
    require_interactive()
    current = settings.new_settings(mode)
    if changes:
        current = settings.apply_changes(current, changes)
        settings.save_user_defaults(mode, current.model_dump(exclude={"mode", "version"}))
    return {"mode": mode, "settings": current.model_dump(), "note": "Defaults apply only to new projects; existing reviews are unchanged."}
