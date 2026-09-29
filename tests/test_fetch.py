from __future__ import annotations

import os
from unittest.mock import patch

from helpers import RECORD, Case, record

from med_lit_mcp import fetch
from med_lit_mcp.projects import run_dir
from med_lit_mcp.store import RUN_FILE, database, read_json

PMC_XML = (
    b"<pmc-articleset><article><front><p>Not body</p></front><body><sec>"
    b"<p>First <bold>claim</bold>.</p></sec></body></article></pmc-articleset>"
)


class FetchTests(Case):
    def test_abstract_fallback_keeps_provenance_and_wiki_status_on_same_text(self) -> None:
        run_id = self.make_run()
        result = fetch.fetch_batch(run_id)
        self.assertEqual((result["fetch"]["abstract_only"], result["remaining"]), (1, 0))
        path = run_dir(run_id)
        item = read_json(path / RUN_FILE)["articles"]["pubmed:123"]
        self.assertEqual(item["fetch_method"], "search_record_abstract")
        self.assertEqual((path / item["text_file"]).read_text(), RECORD["abstract"])
        with database(self.project.db) as conn:
            snapshot = conn.execute("SELECT content_sha256 FROM source_snapshots").fetchone()[0]
        self.assertEqual(snapshot, item["content_sha256"])
        # Explicit refetch retries abstract-only articles; unchanged text keeps the wiki state.
        manifest = read_json(path / RUN_FILE)
        manifest["articles"]["pubmed:123"]["wiki"] = "complete"
        from med_lit_mcp.store import atomic_json

        atomic_json(path / RUN_FILE, manifest)
        fetch.fetch_batch(run_id, ["pubmed:123"])
        self.assertEqual(read_json(path / RUN_FILE)["articles"]["pubmed:123"]["wiki"], "complete")

    def test_full_text_from_pmc_with_api_key_and_never_downgraded(self) -> None:
        run_id = self.make_run([record(123, pmcid="PMC99")])
        with patch.dict(os.environ, {"NCBI_API_KEY": "key"}), patch.object(fetch, "safe_http", return_value=PMC_XML) as http:
            result = fetch.fetch_batch(run_id)
        self.assertIn("api_key=key", http.call_args.args[0])
        self.assertEqual(result["processed"][0]["fetch"], "full_text")
        with patch.object(fetch, "safe_http", side_effect=OSError("down")):
            again = fetch.fetch_batch(run_id, ["pubmed:123"])
        self.assertEqual(again["processed"], [])  # current full text is not refetched
        with database(self.project.db) as conn, patch.object(fetch, "safe_http", side_effect=OSError("down")):
            stored = fetch.save_article(conn, run_id, "pubmed:123", RECORD, ("abstract", "abstract_only", "", "x"))
        self.assertEqual(stored[:2], ("First claim.", "full_text"))

    def test_efetch_failure_falls_back_to_europe_pmc(self) -> None:
        calls = []

        def http(url: str, timeout: float = 20) -> bytes:
            calls.append(url)
            if "eutils" in url:
                raise OSError("NCBI down")
            return PMC_XML

        with patch.object(fetch, "safe_http", side_effect=http):
            text, content_type, _url, method, _details = fetch.fetch_content({"pmcid": "PMC123"})
        self.assertEqual((text, content_type, method), ("First claim.", "full_text", "europepmc_fulltext_xml"))
        self.assertEqual(len(calls), 2)

    def test_unpaywall_finds_pmc_copy_for_record_without_pmcid(self) -> None:
        run_id = self.make_run([record(123, doi="10.2196/68486")])
        locations = [
            {"host_type": "publisher", "version": "publishedVersion", "license": "cc-by", "url_for_pdf": "https://pub.example/a.pdf"},
            {"host_type": "repository", "version": "submittedVersion", "license": "cc-by", "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/12008702"},
        ]
        with patch.object(fetch, "unpaywall_locations", return_value=locations) as lookup, patch.object(fetch, "safe_http", return_value=PMC_XML) as http:
            result = fetch.fetch_batch(run_id)
        lookup.assert_called_once_with("10.2196/68486")
        self.assertIn("id=12008702", http.call_args_list[0].args[0])
        self.assertEqual((result["processed"][0]["fetch"], result["processed"][0]["fetch_method"]), ("full_text", "unpaywall_pmc_efetch_xml"))
        details = read_json(run_dir(run_id) / RUN_FILE)["articles"]["pubmed:123"]["fetch_details"]
        self.assertEqual((details["pmcid"], details["license"], details["host_type"]), ("PMC12008702", "cc-by", "repository"))

    def test_unpaywall_pdf_is_used_and_non_pdf_responses_are_skipped(self) -> None:
        locations = [
            {"host_type": "publisher", "version": "publishedVersion", "license": "cc-by", "url_for_pdf": "https://blocked.example/a.pdf"},
            {"host_type": "repository", "version": "acceptedVersion", "license": "cc-by-nc", "url_for_pdf": "https://repo.example/b.pdf"},
        ]

        def http(url: str, timeout: float = 20) -> bytes:
            return b"<html>Please log in</html>" if "blocked" in url else b"%PDF-1.7 fake"

        long_text = "Results. " * 400

        def fake_pdf_text(data: bytes) -> str:
            if not data.startswith(b"%PDF-"):
                raise ValueError("Not a PDF")
            return long_text

        with (
            patch.object(fetch, "unpaywall_locations", return_value=locations),
            patch.object(fetch, "safe_http", side_effect=http),
            patch.object(fetch, "pdf_text", side_effect=fake_pdf_text),
        ):
            text, content_type, url, method, details = fetch.fetch_content({"doi": "10.1/x", "abstract": "short"})
        self.assertEqual((content_type, url, method), ("full_text", "https://repo.example/b.pdf", "unpaywall_pdf"))
        self.assertEqual((text, details["version"], details["license"]), (long_text, "acceptedVersion", "cc-by-nc"))
        with self.assertRaisesRegex(ValueError, "Not a PDF"):
            fetch.pdf_text(b"<html></html>")

    def test_abstract_only_retry_pass_finishes_even_when_nothing_improves(self) -> None:
        run_id = self.make_run([record(n) for n in range(1, 4)])
        fetch.fetch_batch(run_id, max_items=10)
        first = fetch.fetch_batch(run_id, max_items=2, retry_abstract_only=True)
        self.assertEqual((len(first["processed"]), first["remaining"]), (2, 1))
        second = fetch.fetch_batch(run_id, max_items=2, retry_abstract_only=True)
        self.assertEqual((len(second["processed"]), second["remaining"]), (1, 0))
        self.assertNotIn("fetch_retry_pass", read_json(run_dir(run_id) / RUN_FILE))
        again = fetch.fetch_batch(run_id, max_items=5, retry_abstract_only=True)
        self.assertEqual(len(again["processed"]), 3)  # a new request starts a new pass

    def test_closed_access_falls_back_to_abstract(self) -> None:
        with patch.object(fetch, "unpaywall_locations", return_value=[]):
            result = fetch.fetch_content({"doi": "10.1016/closed", "abstract": "Only the abstract."})
        self.assertEqual(result[:2], ("Only the abstract.", "abstract_only"))

    def test_batches_are_bounded_and_need_email(self) -> None:
        run_id = self.make_run([record(n) for n in range(1, 5)])
        result = fetch.fetch_batch(run_id, max_items=3)
        self.assertEqual((len(result["processed"]), result["remaining"], result["stopped_reason"]), (3, 1, "max_items"))
        clock = iter([0.0, 100.0, 100.0, 100.0])
        with patch.object(fetch.time, "monotonic", side_effect=lambda: next(clock, 100.0)):
            later = fetch.fetch_batch(run_id)
        self.assertEqual(later["remaining"], 0)
        with patch.dict(os.environ, {"NCBI_EMAIL": ""}), self.assertRaisesRegex(ValueError, "NCBI_EMAIL is not set"):
            fetch.fetch_batch(run_id)

    def test_time_budget_stops_between_articles(self) -> None:
        run_id = self.make_run([record(n) for n in range(1, 4)])
        ticks = iter([0.0, 50.0])
        with patch.object(fetch.time, "monotonic", side_effect=lambda: next(ticks, 50.0)):
            result = fetch.fetch_batch(run_id, max_items=10)
        self.assertEqual((len(result["processed"]), result["stopped_reason"]), (1, "time_budget"))

    def test_only_included_articles_are_fetched(self) -> None:
        run_id = self.make_run(include=False)
        with self.assertRaisesRegex(ValueError, "Set screening criteria"):
            fetch.fetch_batch(run_id)
