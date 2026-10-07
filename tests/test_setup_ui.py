from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import anyio
import tomlkit
from helpers import Case

from med_lit_mcp import __version__, cli_setup, clients, keys
from med_lit_mcp.setup_ui import SettingsError, SetupServer, check_connection, ui_command


class BundledPackageTests(Case):
    def wheel(self, *, name='med-lit-mcp', version=__version__):
        package = self.root / 'persistent runtime 한글' / f'med_lit_mcp-{__version__}-py3-none-any.whl'
        package.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr(f'med_lit_mcp-{__version__}.dist-info/METADATA',
                            f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n')
        return package

    def test_bundled_package_registration_uses_persistent_wheel(self):
        package = self.wheel()
        command = ui_command(Namespace(dev=None, launcher=sys.executable, package=str(package)))
        self.assertEqual(command, [str(Path(sys.executable).resolve()),
                                  'tool', 'run', '--python', '3.11', '--from', str(package.resolve()), 'med-lit-mcp'])
        clients.register_codex(command)
        config = clients.codex_config().read_text(encoding='utf-8')
        self.assertIn(str(package.resolve()), tomlkit.parse(config)['mcp_servers']['med-lit']['args'])
        self.assertNotIn('--directory', config)
        self.assertNotIn(f'med-lit-mcp=={__version__}', config)

    def test_invalid_bundled_packages_are_rejected_before_registration(self):
        package = self.wheel()
        package.write_bytes(b'invalid wheel')
        with self.assertRaises(SettingsError):
            ui_command(Namespace(dev=None, launcher=sys.executable, package=str(package)))
        self.assertFalse(clients.codex_config().exists())
        self.assertEqual(keys.read_config(), {})

    def test_installer_package_flag_is_only_for_ui_and_not_source_development(self):
        package = self.wheel()
        with patch('med_lit_mcp.setup_ui.main', return_value=0) as setup:
            self.assertEqual(cli_setup.main(['setup', '--ui', '--client', 'chatgpt', '--package', str(package)]), 0)
            self.assertEqual(setup.call_args.args[0].package, str(package))
        for args in (['setup', '--package', str(package)],
                     ['setup', '--ui', '--package', str(package), '--dev', str(self.root)]):
            with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                cli_setup.main(args)
            self.assertEqual(error.exception.code, 2)


class SetupUITests(Case):
    def setUp(self) -> None:
        super().setUp()
        self.server = SetupServer([sys.executable, "-m", "med_lit_mcp"])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path: str, data: dict | None = None, **headers: str) -> dict:
        request = urllib.request.Request(
            self.server.origin + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"X-Med-Lit-Token": self.server.token, "Content-Type": "application/json", **headers},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def values(self, **changes) -> dict:
        return {"email": "me@example.org", "folder": str(self.root / "reviews"), "keys": {}, **changes}

    def test_settings_do_not_return_stored_keys(self) -> None:
        keys.set_key(keys.BY_NAME["scopus"], "never-display-this-key")
        data = self.request("/api/settings")
        self.assertNotIn("never-display-this-key", json.dumps(data))
        self.assertTrue(next(p for p in data["providers"] if p["name"] == "scopus")["set"])

    def test_save_preserves_keys_and_checks_real_mcp_connection(self) -> None:
        keys.set_key(keys.BY_NAME["scopus"], "existing-secret")
        result = self.request("/api/save", self.values(keys={"scopus": {"action": "keep"}}))
        self.assertTrue(result["connected"])
        self.assertEqual(result["tools"], 33)
        self.assertTrue(self.server.succeeded)
        self.assertTrue(self.server.finished.wait(timeout=1))
        self.assertEqual(keys.read_keys()["SCOPUS_API_KEY"], "existing-secret")
        self.assertNotIn("existing-secret", clients.codex_config().read_text())
        self.assertEqual(keys.read_config()["NCBI_EMAIL"], "me@example.org")

    def test_invalid_settings_have_no_effect(self) -> None:
        for data in (self.values(email="invalid"), self.values(keys={"scopus": {"action": "set", "value": ""}})):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/save", data)
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(keys.read_config(), {})
        self.assertFalse(clients.codex_config().exists())

    def test_wrong_token_origin_and_host_cannot_read_or_write(self) -> None:
        for headers in ({"X-Med-Lit-Token": "wrong"}, {"Origin": "https://example.org"}, {"Host": "example.org"}):
            for path, data in (("/api/settings", None), ("/api/save", self.values())):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.request(path, data, **headers)
                self.assertEqual(error.exception.code, 403)
        self.assertEqual(keys.read_config(), {})

    def test_conflict_is_presented_before_settings_are_changed(self) -> None:
        clients.register_codex(["different", "server"])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/save", self.values())
        self.assertEqual(error.exception.code, 409)
        self.assertTrue(json.load(error.exception)["conflict"])
        self.assertEqual(keys.read_config(), {})
        with patch("med_lit_mcp.setup_ui.check_connection", AsyncMock(return_value={"connected": True})):
            result = self.request("/api/save", self.values(replace=True))
        self.assertTrue(result["connected"])

    def test_removing_one_key_keeps_others(self) -> None:
        keys.set_key(keys.BY_NAME["ncbi"], "delete-me")
        keys.set_key(keys.BY_NAME["scopus"], "keep-me")
        with patch("med_lit_mcp.setup_ui.check_connection", AsyncMock(return_value={"connected": True})):
            self.request("/api/save", self.values(keys={"ncbi": {"action": "remove"}}))
        self.assertEqual(keys.read_keys(), {"SCOPUS_API_KEY": "keep-me"})

    def test_connection_failure_is_retryable_and_does_not_echo_secrets(self) -> None:
        with patch("med_lit_mcp.setup_ui.check_connection", AsyncMock(side_effect=RuntimeError("private-token"))):
            result = self.request("/api/save", self.values())
        self.assertTrue(result["saved"])
        self.assertFalse(result["connected"])
        self.assertNotIn("private-token", json.dumps(result))
        self.assertFalse(self.server.finished.is_set())
        with patch("med_lit_mcp.setup_ui.check_connection", AsyncMock(return_value={"connected": True})):
            self.assertTrue(self.request("/api/save", self.values())["connected"])

    def test_key_check_does_not_save_and_redacts_provider_messages(self) -> None:
        with patch.object(keys, "test_key", return_value=(False, "Rejected secret+123 and secret%2B123")):
            result = self.request("/api/test-key", {"name": "scopus", "value": "  secret+123  "})
        self.assertFalse(result["ok"])
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(keys.read_keys(), {})

    def test_broken_settings_return_a_useful_error_without_contents(self) -> None:
        directory = keys.config_dir()
        directory.mkdir()
        (directory / keys.KEYS_FILE).write_text('broken-private-key')
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/settings")
        self.assertEqual(error.exception.code, 400)
        result = json.load(error.exception)
        self.assertIn("reopen", result["error"])
        self.assertNotIn("broken-private-key", json.dumps(result))

    def test_cancel_changes_nothing(self) -> None:
        self.assertTrue(self.request("/api/cancel", {})["cancelled"])
        self.assertTrue(self.server.finished.wait(timeout=1))
        self.assertFalse(self.server.succeeded)
        self.assertFalse(clients.codex_config().exists())

    def test_native_handshake(self) -> None:
        result = anyio.run(check_connection, [sys.executable, "-m", "med_lit_mcp"])
        self.assertEqual(result, {"connected": True, "tools": 33})

    def test_folder_selection_and_cancel_leave_settings_unchanged(self) -> None:
        folder = str(self.root / 'reviews with spaces 한글')
        with patch('med_lit_mcp.setup_ui.choose_folder', return_value=folder):
            self.assertEqual(self.request('/api/choose-folder', {'folder': ''}), {'folder': folder, 'cancelled': False})
        with patch('med_lit_mcp.setup_ui.choose_folder', return_value=None):
            self.assertTrue(self.request('/api/choose-folder', {'folder': folder})['cancelled'])
        self.assertEqual(keys.read_config(), {})
        self.assertFalse(clients.codex_config().exists())

    def test_unwritable_folder_and_malformed_client_config_keep_credentials(self) -> None:
        with patch('med_lit_mcp.setup_ui.tempfile.TemporaryFile', side_effect=PermissionError):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/save', self.values())
        self.assertIn('Cannot write', json.load(error.exception)['error'])
        config = clients.codex_config()
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('broken = [')
        with self.assertRaises(urllib.error.HTTPError):
            self.request('/api/save', self.values())
        self.assertEqual(keys.read_config(), {})
        self.assertEqual(config.read_text(), 'broken = [')


