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


class PiRegistrationTests(Case):
    def test_pi_registration_writes_only_its_entry_and_is_idempotent(self) -> None:
        path = clients.pi_config()
        self.assertEqual(path, (self.root / "pi-agent" / "mcp.json").resolve())
        path.parent.mkdir(parents=True)
        original = {"autoEnableCodemode": False, "mcpServers": {"other": {"command": "other", "exposure": "codemode"}}}
        path.write_text(json.dumps(original))
        command = ["/usr/bin/uvx", "med-lit-mcp"]
        self.assertIn("pi install", clients.register_pi(command))
        saved = json.loads(path.read_text())
        self.assertEqual(saved["autoEnableCodemode"], False)
        self.assertEqual(saved["mcpServers"]["other"], original["mcpServers"]["other"])
        self.assertEqual(saved["mcpServers"]["med-lit"], clients.pi_registration(command))
        self.assertEqual(saved["mcpServers"]["med-lit"]["exposure"], "direct")
        # The researcher's own change to the entry survives a repeated setup.
        saved["mcpServers"]["med-lit"]["exposure"] = "codemode"
        path.write_text(json.dumps(saved))
        self.assertIn("already registered", clients.register_pi(command))
        self.assertEqual(json.loads(path.read_text())["mcpServers"]["med-lit"]["exposure"], "codemode")

    def test_a_different_pi_registration_needs_confirmation_and_bad_json_is_untouched(self) -> None:
        path = clients.pi_config()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"mcpServers": {"med-lit": {"command": "old", "args": []}}}))
        with self.assertRaises(clients.RegistrationConflict):
            clients.register_pi(["uvx", "med-lit-mcp"])
        clients.register_pi(["uvx", "med-lit-mcp"], replace=True)
        self.assertEqual(json.loads(path.read_text())["mcpServers"]["med-lit"]["command"], "uvx")
        path.write_text("{not json")
        with self.assertRaises(clients.ClientConfigurationError):
            clients.register_pi(["uvx", "med-lit-mcp"])
        self.assertEqual(path.read_text(), "{not json")

    def test_pi_agent_directory_follows_its_environment_variable(self) -> None:
        with patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(self.root / "other-pi")}):
            clients.register_pi(["uvx", "med-lit-mcp"])
            self.assertTrue((self.root / "other-pi" / "mcp.json").is_file())
        with patch.dict(os.environ, {"HOME": str(self.root / "home")}):
            os.environ.pop("PI_CODING_AGENT_DIR")
            with patch.object(clients.Path, "home", return_value=self.root / "home"):
                self.assertEqual(clients.pi_config(), (self.root / "home" / ".pi" / "agent" / "mcp.json").resolve())

    def test_setup_registers_with_pi_when_found(self) -> None:
        with patch.object(cli_setup.shutil, "which", side_effect=lambda name: {"uvx": "/usr/bin/uvx", "pi": "/usr/bin/pi"}.get(name)):
            code = cli_setup.main(["setup", "--email", "me@example.org", "--yes", "--client", "pi"])
        self.assertEqual(code, 0)
        entry = json.loads(clients.pi_config().read_text())["mcpServers"]["med-lit"]
        self.assertEqual(entry["command"], "/usr/bin/uvx")
        self.assertNotIn("env", entry)  # keys stay in med-lit's own config folder
