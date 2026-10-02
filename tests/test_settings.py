from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import QUESTION, Case, record
from mcp.shared.memory import create_connected_server_and_client_session
from test_search import FakeProcess, write_results

from med_lit_mcp import fetch, projects, search, settings, wiki
from med_lit_mcp.medsearch.preprints import is_preprint
from med_lit_mcp.projects import run_dir
from med_lit_mcp.server import mcp
from med_lit_mcp.store import RUN_FILE, atomic_json, read_json


class SettingsFileTests(Case):
    def test_new_projects_copy_user_defaults_for_their_mode(self) -> None:
        self.assertEqual(settings.load_settings(self.project.root).search.years, None)
        settings.save_user_defaults("interactive", {"search": {"years": "2020-", "per_source": 40}})
        settings.save_user_defaults("bot", {"bot": {"lookback_days": 30}})
        review = projects.create_project("Review")
        watch = projects.create_project("Watch", mode="bot")
        review_settings = settings.load_settings(review.root)
        self.assertEqual((review_settings.search.years, review_settings.search.per_source), ("2020-", 40))
        self.assertIsNone(review_settings.bot)
        watch_settings = settings.load_settings(watch.root)
        self.assertEqual((watch_settings.mode, watch_settings.bot.lookback_days), ("bot", 30))
        self.assertEqual(watch_settings.search.years, None)  # bot projects do not inherit interactive defaults
        settings.save_user_defaults("interactive", {"search": {"years": "2025-"}})
        self.assertEqual(settings.load_settings(review.root).search.years, "2020-")  # a copy, not a link
        self.assertTrue((review.root / "med-lit.settings.json").is_file())

    def test_changes_are_validated(self) -> None:
        current = settings.load_settings(self.project.root)
        changed = settings.apply_changes(current, {"search.years": "2010-2020", "fetch.limit": 30})
        self.assertEqual((changed.search.years, changed.fetch.limit), ("2010-2020", 30))
        for bad, message in [
            ({"search.years": "2020"}, "years must look like"),
            ({"search.years": "2020-2010"}, "end year"),
            ({"search.per_source": 500}, "per_source"),
            ({"search.colour": "red"}, "Unknown setting"),
            ({"mode": "bot"}, "cannot be changed"),
            ({"bot.lookback_days": 10}, "only in bot projects"),
        ]:
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, message):
                settings.apply_changes(current, bad)
        self.assertEqual(settings.year_filters("2010-2020"), {"from_date": "2010-01-01", "to_date": "2020-12-31"})
        self.assertEqual(settings.year_filters("all"), {"from_date": "1800-01-01"})
        self.assertEqual(settings.year_filters(None), {})

    def test_preprint_detection(self) -> None:
        self.assertTrue(is_preprint({"publication_types": ["preprint"]}))
        self.assertTrue(is_preprint({"journal": "medRxiv"}))
        self.assertTrue(is_preprint({"doi": "10.1101/2026.01.01.123"}))
        self.assertTrue(is_preprint({"doi": "10.64898/2026.09.18.26363419"}))
        self.assertTrue(is_preprint({"source_id": "PPR1328732"}))
        self.assertFalse(is_preprint({"journal": "JAMA", "doi": "10.1001/jama.2026.1", "publication_types": ["Journal Article"]}))


