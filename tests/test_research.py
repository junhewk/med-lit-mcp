from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from helpers import QUESTION, Case, record

from med_lit_mcp import bot, research, screening, settings, triage, wiki
from med_lit_mcp.projects import run_dir
from med_lit_mcp.store import RUN_FILE, atomic_json, locked_run, read_json, save_run

CRITERIA = {"include": ["language models in decisions"], "exclude": ["technical benchmarks"]}
REASONS = {"I1": "The question asks how LLMs are used in decisions", "E1": "Benchmarks do not involve patients"}


def abstract(number: int) -> str:
    return f"Study {number}: clinicians used a language model to support shared decisions with patients."


class ResearchCase(Case):
    def set_mode(self, mode: str, **changes: Any) -> None:
        current = settings.load_settings(self.project.root)
        values = {"research.mode": mode} | {f"research.{k}": v for k, v in changes.items()}
        settings.write_settings(self.project.root, settings.apply_changes(current, values))

    def run_with(self, count: int) -> str:
        records = [record(n, title=f"Language models in decisions {n}", abstract=abstract(n)) for n in range(1, count + 1)]
        run_id = self.make_run(records, include=False)
        with locked_run(run_dir(run_id)) as manifest:
            for rank, uid in enumerate(manifest["articles"]):
                manifest["articles"][uid]["rank"] = rank
            save_run(run_dir(run_id), manifest)
        return run_id

    def manifest(self, run_id: str) -> dict[str, Any]:
        return read_json(run_dir(run_id) / RUN_FILE)

    def screen_session(self, run_id: str, decide: Any = None, *, new_round: bool = False) -> None:
        """Screen the open session (or, with new_round, the next one); decide(uid) returns (decision, criterion)."""
        while True:
            batch = screening.next_batch(run_id, 25, new_round=new_round)
            new_round = False
            if not batch["items"]:
                return
            entries = []
            for item in batch["items"]:
                decision, criterion = decide(item["uid"]) if decide else ("include", "")
                entries.append({
                    "uid": item["uid"], "decision": decision, "criterion": criterion,
                    "reason": "Clinicians used a language model with patients" if decision == "uncertain" else "per criteria",
                    "evidence": item["title"] if decision != "uncertain" else "",
                })
            screening.record_decisions(run_id, batch["revision"], entries)

    def triage_all(self, run_id: str) -> None:
        while (batch := triage.next_batch(run_id))["items"]:
            triage.record_scores(run_id, batch["revision"], [{"uid": i["uid"], "score": 2} for i in batch["items"]])


class CheckpointTests(ResearchCase):
    def test_a_new_project_starts_with_mode_and_parameters_then_the_question(self) -> None:
        step = research.research_next("Test review")
        self.assertEqual((step["step"], step["ask_researcher"]), ("parameters", True))
        self.assertEqual(set(step["parameters"]["modes"]), {"basic", "full", "deep"})
        self.assertIn("per_source", step["parameters"]["search"])
        self.assertIn("research.mode", step["do"])
        self.set_mode("basic")
        step = research.research_next("Test review")
        self.assertEqual((step["step"], step["ask_researcher"]), ("question", True))
        self.assertIn("validate_question", step["do"])

    def test_a_project_created_before_research_modes_can_choose_one(self) -> None:
        path = settings.settings_path(self.project.root)
        data = json.loads(path.read_text())
        data.pop("research")
        path.write_text(json.dumps(data))
        self.assertIsNone(settings.load_settings(self.project.root).research)
        self.assertEqual(research.research_next("Test review")["step"], "parameters")
        self.set_mode("deep", max_screened=40)
        self.assertEqual(
            settings.load_settings(self.project.root).research.model_dump(),
            {"mode": "deep", "max_screened": 40, "deep_rounds": 3, "round_articles": 20},
        )

    def test_criteria_are_a_checkpoint_and_each_needs_its_reason(self) -> None:
        self.set_mode("basic")
        run_id = self.run_with(3)
        step = research.research_next(run_id=run_id)
        self.assertEqual((step["step"], step["ask_researcher"]), ("criteria", True))
        self.assertEqual(step["search"]["candidates"], 3)
        with self.assertRaisesRegex(ValueError, r"missing: E1 \(technical benchmarks\)"):
            screening.set_criteria(run_id, CRITERIA["include"], CRITERIA["exclude"], reasons={"I1": "asked"})
        with self.assertRaisesRegex(ValueError, "unknown criteria E2"):
            screening.set_criteria(run_id, CRITERIA["include"], CRITERIA["exclude"], reasons=REASONS | {"E2": "x"})
        saved = screening.set_criteria(run_id, CRITERIA["include"], CRITERIA["exclude"], reasons=REASONS)
        self.assertEqual(saved["codes"], {"I1": "language models in decisions", "E1": "technical benchmarks"})
        self.assertEqual(self.manifest(run_id)["selection_history"][-1]["reasons"], REASONS)
        self.assertEqual(research.research_next(run_id=run_id)["step"], "screen")

    def test_criteria_without_reasons_still_work_outside_a_research_mode(self) -> None:
        run_id = self.run_with(1)
        self.assertEqual(screening.set_criteria(run_id, ["a"], [])["reasons"], {})

    def test_bot_projects_are_refused(self) -> None:
        watch = bot.create_bot("Watch", QUESTION, ["pubmed"], CRITERIA, schedule="0 5 * * *")
        with self.assertRaisesRegex(ValueError, "bot project"):
            research.research_next("Watch")
        self.assertIsNone(settings.load_settings(watch.root).research)
        with self.assertRaisesRegex(ValueError, "only in interactive projects"):
            settings.apply_changes(settings.load_settings(watch.root), {"research.mode": "basic"})


