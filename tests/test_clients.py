from __future__ import annotations

import json
import os
import shutil
import subprocess
from unittest.mock import patch

import tomlkit
from helpers import Case

from med_lit_mcp import cli_setup, clients, keys


class ClientTests(Case):
    def test_registration_preserves_other_configuration_and_is_idempotent(self) -> None:
        path = clients.codex_config()
        path.parent.mkdir()
        original = '# Keep this comment\nmodel = "example"\n\n[mcp_servers.other]\ncommand = "other" # Keep this too\n'
        path.write_text(original)
        command = [str(self.root / "folder with spaces" / "uv"), "tool", "run", "med-lit-mcp"]
        clients.register_codex(command)
        saved = path.read_text()
        value = tomlkit.parse(saved)
        self.assertIn('# Keep this comment', saved)
        self.assertIn('# Keep this too', saved)
        self.assertEqual(value["mcp_servers"]["other"]["command"], "other")
        self.assertEqual(value["mcp_servers"]["med-lit"], clients.registration(command))
        self.assertIn("already registered", clients.register_codex(command))
        self.assertEqual(path.read_text(), saved)

    def test_conflict_requires_replacement_and_invalid_toml_is_untouched(self) -> None:
        path = clients.codex_config()
        path.parent.mkdir()
        path.write_text('[mcp_servers.med-lit]\nurl = "https://example.org/mcp"\n')
        original = path.read_text()
        with self.assertRaises(clients.RegistrationConflict):
            clients.register_codex(["uvx", "med-lit-mcp"])
        self.assertEqual(path.read_text(), original)
        clients.register_codex(["uvx", "med-lit-mcp"], replace=True)
        self.assertNotIn("url", tomlkit.parse(path.read_text())["mcp_servers"]["med-lit"])
        path.write_text('invalid = [\n')
        with self.assertRaisesRegex(ValueError, "file was not changed"):
            clients.register_codex(["uvx", "med-lit-mcp"])
        self.assertEqual(path.read_text(), 'invalid = [\n')

    def test_policy_is_preserved_when_updating_timeouts(self) -> None:
        path = clients.codex_config()
        path.parent.mkdir()
        path.write_text('[mcp_servers.med-lit]\ncommand="uvx"\nargs=["med-lit-mcp"]\nenabled_tools=["guide"]\n')
        clients.register_codex(["uvx", "med-lit-mcp"])
        entry = tomlkit.parse(path.read_text())["mcp_servers"]["med-lit"]
        self.assertEqual(entry["enabled_tools"], ["guide"])
        self.assertEqual(entry["startup_timeout_sec"], 180)
        self.assertEqual(entry["tool_timeout_sec"], 600)

    def test_codex_cli_setup_keeps_keys_out_of_native_configuration(self) -> None:
        launcher = str(self.root / "uvx")
        keys.set_key(keys.BY_NAME["scopus"], "private-scopus-key")
        with patch.object(cli_setup.shutil, "which", side_effect=lambda name: {"uvx": launcher, "codex": "codex"}.get(name)):
            code = cli_setup.main(["setup", "--email", "me@example.org", "--yes", "--client", "codex"])
        self.assertEqual(code, 0)
        config = clients.codex_config().read_text()
        self.assertNotIn("private-scopus-key", config)
        self.assertNotIn("NCBI_EMAIL", config)
        self.assertEqual(keys.read_config()["NCBI_EMAIL"], "me@example.org")

    def test_codex_is_discovered_and_missing_explicit_client_fails(self) -> None:
        with patch.object(cli_setup.shutil, "which", side_effect=lambda name: {"uvx": "uvx", "codex": "codex"}.get(name)):
            self.assertEqual(cli_setup.main(["setup", "--email", "me@example.org", "--yes"]), 0)
        self.assertTrue(clients.codex_config().is_file())
        with patch.object(cli_setup.shutil, "which", side_effect=lambda name: "uvx" if name == "uvx" else None):
            self.assertEqual(cli_setup.main(["setup", "--email", "me@example.org", "--yes", "--client", "codex"]), 1)

    def test_real_codex_reads_the_generated_configuration(self) -> None:
        codex = shutil.which("codex")
        if not codex:
            self.skipTest("Codex CLI is not installed")
        command = [str(self.root / "uvx"), "med-lit-mcp"]
        clients.register_codex(command)
        result = subprocess.run([codex, "mcp", "get", "med-lit", "--json"], capture_output=True, text=True, timeout=20, env=os.environ, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = json.loads(result.stdout)
        self.assertEqual(entry["transport"]["command"], command[0])
        self.assertEqual(entry["startup_timeout_sec"], 180)
        self.assertEqual(entry["tool_timeout_sec"], 600)
