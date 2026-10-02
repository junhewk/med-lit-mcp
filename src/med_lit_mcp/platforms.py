"""Local file protection and process operations on Unix and Windows."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path


def protect(path: Path, *, directory: bool = False) -> None:
    """Give only the current user access, including an explicit Windows DACL."""
    if os.name != "nt":
        path.chmod(0o700 if directory else 0o600)
        return
    import pywintypes

    try:
        _protect_windows(path, directory=directory)
    except pywintypes.error as exc:
        raise PermissionError("Could not restrict local settings to the current Windows user.") from exc


def _protect_windows(path: Path, *, directory: bool) -> None:
    import ntsecuritycon
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    acl = win32security.ACL()
    inheritance = win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE if directory else 0
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, inheritance, ntsecuritycon.FILE_ALL_ACCESS, sid)
    win32security.SetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None,
    )


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        import pywintypes
        import win32api
        import win32con
        import win32process

        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, pid)
        except pywintypes.error as exc:
            return exc.winerror == 5  # Access denied means the process exists.
        try:
            return win32process.GetExitCodeProcess(handle) == win32con.STILL_ACTIVE
        finally:
            handle.Close()
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def stop_process_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            capture_output=True, timeout=30, check=False,
        )
    else:
        os.killpg(pid, signal.SIGTERM)
