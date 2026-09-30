from __future__ import annotations

import json
import os
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from helpers import QUESTION, Case, record
from mcp.shared.memory import create_connected_server_and_client_session
from test_search import FakeProcess, write_results

from med_lit_mcp import (
    bot,
    cli_bot,
    cli_setup,
    projects,
    screening,
    search,
    settings,
    wiki,
)
from med_lit_mcp.server import mcp
from med_lit_mcp.store import RUN_FILE, atomic_json, database, read_json

CRITERIA = {"include": ["language models in decisions"], "exclude": ["benchmarks"]}
TEXT = (
    "Large language models (LLMs) supported shared decision-making (SDM) in oncology clinics. "
    "Clinicians reviewed the LLM output with patients."
)


class BotCase(Case):
    def setUp(self) -> None:
        super().setUp()
        self.found: list[dict[str, Any]] = []
        self.bot = bot.create_bot("Watch", QUESTION, ["pubmed", "openalex"], CRITERIA, schedule="0 5 * * *")
        self.state = bot.read_state(self.bot)
        self.run_id = self.state["run_id"]
        self.path = self.bot.runs / self.run_id
        self.project = self.bot  # add_text stores article text in this project's database

    def launch(self, argv: list[str], **kwargs: Any) -> FakeProcess:
        output = Path(argv[argv.index("--output") + 1])
        write_results(output, self.found or [])
        atomic_json(output / "summary.json", {"source_failures": {}, "records_by_source": {"pubmed": len(self.found)}})
        if not self.found:
            (output / "results.jsonl").write_text("")
        # The engine ranks the last record first.
        ranked = "".join(json.dumps(r) + "\n" for r in reversed(self.found))
        (output / "ranked-results.jsonl").write_text(ranked)
        self.argv = argv
        return FakeProcess(argv, write=False, **kwargs)

    def start(self) -> dict[str, Any]:
        with patch.object(search.subprocess, "Popen", side_effect=self.launch):
            return bot.bot_start("Watch")

    def manifest(self) -> dict[str, Any]:
        return read_json(self.path / RUN_FILE)

    def screen(self, decisions: dict[str, str]) -> None:
        manifest = self.manifest()
        entries = []
        for uid, decision in decisions.items():
            title = manifest["articles"][uid]["record"]["title"]
            entries.append({"uid": uid, "decision": decision, "reason": "per criteria", "evidence": title if decision != "uncertain" else ""})
        screening.record_decisions(self.run_id, manifest["selection_revision"], entries)