class ScreeningTests(ResearchCase):
    def setUp(self) -> None:
        super().setUp()
        self.set_mode("basic")

    def test_excludes_name_the_criterion_they_rest_on(self) -> None:
        run_id = self.run_with(3)
        screening.set_criteria(run_id, CRITERIA["include"], CRITERIA["exclude"], reasons=REASONS)
        batch = screening.next_batch(run_id, 3)
        self.assertEqual(batch["criterion_codes"], {"I1": "language models in decisions", "E1": "technical benchmarks"})
        first, second, third = (item["uid"] for item in batch["items"])
        titles = {item["uid"]: item["title"] for item in batch["items"]}
        result = screening.record_decisions(run_id, batch["revision"], [
            {"uid": first, "decision": "exclude", "reason": "benchmark", "evidence": titles[first], "criterion": "e1"},
            {"uid": second, "decision": "exclude", "reason": "benchmark", "evidence": titles[second]},
            {"uid": third, "decision": "exclude", "reason": "benchmark", "evidence": titles[third], "criterion": "E7"},
        ])
        self.assertEqual([d["uid"] for d in result["downgraded"]], [second, third])
        self.assertIn("exclude needs the criterion", result["downgraded"][0]["error"])
        self.assertIn("'E7' is not one of the criteria", result["downgraded"][1]["error"])
        self.assertEqual(self.manifest(run_id)["articles"][first]["screening"]["criterion"], "E1")
        self.assertNotIn("next_step", result)  # two were downgraded: they may be resubmitted first
        fixed = screening.record_decisions(run_id, batch["revision"], [
            {"uid": second, "decision": "exclude", "reason": "benchmark", "evidence": titles[second], "criterion": "E1"},
            {"uid": third, "decision": "exclude", "reason": "benchmark", "evidence": titles[third], "criterion": "E1"},
        ])
        self.assertEqual(fixed["next_step"]["step"], "report")

    def test_session_boundary_is_a_checkpoint_and_stopping_counts_the_unscreened(self) -> None:
        run_id = self.run_with(25)
        screening.set_criteria(run_id, CRITERIA["include"], CRITERIA["exclude"], reasons=REASONS)
        self.assertEqual(research.research_next(run_id=run_id)["step"], "triage")
        self.triage_all(run_id)
        self.assertEqual(research.research_next(run_id=run_id)["step"], "screen")
        # The result that ends the session carries the checkpoint itself, for an agent that asks at once.
        batch = screening.next_batch(run_id, 25)
        last = screening.record_decisions(run_id, batch["revision"], [
            {"uid": i["uid"], "decision": "include", "reason": "per criteria", "evidence": i["title"]} for i in batch["items"]
        ])
        checkpoint = last["next_step"]
        self.assertEqual((checkpoint["step"], checkpoint["ask_researcher"]), ("screening", True))
        self.assertEqual(checkpoint["selection"], {"include": 20, "exclude": 0, "uncertain": 0, "pending": 5})
        self.assertIn("confirm='stop_screening'", checkpoint["do"])
        self.assertEqual(screening.next_batch(run_id, 25)["next_step"]["step"], "screening")
        step = research.research_next(run_id=run_id)
        self.assertEqual((step["step"], step["ask_researcher"]), ("screening", True))
        self.assertIn("new_round=true", step["do"])
        # Going straight to the report names the stop route instead of only "continue".
        with self.assertRaisesRegex(ValueError, "Screening is not finished.*confirm='stop_screening'"):
            research.next_report(run_id)
        step = research.research_next(run_id=run_id, confirm="stop_screening")
        self.assertEqual(step["step"], "report")
        stopped = self.manifest(run_id)["screening_stopped"]
        self.assertEqual((stopped["unscreened"], stopped["by"]), (5, "researcher"))
        self.assertEqual(research.next_report(run_id)["methods"]["selection"]["pending"], 5)
        uids = [a["uid"] for a in research.next_report(run_id)["articles"]]
        quote = {"uid": uids[0], "finding": "LLMs supported decisions", "quote": "used a language model to support shared decisions"}
        self.assertTrue(research.record_report(run_id, f"Studies [{uids[0]}].", "", [quote], [])["saved"])
        self.assertEqual(research.research_next(run_id=run_id)["step"], "done")
        # Asking for the next session lifts the stop, and research_next leads the screening again.
        self.assertTrue(screening.next_batch(run_id, 25, new_round=True)["items"])
        self.assertNotIn("screening_stopped", self.manifest(run_id))
        self.assertEqual(research.research_next(run_id=run_id)["step"], "screen")
        # Excluding the rest leaves the included set as it was, but the report's counts are out of date.
        self.screen_session(run_id, lambda uid: ("exclude", "E1"))
        step = research.research_next(run_id=run_id)
        self.assertEqual(step["step"], "report")
        self.assertIn("out of date", step["do"])

    def test_max_screened_goes_on_without_asking_and_stops_at_the_number(self) -> None:
        self.set_mode("basic", max_screened=20)
        run_id = self.run_with(45)
        screening.set_criteria(run_id, CRITERIA["include"], CRITERIA["exclude"], reasons=REASONS)
        self.triage_all(run_id)
        self.screen_session(run_id)
        step = research.research_next(run_id=run_id)
        self.assertEqual(step["step"], "report")
        self.assertEqual(self.manifest(run_id)["screening_stopped"]["by"], "research.max_screened (20)")
        self.set_mode("basic", max_screened=40)  # raising the number screens on
        step = research.research_next(run_id=run_id)
        self.assertEqual((step["step"], step["ask_researcher"]), ("screen", False))
        self.assertIn("new_round=true", step["do"])

    def test_uncertain_articles_wait_for_the_researcher(self) -> None:
        run_id = self.run_with(2)
        screening.set_criteria(run_id, CRITERIA["include"], CRITERIA["exclude"], reasons=REASONS)
        uids = list(self.manifest(run_id)["articles"])
        self.screen_session(run_id, lambda uid: ("uncertain", "") if uid == uids[0] else ("include", ""))
        step = research.research_next(run_id=run_id)
        self.assertEqual((step["step"], step["uncertain_total"]), ("uncertain", 1))
        self.assertEqual(step["uncertain"][0]["uid"], uids[0])
        self.assertEqual(research.research_next(run_id=run_id, confirm="leave_uncertain")["step"], "report")
        screening.review(run_id, uids[0], "exclude", "a benchmark after all", criterion="E1")
        self.assertEqual(self.manifest(run_id)["articles"][uids[0]]["screening"]["criterion"], "E1")
        with self.assertRaisesRegex(ValueError, "not one of the criteria"):
            screening.review(run_id, uids[0], "exclude", "x", criterion="E9")


