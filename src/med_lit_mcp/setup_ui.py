"""Short-lived local settings form for novice ChatGPT desktop users."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus, urlsplit

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from . import __version__, clients, keys
from .cli_setup import EMAIL, server_command
from .config import projects_dir, user_path
from .installation import validate_wheel
from .store import file_lock


class SettingsError(ValueError):
    """A message safe to display in the settings form."""


def choose_folder(folder: str) -> str | None:
    """Open the OS folder dialog. Cancellation leaves the typed path intact."""
    if sys.platform == "darwin":
        script = '''on run argv
try
set initialFolder to path to home folder
if (count of argv) > 0 then
try
set initialFolder to POSIX file (item 1 of argv) as alias
end try
end if
return POSIX path of (choose folder with prompt "Choose a folder for med-lit reviews" default location initialFolder)
on error number -128
return ""
end try
end run'''
        command = ["/usr/bin/osascript", "-e", script, folder]
        env = None
    elif sys.platform == "win32":
        script = """[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false);
Add-Type -AssemblyName System.Windows.Forms;
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog;
$dialog.Description = 'Choose a folder for med-lit reviews';
$dialog.SelectedPath = $env:MED_LIT_FOLDER_PICKER_INITIAL;
try { if ($dialog.ShowDialog() -eq 'OK') { Write-Output $dialog.SelectedPath } }
finally { $dialog.Dispose() }"""
        command = ["powershell.exe", "-NoLogo", "-NoProfile", "-STA", "-Command", script]
        env = {**os.environ, "MED_LIT_FOLDER_PICKER_INITIAL": folder}
    else:
        raise SettingsError("Folder selection is available on macOS and Windows. Enter a folder path instead.")
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SettingsError("Could not open the folder chooser. Enter a folder path instead.") from exc
    if result.returncode:
        raise SettingsError("Could not open the folder chooser. Enter a folder path instead.")
    return result.stdout.strip() or None


async def check_connection(command: list[str]) -> dict:
    """Exercise the actual launcher and MCP handshake, without writing review data."""
    with anyio.fail_after(180), open(os.devnull, "w") as errors:  # noqa: ASYNC230
        async with (
            stdio_client(StdioServerParameters(command=command[0], args=command[1:]), errlog=errors) as streams,
            ClientSession(*streams) as session,
        ):
            await session.initialize()
            tools = (await session.list_tools()).tools
            result = await session.call_tool("guide", {"topic": "workflow"})
            if result.isError:
                raise SettingsError("The server could not read its workflow guide.")
    return {"connected": True, "tools": len(tools)}


def ui_command(args: argparse.Namespace) -> list[str]:
    if args.dev:
        return server_command(args.dev)
    launcher = args.launcher or shutil.which("uv")
    if not launcher or not Path(launcher).is_absolute() or not Path(launcher).is_file():
        raise SettingsError("The setup runtime was not found. Reopen the med-lit installer to retry.")
    package = getattr(args, "package", None)
    source = f"med-lit-mcp=={__version__}"
    if package:
        # A signed installer keeps its wheel outside the mounted download, so
        # native clients can start the same version after the disk is ejected.
        try:
            wheel = validate_wheel(package)
        except ValueError as exc:
            raise SettingsError("The installer package is missing or invalid. Reopen the med-lit installer to repair it.") from exc
        source = str(wheel)
    return [str(Path(launcher).resolve()), "tool", "run", "--python", "3.11", "--from", source, "med-lit-mcp"]


class SetupServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, command: list[str], *, register: bool = True, email: str | None = None, folder: str | None = None):
        super().__init__(("127.0.0.1", 0), SetupHandler)
        self.command = command
        self.register = register
        self.email = email
        self.folder = folder
        self.token = secrets.token_urlsafe(32)
        self.last_activity = time.monotonic()
        self.finished = threading.Event()
        self.busy = threading.Lock()
        self.succeeded = False

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"


class SetupHandler(BaseHTTPRequestHandler):
    server: SetupServer

    def log_message(self, *args: Any) -> None:
        pass  # Neither settings nor session URLs belong in HTTP logs.

    def respond(self, status: int, value: Any, *, html: bool = False) -> None:
        body = value.encode("utf-8") if html else json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self, *, api: bool = True) -> bool:
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            self.respond(403, {"error": "Open settings using the original link."})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            self.respond(403, {"error": "Cross-origin requests are not allowed."})
            return False
        if api and not secrets.compare_digest(self.headers.get("X-Med-Lit-Token", ""), self.server.token):
            self.respond(403, {"error": "This settings session has expired. Reopen the med-lit installer."})
            return False
        self.server.last_activity = time.monotonic()
        return True

    def do_GET(self) -> None:
        if not self.authorized(api=self.path != "/"):
            return
        if self.path == "/":
            self.respond(200, files("med_lit_mcp").joinpath("setup/index.html").read_text(encoding="utf-8"), html=True)
        elif self.path == "/api/settings":
            try:
                config = keys.read_config()
                stored = keys.read_keys()
            except (OSError, ValueError):
                self.respond(400, {"error": "Could not open saved settings. Ask ChatGPT to check the med-lit settings files, then reopen this form."})
                return
            self.respond(200, {
                "email": self.server.email or config.get("NCBI_EMAIL", ""),
                "folder": self.server.folder or config.get("MED_LIT_PROJECTS_DIR", str(projects_dir())),
                "providers": [
                    {"name": p.name, "title": p.title, "purpose": p.purpose, "signup": p.signup, "set": bool(stored.get(p.env))}
                    for p in keys.PROVIDERS
                ],
            })
        else:
            self.respond(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if not self.authorized():
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.respond(415, {"error": "JSON content required"})
            return
        if self.server.finished.is_set():
            self.respond(409, {"error": "Setup has finished. Reopen settings to make more changes."})
            return
        if not self.server.busy.acquire(blocking=False):
            self.respond(409, {"error": "A settings operation is in progress. Please wait."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 32768:
                raise SettingsError("Settings request is too large or empty.")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise SettingsError("Invalid settings request.")
            if self.path == "/api/choose-folder":
                initial = data.get("folder", "")
                if not isinstance(initial, str):
                    raise SettingsError("Invalid folder path.")
                folder = choose_folder(initial)
                self.respond(200, {"folder": folder, "cancelled": folder is None})
            elif self.path == "/api/test-key":
                provider = keys.BY_NAME.get(data.get("name"))
                value = data.get("value")
                if provider is None or not isinstance(value, str) or not value.strip():
                    raise SettingsError("Enter a key before checking it.")
                value = value.strip()
                insttoken = data.get("insttoken") or keys.read_keys().get("SCOPUS_INSTTOKEN")
                if insttoken is not None and not isinstance(insttoken, str):
                    raise SettingsError("Invalid institutional token.")
                ok, message = keys.test_key(provider, value, insttoken=insttoken)
                for secret in (value, insttoken):
                    if secret:
                        for representation in (secret, quote(secret, safe=""), quote_plus(secret)):
                            message = message.replace(representation, "[hidden]")
                self.respond(200, {"ok": ok, "message": message})
            elif self.path == "/api/save":
                result = self.save(data)
                complete = result.get("connected") or not self.server.register
                if complete:
                    self.server.succeeded = True
                self.respond(200, result)
                if complete:
                    self.server.finished.set()
            elif self.path == "/api/cancel":
                self.respond(200, {"cancelled": True})
                self.server.finished.set()
            else:
                self.respond(404, {"error": "Not found"})
        except clients.RegistrationConflict:
            self.respond(409, {"error": "A different med-lit connection already exists. Choose Replace connection to continue.", "conflict": True})
        except clients.ClientConfigurationError:
            self.respond(400, {"error": "Could not update the client connection. Ask ChatGPT to check its existing MCP configuration, then retry. Your email and keys were not changed."})
        except SettingsError as exc:
            self.respond(400, {"error": str(exc)})
        except (OSError, ValueError, TypeError, KeyError):
            self.respond(400, {"error": "Could not save or check these settings. Check the email, folder and key entries, then retry. Existing settings may have been saved."})
        finally:
            self.server.busy.release()

    def save(self, data: dict) -> dict:
        email, folder = data.get("email"), data.get("folder")
        if not isinstance(email, str) or not EMAIL.fullmatch(email.strip()):
            raise SettingsError("Enter a valid contact email.")
        if not isinstance(folder, str) or not folder.strip():
            raise SettingsError("Enter a folder for reviews.")
        root = user_path(folder)
        if root.exists() and not root.is_dir():
            raise SettingsError("The review folder points to a file.")
        changes = data.get("keys", {})
        if not isinstance(changes, dict) or set(changes) - set(keys.BY_NAME):
            raise SettingsError("Invalid key entries.")
        for name, change in changes.items():
            if not isinstance(change, dict) or change.get("action") not in ("keep", "set", "remove"):
                raise SettingsError("Invalid key action.")
            if change["action"] == "set" and (not isinstance(change.get("value"), str) or not change["value"].strip()):
                raise SettingsError("A new key cannot be empty.")
        replace = data.get("replace", False)
        if not isinstance(replace, bool):
            raise SettingsError("Invalid replacement choice.")
        root.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryFile(dir=root):
                pass
        except OSError as exc:
            raise SettingsError("Cannot write to this review folder. Choose a folder you can write to.") from exc
        # Check registration conflicts before changing stored settings.
        if self.server.register:
            registration = clients.register_codex(self.server.command, replace=replace)
        else:
            registration = "Settings saved without registering a connection."
        with file_lock(keys.config_dir() / ".settings.lock"):
            keys.write_config({"NCBI_EMAIL": email.strip(), "MED_LIT_PROJECTS_DIR": str(root)})
            for name, change in changes.items():
                provider = keys.BY_NAME[name]
                if change["action"] == "set":
                    keys.set_key(provider, change["value"])
                elif change["action"] == "remove":
                    keys.remove_key(provider)
        if not self.server.register:
            return {"saved": True, "complete": True, "message": registration}
        try:
            result = anyio.run(check_connection, self.server.command)
        except (ExceptionGroup, OSError, TimeoutError, ValueError, RuntimeError, McpError):
            # Exceptions from the child must never echo environment or stored keys.
            return {"saved": True, "connected": False, "message": "Settings saved, but the connection check failed. Check your internet connection and select Retry connection. Reopen the installer if the problem continues."}
        return result | {"saved": True, "message": "med-lit is ready. Restart ChatGPT or start a new conversation, then ask it to start a review."}


def main(args: argparse.Namespace) -> int:
    with SetupServer(ui_command(args), register=not args.no_register, email=args.email, folder=args.projects_dir) as server:
        url = f"{server.origin}/#{server.token}"
        print(f"med-lit settings: {url}")  # noqa: T201
        print("Complete the form in your browser. This session closes after 15 minutes of inactivity.")  # noqa: T201
        def open_browser() -> None:
            try:
                opened = webbrowser.open(url)
            except (OSError, webbrowser.Error):
                opened = False
            if not opened:
                print("Could not open your browser. Open the settings link above to continue.")  # noqa: T201

        # Some custom browser commands wait for the browser to close. Keep serving
        # settings while the OS opens its window, including slow or blocking handlers.
        threading.Thread(target=open_browser, daemon=True).start()
        server.timeout = 1
        while not server.finished.is_set():
            server.handle_request()
            if not server.busy.locked() and time.monotonic() - server.last_activity > 900:
                return 1
        return 0 if server.succeeded else 1
