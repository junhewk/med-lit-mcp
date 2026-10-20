"""The install scripts, run with stand-in uv/uvx that record how they were called."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


@unittest.skipIf(sys.platform == "win32", "bash script")
class InstallShTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp)
        self.bin = self.temp / "bin"
        self.bin.mkdir()
        self.log = self.temp / "calls.log"
        for name in ("uv", "uvx"):
            stub = self.bin / name
            stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.log}"\ncase "$*" in *--version*) echo "med-lit-mcp 9.9.9";; esac\n')
            stub.chmod(0o755)

    def run_script(self, *args: str, path: str | None = None) -> subprocess.CompletedProcess[str]:
        env = {"PATH": path if path is not None else f"{self.bin}:/usr/bin:/bin", "HOME": str(self.temp)}
        return subprocess.run(["bash", str(SCRIPTS / "install.sh"), *args], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60, check=False)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()

    def test_parses(self) -> None:
        subprocess.run(["bash", "-n", str(SCRIPTS / "install.sh")], check=True)

    def test_runs_the_newest_release_then_setup_then_check(self) -> None:
        result = self.run_script("--client", "pi", "--client", "codex", "--email", "me@example.org", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls(),
            [
                "uvx med-lit-mcp@latest --version",
                "uvx med-lit-mcp@latest setup --client pi --client codex --email me@example.org --yes",
                "uvx med-lit-mcp@latest --check",
            ],
        )

    def test_chatgpt_opens_the_browser_form(self) -> None:
        result = self.run_script("--chatgpt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("uvx med-lit-mcp@latest setup --ui --client chatgpt", self.calls())
        self.assertEqual(self.run_script("--chatgpt", "--yes").returncode, 2)

    def test_a_checkout_runs_through_uv_run(self) -> None:
        result = self.run_script("--dev", str(ROOT), "--no-register")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"uv run --quiet --directory {ROOT} med-lit-mcp setup --no-register --dev {ROOT}", self.calls())

    @unittest.skipIf(shutil.which("uv", path="/usr/bin:/bin"), "uv is installed system-wide here")
    def test_missing_uv_explains_how_to_install_it(self) -> None:
        result = self.run_script("--yes", path="/usr/bin:/bin")
        self.assertEqual(result.returncode, 1)
        self.assertIn("astral.sh/uv/install.sh", result.stdout)

    def test_unknown_options_are_refused(self) -> None:
        self.assertEqual(self.run_script("--frobnicate").returncode, 2)


@unittest.skipUnless(shutil.which("pwsh") or shutil.which("powershell"), "PowerShell not available")
class InstallPowerShellTests(unittest.TestCase):
    def test_parses(self) -> None:
        shell = shutil.which("pwsh") or shutil.which("powershell")
        script = str(SCRIPTS / "install-windows.ps1").replace("'", "''")
        check = (
            "$errors = $null; $tokens = $null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count) { $errors | ForEach-Object { Write-Output $_.Message }; exit 1 }"
        )
        result = subprocess.run([shell, "-NoProfile", "-Command", check], capture_output=True, text=True, timeout=120, env={**os.environ}, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