class ReportTests(ResearchCase):
    def screened_run(self, count: int = 3) -> tuple[str, list[str]]:
        run_id = self.run_with(count)
        screening.set_criteria(run_id, CRITERIA["include"], CRITERIA["exclude"], reasons=REASONS)
        uids = list(self.manifest(run_id)["articles"])
        self.screen_session(run_id, lambda uid: ("exclude", "E1") if uid == uids[-1] else ("include", ""))
        return run_id, uids

    def test_basic_report_is_checked_rendered_and_replied(self) -> None:
        self.set_mode("basic")
        run_id, uids = self.screened_run()
        self.assertEqual(research.research_next(run_id=run_id)["step"], "report")
        context = research.next_report(run_id)
        self.assertEqual((context["included_total"], context["remaining"]), (2, 0))
        self.assertEqual(context["methods"]["exclusion_reasons"], {"E1": 1})
        self.assertEqual(context["methods"]["criteria"]["E1"]["reason"], REASONS["E1"])
        self.assertEqual(context["articles"][0]["text"], "abstract")
        self.assertIn("The answer rests on the studies' abstracts; their full texts were not read.", context["limitations_added"])
        good = {"uid": uids[0], "finding": "LLMs supported decisions", "quote": "used a language model to support shared decisions"}
        rejected = research.record_report(
            run_id, f"Two studies [{uids[0]}] [{uids[2]}].", "", [good, {"uid": uids[1], "finding": "x", "quote": "invented words"}], [],
        )
        self.assertFalse(rejected["saved"])
        self.assertEqual(len(rejected["problems"]), 2)
        self.assertIn(f"[{uids[2]}] is not an included article", rejected["problems"][0])
        self.assertIn("quote is not in the article's stored text", rejected["problems"][1])
        self.assertFalse((self.project.root / research.REPORTS).exists())
        saved = research.record_report(
            run_id, f"Two studies used LLMs to support shared decisions [{uids[0]}] [{uids[1]}].",
            f"## Uses\n\nDecision support [{uids[0]}].", [good], ["Two small studies"],
        )
        self.assertTrue(saved["saved"])
        text = (self.project.root / self.manifest(run_id)["report"]["path"]).read_text()
        self.assertTrue(text.startswith("---\ngenerator: med-lit-mcp\n"))
        for part in ("## In short", "Two studies used LLMs to support shared decisions [1, 2].", "## Uses\n\nDecision support [1].",
                     "## How much to trust this", "- Two small studies", "The answer rests on the studies' abstracts",
                     "## How this review was done", "We searched PubMed on ", "2 met them and 1 did not.",
                     "## References", "1. A Researcher (2026). Language models in decisions 1. *Medical Education*.",
                     "### Key findings and their quotations", "- **[1]** LLMs supported decisions",
                     "| Exclude | technical benchmarks | Benchmarks do not involve patients |",
                     "### Why articles were excluded\n\n- technical benchmarks: 1"):
            self.assertIn(part, text)
        self.assertNotIn("[pubmed:", text.split("## References")[0])  # no uids in the prose
        self.assertTrue(saved["message"].startswith("Two studies used LLMs"))
        self.assertIn("The full report, with references:", saved["message"])
        self.assertIn("[1, 2]", saved["message"])
        done = research.research_next(run_id=run_id)
        self.assertEqual((done["step"], done["message"]), ("done", saved["message"]))
        # A changed selection makes the report out of date; rewriting keeps its file.
        screening.review(run_id, uids[1], "exclude", "the researcher's call", criterion="I1")
        step = research.research_next(run_id=run_id)
        self.assertEqual(step["step"], "report")
        self.assertIn("out of date", step["do"])
        first_path = self.manifest(run_id)["report"]["path"]
        research.record_report(run_id, f"One study [{uids[0]}] remains included.", "", [good], [])
        rewritten = self.manifest(run_id)
        self.assertTrue(rewritten["report"]["path"].endswith("-2.md"))
        self.assertEqual([r["path"] for r in rewritten["report_history"]], [first_path])
        self.assertTrue((self.project.root / first_path).is_file())

    def test_a_saved_report_renders_again_from_its_stored_content(self) -> None:
        self.set_mode("basic")
        run_id, uids = self.screened_run(2)
        good = {"uid": uids[0], "finding": "LLMs supported decisions", "quote": "used a language model to support shared decisions"}
        saved = research.record_report(run_id, f"One study [{uids[0]}].", "", [good], ["Small"])
        path = Path(saved["path"])
        first = path.read_text()
        path.write_text("---\ngenerator: med-lit-mcp\n---\nold format\n")
        self.assertEqual(research.render_report(run_id), path)
        self.assertEqual(path.read_text(), first)

    def test_report_comes_in_portions_that_fit_one_result(self) -> None:
        self.set_mode("basic")
        run_id = self.run_with(12)
        with locked_run(run_dir(run_id)) as manifest:
            for item in manifest["articles"].values():
                item["record"]["abstract"] = "Clinicians used a language model. " * 110
            save_run(run_dir(run_id), manifest)
        screening.set_criteria(run_id, CRITERIA["include"], CRITERIA["exclude"], reasons=REASONS)
        self.screen_session(run_id)
        seen, offset = [], 0
        while True:
            portion = research.next_report(run_id, offset)
            self.assertLessEqual(len(json.dumps(portion, indent=2).encode()), 18 * 1024 + 512)
            seen += [a["uid"] for a in portion["articles"]]
            if not portion["remaining"]:
                break
            offset += len(portion["articles"])
        self.assertEqual(len(seen), 12)
        self.assertEqual(len(set(seen)), 12)

    def test_full_portions_fit_one_result_and_carry_the_rules_once(self) -> None:
        self.set_mode("full")
        run_id = self.run_with(4)
        screening.set_criteria(run_id, CRITERIA["include"], CRITERIA["exclude"], reasons=REASONS)
        self.screen_session(run_id)
        for uid in self.manifest(run_id)["articles"]:
            self.add_text(run_id, uid, "Full text. " * 3000)
        rows = {
            "roles": [{"entity": f"Entity {n}", "type": "CONCEPT", "role": "concept", "mention": "word " * 50} for n in range(20)],
            "relationships": [{"source": "A", "relationship": "supports", "target": "B", "evidence": "word " * 70} for _ in range(10)],
        }
        with patch.object(research, "_wiki_evidence", return_value=rows):
            first = research.next_report(run_id)
            later = research.next_report(run_id, len(first["articles"]))
        for portion in (first, later):
            self.assertLessEqual(len(json.dumps(portion, indent=2).encode()), 18 * 1024)
            self.assertNotIn("oversized", portion)
        self.assertIn("rules", first)
        self.assertNotIn("rules", later)
        self.assertNotIn("methods", later)

    def test_a_report_never_overwrites_a_file_of_the_researcher(self) -> None:
        self.set_mode("basic")
        run_id, uids = self.screened_run(2)
        target = research._report_path(self.project, run_id)
        target.parent.mkdir(parents=True)
        target.write_text("my notes\n")
        saved = research.record_report(run_id, f"One study [{uids[0]}].", "", [], [])
        self.assertEqual(target.read_text(), "my notes\n")
        self.assertTrue(saved["path"].endswith("-2.md"))

    def test_full_mode_fetches_builds_the_wiki_and_quotes_full_text(self) -> None:
        self.set_mode("full")
        run_id, uids = self.screened_run(2)
        step = research.research_next(run_id=run_id)
        self.assertEqual((step["step"], step["to_fetch"]), ("fetch", 1))
        full = "Methods. In 40 consultations the model drafted options and clinicians edited every draft."
        self.add_text(run_id, uids[0], full)
        step = research.research_next(run_id=run_id)
        self.assertEqual((step["step"], step["wiki_step"]), ("wiki", "extract"))
        with patch.object(wiki, "work_plan", return_value={"step": "export", "tasks": []}):  # the wiki is built
            step = research.research_next(run_id=run_id)
        self.assertEqual(step["step"], "report")
        context = research.next_report(run_id)
        self.assertEqual(context["articles"][0]["text"], "full_text")
        self.assertIn("get_article_page", context["note"])
        quote = {"uid": uids[0], "finding": "Clinicians edited drafts", "quote": "clinicians edited every draft"}
        saved = research.record_report(run_id, f"The model drafted options [{uids[0]}].", "", [quote], [])
        self.assertTrue(saved["saved"])
        text = (self.project.root / self.manifest(run_id)["report"]["path"]).read_text()
        self.assertIn("(1 of 1)", text)
        self.assertNotIn("beyond the full-text limit", text)
        # Switching modes makes the report out of date.
        self.set_mode("basic")
        self.assertEqual(research.research_next(run_id=run_id)["step"], "report")


