from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

from helpers import QUESTION, RECORD, Case, record

from med_lit_mcp import search
from med_lit_mcp.projects import run_dir
from med_lit_mcp.store import RUN_FILE, atomic_json, read_json, save_run


class FakeProcess:
    def __init__(self, argv: list[str], *, finish: bool = True, write: bool = True, **kwargs: Any) -> None:
        self.argv, self.kwargs, self.pid = argv, kwargs, 424242
        self.returncode: int | None = 0 if finish else None
        if write:
            write_results(Path(argv[argv.index("--output") + 1]) if "--output" in argv else Path(argv[-1]))

    def poll(self) -> int | None:
        return self.returncode


def write_results(output: Path, records: list[dict[str, Any]] | None = None) -> None:
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "manifest.json", {"status": "complete"})
    atomic_json(output / "strategy.json", {"saved": True})
    atomic_json(
        output / "summary.json",
        {"source_failures": {"openalex": "unavailable"}, "records_by_source": {"pubmed": 2}},
    )
    lines = records or [RECORD, {**RECORD, "source": "pmc"}]
    (output / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))


class SearchTests(Case):
    def test_normalizes_model_drafted_component_list(self) -> None:
        drafted = {
            "framework": "pcc",
            "question": "How are LLMs used in shared decision making?",
            "components": [
                {"id": "population", "groups": [{"text": "patients"}]},
                {"id": "concept", "groups": [{"text": "LLM"}, {"label": "LLM", "text": "language models"}]},
            ],
        }
        question = search.normalize_question(drafted)
        self.assertEqual((question["schema_version"], question["framework"]), ("2", "PCC"))
        labels = [group["label"] for group in question["components"]["concept"]["groups"]]
        self.assertEqual(labels, ["LLM", "LLM 2"])
        with self.assertRaisesRegex(ValueError, "components.concept is required for PCC"):
            search.normalize_question({**drafted, "components": drafted["components"][:1]})

    def test_validate_question_uses_search_package_and_warns_without_email(self) -> None:
        def warnings(*args: Any) -> str:
            return "\n".join(search.validate_question(*args)["warnings"])

        result = search.validate_question(QUESTION)
        self.assertEqual(result["sources"], ["pubmed", "pmc", "openalex"])
        draft = search.load_draft(result["question_id"])
        self.assertEqual((draft["question"], draft["sources"]), (result["normalized_question"], result["sources"]))
        with self.assertRaisesRegex(ValueError, "Unknown question_id"):
            search.load_draft("f" * 32)
        self.assertIn("three-year default", result["warnings"][0])
        self.assertIn("skipped by default", warnings(QUESTION))
        dated = {**QUESTION, "filters": {"from_date": "2015-01-01"}}
        with patch.dict(os.environ, {"S2_API_KEY": "key"}):
            keyed = search.validate_question(dated)
        self.assertEqual((keyed["sources"][-1], keyed["warnings"]), ("semantic-scholar", []))
        self.assertIn("429", warnings(QUESTION, ["semantic-scholar"]))
        self.assertIn("SCOPUS_API_KEY", warnings(QUESTION, ["scopus"]))
        with patch.dict(os.environ, {"SCOPUS_API_KEY": "key"}):
            self.assertEqual(search.validate_question(QUESTION)["sources"][-1], "scopus")
        with patch.dict(os.environ, {"NCBI_EMAIL": ""}):
            self.assertIn("NCBI_EMAIL", warnings(QUESTION))
        with self.assertRaisesRegex(ValueError, "europepmc runs on its own"):
            search.validate_question(QUESTION, ["pubmed", "europepmc"])
        self.assertEqual(search.normalize_sources(["semantic_scholar"]), ["semantic-scholar"])
        bad = {**QUESTION, "filters": {"from_date": "2026-01-01", "to_date": "2020-01-01"}}
        with self.assertRaisesRegex(ValueError, "Question rejected"):
            search.validate_question(bad)

    def test_background_search_imports_deduplicated_results(self) -> None:
        with patch.object(search.subprocess, "Popen", side_effect=lambda argv, **kw: FakeProcess(argv, **kw)) as popen:
            result = search.start_search(self.project, QUESTION, ["pubmed"], limit_per_source=5, wait_seconds=0)
        argv, kwargs = popen.call_args.args[0], popen.call_args.kwargs
        self.assertEqual(argv[:4], [sys.executable, "-m", "med_lit_mcp.medsearch", "run"])
        self.assertEqual(argv[-2:], ["--sources", "pubmed"])
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotIn(kwargs["stdout"], (None, subprocess.PIPE))
        self.assertEqual((result["search_status"], result["candidates"]), ("complete", 1))
        self.assertEqual(result["source_failures"], {"openalex": "unavailable"})
        manifest = read_json(run_dir(result["run_id"]) / RUN_FILE)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(result["project"], "Test review")
        self.assertNotIn("search_pid", manifest)

    def test_running_search_is_resumed_until_complete(self) -> None:
        holder: dict[str, FakeProcess] = {}

        def launch(argv: list[str], **kwargs: Any) -> FakeProcess:
            holder["process"] = FakeProcess(argv, finish=False, write=False, **kwargs)
            return holder["process"]

        with patch.object(search.subprocess, "Popen", side_effect=launch):
            result = search.start_search(self.project, QUESTION, wait_seconds=0)
        self.assertEqual(result["search_status"], "running")
        self.assertIn("resume_search", result["note"])
        write_results(run_dir(result["run_id"]) / "search", [record(1), record(2)])
        holder["process"].returncode = 0
        resumed = search.resume_search(result["run_id"], wait_seconds=0)
        self.assertEqual((resumed["search_status"], resumed["candidates"]), ("complete", 2))

    def test_dead_search_fails_with_stderr_and_retries_from_checkpoint(self) -> None:
        def crash(argv: list[str], **kwargs: Any) -> FakeProcess:
            kwargs["stderr"].write(b"Resolving\nerror: components must be an object\n")
            process = FakeProcess(argv, write=False, **kwargs)
            process.returncode = 2
            return process

        with patch.object(search.subprocess, "Popen", side_effect=crash):
            result = search.start_search(self.project, QUESTION, wait_seconds=0)
        self.assertEqual(result["search_status"], "failed")
        self.assertEqual(result["search_error"], "search process exited with status 2: error: components must be an object")
        path = run_dir(result["run_id"])
        write_results(path / "search")
        with patch.object(search.subprocess, "Popen", side_effect=lambda argv, **kw: FakeProcess(argv, write=False, **kw)) as popen:
            resumed = search.resume_search(result["run_id"], wait_seconds=0)
        self.assertEqual(popen.call_args.args[0][-2:], ["search", str(path / "search")])
        self.assertEqual(resumed["search_status"], "complete")

    def test_failed_sources_are_retried_and_merged_into_the_run(self) -> None:
        def partial(argv: list[str], **kwargs: Any) -> FakeProcess:
            output = Path(argv[argv.index("--output") + 1])
            write_results(output, [record(1)])
            summary = read_json(output / "summary.json")
            summary.update(
                source_failures={"pmc": "HTTP 500", "semantic-scholar": "HTTP 429"},
                records_by_source={"pubmed": 1, "pmc": 0, "openalex": 0},
            )
            atomic_json(output / "summary.json", summary)
            return FakeProcess(argv, write=False, **kwargs)

        with patch.object(search.subprocess, "Popen", side_effect=partial):
            result = search.start_search(self.project, QUESTION, ["pubmed", "pmc", "openalex", "semantic-scholar"], wait_seconds=0)
        self.assertIn("retry_failed_sources=true", result["note"])
        run_id = result["run_id"]
        self.assertEqual(search.resume_search(run_id, wait_seconds=0)["candidates"], 1)  # no retry unless asked

        def crash(argv: list[str], **kwargs: Any) -> FakeProcess:
            kwargs["stderr"].write(b"error: still down\n")
            process = FakeProcess(argv, write=False, **kwargs)
            process.returncode = 1
            return process

        with patch.object(search.subprocess, "Popen", side_effect=crash):
            failed = search.resume_search(run_id, wait_seconds=0, retry_failed_sources=True)
        self.assertEqual(failed["search_status"], "complete")  # earlier results stay usable
        self.assertIn("still down", failed["last_retry"]["error"])

        def recovered(argv: list[str], **kwargs: Any) -> FakeProcess:
            output = Path(argv[argv.index("--output") + 1])
            write_results(output, [record(1), record(2, pmcid="PMC2")])
            atomic_json(output / "summary.json", {"source_failures": {"semantic-scholar": "HTTP 429"}, "records_by_source": {"pmc": 2}})
            return FakeProcess(argv, write=False, **kwargs)

        with patch.object(search.subprocess, "Popen", side_effect=recovered) as popen:
            retried = search.resume_search(run_id, wait_seconds=0, retry_failed_sources=True)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[argv.index("--sources") + 1], "pmc,semantic-scholar")
        self.assertTrue(argv[argv.index("--output") + 1].endswith("search-retry-2"))
        self.assertEqual((retried["candidates"], retried["last_retry"]["new_articles"]), (2, 1))
        self.assertEqual(retried["source_failures"], {"semantic-scholar": "HTTP 429"})
        self.assertEqual(retried["records_by_source"], {"pubmed": 1, "pmc": 2, "openalex": 0})

    def test_search_process_lost_after_restart_is_detected_by_pid(self) -> None:
        run_id = self.make_run(include=False)
        path = run_dir(run_id)
        manifest = read_json(path / RUN_FILE)
        manifest.update(search_status="running", search_pid=2**22 + 12345, search_started_at=manifest["created_at"])
        save_run(path, manifest)
        with patch.object(search.os, "kill", side_effect=ProcessLookupError):
            with patch.object(search.subprocess, "Popen", side_effect=lambda argv, **kw: FakeProcess(argv, **kw)):
                result = search.resume_search(run_id, wait_seconds=0)
        self.assertEqual(result["search_status"], "complete")

    def test_europepmc_search_maps_record_and_provenance(self) -> None:
        payload = {
            "hitCount": 1,
            "resultList": {"result": [{
                "id": "123", "source": "MED", "pmid": "123", "pmcid": "PMC123", "title": "Study",
                "abstractText": "<p>Training &amp; evaluation.</p>",
            }]},
        }
        with patch.object(search, "safe_http", return_value=json.dumps(payload).encode()) as request:
            result = search.start_search(self.project, QUESTION, ["europepmc"], limit_per_source=3, wait_seconds=0)
        self.assertEqual(result["candidates"], 1)
        url = request.call_args.args[0]
        self.assertIn("TITLE_ABS", url)
        self.assertIn("NOT+SRC%3APPR", url)  # preprints are excluded by default
        self.assertIn("FIRST_PDATE%3A%5B", url)  # the three-year default applies here too
        item = read_json(run_dir(result["run_id"]) / RUN_FILE)["articles"]["pmc:PMC123"]
        self.assertEqual(item["record"]["abstract"], "Training & evaluation.")

    def test_legacy_runs_are_read_only(self) -> None:
        run_id = self.make_run()
        path = run_dir(run_id)
        manifest = read_json(path / RUN_FILE)
        manifest["schema_version"] = 0
        atomic_json(path / RUN_FILE, manifest)
        with self.assertRaisesRegex(ValueError, "read-only"):
            search.resume_search(run_id, wait_seconds=0)


