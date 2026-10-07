from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from helpers import QUESTION, RECORD, Case
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ListToolsResult
from test_search import FakeProcess

from med_lit_mcp import fetch, search
from med_lit_mcp.guides import GUIDES
from med_lit_mcp.server import mcp

ROOT = Path(__file__).resolve().parents[1]


def assert_complete_annotations(test: unittest.TestCase, result: ListToolsResult) -> None:
    for tool in result.model_dump(mode="json", exclude_none=True)["tools"]:
        annotations = tool.get("annotations", {})
        for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
            test.assertIs(type(annotations.get(hint)), bool, f"{tool['name']}.{hint}")
        if annotations["readOnlyHint"]:
            test.assertFalse(annotations["destructiveHint"], tool["name"])
            test.assertTrue(annotations["idempotentHint"], tool["name"])


def structured(result: Any) -> Any:
    assert not result.isError, result.content[0].text
    value = result.structuredContent
    return value["result"] if set(value) == {"result"} else value


class ServerToolTests(Case, unittest.IsolatedAsyncioTestCase):
    async def test_query_annotations_include_lazy_database_creation(self) -> None:
        self.assertFalse(self.project.db.exists())
        async with create_connected_server_and_client_session(mcp._mcp_server) as client:
            listed = await client.list_tools()
            assert_complete_annotations(self, listed)
            tools = {tool.name: tool for tool in listed.tools}
            for name in ("get_article_page", "find_entities", "list_duplicate_candidates", "get_run_status"):
                self.assertFalse(tools[name].annotations.readOnlyHint, name)
                self.assertTrue(tools[name].annotations.destructiveHint, name)
                self.assertTrue(tools[name].annotations.idempotentHint, name)
                self.assertFalse(tools[name].annotations.openWorldHint, name)
            result = structured(await client.call_tool("find_entities", {"query": "chatbot"}))
        self.assertEqual(result, [])
        self.assertTrue(self.project.db.is_file())

    async def test_full_workflow_through_mcp_tools(self) -> None:
        async with create_connected_server_and_client_session(mcp._mcp_server) as client:
            listed = await client.list_tools()
            assert_complete_annotations(self, listed)
            tools = {tool.name: tool for tool in listed.tools}
            self.assertEqual(len(tools), 33)
            self.assertIn("semantic-scholar", json.dumps(tools["validate_question"].inputSchema))
            self.assertNotIn("components", json.dumps(tools["start_search"].inputSchema))
            # Clients that load tools on demand find tools by name: every tool is named in the
            # instructions or in a guide the instructions point to.
            findable = mcp.instructions + " ".join(GUIDES.values())
            self.assertEqual([name for name in tools if name not in findable], [])
            ontology = await client.call_tool("guide", {"topic": "ontology"})
            self.assertIn("CONDITION", ontology.content[0].text)
            self.assertFalse(tools["validate_question"].annotations.readOnlyHint)
            self.assertFalse(tools["validate_question"].annotations.idempotentHint)
            self.assertFalse(tools["wiki_tasks"].annotations.readOnlyHint)
            self.assertFalse(tools["record_extraction"].annotations.idempotentHint)
            self.assertTrue(tools["record_extraction"].annotations.destructiveHint)
            self.assertTrue(tools["export_wiki"].annotations.destructiveHint)
            self.assertTrue(tools["export_wiki"].annotations.idempotentHint)
            self.assertTrue(tools["start_search"].annotations.openWorldHint)
            self.assertFalse(tools["start_search"].annotations.destructiveHint)
            self.assertTrue(tools["resume_search"].annotations.destructiveHint)

            bad = await client.call_tool("validate_question", {"question": {**QUESTION, "framework": "SPIDER"}})
            self.assertTrue(bad.isError)
            checked = structured(await client.call_tool("validate_question", {"question": QUESTION}))
            self.assertEqual(checked["normalized_question"]["schema_version"], "2")

            with patch.object(search.subprocess, "Popen", side_effect=lambda argv, **kw: FakeProcess(argv, **kw)):
                run = structured(await client.call_tool("start_search", {"question_id": checked["question_id"], "wait_seconds": 0}))
            self.assertEqual(run["project"], "Test review")  # the only project is used by default
            created = structured(await client.call_tool("create_project", {"name": "Second review"}))
            self.assertTrue(created["path"].endswith("Second review"))
            ambiguous = await client.call_tool("list_runs", {})
            self.assertTrue(ambiguous.isError)
            self.assertIn("Several projects", ambiguous.content[0].text)
            run_id = run["run_id"]
            self.assertEqual(run["candidates"], 1)

            await client.call_tool("set_screening_criteria", {"run_id": run_id, "include": ["chatbot"], "exclude": []})
            batch = structured(await client.call_tool("next_screening_batch", {"run_id": run_id}))
            recorded = structured(
                await client.call_tool(
                    "record_screening_decisions",
                    {
                        "run_id": run_id,
                        "revision": batch["revision"],
                        "decisions": [{"uid": batch["items"][0]["uid"], "decision": "include", "reason": "Uses a chatbot", "evidence": "with a chatbot"}],
                    },
                )
            )
            self.assertEqual(recorded["remaining"], 0)

            with patch.object(fetch, "safe_http", side_effect=OSError("offline")):
                fetched = structured(await client.call_tool("fetch_articles", {"run_id": run_id}))
            self.assertEqual(fetched["fetch"]["abstract_only"], 1)

            page = await client.call_tool("next_wiki_article", {"run_id": run_id})
            self.assertEqual(len(page.content), 2)
            header = json.loads(page.content[0].text)
            self.assertTrue(page.content[1].text.startswith(f'<article uid="{header["uid"]}" page="1/1"'))
            extraction = structured(
                await client.call_tool(
                    "record_extraction",
                    {
                        "run_id": run_id, "uid": header["uid"], "content_sha256": header["content_sha256"], "page": 0,
                        "entities": [{"name": "chatbot", "entity_type": "TECHNOLOGY", "description": "Practice partner.", "mention": "a chatbot"}],
                        "relationships": [],
                    },
                )
            )
            self.assertEqual(extraction["article_wiki_status"], "kg_complete")
            self.assertEqual(json.loads((await client.call_tool("next_wiki_article", {"run_id": run_id})).content[0].text)["done"], True)
            synthesis = structured(await client.call_tool("next_synthesis", {"run_id": run_id, "min_sources": 1}))
            self.assertEqual(synthesis["project"], "Test review")
            saved = structured(
                await client.call_tool(
                    "record_synthesis",
                    {
                        "project": "Test review",
                        "entity_id": synthesis["id"], "input_digest": synthesis["input_digest"],
                        "summary": "A chatbot used for practice.",
                        "synthesis": "## Overview\n\n" + f"Students practised with a chatbot [{header['uid']}]. " * 6,
                        "key_aspects": ["practice"], "related_entities": [],
                    },
                )
            )
            self.assertTrue(Path(saved["page"]).is_file())
            exported = structured(await client.call_tool("export_wiki", {"project": "Test review"}))
            self.assertEqual(exported["synthesized_entities"], 1)
            status = structured(await client.call_tool("get_run_status", {"run_id": run_id}))
            self.assertEqual(status["wiki"]["complete"], 1)
            listed = structured(await client.call_tool("list_articles", {"run_id": run_id, "decision": "include"}))
            self.assertEqual(listed["items"][0]["title"], RECORD["title"])
            self.assertEqual(structured(await client.call_tool("list_runs", {"project": "Test review"}))[0]["run_id"], run_id)
            listed_projects = structured(await client.call_tool("list_projects", {}))
            self.assertEqual([p["project"] for p in listed_projects], ["Second review", "Test review"])
            prompts = {prompt.name for prompt in (await client.list_prompts()).prompts}
            self.assertEqual(prompts, {"plan_search", "screen_run", "build_wiki"})


class StdioTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_server_speaks_clean_json_rpc(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "med_lit_mcp"],
                env={"MED_LIT_STATE_DIR": data, "MED_LIT_CONFIG_DIR": data, "PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", data)},
                cwd=str(ROOT),
            )
            async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
                initialized = await client.initialize()
                self.assertIn("search -> screening -> fetch -> wiki", initialized.instructions)
                listed = await client.list_tools()
                assert_complete_annotations(self, listed)
                self.assertEqual(len(listed.tools), 33)
                result = await client.call_tool("list_projects", {})
                self.assertFalse(result.isError)

    async def test_stages_setting_hides_later_stage_tools(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "med_lit_mcp"],
                env={"MED_LIT_STATE_DIR": data, "MED_LIT_CONFIG_DIR": data, "MED_LIT_STAGES": "fetch", "PATH": os.environ["PATH"]},
                cwd=str(ROOT),
            )
            async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
                await client.initialize()
                listed = await client.list_tools()
                assert_complete_annotations(self, listed)
                names = {tool.name for tool in listed.tools}
                prompts = {prompt.name for prompt in (await client.list_prompts()).prompts}
        self.assertEqual(len(names), 19)
        self.assertIn("create_project", names)
        self.assertIn("fetch_articles", names)
        self.assertNotIn("next_wiki_article", names)
        self.assertEqual(prompts, {"plan_search", "screen_run"})

    def test_stage_names_are_validated_and_include_prerequisites(self) -> None:
        from med_lit_mcp import config

        with patch.dict(os.environ, {"MED_LIT_STAGES": "screening"}):
            self.assertEqual(config.enabled_stages(), ("search", "screening"))
        with patch.dict(os.environ, {"MED_LIT_STAGES": "search,wikis"}), self.assertRaisesRegex(ValueError, "wikis"):
            config.enabled_stages()

    def test_check_reports_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            completed = subprocess.run(
                [sys.executable, "-m", "med_lit_mcp", "--check"],
                capture_output=True, text=True, check=True,
                env={**os.environ, "MED_LIT_STATE_DIR": data, "MED_LIT_CONFIG_DIR": data, "NCBI_EMAIL": ""},
            )
        report = json.loads(completed.stdout)
        self.assertEqual((report["database_schema"], report["ncbi_email"], report["tools"]), (2, False, 33))
        self.assertEqual(report["projects"], [])
        self.assertEqual(report["stages"], ["search", "screening", "fetch", "wiki"])
        self.assertIn("NCBI_EMAIL", completed.stderr)
