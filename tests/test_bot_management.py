from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from helpers import QUESTION, Case, record
from mcp.shared.memory import create_connected_server_and_client_session
from test_bot import FAKE_HERMES

from med_lit_mcp import bot, bot_management, cli_bot, projects, settings
from med_lit_mcp.server import mcp


@unittest.skipIf(os.name == "nt", "Hermes fixture uses a Unix shell")
class BotManagementTests(Case, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.run_id = self.make_run([record(1)])
        profile = self.root / "hermes" / "profiles" / "medlitbot"
        profile.mkdir(parents=True)
        (profile / "profile.yaml").write_text("description: test\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        hermes = self.bin / "hermes"
        hermes.write_text(FAKE_HERMES.format(log=self.root / "hermes.log", profile=profile))
        hermes.chmod(0o755)
        os.environ["PATH"] = f"{self.bin}:{os.environ['PATH']}"

    def create(self):
        return bot_management.create("Weekly watch", self.run_id, "0 9 * * 1", confirmed=True)

    def test_preview_has_no_side_effects_and_invalid_values_never_create_a_bot(self):
        preview = bot_management.create("Weekly watch", self.run_id, "0 9 * * 1")
        self.assertTrue(preview["ask_researcher"])
        self.assertEqual(len(projects.list_projects()), 1)
        self.assertFalse((self.root / "hermes.log").exists())
        for schedule in ("0 29 * * *", "99 9 * * 1", "0 9 * * 9", "* * * * *"):
            with self.assertRaises(ValueError):
                bot_management.create("Bad", self.run_id, schedule, confirmed=True)
        with self.assertRaises(ValueError):
            bot_management.create("Bad", self.run_id, "0 9 * * 1", {"bot.max_new_articles": 21}, True)
        with self.assertRaises(ValueError):
            bot_management.create("Bad", self.run_id, "0 9 * * 1", {"bot": {"max_new_articles": 21}}, True)
        self.assertEqual(len(projects.list_projects()), 1)

    async def test_create_and_manage_through_real_mcp_dispatch(self):
        async with create_connected_server_and_client_session(mcp._mcp_server) as client:
            sources = await client.call_tool("bot_sources", {})
            self.assertIn(self.run_id, json.dumps(sources.model_dump(mode="json")))
            result = await client.call_tool("create_bot", {
                "name": "Weekly watch", "from_run": self.run_id, "schedule": "0 9 * * 1", "confirmed": True,
            })
            self.assertFalse(result.isError, result)
            self.assertEqual(result.structuredContent["job_id"], "0123456789ab")
            updated = await client.call_tool("manage_bot", {
                "project": "Weekly watch", "action": "update", "schedule": "30 8 * * *",
                "changes": {"bot.max_new_articles": 10}, "include": ["communication training"],
                "exclude": ["benchmarks"], "confirmed": True,
            })
            self.assertFalse(updated.isError, updated)
            self.assertEqual(updated.structuredContent["schedule"], "30 8 * * *")
            self.assertEqual(updated.structuredContent["settings"]["bot"]["max_new_articles"], 10)
            self.assertEqual(updated.structuredContent["criteria"]["version"], 2)
            for action, expected in (("pause", "paused"), ("resume", "active"), ("archive", "archived")):
                changed = await client.call_tool("manage_bot", {"project": "Weekly watch", "action": action, "confirmed": True})
                self.assertEqual(changed.structuredContent["status"], expected)
            self.assertTrue(projects.get_project("Weekly watch").root.exists())
        log = (self.root / "hermes.log").read_text()
        self.assertIn("--env MED_LIT_SCOPE=bot", log)
        self.assertIn(" MED_LIT_CONFIG_DIR=", log)
        registration = next(line for line in log.splitlines() if "mcp add med-lit" in line)
        self.assertEqual(registration.count("--env"), 1)
        self.assertIn("--deliver bot-chat", log)
        self.assertIn("--schedule 30 8 * * *", log)

    def test_repeated_creation_does_not_duplicate_the_scheduled_job(self):
        self.create()
        repeat = self.create()
        self.assertFalse(repeat["created"])
        self.assertEqual((self.root / "hermes.log").read_text().count("cron create"), 1)

    def test_schedule_failure_keeps_saved_settings_unchanged(self):
        self.create()
        owner = projects.get_project("Weekly watch")
        before = settings.load_settings(owner.root)
        with patch.object(cli_bot, "job_command", side_effect=SystemExit("scheduler unavailable")), \
                self.assertRaisesRegex(ValueError, "scheduler unavailable"):
            bot_management.manage("Weekly watch", "update", "15 7 * * *", {"bot.max_new_articles": 10}, confirmed=True)
        self.assertEqual(bot.read_state(owner)["schedule"], "0 9 * * 1")
        self.assertEqual(settings.load_settings(owner.root), before)

    def test_failed_creation_can_be_retried_without_duplicate_project(self):
        with patch.object(cli_bot, "ensure_profile", side_effect=SystemExit("profile unavailable")), self.assertRaises(ValueError):
            self.create()
        owner = projects.get_project("Weekly watch")
        self.assertEqual(bot.read_state(owner)["status"], "paused")
        self.assertTrue(self.create()["created"])
        self.assertEqual(len(projects.list_projects()), 2)

    def test_retry_recovers_a_created_routine_without_creating_another(self):
        with patch.object(cli_bot, "find_job", side_effect=[None, "0123456789ab"]), \
                patch.object(cli_bot, "create_job", side_effect=SystemExit("lost response")) as create:
            with self.assertRaises(ValueError):
                self.create()
            recovered = self.create()
        self.assertEqual(recovered["job_id"], "0123456789ab")
        self.assertEqual(create.call_count, 1)

    def test_existing_routine_lookup_matches_the_full_name(self):
        from subprocess import CompletedProcess

        listing = "  0123456789ab [active]\n    Name:      [bot:medlitbot] Weekly watch\n"
        with patch.object(cli_bot, "_hermes", return_value=CompletedProcess([], 0, listing, "")):
            self.assertEqual(cli_bot.find_job("hermes", "Weekly watch"), "0123456789ab")
            self.assertIsNone(cli_bot.find_job("hermes", "Weekly"))

    def test_bot_keeps_the_connected_servers_launcher(self):
        command = ["/a path/uv", "run", "--directory", "/checkout", "med-lit-mcp"]
        with patch.dict(os.environ, {"MED_LIT_LAUNCH_COMMAND": json.dumps(command)}):
            self.assertEqual(bot_management.launcher(), command)

    def test_bot_scope_rejects_all_management_and_preview_does_not_apply(self):
        self.create()
        preview = bot_management.manage("Weekly watch", "pause")
        self.assertTrue(preview["ask_researcher"])
        self.assertEqual(bot.read_state(projects.get_project("Weekly watch"))["status"], "active")
        with patch.dict(os.environ, {"MED_LIT_SCOPE": "bot"}):
            for function, args in ((bot_management.sources, ()), (bot_management.create, ("Other", self.run_id, "0 5 * * *")),
                                   (bot_management.manage, ("Weekly watch", "pause"))):
                with self.assertRaisesRegex(ValueError, "normal researcher chat"):
                    function(*args)

    def test_question_changes_are_versioned_and_retries_keep_the_same_version(self):
        self.create()
        changed_question = {**QUESTION, "question": "How do language models help shared decisions?"}
        result = bot_management.manage("Weekly watch", "update", question=changed_question, confirmed=True)
        self.assertEqual(result["question"]["version"], 2)
        owner = projects.get_project("Weekly watch")
        self.assertTrue(bot.read_state(owner)["backfill"])
        repeat = bot_management.manage("Weekly watch", "update", question=changed_question, confirmed=True)
        self.assertEqual(repeat["question"]["version"], 2)
        with self.assertRaisesRegex(ValueError, "at least one"):
            bot_management.manage("Weekly watch", "update", sources=[], confirmed=True)

    def test_archived_bot_cannot_be_recreated_by_replaying_a_creation_call(self):
        self.create()
        bot_management.manage("Weekly watch", "archive", confirmed=True)
        with self.assertRaisesRegex(ValueError, "archived"):
            self.create()