class StatusTests(ResearchCase):
    def test_run_status_names_the_report_step(self) -> None:
        from med_lit_mcp.runs import next_steps

        run_id = self.make_run()
        self.assertIn("next_report / record_report to write the report answering the question", next_steps(self.manifest(run_id)))
        atomic_json(run_dir(run_id) / RUN_FILE, self.manifest(run_id) | {"report": {"path": "reports/x.md"}})
        self.assertEqual(research.report_brief(self.manifest(run_id))["path"], "reports/x.md")


class DeepTests(ResearchCase):
    def setUp(self) -> None:
        super().setUp()
        self.set_mode("deep", deep_rounds=2, round_articles=3)
        self.found: list[dict[str, Any]] = []

    def launch(self, argv: list[str], **kwargs: Any) -> Any:
        from test_search import FakeProcess, write_results

        output = Path(argv[argv.index("--output") + 1])
        write_results(output, self.found)
        atomic_json(output / "summary.json", {"source_failures": {}, "records_by_source": {"pubmed": len(self.found)}})
        (output / "ranked-results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in self.found))
        return FakeProcess(argv, write=False, **kwargs)

    def test_rounds_search_again_until_the_evidence_is_enough(self) -> None:
        from med_lit_mcp import search

        run_id = self.run_with(2)
        screening.set_criteria(run_id, CRITERIA["include"], CRITERIA["exclude"], reasons=REASONS)
        uids = list(self.manifest(run_id)["articles"])
        self.screen_session(run_id)
        plan = patch.object(wiki, "work_plan", return_value={"step": "export", "tasks": []})
        for uid in uids:
            self.add_text(run_id, uid, "Clinicians used a language model to support shared decisions. " * 20)
        with plan:
            step = research.research_next(run_id=run_id)
        self.assertEqual((step["step"], step["round"], step["rounds_left"]), ("evaluate", 1, 1))
        # Not enough: a revised search runs into the same review and its best new articles are kept.
        revised = {**QUESTION, "components": {**QUESTION["components"], "context": {"groups": [{"text": "decision aids"}]}}}
        with self.assertRaisesRegex(ValueError, "pass question"):
            research.record_evaluation(run_id, False, "Only two studies, both on clinic use [pubmed:1].", ["home use"], [uids[0]])
        with self.assertRaisesRegex(ValueError, "same as an earlier round"):
            research.record_evaluation(run_id, False, "Only two studies, both on clinic use.", ["home use"], [uids[0]], question=QUESTION)
        self.found = [record(n, title=f"Decision aids with language models {n}", abstract=abstract(n)) for n in range(10, 15)]
        with patch.object(search.subprocess, "Popen", side_effect=self.launch):
            started = research.record_evaluation(
                run_id, False, f"Only two studies, both on clinic use [{uids[0]}].", ["home use"], [uids[0]], question=revised,
            )
        self.assertEqual(started["round"], 1)
        step = research.research_next(run_id=run_id)
        self.assertEqual((step["step"], step["candidates"], step["keep"]), ("triage", 5, 3))
        self.triage_all(run_id)
        step = research.research_next(run_id=run_id)
        self.assertEqual(step["step"], "screen")
        self.assertEqual(len(self.manifest(run_id)["articles"]), 5)  # 2 + the best 3 of 5
        self.assertIn("new_round=true", step["do"])
        self.screen_session(run_id, new_round=True)
        new = [u for u in self.manifest(run_id)["articles"] if u not in uids]
        for uid in new:
            self.add_text(run_id, uid, "Decision aids with a language model at home. " * 20)
        with plan:
            step = research.research_next(run_id=run_id)
        self.assertEqual((step["step"], step["round"], step["rounds_left"]), ("evaluate", 2, 0))
        self.assertNotIn("question=", step["do"])
        research.record_evaluation(run_id, True, f"Clinic and home use are both covered [{new[0]}].", [], [uids[0], new[0]])
        with plan:
            self.assertEqual(research.research_next(run_id=run_id)["step"], "report")
        facts = research.next_report(run_id)["methods"]
        self.assertEqual([(r["round"], r["new_articles"]) for r in facts["rounds"]], [(1, 0), (2, 3)])
        quote = {"uid": new[0], "finding": "Home use", "quote": "Decision aids with a language model at home."}
        saved = research.record_report(run_id, f"LLMs support decisions in clinics and at home [{uids[0]}] [{new[0]}].", "", [quote], [])
        text = Path(saved["path"]).read_text()
        self.assertIn("This found 2 distinct articles", text)  # the first search; round 2 added 3
        self.assertIn("The review ran in 2 rounds", text)
        self.assertIn("(the best 3 of 5 new articles by title)", text)
        self.assertIn("round 2 added 3 with a revised search (the best 3 of 5 new articles by title)", text)
        self.assertIn("## Is the evidence enough?\n\nClinic and home use are both covered [2].", text)
        self.assertIn("- Round 2 search:", text)

    def test_the_round_limit_ends_the_review_and_the_report_says_so(self) -> None:
        self.set_mode("deep", deep_rounds=1)
        run_id = self.run_with(2)
        screening.set_criteria(run_id, CRITERIA["include"], CRITERIA["exclude"], reasons=REASONS)
        self.screen_session(run_id)
        with patch.object(wiki, "work_plan", return_value={"step": "export", "tasks": []}):
            for uid in self.manifest(run_id)["articles"]:
                self.add_text(run_id, uid, "Clinicians used a language model. " * 20)
            self.assertEqual(research.research_next(run_id=run_id)["rounds_left"], 0)
            research.record_evaluation(run_id, False, "Home use is not covered by any study.", ["home use"], [])
            self.assertEqual(research.research_next(run_id=run_id)["step"], "report")
        limits = research.next_report(run_id)["limitations_added"]
        self.assertIn("The review stopped at its round limit before the evidence was judged sufficient.", limits)

    def test_deep_criteria_step_says_later_rounds_keep_them(self) -> None:
        run_id = self.run_with(2)
        step = research.research_next(run_id=run_id)
        self.assertEqual(step["step"], "criteria")
        self.assertIn("not from this round's search terms", step["do"])
        self.set_mode("full")
        self.assertNotIn("this round's search terms", research.research_next(run_id=run_id)["do"])

    def test_deep_screening_goes_on_without_asking(self) -> None:
        run_id = self.run_with(25)
        screening.set_criteria(run_id, CRITERIA["include"], CRITERIA["exclude"], reasons=REASONS)
        self.triage_all(run_id)
        self.screen_session(run_id)
        step = research.research_next(run_id=run_id)
        self.assertEqual((step["step"], step["ask_researcher"]), ("screen", False))
        self.assertIn("new_round=true", step["do"])

    def test_deep_mode_ignores_max_screened_so_every_round_is_screened(self) -> None:
        self.set_mode("deep", max_screened=20)
        run_id = self.run_with(25)
        screening.set_criteria(run_id, CRITERIA["include"], CRITERIA["exclude"], reasons=REASONS)
        self.triage_all(run_id)
        self.screen_session(run_id)
        step = research.research_next(run_id=run_id)
        self.assertEqual(step["step"], "screen")  # the other 5 too, without a stop
        self.assertNotIn("screening_stopped", self.manifest(run_id))
        with self.assertRaisesRegex(ValueError, "Deep mode screens each round"):
            research.research_next(run_id=run_id, confirm="stop_screening")

    def test_evaluation_is_only_for_deep_mode(self) -> None:
        self.set_mode("basic")
        run_id = self.run_with(1)
        with self.assertRaisesRegex(ValueError, "for deep mode"):
            research.record_evaluation(run_id, True, "Enough for this question.", [], [])
