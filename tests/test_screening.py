from __future__ import annotations

import os
import unittest
from unittest import mock

from helpers import RECORD, Case, record
from mcp.shared.memory import create_connected_server_and_client_session

from med_lit_mcp import fetch, screening, triage
from med_lit_mcp.limits import encoded_size, response_bytes
from med_lit_mcp.projects import run_dir
from med_lit_mcp.server import mcp
from med_lit_mcp.store import RUN_FILE, read_json, save_run


class ScreeningTests(Case):
    def setUp(self) -> None:
        super().setUp()
        self.run_id = self.make_run(
            [RECORD, record(124, title="", abstract=""), record(125, title="Veterinary training")],
            include=False,
        )

    def decide(self, uid: str, decision: str, evidence: str = "", reason: str = "Stated in abstract") -> dict:
        return {"uid": uid, "decision": decision, "reason": reason, "evidence": evidence}

    def test_agent_screening_validates_evidence_and_blocks_fetch_until_done(self) -> None:
        criteria = screening.set_criteria(self.run_id, ["communication training"], ["veterinary"])
        self.assertEqual((criteria["revision"], criteria["pending"]), (1, 3))
        batch = screening.next_batch(self.run_id, batch_size=1)
        self.assertEqual([item["uid"] for item in batch["items"]], ["pubmed:123"])
        self.assertEqual(batch["remaining"], 2)  # the empty record was auto-marked uncertain
        with self.assertRaisesRegex(ValueError, "Screening is not finished"):
            fetch.fetch_batch(self.run_id)
        result = screening.record_decisions(
            self.run_id,
            1,
            [
                self.decide("pubmed:123", "include", "MEDICAL STUDENTS practised  shared decision making"),
                self.decide("pubmed:125", "exclude", "not in the record"),
                self.decide("pubmed:999", "include", "x"),
            ],
            client="test",
        )
        self.assertEqual(result["recorded"], 2)
        self.assertEqual(result["downgraded"][0]["uid"], "pubmed:125")
        self.assertEqual(result["rejected"][0]["error"], "unknown article ID")
        self.assertEqual(result["selection"], {"include": 1, "exclude": 0, "uncertain": 2, "pending": 0})
        corrected = screening.record_decisions(
            self.run_id, 1, [self.decide("pubmed:125", "exclude", "Veterinary training")]
        )
        self.assertEqual(corrected["selection"]["exclude"], 1)
        again = screening.record_decisions(self.run_id, 1, [self.decide("pubmed:125", "include", "Veterinary")])
        self.assertIn("review_article", again["rejected"][0]["error"])
        saved = read_json(run_dir(self.run_id) / RUN_FILE)["articles"]
        self.assertEqual(saved["pubmed:123"]["screening"]["method"], "agent")
        self.assertEqual(saved["pubmed:124"]["screening"]["method"], "system")
        self.assertEqual(saved["pubmed:125"]["screening_history"][0]["validation_error"][:8], "evidence")

    def test_changed_criteria_need_replace_and_keep_history(self) -> None:
        screening.set_criteria(self.run_id, ["communication training"], [])
        screening.next_batch(self.run_id)
        screening.record_decisions(self.run_id, 1, [self.decide("pubmed:123", "include", "chatbot")])
        with self.assertRaisesRegex(ValueError, "replace=true"):
            screening.set_criteria(self.run_id, ["medical students"], [])
        revised = screening.set_criteria(self.run_id, ["medical students"], [], replace=True)
        self.assertTrue(revised["rescreen_required"])
        with self.assertRaisesRegex(ValueError, "revision 2"):
            screening.record_decisions(self.run_id, 1, [self.decide("pubmed:123", "include", "chatbot")])
        saved = read_json(run_dir(self.run_id) / RUN_FILE)
        self.assertEqual(saved["selection_history"][-1]["criteria"]["include"], ["medical students"])
        self.assertEqual(saved["articles"]["pubmed:123"]["screening_history"][-1]["revision"], 1)
        self.assertEqual(screening.set_criteria(self.run_id, ["medical students"], [])["changed"], False)

    def test_manual_review_overrides_with_history(self) -> None:
        screening.set_criteria(self.run_id, ["communication training"], [])
        screening.next_batch(self.run_id)
        result = screening.review(self.run_id, "pubmed:124", "exclude", "No abstract; researcher excluded")
        self.assertEqual(result["selection"]["exclude"], 1)
        item = read_json(run_dir(self.run_id) / RUN_FILE)["articles"]["pubmed:124"]
        self.assertEqual((item["screening"]["method"], len(item["screening_history"])), ("manual", 1))
        with self.assertRaisesRegex(ValueError, "reason"):
            screening.review(self.run_id, "pubmed:124", "include", " ")

    def test_an_uncertain_decision_needs_a_specific_reason(self) -> None:
        screening.set_criteria(self.run_id, ["communication training"], ["veterinary"])
        vague = screening.record_decisions(
            self.run_id, 1, [self.decide("pubmed:125", "uncertain", reason="Ambiguous case for researcher review")]
        )
        self.assertIn("name the criterion", vague["downgraded"][0]["error"])
        specific = screening.record_decisions(
            self.run_id, 1,
            [self.decide("pubmed:125", "uncertain", reason="Abstract does not say whether the veterinary trainees are students")],
        )
        self.assertEqual((specific["recorded"], specific["downgraded"]), (1, []))

    def test_a_vague_reason_from_before_the_check_is_screened_again(self) -> None:
        screening.set_criteria(self.run_id, ["communication training"], ["veterinary"])
        screening.next_batch(self.run_id)
        screening.record_decisions(
            self.run_id, 1, [self.decide("pubmed:125", "uncertain", reason="Ambiguous case for researcher review")]
        )  # downgraded with a validation error: one correction allowed, then the researcher decides
        screening.review(self.run_id, "pubmed:124", "exclude", "Unclear")  # the researcher's own words stay
        path = run_dir(self.run_id)
        manifest = read_json(path / RUN_FILE)
        manifest["articles"]["pubmed:123"]["screening"] = {  # as recorded before reasons were checked
            "decision": "uncertain", "reason": "Ambiguous case for researcher review", "evidence": "",
            "revision": 1, "method": "agent", "reviewed_at": "2026-09-30T09:49:20+00:00",
        }
        save_run(path, manifest)
        batch = screening.next_batch(self.run_id)
        self.assertEqual(([item["uid"] for item in batch["items"]], batch["remaining"]), (["pubmed:123"], 1))
        saved = read_json(path / RUN_FILE)["articles"]
        self.assertEqual(saved["pubmed:123"]["screening_history"][-1]["requeued"], "reason not specific")
        self.assertEqual(saved["pubmed:125"]["screening"]["decision"], "uncertain")
        self.assertEqual(saved["pubmed:124"]["screening"]["method"], "manual")


