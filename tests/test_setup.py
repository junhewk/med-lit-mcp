from __future__ import annotations

import json
import os
import stat
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import Case

from med_lit_mcp import cli_setup, keys, settings

FAKE_CLIENT = """#!/bin/sh
echo "$0 $*" >> "{log}"
cat >> "{log}.stdin"
case "$1 $2" in
  "mcp list") echo "  other-server   /usr/bin/x   all   enabled" ;;
  "mcp add") echo "  Saved 'med-lit'" ;;
  "mcp get") exit 1 ;;
esac
"""


class KeyStoreTests(Case):
    def test_store_is_private_and_the_environment_wins(self) -> None:
        scopus = keys.BY_NAME["scopus"]
        path = keys.set_key(scopus, "  scopus-secret-1234 ")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(json.loads(path.read_text()), {"SCOPUS_API_KEY": "scopus-secret-1234"})
        keys.write_config({"NCBI_EMAIL": "stored@example.org", "MED_LIT_PROJECTS_DIR": "/data/reviews"})
        with patch.dict(os.environ, {"NCBI_EMAIL": "client@example.org"}):
            os.environ.pop("SCOPUS_API_KEY", None)
            os.environ.pop("MED_LIT_PROJECTS_DIR", None)
            keys.load_into_environ()
            self.assertEqual(os.environ["NCBI_EMAIL"], "client@example.org")  # the client's value wins
            self.assertEqual(os.environ["SCOPUS_API_KEY"], "scopus-secret-1234")
            self.assertEqual(os.environ["MED_LIT_PROJECTS_DIR"], "/data/reviews")
            row = next(r for r in keys.status() if r["key"] == "scopus")
            self.assertEqual((row["source"], row["value"]), ("key store", "…1234"))
        self.assertTrue(keys.remove_key(scopus))
        self.assertFalse(keys.remove_key(scopus))

    def test_key_checks_distinguish_invalid_from_unauthorized(self) -> None:
        scopus = keys.BY_NAME["scopus"]
        cases = [
            ((200, "{}"), True, "works"),
            ((401, '{"statusText":"Invalid API Key"}'), False, "invalid"),
            ((403, '{"statusText":"AUTHORIZATION_ERROR"}'), False, "institution"),
        ]
        for response, ok, words in cases:
            with patch.object(keys, "_request", return_value=response) as request:
                result = keys.test_key(scopus, "key", insttoken="token")
            self.assertEqual(result[0], ok)
            self.assertIn(words, result[1])
            self.assertEqual(request.call_args.args[1]["X-ELS-Insttoken"], "token")
        with patch.object(keys, "_request", return_value=(400, '{"error":"API key invalid"}')):
            self.assertEqual(keys.test_key(keys.BY_NAME["ncbi"], "bad")[0], False)
        with patch.object(keys, "_request", return_value=(429, "slow down")):
            self.assertIsNone(keys.test_key(keys.BY_NAME["openalex"], "k")[0])


class SetupTests(Case):
    def fake_clients(self) -> Path:
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name in ("hermes", "claude", "uvx"):
            script = bin_dir / name
            script.write_text(FAKE_CLIENT.format(log=self.root / f"{name}.log"))
            script.chmod(0o755)
        return bin_dir

    @unittest.skipIf(os.name == "nt", "Hermes and Claude fake executables use a Unix shell")
    def test_unattended_setup_saves_settings_and_registers_every_client(self) -> None:
        bin_dir = self.fake_clients()
        with patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ['PATH']}"}):
            code = cli_setup.main(["setup", "--email", "me@example.org", "--projects-dir", "~/reviews", "--yes"])
        self.assertEqual(code, 0)
        stored = json.loads((keys.config_dir() / keys.CONFIG_FILE).read_text())
        self.assertEqual(stored["NCBI_EMAIL"], "me@example.org")
        self.assertEqual(stored["MED_LIT_PROJECTS_DIR"], str(Path.home() / "reviews"))
        hermes = (self.root / "hermes.log").read_text()
        self.assertIn(f"mcp add med-lit --command {bin_dir}/uvx --connect-timeout 180 --args med-lit-mcp", hermes)
        self.assertNotIn("NCBI_EMAIL", hermes)  # settings and keys stay in the med-lit config folder
        claude = (self.root / "claude.log").read_text()
        self.assertIn(f"mcp add med-lit --scope user -- {bin_dir}/uvx med-lit-mcp", claude)

    def test_interactive_keys_are_hidden_tested_and_only_kept_when_valid(self) -> None:
        answers = iter(["n", "n", "n", "y", "n"])  # add a Scopus key only
        console = cli_setup.Console(False, ask=lambda _prompt: next(answers), secret=lambda _prompt: "scopus-key-9999")
        with patch.object(keys, "test_key", return_value=(False, "Elsevier rejected the key as invalid")):
            cli_setup.ask_key(keys.BY_NAME["scopus"], console)
        self.assertEqual(keys.read_keys(), {})  # rejected and not confirmed
        with patch.object(keys, "test_key", return_value=(True, "Scopus search works from this machine")):
            cli_setup.ask_key(keys.BY_NAME["scopus"], console)
        self.assertEqual(keys.read_keys(), {"SCOPUS_API_KEY": "scopus-key-9999"})

    def test_no_register_only_saves_settings(self) -> None:
        bin_dir = self.fake_clients()
        with patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ['PATH']}"}):
            cli_setup.main(["setup", "--email", "me@example.org", "--yes", "--no-register"])
        self.assertTrue((keys.config_dir() / keys.CONFIG_FILE).is_file())
        self.assertFalse((self.root / "hermes.log").exists())

    def test_dev_checkout_runs_through_uv_run(self) -> None:
        command = cli_setup.server_command(str(self.root))
        self.assertEqual(command[1:], ["run", "--quiet", "--directory", str(self.root.resolve()), "med-lit-mcp"])

    def test_default_settings_are_asked_validated_and_saved(self) -> None:
        answers = iter(["2020-2010", "2020-", "40", "", "30", ""])  # the first years answer is rejected
        console = cli_setup.Console(False, ask=lambda _prompt: next(answers))
        cli_setup.ask_defaults(console)
        saved = settings.user_defaults()["interactive"]
        self.assertEqual(saved["search"], {"years": "2020-", "per_source": 40, "preprint_allow": False})
        self.assertEqual((saved["fetch"]["limit"], saved["wiki"]["max_pages"]), (30, 3))
        self.assertEqual(settings.new_settings().search.years, "2020-")
