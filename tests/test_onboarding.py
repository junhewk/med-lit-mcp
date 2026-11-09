from __future__ import annotations

import json
import os
import unittest
import urllib.request
from unittest.mock import patch

import anyio
from helpers import Case
from mcp.shared.memory import create_connected_server_and_client_session

from med_lit_mcp import keys, onboarding, settings
from med_lit_mcp.server import mcp


class PrivateSettingsTests(Case, unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        onboarding.close_form()
        super().tearDown()

    async def test_private_form_saves_without_putting_values_or_access_token_in_mcp_results(self):
        os.environ.pop("NCBI_EMAIL", None)
        urls = []
        with patch.object(onboarding, "_open_browser", side_effect=lambda url: urls.append(url) or True):
            async with create_connected_server_and_client_session(mcp._mcp_server) as client:
                initial = await client.call_tool("med_lit_settings", {})
                self.assertFalse(initial.structuredContent["configured"])
                required = await client.call_tool("research_next", {})
                self.assertEqual(required.structuredContent["step"], "setup")
                opened = await client.call_tool("med_lit_settings", {"action": "open"})
                self.assertEqual(opened.structuredContent["form"], "open")
                origin, token = urls[0].split("/#")
                email, secret = "private.person@example.org", "private-api-key-7149"
                payload = {"email": email, "folder": str(self.root / "reviews"),
                           "keys": {"scopus": {"action": "set", "value": secret}}}
                req = urllib.request.Request(origin + "/api/save", data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json", "X-Med-Lit-Token": token})
                def submit():
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return json.load(response)

                self.assertTrue((await anyio.to_thread.run_sync(submit))["complete"])
                saved = await client.call_tool("med_lit_settings", {"action": "status"})
                self.assertTrue(saved.structuredContent["configured"])
                self.assertTrue(saved.structuredContent["providers"]["scopus"])
                transcript = json.dumps([r.model_dump(mode="json") for r in (initial, required, opened, saved)])
                for private in (email, secret, "7149", token, origin):
                    self.assertNotIn(private, transcript)
        self.assertEqual(os.environ["NCBI_EMAIL"], email)
        self.assertEqual(keys.read_keys()["SCOPUS_API_KEY"], secret)

    def test_changed_and_removed_keys_refresh_without_overwriting_client_environment(self):
        os.environ.pop("SCOPUS_API_KEY", None)
        provider = keys.BY_NAME["scopus"]
        keys.set_key(provider, "first-private-key")
        keys.load_into_environ()
        self.assertEqual(os.environ[provider.env], "first-private-key")
        keys.set_key(provider, "second-private-key")
        keys.load_into_environ()
        self.assertEqual(os.environ[provider.env], "second-private-key")
        keys.remove_key(provider)
        keys.load_into_environ()
        self.assertNotIn(provider.env, os.environ)
        os.environ[provider.env] = "client-supplied-key"
        keys.set_key(provider, "saved-key")
        keys.load_into_environ()
        self.assertEqual(os.environ[provider.env], "client-supplied-key")

    def test_browser_failure_keeps_private_url_out_of_result(self):
        with patch.object(onboarding, "_open_browser", return_value=False):
            result = onboarding.open_form()
        self.assertEqual(result["form"], "unavailable")
        self.assertNotIn("127.0.0.1", json.dumps(result))
        self.assertIn("--terminal", result["command"])

    def test_private_form_uses_the_connected_client_name(self):
        for client, label in (("claude-code", "Claude Code"), ("codex", "Codex"), ("hermes", "Hermes"), ("unknown", "your agent")):
            with self.subTest(client=client), patch.dict(os.environ, {"MED_LIT_CLIENT": client}), \
                    patch.object(onboarding, "_open_browser", return_value=True):
                onboarding.open_form()
                server = onboarding._form
                self.assertFalse(server.register)
                request = urllib.request.Request(server.origin + "/api/settings", headers={"X-Med-Lit-Token": server.token})
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(json.load(response)["client_name"], label)
                onboarding.close_form()

    def test_defaults_are_conversational_but_cannot_store_credentials(self):
        before = settings.load_settings(self.project.root)
        result = onboarding.defaults("interactive", {"search.per_source": 35})
        self.assertEqual(result["settings"]["search"]["per_source"], 35)
        self.assertEqual(settings.new_settings().search.per_source, 35)
        self.assertEqual(settings.load_settings(self.project.root), before)
        with self.assertRaises(ValueError):
            onboarding.defaults("interactive", {"NCBI_EMAIL": "secret@example.org"})
        self.assertEqual(keys.read_config(), {})

    async def test_bot_scope_rejects_configuration_tools_even_when_called_directly(self):
        with patch.dict(os.environ, {"MED_LIT_SCOPE": "bot"}):
            async with create_connected_server_and_client_session(mcp._mcp_server) as client:
                for name, args in (("med_lit_settings", {"action": "open"}),
                                   ("review_defaults", {"changes": {"bot.max_new_articles": 8}, "mode": "bot"}),
                                   ("bot_sources", {})):
                    result = await client.call_tool(name, args)
                    self.assertTrue(result.isError, name)