class BotRunTests(BotCase):
    def test_a_new_bot_starts_empty_with_frozen_question_and_criteria(self) -> None:
        self.assertEqual(self.bot.mode, "bot")
        config = settings.load_settings(self.bot.root)
        self.assertEqual((config.search.per_source, config.bot.lookback_days), (100, 90))
        manifest = self.manifest()
        self.assertEqual((manifest["articles"], manifest["selection_revision"]), ({}, 1))
        self.assertEqual(manifest["selection_criteria"], CRITERIA)
        self.assertEqual(self.state["questions"][0]["sources"], ["pubmed", "openalex"])
        with self.assertRaisesRegex(ValueError, "europepmc"):
            bot.create_bot("Other", QUESTION, ["europepmc"], CRITERIA, schedule="0 5 * * *")

    def test_a_run_screens_fetches_and_reports_within_its_caps(self) -> None:
        settings.write_settings(
            self.bot.root, settings.apply_changes(settings.load_settings(self.bot.root), {"bot.max_new_articles": 3})
        )
        self.found = [record(n, title=f"Study {n}") for n in range(1, 6)]
        started = self.start()
        self.assertEqual(started["search"], {"search_status": "complete"})
        window = (date.today() - timedelta(days=90)).isoformat()  # noqa: DTZ011
        self.assertEqual(read_json(self.path / "question-update-1.json")["filters"]["from_date"], window)
        self.assertEqual(self.argv[self.argv.index("--limit-per-source") + 1], "100")
        manifest = self.manifest()
        self.assertEqual(sorted(manifest["articles"]), ["pubmed:3", "pubmed:4", "pubmed:5"])  # best-ranked three
        self.assertEqual(manifest["updates"][0]["dropped_over_cap"], ["pubmed:2", "pubmed:1"])
        self.assertEqual(self.start()["busy"], True)  # no overlapping runs

        step = bot.bot_next("Watch")
        self.assertEqual((step["step"], step["pending"]), ("screen", 3))
        self.screen({"pubmed:5": "include", "pubmed:4": "exclude", "pubmed:3": "uncertain"})
        self.assertEqual(bot.bot_next("Watch")["step"], "fetch")
        self.add_text(self.run_id, "pubmed:5", TEXT)
        step = bot.bot_next("Watch")
        self.assertEqual((step["step"], step["stage"]), ("wiki", "extract"))
        self.assertIn("pubmed:5", step["tasks"][0])

        finished = bot.bot_finish("Watch")
        report = Path(finished["report"])
        self.assertEqual(report.parent, self.bot.root / "updates")
        text = report.read_text()
        self.assertIn("New articles: 3; 2 more were over the cap of 3", text)
        self.assertIn("included 1, excluded 1, uncertain 1", text)
        self.assertIn("Waiting for your decision (1)", text)
        self.assertIn("1 to extract", text)
        self.assertTrue(finished["message"].startswith(f"# Watch: update {date.today().isoformat()}"))  # noqa: DTZ011
        state = bot.read_state(self.bot)
        self.assertIsNone(state["active"])
        self.assertEqual(state["history"][-1]["new_articles"], 3)

    def test_a_quiet_run_is_silent_and_known_articles_are_skipped(self) -> None:
        self.found = [record(1)]
        self.start()
        self.screen({"pubmed:1": "exclude"})
        self.assertEqual(bot.bot_next("Watch")["step"], "finish")
        self.assertNotEqual(bot.bot_finish("Watch")["message"], bot.SILENT)
        self.start()  # finds the same article again
        self.assertEqual(self.manifest()["updates"][1]["new_articles"], 0)
        self.assertEqual(bot.bot_next("Watch")["step"], "finish")
        self.assertEqual(bot.bot_finish("Watch"), {"message": bot.SILENT, "note": "Nothing new this run"})
        self.assertEqual(len(list((self.bot.root / "updates").glob("*.md"))), 1)

    def test_new_criteria_rescreen_and_withdraw_articles_from_the_wiki(self) -> None:
        self.found = [record(1, title="Oncology SDM")]
        self.start()
        self.screen({"pubmed:1": "include"})
        digest = self.add_text(self.run_id, "pubmed:1", TEXT)
        wiki.record_extraction(
            self.run_id, "pubmed:1", digest, 0,
            [{"name": "large language models", "entity_type": "TECHNOLOGY", "description": "LLMs.", "mention": "Large language models"}], [],
        )
        bot.bot_finish("Watch")
        bot.set_criteria(self.bot, ["oncology only"], ["language models"])
        self.assertEqual(bot.read_state(self.bot)["criteria"][-1]["version"], 2)
        self.start()
        self.assertEqual(bot.bot_next("Watch")["pending"], 1)  # everything is screened again
        self.screen({"pubmed:1": "exclude"})
        self.assertEqual(bot.bot_next("Watch")["step"], "finish")
        with database(self.bot.db) as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM articles WHERE uid='pubmed:1'").fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM kg_mentions").fetchone()[0], 0)
        item = self.manifest()["articles"]["pubmed:1"]
        self.assertEqual((item["fetch"], item["screening_history"][0]["decision"]), ("pending", "include"))
        self.assertIn("pubmed:1", bot.bot_finish("Watch")["message"])

    def test_a_new_question_backfills_from_the_start_date(self) -> None:
        self.start()
        bot.bot_finish("Watch")
        changed = {**QUESTION, "question": "How are chatbots used in shared decision making?"}
        result = bot.set_question(self.bot, changed, ["pubmed"])
        start = date.fromisoformat(self.state["start_date"]) - timedelta(days=90)
        self.assertEqual(result["backfill_from"], start.isoformat())
        self.assertIn("chatbots", read_json(self.path / "question.json")["question"])
        self.start()
        self.assertEqual(self.manifest()["updates"][-1]["from_date"], start.isoformat())
        self.assertEqual(self.argv[self.argv.index("--sources") + 1], "pubmed")
        bot.bot_finish("Watch")
        self.assertIsNone(bot.read_state(self.bot)["backfill"])

    def test_a_run_whose_process_ended_releases_the_project(self) -> None:
        self.start()
        self.assertTrue(self.start()["busy"])  # this process is alive and has just written
        state_path = self.bot.work / bot.BOT_FILE
        state = read_json(state_path)
        state["active"]["pid"] = 2**22 + 12345  # no such process
        atomic_json(state_path, state)
        self.assertNotIn("busy", self.start())
        self.assertIn("abandoned", bot.read_state(self.bot)["history"][-1]["outcome"])

    def test_the_server_holds_a_run_to_its_page_cap(self) -> None:
        settings.write_settings(
            self.bot.root, settings.apply_changes(settings.load_settings(self.bot.root), {"bot.max_syntheses": 0})
        )
        self.assertIsNone(bot.synthesis_allowance(self.bot))  # no run in progress
        self.start()
        self.assertEqual(bot.synthesis_allowance(self.bot), 0)
        self.assertTrue(wiki.next_synthesis(self.bot)["done"])
        with self.assertRaisesRegex(ValueError, "max_syntheses"):
            wiki.record_synthesis(self.bot, 1, "digest", "s", "x" * 300, ["a"], [])

    def test_paused_bots_do_not_run(self) -> None:
        bot.set_status(self.bot, "paused")
        with self.assertRaisesRegex(ValueError, "paused"):
            self.start()

    def test_synthesis_tasks_follow_the_allowance(self) -> None:
        with patch.object(wiki, "_synthesis_queue", return_value=(list(range(12)), 0)):
            run_id = self.make_run([record(1)])
            self.add_text(run_id, "pubmed:1", TEXT)
            with database(self.project.db) as conn:
                conn.execute("UPDATE articles SET kg_sha256=content_sha256")
                conn.commit()
            plan = wiki.work_plan(run_id, max_syntheses=7)
            self.assertEqual([t.split("Up to ")[1][:1] for t in plan["tasks"]], ["5", "2"])
            self.assertEqual(wiki.work_plan(run_id, max_syntheses=0)["step"], "export")
            self.assertEqual(len(wiki.work_plan(run_id)["tasks"]), 3)