class BrowserStartupTests(Case):
    def test_blocking_browser_launcher_does_not_block_settings_http(self):
        from argparse import Namespace
        from med_lit_mcp import setup_ui
        browser_started = threading.Event()
        release_browser = threading.Event()
        server_created = threading.Event()
        server_holder = []
        result = []
        real_server = SetupServer

        def create_server(*args, **kwargs):
            server = real_server(*args, **kwargs)
            server_holder.append(server)
            server_created.set()
            return server

        def blocked_browser(url):
            browser_started.set()
            release_browser.wait(timeout=5)
            return True

        args = Namespace(no_register=True, email='test@example.org', projects_dir=str(self.root/'reviews'))
        with patch.object(setup_ui, 'SetupServer', side_effect=create_server), patch.object(setup_ui, 'ui_command', return_value=[sys.executable]), patch.object(setup_ui.webbrowser, 'open', side_effect=blocked_browser):
            worker = threading.Thread(target=lambda: result.append(setup_ui.main(args)), daemon=True)
            worker.start()
            self.addCleanup(release_browser.set)
            self.assertTrue(server_created.wait(timeout=2))
            self.assertTrue(browser_started.wait(timeout=2))
            server = server_holder[0]
            try:
                with urllib.request.urlopen(server.origin+'/', timeout=1) as response:
                    self.assertIn('med-lit', response.read().decode('utf-8'))
                request = urllib.request.Request(server.origin+'/api/settings', headers={'X-Med-Lit-Token':server.token})
                with urllib.request.urlopen(request, timeout=1) as response:
                    self.assertEqual(json.load(response)['email'], 'test@example.org')
                self.assertFalse(release_browser.is_set())
                request = urllib.request.Request(server.origin+'/api/cancel', data=b'{}', headers={'Content-Type':'application/json','X-Med-Lit-Token':server.token})
                with urllib.request.urlopen(request,timeout=2) as response:
                    self.assertTrue(json.load(response)['cancelled'])
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(result, [1])
            finally:
                release_browser.set()
                server.finished.set()
                worker.join(timeout=3)