class BatchSizeTests(Case):
    """A batch is sized by the encoded response a client receives, not by abstract characters."""

    def batch(self, records: list[dict], run_id: str, batch_size: int = 10) -> dict:
        run_id = self.make_run(records, include=False, run_id=run_id)
        screening.set_criteria(run_id, ["communication training"], [])
        return screening.next_batch(run_id, batch_size=batch_size)

    def test_a_batch_stops_at_its_encoded_size(self) -> None:
        long = "Communication training for medical students. " * 120  # about 5,400 characters
        batch = self.batch([record(n, abstract=long) for n in range(10)], "b" * 32)
        self.assertLessEqual(encoded_size(batch), response_bytes())
        self.assertTrue(1 <= len(batch["items"]) < 10)
        self.assertEqual(batch["remaining"], 10)
        self.assertNotIn("oversized", batch)
        self.assertEqual(len(self.batch([record(n) for n in range(10)], "c" * 32)["items"]), 10)

    def test_multibyte_abstracts_are_measured_in_utf8_bytes(self) -> None:
        korean = "환자와 의사의 공동 의사결정에 관한 연구입니다. " * 60  # about 1,700 characters, 4,300 bytes
        batch = self.batch([record(n, abstract=korean) for n in range(10)], "e" * 32)
        self.assertLessEqual(encoded_size(batch), response_bytes())
        self.assertLess(len(batch["items"]), 10)  # by characters alone all ten would have fitted
        self.assertTrue(all(item["abstract"] == korean.strip() for item in batch["items"]))

    def test_one_oversized_record_is_cut_and_marked(self) -> None:
        korean = "공동 의사결정은 환자와 의사가 함께 내리는 결정이다 " * 260  # over the 6,000-character limit; ~16,000 bytes kept
        batch = self.batch([record(1, abstract=korean, title="Shared decision making " * 120)], "f" * 32)
        self.assertLessEqual(encoded_size(batch), response_bytes())
        (item,) = batch["items"]
        self.assertTrue(item["abstract_truncated"])
        self.assertIn("cut", item["abstract_cut"])
        self.assertTrue(korean.startswith(item["abstract"]) and len(item["abstract"]) > 1000)

    def test_the_budget_follows_the_environment(self) -> None:
        long = "Communication training for medical students. " * 120
        with mock.patch.dict(os.environ, {"MED_LIT_RESPONSE_BYTES": "8000"}):
            batch = self.batch([record(n, abstract=long) for n in range(10)], "0a" * 16)
            self.assertLessEqual(encoded_size(batch), 8000)
            self.assertEqual(len(batch["items"]), 1)