class BotScopeTests(BotCase, unittest.IsolatedAsyncioTestCase):
    async def test_interactive_chat_can_read_a_bot_but_not_steer_it(self) -> None:
        async with create_connected_server_and_client_session(mcp._mcp_server) as client:
            question = await client.call_tool("validate_question", {"question": QUESTION, "project": "Watch"})
            refused = await client.call_tool("start_search", {"question_id": question.structuredContent["question_id"]})
            self.assertTrue(refused.isError)
            self.assertIn("setup bot --edit", refused.content[0].text)
            criteria = await client.call_tool("set_screening_criteria", {"run_id": self.run_id, "include": ["x"], "exclude": [], "replace": True})
            self.assertTrue(criteria.isError)
            listed = (await client.call_tool("list_projects", {})).structuredContent["result"]
            self.assertEqual({row["project"]: row["mode"] for row in listed}, {"Test review": "interactive", "Watch": "bot"})

    async def test_the_bot_server_sees_only_bots_and_leaves_uncertain_articles_alone(self) -> None:
        with patch.dict(os.environ, {"MED_LIT_SCOPE": "bot"}):
            self.assertEqual([row["project"] for row in projects.list_projects()], ["Watch"])
            with self.assertRaisesRegex(ValueError, "Unknown project"):
                projects.get_project("Test review")
            async with create_connected_server_and_client_session(mcp._mcp_server) as client:
                review = await client.call_tool(
                    "review_article", {"run_id": self.run_id, "uid": "pubmed:1", "decision": "include", "reason": "x"}
                )
                self.assertIn("never decides", review.content[0].text)
                created = await client.call_tool("create_project", {"name": "Sneaky"})
                self.assertTrue(created.isError)


FAKE_HERMES = """#!/bin/sh
echo "$*" >> "{log}"
[ "$1" = "-p" ] && shift 2
case "$1 $2" in
  "profile list") echo " default   model   running" ;;
  "mcp list") printf '  Name   Transport\\n  ────   ─────\\n  med-lit   uv run   all\\n  github   npx   all\\n' ;;
  "mcp add") echo "  Saved '$3'" ;;
  "cron create") echo "Created job 0123456789ab" ;;
esac
"""


class SetupBotTests(Case):
    def test_setup_bot_copies_a_tried_search_and_schedules_it_in_the_bot_profile(self) -> None:
        run_id = self.make_run([record(1)])
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name, body in (("hermes", FAKE_HERMES), ("uvx", "#!/bin/sh\n")):
            (bin_dir / name).write_text(body.format(log=self.root / "hermes.log"))
            (bin_dir / name).chmod(0o755)
        with patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ['PATH']}"}):
            code = cli_setup.main(["setup", "bot", "--from-run", run_id, "--name", "SDM watch", "--schedule", "30 5 * * 1", "--yes"])
        self.assertEqual(code, 0)
        project = projects.get_project("SDM watch")
        state = bot.read_state(project)
        self.assertEqual((state["schedule"], state["hermes"]["job_id"]), ("30 5 * * 1", "0123456789ab"))
        self.assertEqual(state["criteria"][0]["include"], ["communication training"])
        log = (self.root / "hermes.log").read_text()
        self.assertIn("profile create medlitbot --clone", log)
        self.assertIn("-p medlitbot mcp remove github", log)
        self.assertIn(f"-p medlitbot mcp add med-lit --command {bin_dir}/uvx --env MED_LIT_SCOPE=bot", log)
        self.assertIn("-p medlitbot config set platform_toolsets.cron [delegation, med-lit]", log)
        self.assertIn('-p medlitbot cron create 30 5 * * 1 You are the med-lit literature bot for the project "SDM watch"', log)
        self.assertEqual(cli_bot.schedule_text(state["schedule"]), "every Mon at 05:30")

    def test_bots_are_staggered_and_need_a_tried_search(self) -> None:
        self.assertEqual(cli_bot.suggest_time({"05:00", "05:30"}), "06:00")
        with self.assertRaisesRegex(SystemExit, "normal review"):
            cli_bot.choose_run(cli_setup.Console(True), None)