class SettingsAppliedTests(Case):
    def test_search_uses_the_project_years_filters_and_limit(self) -> None:
        settings.write_settings(
            self.project.root,
            settings.apply_changes(
                settings.load_settings(self.project.root),
                {"search.years": "2015-", "search.per_source": 7, "search.languages": ["english"]},
            ),
        )
        checked = search.validate_question(QUESTION, None, self.project)
        self.assertEqual(
            checked["normalized_question"]["filters"],
            {"from_date": "2015-01-01", "languages": ["english"], "exclude_preprints": True},
        )
        self.assertEqual(checked["settings"]["per_source"], 7)
        self.assertFalse(any("three-year default" in w for w in checked["warnings"]))
        dated = {**QUESTION, "filters": {"from_date": "2022-06-01"}}
        self.assertEqual(search.validate_question(dated, None, self.project)["normalized_question"]["filters"]["from_date"], "2022-06-01")
        with patch.object(search.subprocess, "Popen", side_effect=lambda argv, **kw: FakeProcess(argv, **kw)) as popen:
            search.start_search(self.project, QUESTION, ["pubmed"], wait_seconds=0)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[argv.index("--limit-per-source") + 1], "7")

    def test_preprints_are_skipped_at_import_unless_allowed(self) -> None:
        records = [record(1), record(2, journal="medRxiv", title="A preprint"), record(3, publication_types=["preprint"])]

        def launch(argv: list[str], **kwargs: object) -> FakeProcess:
            write_results(Path(argv[argv.index("--output") + 1]), records)
            return FakeProcess(argv, write=False, **kwargs)

        with patch.object(search.subprocess, "Popen", side_effect=launch):
            result = search.start_search(self.project, QUESTION, ["pubmed"], wait_seconds=0)
        manifest = read_json(run_dir(result["run_id"]) / RUN_FILE)
        self.assertEqual((result["candidates"], len(manifest["skipped_preprints"])), (1, 2))
        settings.write_settings(
            self.project.root,
            settings.apply_changes(settings.load_settings(self.project.root), {"search.preprint_allow": True}),
        )
        with patch.object(search.subprocess, "Popen", side_effect=launch):
            allowed = search.start_search(self.project, QUESTION, ["pubmed"], wait_seconds=0)
        # The preprints are new to the project; record 1 was found by the first search.
        self.assertEqual((allowed["candidates"], allowed["already_known"]), (2, 1))
        question = read_json(run_dir(allowed["run_id"]) / "question.json")
        self.assertNotIn("exclude_preprints", question["filters"])

    def test_fetch_follows_ranking_and_skips_beyond_the_limit(self) -> None:
        run_id = self.make_run([record(n) for n in range(1, 5)])
        path = run_dir(run_id)
        manifest = read_json(path / RUN_FILE)
        for rank, uid in zip((4, 1, 3, 2), sorted(manifest["articles"]), strict=True):
            manifest["articles"][uid]["rank"] = rank
        atomic_json(path / RUN_FILE, manifest)
        settings.write_settings(
            self.project.root,
            settings.apply_changes(settings.load_settings(self.project.root), {"fetch.limit": 2, "fetch.mode": "abstract_only"}),
        )
        with patch.object(fetch, "fetch_content", wraps=fetch.fetch_content) as content:
            result = fetch.fetch_batch(run_id, max_items=10)
        self.assertEqual([p["uid"] for p in result["processed"]], ["pubmed:2", "pubmed:4"])  # ranks 1 and 2
        self.assertEqual(sorted(result["skipped_over_limit"]), ["pubmed:1", "pubmed:3"])
        self.assertTrue(all(call.kwargs["abstract_only"] for call in content.call_args_list))
        self.assertEqual((result["remaining"], result["fetch"]["skipped"]), (0, 2))

    def test_wiki_reads_the_default_page_count(self) -> None:
        run_id = self.make_run()
        self.add_text(run_id, "pubmed:123", "\n\n".join("word " * 2400 for _ in range(10)))
        header = wiki.next_article(run_id)["header"]
        self.assertEqual(header["page_count"], 3)  # wiki.max_pages default
        plan = wiki.work_plan(run_id)
        self.assertIn("at most 5 tasks", plan["how"])

    def test_wiki_plan_previews_page_limit_and_respects_reader_override(self) -> None:
        run_id = self.make_run()
        self.add_text(run_id, "pubmed:123", "\n\n".join("word " * 2400 for _ in range(10)))
        settings.write_settings(
            self.project.root,
            settings.apply_changes(settings.load_settings(self.project.root), {"wiki.max_pages": 1}),
        )
        self.assertIn("1 page(s) left", wiki.work_plan(run_id)["tasks"][0])
        self.assertEqual(wiki.next_article(run_id)["header"]["page_count"], 1)
        self.assertEqual(wiki.next_article(run_id, max_pages=2)["header"]["page_count"], 2)
        self.assertIn("2 page(s) left", wiki.work_plan(run_id)["tasks"][0])


class SettingsToolTests(Case, unittest.IsolatedAsyncioTestCase):
    async def test_project_settings_tool_shows_changes_and_protects_bots(self) -> None:
        projects.create_project("Watch", mode="bot")
        async with create_connected_server_and_client_session(mcp._mcp_server) as client:
            shown = await client.call_tool("project_settings", {"project": "Test review"})
            self.assertEqual(shown.structuredContent["settings"]["search"]["per_source"], 20)
            changed = await client.call_tool("project_settings", {"project": "Test review", "changes": {"search.years": "2018-"}})
            self.assertEqual(changed.structuredContent["settings"]["search"]["years"], "2018-")
            on_disk = json.loads((self.project.root / "med-lit.settings.json").read_text())
            self.assertEqual(on_disk["search"]["years"], "2018-")
            invalid = await client.call_tool("project_settings", {"project": "Test review", "changes": {"search.years": "soon"}})
            self.assertTrue(invalid.isError)
            bot = await client.call_tool("project_settings", {"project": "Watch", "changes": {"search.years": "2018-"}})
            self.assertTrue(bot.isError)
            self.assertIn("setup bot --edit", bot.content[0].text)