class TriageSessionTests(Case):
    def setUp(self) -> None:
        super().setUp()
        self.run_id = self.make_run([record(n, title=f"Study {n}") for n in range(1, 26)], include=False, run_id="d" * 32)
        self.revision = screening.set_criteria(self.run_id, ["communication training"], [])["revision"]

    def triage_all(self, scores: dict[str, int]) -> None:
        while True:
            batch = triage.next_batch(self.run_id)
            if not batch["items"]:
                return
            entries = [{"uid": item["uid"], "score": scores.get(item["uid"], 1)} for item in batch["items"]]
            triage.record_scores(self.run_id, batch["revision"], entries)

    def decide_all(self, items: list[dict]) -> None:
        screening.record_decisions(
            self.run_id, self.revision,
            [{"uid": i["uid"], "decision": "exclude", "reason": "per criteria", "evidence": i["title"]} for i in items],
        )

    def test_more_than_a_session_waits_for_triage_then_screens_the_best_twenty(self) -> None:
        first = screening.next_batch(self.run_id)
        self.assertEqual((first["items"], first["triage_needed"], first["remaining"]), ([], True, 25))
        batch = triage.next_batch(self.run_id)
        self.assertEqual((len(batch["items"]), batch["remaining"], batch["scale"][3]), (25, 25, "clearly meets the criteria"))
        self.assertIn("communication training", batch["criteria"]["include"])
        self.triage_all({"pubmed:25": 3, "pubmed:24": 0, "pubmed:3": 2})
        session: list[str] = []
        while True:
            served = screening.next_batch(self.run_id, batch_size=25)
            if not served["items"]:
                break
            session += [item["uid"] for item in served["items"]]
            self.decide_all(served["items"])
        self.assertEqual(len(session), 20)
        self.assertEqual(session[:2], ["pubmed:25", "pubmed:3"])  # best triage scores first, then search rank
        self.assertNotIn("pubmed:24", session)
        self.assertTrue(served["session_complete"])
        self.assertIn("5 articles wait for later sessions", served["message"])
        later = screening.next_batch(self.run_id, batch_size=25, new_round=True)
        self.assertEqual(len(later["items"]), 5)
        self.assertIn("pubmed:24", [item["uid"] for item in later["items"]])

    def test_triage_batches_stop_at_their_encoded_size_and_reject_strays(self) -> None:
        manifest = read_json(run_dir(self.run_id) / RUN_FILE)
        for item in manifest["articles"].values():
            item["record"]["title"] = "A long title about communication training " * 40  # about 1,700 characters
        save_run(run_dir(self.run_id), manifest)
        batch = triage.next_batch(self.run_id)
        self.assertLessEqual(encoded_size(batch), response_bytes())
        self.assertTrue(5 <= len(batch["items"]) < 25)
        self.assertEqual(batch["remaining"], 25)
        result = triage.record_scores(self.run_id, self.revision, [{"uid": "pubmed:999", "score": 3}])
        self.assertEqual(result["rejected"][0]["error"], "not waiting for triage")
        with self.assertRaisesRegex(ValueError, "revision"):
            triage.record_scores(self.run_id, self.revision + 1, [{"uid": "pubmed:1", "score": 3}])

    def test_new_criteria_need_new_triage(self) -> None:
        self.triage_all({})
        self.assertFalse(triage.needed(read_json(run_dir(self.run_id) / RUN_FILE)))
        screening.set_criteria(self.run_id, ["medical students"], [], replace=True)
        self.assertTrue(triage.needed(read_json(run_dir(self.run_id) / RUN_FILE)))


class BatchTransportTests(Case, unittest.IsolatedAsyncioTestCase):
    async def test_the_measured_size_is_the_text_the_client_receives(self) -> None:
        korean = "환자와 의사의 공동 의사결정에 관한 연구입니다. " * 60
        long = "Communication training for medical students. " * 120
        records = [record(n, abstract=korean if n % 2 else long) for n in range(10)]
        run_id = self.make_run(records, include=False, run_id="ab" * 16)
        screening.set_criteria(run_id, ["communication training"], [])
        async with create_connected_server_and_client_session(mcp._mcp_server) as client:
            result = await client.call_tool("next_screening_batch", {"run_id": run_id, "batch_size": 10})
        self.assertFalse(result.isError)
        text = "\n".join(block.text for block in result.content if block.type == "text")
        self.assertEqual(len(text.encode("utf-8")), encoded_size(result.structuredContent))
        self.assertLessEqual(len(text.encode("utf-8")), response_bytes())
