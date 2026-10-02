from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from helpers import Case, record

from med_lit_mcp import wiki, wiki_export
from med_lit_mcp.projects import run_dir
from med_lit_mcp.store import RUN_FILE, database, read_json

TEXT_A = (
    "Large language models (LLMs) supported shared decision-making (SDM) in oncology clinics. "
    "Clinicians reviewed the LLM output with patients.\n\n"
    "The chatbot improved patient understanding of treatment options."
)
TEXT_B = (
    "In cardiology, LLMs drafted decision aids. The large language models were checked by "
    "clinicians before shared decision-making conversations."
)


def entity(name: str, mention: str, kind: str = "TECHNOLOGY", **extra: object) -> dict:
    return {"name": name, "entity_type": kind, "description": f"{name} in this study.", "mention": mention, **extra}


def relation(source: str, target: str, evidence: str) -> dict:
    return {"source": source, "target": target, "relationship": "supports", "detail": "", "evidence": evidence}


class WikiTests(Case):
    def setUp(self) -> None:
        super().setUp()
        self.run_id = self.make_run([record(1, title="Oncology SDM"), record(2, title="Cardiology aids")])
        self.sha = {
            "pubmed:1": self.add_text(self.run_id, "pubmed:1", TEXT_A),
            "pubmed:2": self.add_text(self.run_id, "pubmed:2", TEXT_B, "abstract_only"),
        }

    def extract(self, uid: str, entities: list[dict], relationships: list[dict] | None = None, page: int = 0) -> dict:
        return wiki.record_extraction(self.run_id, uid, self.sha[uid], page, entities, relationships or [])

    def counts(self) -> tuple[int, int, int]:
        with database(self.project.db) as conn:
            return tuple(
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("kg_entities", "kg_mentions", "kg_relationship_evidence")
            )

    def test_paging_covers_long_text_deterministically(self) -> None:
        paragraphs = [f"Paragraph {n} " + "word " * 180 for n in range(60)]
        text = "\n\n".join(paragraphs) + "\n\nFinal sentence here."
        pages = wiki.paginate("Title", text)
        self.assertGreater(len(pages), 3)
        self.assertTrue(all(len(page) <= wiki.PAGE_CHARS for page in pages))
        self.assertIn("Final sentence here.", pages[-1])
        self.assertEqual(pages, wiki.paginate("Title", text))
        hard = wiki.paginate("T", "x" * 30_000)
        self.assertEqual(sum(page.count("x") for page in hard), 30_000)
        self.assertTrue(all(len(page) <= wiki.PAGE_CHARS for page in hard))

    def test_next_article_serves_pages_with_vocabulary(self) -> None:
        first = wiki.next_article(self.run_id)
        header = first["header"]
        self.assertEqual((header["uid"], header["page"], header["page_count"]), ("pubmed:1", 0, 1))
        self.assertIn("Large language models (LLMs)", first["text"])
        self.assertEqual(header["research_question"], "How are LLMs used in shared decision making?")
        self.extract("pubmed:1", [entity("large language model", "Large language models")])
        second = wiki.next_article(self.run_id)
        self.assertEqual(second["header"]["uid"], "pubmed:2")
        self.assertIn("abstract", second["header"]["notice"])
        self.assertEqual(second["header"]["known_entities"][0]["name"], "large language model")

    def test_wiki_tasks_walk_extract_duplicates_synthesize_export(self) -> None:
        plan = wiki.work_plan(self.run_id)
        self.assertEqual((plan["step"], len(plan["tasks"])), ("extract", 2))
        self.assertIn("uid='pubmed:1'", plan["tasks"][0])
        self.extract("pubmed:1", [entity("decision aid", "treatment options", "CONCEPT"), entity("chatbot", "The chatbot")])
        self.extract("pubmed:2", [entity("patient decision aid", "decision aids", "CONCEPT"), entity("chatbot", "cardiology")])
        self.assertEqual(wiki.work_plan(self.run_id)["step"], "duplicates")
        pair = wiki.list_duplicate_candidates(self.project, self.run_id)["pairs"][0]
        wiki.resolve_duplicates(self.project, [{"entity_id": pair["entity"]["id"], "candidate_id": pair["candidate"]["id"], "action": "distinct"}])
        plan = wiki.work_plan(self.run_id)
        self.assertEqual((plan["step"], len(plan["tasks"])), ("synthesize", 1))
        context = wiki.next_synthesis(self.project, self.run_id)
        body = "## Overview\n\n" + "Chatbots helped [pubmed:1] and [pubmed:2]. " * 6
        wiki.record_synthesis(self.project, context["id"], context["input_digest"], "Chatbots.", body, ["support"], [])
        self.assertEqual(wiki.work_plan(self.run_id)["step"], "export")

    def test_next_article_can_be_scoped_to_one_article(self) -> None:
        scoped = wiki.next_article(self.run_id, uid="pubmed:2")
        self.assertEqual(scoped["header"]["uid"], "pubmed:2")
        self.extract("pubmed:2", [])
        self.assertEqual(wiki.next_article(self.run_id, uid="pubmed:2")["uid"], "pubmed:2")
        self.assertTrue(wiki.next_article(self.run_id, uid="pubmed:2")["done"])
        self.assertEqual(wiki.next_article(self.run_id)["header"]["uid"], "pubmed:1")
        with self.assertRaisesRegex(ValueError, "not a fetched"):
            wiki.next_article(self.run_id, uid="pubmed:9")

    def test_extraction_validates_and_resolves_acronyms(self) -> None:
        result = self.extract(
            "pubmed:1",
            [
                entity("large language model (LLM)", "Large language models (LLMs)"),
                entity("shared decision-making", "shared decision-making (SDM)", "CONCEPT"),
                entity("Clinician", "clinicians", "PERSON"),
                entity("GPT-5", "GPT-5 was used"),
                entity("PMC12345", "oncology clinics", "CONCEPT"),
            ],
            [
                relation("LLM", "Shared Decision-Making", "supported shared decision-making"),
                relation("large language model", "GPT-5", "supported"),
                relation("LLM", "SDM", "not in the text"),
            ],
        )
        self.assertEqual([item["action"] for item in result["accepted"]], ["created", "created", "created"])
        reasons = {item["name"]: item["reason"] for item in result["rejected"]}
        self.assertIn("verbatim", reasons["GPT-5"])
        self.assertIn("metadata", reasons["PMC12345"])
        self.assertIn("accepted entities", reasons["supports"])
        self.assertEqual(result["relationships_recorded"], 1)
        self.assertEqual(result["article_wiki_status"], "kg_complete")
        second = self.extract(
            "pubmed:2",
            [entity("LLMs", "LLMs drafted"), entity("SDM", "shared decision-making", "CONCEPT")],
        )
        self.assertEqual([item["action"] for item in second["accepted"]], ["alias", "alias"])
        self.assertEqual(second["accepted"][0]["canonical_name"], "large language model")
        found = wiki.find_entities(self.project, "LLM")
        self.assertEqual(found[0]["name"], "large language model")
        self.assertEqual(found[0]["source_count"], 2)

    def test_rerecording_a_page_is_idempotent_and_text_changes_reset(self) -> None:
        payload = [entity("large language model", "Large language models"), entity("chatbot", "The chatbot")]
        rels = [relation("chatbot", "large language model", "The chatbot improved")]
        self.extract("pubmed:1", payload, rels)
        before = self.counts()
        self.extract("pubmed:1", payload, rels)
        self.assertEqual(self.counts(), before)
        self.sha["pubmed:1"] = self.add_text(self.run_id, "pubmed:1", TEXT_A + "\n\nAn added paragraph.")
        with self.assertRaisesRegex(ValueError, "changed"):
            wiki.record_extraction(self.run_id, "pubmed:1", "stale", 0, payload, [])
        page = wiki.next_article(self.run_id)
        self.assertEqual(page["header"]["pages_recorded"], [])
        self.assertEqual(self.counts()[1:], (0, 0))

    def test_duplicates_are_queued_merged_or_kept_distinct(self) -> None:
        self.extract("pubmed:1", [entity("decision aid", "treatment options", "CONCEPT"), entity("chatbot", "The chatbot")])
        result = self.extract(
            "pubmed:2",
            [entity("patient decision aid", "decision aids", "CONCEPT"), entity("chat bot", "cardiology", "TECHNOLOGY")],
        )
        names = {item["name"]: [c["name"] for c in item["candidates"]] for item in result["possible_duplicates"]}
        self.assertEqual(names, {"patient decision aid": ["decision aid"], "chat bot": ["chatbot"]})
        pairs = wiki.list_duplicate_candidates(self.project, self.run_id)["pairs"]
        self.assertEqual(len(pairs), 2)
        by_name = {pair["entity"]["name"]: pair for pair in pairs}
        merge = by_name["chat bot"]
        distinct = by_name["patient decision aid"]
        outcome = wiki.resolve_duplicates(
            self.project,
            [
                {"entity_id": merge["entity"]["id"], "candidate_id": merge["candidate"]["id"], "action": "merge", "keep": "candidate", "reason": "spelling"},
                {"entity_id": distinct["entity"]["id"], "candidate_id": distinct["candidate"]["id"], "action": "distinct", "reason": "different"},
            ]
        )
        self.assertEqual(outcome["pending_duplicates"], 0)
        chatbot = wiki.find_entities(self.project, "chat bot")[0]
        self.assertEqual((chatbot["name"], chatbot["source_count"]), ("chatbot", 2))
        # A distinct pair is not proposed again when the page is re-recorded.
        again = self.extract(
            "pubmed:2", [entity("patient decision aid", "decision aids", "CONCEPT")]
        )
        self.assertEqual(again["possible_duplicates"], [])

    def test_merge_rewires_relationships_and_renames(self) -> None:
        self.extract(
            "pubmed:1",
            [entity("chatbot", "The chatbot"), entity("patient understanding", "patient understanding", "CONCEPT")],
            [relation("chatbot", "patient understanding", "The chatbot improved patient understanding")],
        )
        self.extract(
            "pubmed:2",
            [entity("conversational agent", "LLMs drafted"), entity("clinician review", "checked by clinicians", "METHOD")],
            [relation("conversational agent", "clinician review", "checked by clinicians")],
        )
        keep = wiki.find_entities(self.project, "chatbot")[0]["id"]
        other = wiki.find_entities(self.project, "conversational agent")[0]["id"]
        merged = wiki.merge_entities(self.project, keep, [other], canonical_name="Chatbot", reason="same system")
        self.assertIn("conversational agent", merged["aliases"])
        self.assertEqual((merged["name"], merged["source_count"]), ("Chatbot", 2))
        with database(self.project.db) as conn:
            sources = {r[0] for r in conn.execute("SELECT source_entity_id FROM kg_relationships")}
        self.assertEqual(sources, {keep})
        other_id = wiki.find_entities(self.project, "patient understanding")[0]["id"]
        with self.assertRaisesRegex(ValueError, "already names entity"):
            wiki.merge_entities(self.project, keep, [], canonical_name="patient understanding", reason="rename")
        self.assertNotEqual(other_id, keep)

    def test_synthesis_requires_fresh_digest_and_valid_citations_then_exports(self) -> None:
        wiki_root = self.project.root
        self.extract("pubmed:1", [entity("large language model", "Large language models"), entity("chatbot", "The chatbot")])
        self.extract("pubmed:2", [entity("large language model", "large language models")])
        self.assertEqual(wiki.next_article(self.run_id)["done"], True)
        context = wiki.next_synthesis(self.project, self.run_id)
        self.assertEqual((context["name"], context["remaining"]), ("large language model", 1))
        self.assertEqual({s["uid"] for s in context["sources"]}, {"pubmed:1", "pubmed:2"})
        body = "## Overview\n\n" + "LLMs supported decisions [pubmed:1] and drafted aids [pubmed:2]. " * 4
        args = dict(summary="LLMs in SDM.", key_aspects=["decision support"], related_entities=[{"name": "chatbot"}])
        with self.assertRaisesRegex(ValueError, "changed"):
            wiki.record_synthesis(self.project, context["id"], "stale", synthesis=body, **args)
        with self.assertRaisesRegex(ValueError, "Cite at least one"):
            wiki.record_synthesis(self.project, context["id"], context["input_digest"], synthesis="no citations " * 30, **args)
        with self.assertRaisesRegex(ValueError, "pubmed:9"):
            wiki.record_synthesis(self.project, context["id"], context["input_digest"], synthesis=body + "[pubmed:9]", **args)
        saved = wiki.record_synthesis(
            self.project, context["id"], context["input_digest"], synthesis=body + " See [[chatbot]] and [[unknown thing]].", **args
        )
        self.assertEqual(saved["version"], 1)
        self.assertIn("unknown thing", saved["warnings"][0])
        page = Path(saved["page"]).read_text(encoding="utf-8")
        self.assertTrue(page.startswith("---\ngenerator: med-lit-mcp\n"))
        self.assertEqual(Path(saved["page"]), self.project.root / "entities" / "large language model.md")
        self.assertIn("[pubmed:1](../sources/Researcher%202026%20-%20Oncology%20SDM.md)", page)
        self.assertIn("[chatbot](../entities/chatbot.md)", page)
        self.assertIn("(abstract only)", page)
        self.assertEqual(wiki.next_synthesis(self.project, self.run_id)["done"], True)
        status = read_json(run_dir(self.run_id) / RUN_FILE)["articles"]
        self.assertEqual({item["wiki"] for item in status.values()}, {"complete"})
        self.assertIn("Oncology SDM", (wiki_root / "log.md").read_text(encoding="utf-8"))
        # New evidence marks the synthesis stale and queues it again.
        self.extract("pubmed:1", [entity("large language model", "LLM output")])
        self.assertEqual(wiki.next_synthesis(self.project, self.run_id)["name"], "large language model")

    def test_roles_and_relationships_reach_the_exported_pages(self) -> None:
        self.extract(
            "pubmed:1",
            [
                entity("large language model", "Large language models", role="intervention"),
                entity("shared decision-making", "shared decision-making (SDM)", "CONCEPT", role="outcome"),
                entity("chatbot", "The chatbot"),
            ],
            [relation("large language model", "shared decision-making", "supported shared decision-making")],
        )
        context = wiki.next_synthesis(self.project, self.run_id, min_sources=1)
        self.assertEqual(context["sources"][0]["roles"], ["intervention"])
        self.assertEqual(context["mentions"][0]["role"], "intervention")
        wiki_export.export_wiki(self.project)
        root = self.project.root
        page = (root / "entities" / "large language model.md").read_text(encoding="utf-8")
        self.assertIn('sgb_type: "TOOL"', page)
        self.assertIn('ontology: "med-lit/1"', page)
        self.assertIn("## Relationships", page)
        self.assertIn(
            "- supports [shared decision-making](../entities/shared%20decision-making.md) — “supported shared decision-making”",
            page,
        )
        self.assertIn("(as intervention)", page)
        target = (root / "entities" / "shared decision-making.md").read_text(encoding="utf-8")
        self.assertIn('aliases: ["SDM"]', target)
        self.assertRegex(target, r"- \[large language model\]\(.*\) supports this")
        source = (root / "sources" / "Researcher 2026 - Oncology SDM.md").read_text(encoding="utf-8")
        self.assertIn("· TECHNOLOGY · intervention", source)

    def test_duplicates_are_proposed_across_types_with_the_same_parent(self) -> None:
        self.extract("pubmed:1", [entity("patient understanding", "patient understanding", "CONCEPT")])
        result = self.extract("pubmed:2", [entity("patient understandings scale", "decision aids", "CONDITION")])
        self.assertEqual(result["possible_duplicates"][0]["candidates"][0]["name"], "patient understanding")
        other = self.extract("pubmed:2", [entity("patient understanding survey", "decision aids", "METHOD")])
        self.assertEqual(other["possible_duplicates"], [])  # METHOD and CONCEPT have different parents

    def test_export_writes_pages_and_removes_only_generated_files(self) -> None:
        self.extract("pubmed:1", [entity("chatbot", "The chatbot")])
        root = self.project.root
        (root / "entities").mkdir(parents=True)
        (root / "entities" / "my-notes.md").write_text("# mine")
        (root / "entities" / "old page.md").write_text("---\ngenerator: med-lit-mcp\n---\n# old")
        (root / "index.md").write_text("# My own index")
        result = wiki_export.export_wiki(self.project)
        self.assertEqual((result["entity_pages"], result["source_pages"], result["removed_files"]), (1, 2, 1))
        self.assertTrue((root / "entities" / "my-notes.md").exists())
        self.assertEqual((root / "index.md").read_text(encoding="utf-8"), "# My own index")
        self.assertEqual(result["not_overwritten"], ["index.md"])
        source = (root / "sources" / "Researcher 2026 - Cardiology aids.md").read_text(encoding="utf-8")
        self.assertIn("Only the abstract was available", source)
        self.assertIn("No synthesis yet", (root / "entities" / "chatbot.md").read_text(encoding="utf-8"))
        self.assertFalse(any(path.parts[-2] == ".med-lit" for path in root.rglob("*.md")))
        # Orphaned entities disappear once their only page is re-recorded without them.
        self.extract("pubmed:1", [])
        self.assertEqual(wiki_export.export_wiki(self.project)["removed_orphan_entities"], 1)
        self.assertFalse((root / "entities" / "chatbot.md").exists())

    def test_page_names_are_readable_unique_and_follow_renames(self) -> None:
        self.extract("pubmed:1", [entity("C#", "The chatbot"), entity("C", "patient understanding", "CONCEPT")])
        wiki_export.export_wiki(self.project)
        names = sorted(path.name for path in (self.project.root / "entities").glob("*.md"))
        self.assertEqual(names, ["C (concept).md", "C.md"])  # "#" is not allowed in file names
        keep = wiki.find_entities(self.project, "C#")[0]["id"]
        wiki.merge_entities(self.project, keep, [], canonical_name="C sharp", reason="rename")
        wiki_export.export_wiki(self.project)
        names = sorted(path.name for path in (self.project.root / "entities").glob("*.md"))
        self.assertEqual(names, ["C (concept).md", "C sharp.md"])

    def graph(self) -> dict:
        return read_json(self.project.work / wiki_export.GRAPH_FILE)

    def assert_pages_exist(self, graph: dict) -> None:
        for row in graph["entities"] + graph["articles"]:
            self.assertIsNotNone(row["page"], row)
            self.assertTrue((self.project.root / row["page"]).is_file(), row["page"])

    def build_graph(self) -> None:
        self.extract(
            "pubmed:1",
            [
                entity("large language model", "Large language models (LLMs)", role="intervention"),
                entity("shared decision-making", "shared decision-making (SDM)", "CONCEPT", role="outcome"),
                entity("chatbot", "The chatbot"),
            ],
            [relation("large language model", "shared decision-making", "supported shared decision-making")],
        )
        self.extract(
            "pubmed:2",
            [entity("large language model", "LLMs drafted"), entity("clinician review", "checked by clinicians", "METHOD")],
            [relation("large language model", "clinician review", "checked by clinicians")],
        )

    def test_graph_export_matches_the_database_and_pages(self) -> None:
        self.build_graph()
        result = wiki_export.export_wiki(self.project)
        self.assertTrue(result["graph_written"])
        graph = self.graph()
        self.assertEqual((graph["format"], graph["project"]["id"]), ("med-lit-sgb/1", self.project.id))
        self.assertIsNone(graph["bot_update"])
        self.assert_pages_exist(graph)
        with database(self.project.db) as conn:
            mentions = conn.execute("SELECT COUNT(*) FROM (SELECT DISTINCT article_uid, entity_id, role FROM kg_mentions)").fetchone()[0]
            relationships = conn.execute("SELECT COUNT(*) FROM kg_relationships").fetchone()[0]
            evidence = conn.execute("SELECT COUNT(*) FROM kg_relationship_evidence").fetchone()[0]
            entities = conn.execute("SELECT COUNT(*) FROM kg_entities").fetchone()[0]
        self.assertEqual(len(graph["mentions"]), mentions)
        self.assertEqual(len(graph["relationships"]), relationships)
        self.assertEqual(sum(len(r["evidence"]) for r in graph["relationships"]), evidence)
        self.assertEqual((len(graph["entities"]), len(graph["articles"])), (entities, 2))
        llm = next(e for e in graph["entities"] if e["name"] == "large language model")
        self.assertEqual((llm["type"], llm["sgb_type"], llm["page"]), ("TECHNOLOGY", "TOOL", "entities/large language model.md"))
        self.assertIn({"alias": "LLM", "source": "acronym"}, llm["aliases"])
        self.assertNotIn("large language model", [a["alias"] for a in llm["aliases"]])
        self.assertIn({"article": "pubmed:1", "entity": llm["id"], "role": "intervention"}, graph["mentions"])
        self.assertEqual(graph["relationships"][0]["evidence"], [{"article": "pubmed:1", "quote": "supported shared decision-making"}])
        self.assertEqual({key for a in graph["articles"] for key in a}, {"uid", "title", "doi", "published", "content_type", "page"})

    def test_graph_export_is_unchanged_without_data_changes(self) -> None:
        self.build_graph()
        wiki_export.export_wiki(self.project)
        path = self.project.work / wiki_export.GRAPH_FILE
        before = path.read_bytes()
        self.assertFalse(wiki_export.export_wiki(self.project)["graph_written"])
        self.assertEqual(path.read_bytes(), before)

    def test_an_interrupted_graph_export_keeps_the_previous_file(self) -> None:
        self.build_graph()
        wiki_export.export_wiki(self.project)
        path = self.project.work / wiki_export.GRAPH_FILE
        before = path.read_bytes()
        keep = wiki.find_entities(self.project, "chatbot")[0]["id"]
        wiki.merge_entities(self.project, keep, [], canonical_name="Chat assistant", reason="rename")
        with patch("med_lit_mcp.store.os.replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            wiki_export.export_wiki(self.project)
        self.assertEqual(path.read_bytes(), before)
        self.assert_pages_exist(json.loads(before))
        self.assertEqual([p.name for p in self.project.work.glob(f".{wiki_export.GRAPH_FILE}.*")], [])

    def test_graph_export_shows_merges(self) -> None:
        self.build_graph()
        keep = wiki.find_entities(self.project, "large language model")[0]["id"]
        other = wiki.find_entities(self.project, "chatbot")[0]["id"]
        wiki.merge_entities(self.project, keep, [other], reason="same system")
        wiki_export.export_wiki(self.project)
        graph = self.graph()
        ids = [e["id"] for e in graph["entities"]]
        self.assertIn(keep, ids)
        self.assertNotIn(other, ids)
        self.assertEqual([(m["kept"], m["merged"], m["name"]) for m in graph["merges"]], [(keep, other, "chatbot")])
        self.assert_pages_exist(graph)

    def test_a_withdrawn_article_leaves_the_graph_export(self) -> None:
        self.build_graph()
        with database(self.project.db) as conn:
            wiki.withdraw_article(conn, "pubmed:2", 2)
        wiki_export.export_wiki(self.project)
        text = (self.project.work / wiki_export.GRAPH_FILE).read_text(encoding="utf-8")
        self.assertNotIn("pubmed:2", text)
        graph = json.loads(text)
        self.assertEqual([a["uid"] for a in graph["articles"]], ["pubmed:1"])
        self.assertNotIn("clinician review", [e["name"] for e in graph["entities"]])
        self.assert_pages_exist(graph)

    def test_a_synthesis_writes_the_graph_export_with_every_page(self) -> None:
        self.build_graph()
        context = wiki.next_synthesis(self.project, self.run_id)
        self.assertEqual(context["name"], "large language model")
        body = "## Overview\n\n" + "LLMs supported decisions [pubmed:1] and drafted aids [pubmed:2]. " * 4
        wiki.record_synthesis(
            self.project, context["id"], context["input_digest"], summary="LLMs.", synthesis=body, key_aspects=["aid"]
        )
        graph = self.graph()
        self.assertEqual(len(graph["entities"]), 4)  # entities without a synthesis get their pages too
        self.assert_pages_exist(graph)
        self.assertTrue((self.project.root / "entities" / "chatbot.md").is_file())


TEXT_C = (
    "A randomized trial compared large language models with standardized patients for history-taking "
    "practice. The large language models gave immediate feedback.\n\n"
    "Oncology clinics hosted the sessions."
)


class PageUpdateTests(Case):
    def setUp(self) -> None:
        super().setUp()
        self.run_id = self.make_run([record(1, title="Oncology SDM"), record(2, title="Cardiology aids"), record(3, title="Trial")])
        self.sha = {uid: self.add_text(self.run_id, uid, text) for uid, text in
                    (("pubmed:1", TEXT_A), ("pubmed:2", TEXT_B), ("pubmed:3", TEXT_C))}

    def extract(self, uid: str, entities: list[dict]) -> dict:
        return wiki.record_extraction(self.run_id, uid, self.sha[uid], 0, entities, [])

    def write_first_page(self) -> dict:
        self.extract("pubmed:1", [entity("large language model", "Large language models", role="intervention")])
        self.extract("pubmed:2", [entity("large language model", "LLMs drafted", role="intervention")])
        context = wiki.next_synthesis(self.project, self.run_id)
        self.assertEqual((context["mode"], len(context["mentions"])), ("new", 2))
        body = (
            "## Overview\n\nLLMs supported decisions [pubmed:1] and drafted aids [pubmed:2].\n\n"
            "## Recurring Themes\n\nClinicians checked the output [pubmed:2].\n"
        )
        return wiki.record_synthesis(self.project, context["id"], context["input_digest"], "LLMs.", body, ["support"], [])

    def test_new_evidence_updates_only_the_changed_sections(self) -> None:
        self.write_first_page()
        self.extract("pubmed:3", [entity("large language model", "large language models gave immediate feedback", role="intervention")])
        context = wiki.next_synthesis(self.project, self.run_id)
        self.assertEqual(context["mode"], "update")
        self.assertEqual([m["uid"] for m in context["mentions"]], ["pubmed:3"])  # only the new evidence
        self.assertEqual({s["uid"]: s["new"] for s in context["sources"]}, {"pubmed:1": False, "pubmed:2": False, "pubmed:3": True})
        self.assertIn("Clinicians checked the output", context["current_synthesis"])
        self.assertEqual(context["current_sections"], ["Overview", "Recurring Themes"])
        self.assertIn("Update it; do not rewrite it", context["instructions"])
        with self.assertRaisesRegex(ValueError, "changed sections"):
            wiki.record_synthesis(self.project, context["id"], context["input_digest"])
        saved = wiki.record_synthesis(
            self.project, context["id"], context["input_digest"],
            sections={"Recurring Themes": "## Recurring Themes\n\nClinicians checked the output [pubmed:2]; a trial added feedback [pubmed:3]."},
        )
        self.assertEqual((saved["mode"], saved["version"]), ("update", 2))
        with database(self.project.db) as conn:
            row = conn.execute("SELECT summary, synthesis, stale, sources_json FROM kg_syntheses").fetchone()
        self.assertEqual(row["summary"], "LLMs.")  # kept
        self.assertIn("## Overview\n\nLLMs supported decisions [pubmed:1] and drafted aids [pubmed:2].", row["synthesis"])
        self.assertIn("a trial added feedback [pubmed:3]", row["synthesis"])
        self.assertEqual(row["synthesis"].count("## Recurring Themes"), 1)
        self.assertEqual((row["stale"], json.loads(row["sources_json"])), (0, ["pubmed:1", "pubmed:2", "pubmed:3"]))
        self.assertTrue(wiki.next_synthesis(self.project, self.run_id)["done"])

    def test_setting_only_evidence_does_not_reopen_a_page(self) -> None:
        self.write_first_page()
        self.extract("pubmed:3", [entity("large language model", "large language models", role="context")])
        self.assertTrue(wiki.next_synthesis(self.project, self.run_id)["done"])
        with database(self.project.db) as conn:
            self.assertEqual(conn.execute("SELECT stale, version FROM kg_syntheses").fetchone()[:], (0, 1))

    def test_withdrawn_sources_must_leave_the_page(self) -> None:
        self.write_first_page()
        with database(self.project.db) as conn:
            wiki.withdraw_article(conn, "pubmed:2", min_sources=1)
        context = wiki.next_synthesis(self.project, self.run_id, min_sources=1)
        self.assertEqual((context["mode"], context["removed_sources"], context["mentions"]), ("update", ["pubmed:2"], []))
        with self.assertRaisesRegex(ValueError, r"pubmed:2 withdrawn from the review\); fix sections: Overview$"):
            wiki.record_synthesis(self.project, context["id"], context["input_digest"], sections={"Recurring Themes": ""})
        saved = wiki.record_synthesis(
            self.project, context["id"], context["input_digest"],
            sections={"Overview": "LLMs supported decisions [pubmed:1].", "Recurring Themes": ""},
        )
        with database(self.project.db) as conn:
            text = conn.execute("SELECT synthesis FROM kg_syntheses").fetchone()[0]
        self.assertEqual((saved["version"], "pubmed:2" in text, "Recurring Themes" in text), (2, False, False))

    def test_new_pages_come_before_updates(self) -> None:
        self.write_first_page()
        self.extract("pubmed:3", [entity("large language model", "large language models gave immediate feedback", role="intervention"),
                                  entity("standardized patient", "standardized patients", "METHOD", role="comparator")])
        self.extract("pubmed:1", [entity("large language model", "Large language models", role="intervention"),
                                  entity("standardized patient", "shared decision-making", "METHOD")])
        context = wiki.next_synthesis(self.project, self.run_id)
        self.assertEqual((context["name"], context["mode"]), ("standardized patient", "new"))

    def test_existing_pages_are_backfilled_by_the_migration(self) -> None:
        import sqlite3

        from med_lit_mcp import store

        path = self.root / "old.sqlite3"
        conn = sqlite3.connect(path)
        conn.executescript(store.MIGRATIONS[0] + "; PRAGMA user_version=1;")
        conn.executescript(
            """INSERT INTO articles VALUES ('pubmed:1','T',NULL,NULL,NULL,'2026',NULL,NULL,NULL,'x','full_text','s1',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'2026');
               INSERT INTO kg_entities VALUES (1,'LLM','llm','TECHNOLOGY',NULL,NULL,'2026','2026');
               INSERT INTO kg_extraction_pages VALUES ('pubmed:1','s1',0,1,'{}',NULL,'2026-01-01T00:00:00');
               INSERT INTO kg_mentions VALUES (1,'pubmed:1',1,0,'LLM','LLM text',NULL,'intervention');
               INSERT INTO kg_syntheses VALUES (1,'s','## Overview [pubmed:1]','[]','[]','d',1,0,1,NULL,'2026-02-01T00:00:00');"""
        )
        conn.close()
        with database(path) as migrated:
            self.assertEqual(migrated.execute("SELECT mention_id FROM kg_synthesis_mentions").fetchall()[0][0], 1)
            self.assertEqual(json.loads(migrated.execute("SELECT sources_json FROM kg_syntheses").fetchone()[0]), ["pubmed:1"])
