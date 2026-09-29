from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from med_lit_mcp.fetch import save_article
from med_lit_mcp.projects import Project, create_project, run_dir
from med_lit_mcp.store import RUN_FILE, atomic_json, database, now

RECORD = {
    "source": "pubmed",
    "source_id": "123",
    "pmid": "123",
    "pmcid": None,
    "title": "Communication training with language models",
    "abstract": "Medical students practised shared decision making with a chatbot.",
    "authors": ["A Researcher"],
    "journal": "Medical Education",
    "publication_date": "2026-01-01",
    "url": "https://pubmed.ncbi.nlm.nih.gov/123/",
    "doi": "10.1/example",
}
QUESTION = {
    "framework": "PCC",
    "question": "How are LLMs used in shared decision making?",
    "components": {
        "population": {"groups": [{"text": "patients", "synonyms": ["clinicians"]}]},
        "concept": {"groups": [{"text": "large language models", "synonyms": ["LLM"]}]},
        "context": {"groups": [{"text": "shared decision making"}]},
    },
}


def record(number: int, **fields: Any) -> dict[str, Any]:
    return {**RECORD, "source_id": str(number), "pmid": str(number), **fields}


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("NCBI_", "MED_LIT_"))}
        env.update(
            MED_LIT_STATE_DIR=str(self.root / "state"),
            MED_LIT_PROJECTS_DIR=str(self.root / "projects"),
            NCBI_EMAIL="tester@example.org",
        )
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.project: Project = create_project("Test review")
        # No network in tests: Unpaywall knows no open-access copy unless a test says otherwise.
        unpaywall = patch("med_lit_mcp.fetch.unpaywall_locations", return_value=[])
        unpaywall.start()
        self.addCleanup(unpaywall.stop)

    def make_run(
        self,
        records: list[dict[str, Any]] | None = None,
        *,
        include: bool = True,
        run_id: str = "a" * 32,
    ) -> str:
        records = records or [RECORD]
        path = self.project.runs / run_id
        path.mkdir(parents=True)
        atomic_json(path / "question.json", {**copy.deepcopy(QUESTION), "schema_version": "2"})
        articles = {}
        for value in records:
            item: dict[str, Any] = {"record": copy.deepcopy(value), "fetch": "pending", "wiki": "pending"}
            if include:
                item["screening"] = {
                    "decision": "include", "reason": "Relevant", "evidence": value["title"],
                    "revision": 1, "method": "agent",
                }
            articles[f"pubmed:{value['pmid']}"] = item
        atomic_json(
            path / RUN_FILE,
            {
                "schema_version": 1,
                "run_id": run_id,
                "created_at": now(),
                "search_status": "complete",
                "sources": ["pubmed"],
                "limit_per_source": 20,
                "selection_revision": 1 if include else None,
                "selection_criteria": {"include": ["communication training"], "exclude": []} if include else None,
                "articles": articles,
            },
        )
        return run_id

    def add_text(self, run_id: str, uid: str, text: str, content_type: str = "full_text") -> str:
        """Store article text as fetch would and mark it fetched in the manifest."""
        from med_lit_mcp.store import locked_run, save_run

        path = run_dir(run_id)
        with locked_run(path) as manifest, database(self.project.db) as conn:
            item = manifest["articles"][uid]
            *_, digest = save_article(conn, run_id, uid, item["record"], (text, content_type, "https://example.org", "test"))
            item.update(fetch=content_type, content_sha256=digest)
            save_run(path, manifest)
        return digest
