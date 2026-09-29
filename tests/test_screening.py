from __future__ import annotations

from helpers import RECORD, Case, record

from med_lit_mcp import fetch, screening
from med_lit_mcp.projects import run_dir
from med_lit_mcp.store import RUN_FILE, read_json


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