class KnownArticleTests(Case):
    def search(self, records: list[dict[str, Any]], sources: list[str] | None = None) -> dict[str, Any]:
        def launch(argv: list[str], **kwargs: Any) -> FakeProcess:
            write_results(Path(argv[argv.index("--output") + 1]), records)
            return FakeProcess(argv, write=False, **kwargs)

        with patch.object(search.subprocess, "Popen", side_effect=launch):
            return search.start_search(self.project, QUESTION, sources or ["pubmed", "openalex"], wait_seconds=0)

    def test_a_later_search_imports_only_articles_new_to_the_project(self) -> None:
        first = self.search([record(1), record(2, pmcid="PMC2")])
        self.assertEqual(first["candidates"], 2)
        # The same article found again under other identifiers: by DOI, by PMCID, and by uid.
        by_doi = {"source": "openalex", "source_id": "W1", "doi": "https://doi.org/10.1/EXAMPLE.1", "title": "Same"}
        by_pmcid = {"source": "openalex", "source_id": "W2", "pmcid": "2", "title": "Same"}
        second = self.search([by_doi, by_pmcid, record(1), record(3)])
        self.assertEqual((second["candidates"], second["already_known"]), (1, 3))
        manifest = read_json(run_dir(second["run_id"]) / RUN_FILE)
        self.assertEqual(list(manifest["articles"]), ["pubmed:3"])
        self.assertEqual(manifest["already_known"]["openalex:W1"], {"run_id": first["run_id"], "uid": "pubmed:1"})

    def test_one_article_from_two_sources_in_one_search_is_imported_once(self) -> None:
        twin = {"source": "openalex", "source_id": "W9", "doi": "10.1/example.9", "title": "Twin"}
        result = self.search([record(9), twin])
        self.assertEqual((result["candidates"], result["already_known"]), (1, 0))
