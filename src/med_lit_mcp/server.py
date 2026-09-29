"""med-lit-mcp: stdio MCP server for staged medical literature review."""

from __future__ import annotations

import argparse
import functools
import json
import sys
from collections.abc import Callable
from typing import Annotated, Any, Literal, TypeVar

import anyio
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import TextContent, ToolAnnotations
from pydantic import Field

from . import (
    __version__,
    config,
    fetch,
    projects,
    runs,
    screening,
    search,
    wiki,
    wiki_export,
)
from .guides import GUIDES
from .schemas import (
    DecisionFilter,
    DuplicateDecision,
    EntityType,
    ExtractedEntity,
    ExtractedRelationship,
    FetchStatus,
    RelatedEntity,
    ResearchQuestion,
    ReviewDecision,
    ScreeningDecision,
    Source,
    WikiStatus,
)

INSTRUCTIONS = """\
med-lit runs an auditable medical literature review: project -> search -> screening -> fetch -> wiki.
Start each stage only when the researcher asks, and get their approval of the search question before
searching. Only verbatim quotes count as evidence; uncertain screening decisions are the
researcher's. State lives on disk: check get_run_status rather than chat history.

Tools by stage (if your client loads tools on demand, look them up by these names):
- projects: list_projects, create_project, open_project
- search: validate_question -> start_search(question_id); resume_search
- screening: set_screening_criteria, next_screening_batch, record_screening_decisions, review_article
- fetch: fetch_articles
- wiki: wiki_tasks (start here and follow it), export_wiki
- status: get_run_status, list_runs, list_articles
- rules: guide(topic) with workflow, question, screening, fetch, wiki, extraction, duplicates,
  synthesis or ontology. Read guide("workflow") first, and the stage's guide before starting it.
"""

mcp = FastMCP("med-lit", instructions=INSTRUCTIONS)
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
LOCAL = ToolAnnotations(readOnlyHint=False, openWorldHint=False)
NETWORK = ToolAnnotations(readOnlyHint=False, openWorldHint=True)
T = TypeVar("T")
RunId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$", description="Run ID from start_search")]
ProjectName = Annotated[
    str | None, Field(description="Project name from list_projects; may be omitted when only one project exists")
]


async def _run(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    return await anyio.to_thread.run_sync(functools.partial(function, *args, **kwargs))


def _client(ctx: Context | None) -> str | None:
    try:
        info = ctx.session.client_params.clientInfo  # type: ignore[union-attr]
        return f"{info.name} {info.version}".strip()
    except AttributeError:
        return None


def _question(question: ResearchQuestion) -> dict[str, Any]:
    return question.model_dump(mode="json", exclude_none=True)


def _page(result: dict[str, Any]) -> list[TextContent]:
    if result.get("done"):
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
    header = result["header"]
    block = (
        f'<article uid="{header["uid"]}" page="{header["page"] + 1}/{header["page_count"]}" '
        f'content_type="{header["content_type"]}">\n{result["text"]}\n</article>'
    )
    return [
        TextContent(type="text", text=json.dumps(header, ensure_ascii=False)),
        TextContent(type="text", text=block),
    ]


@mcp.tool(annotations=LOCAL)
async def create_project(
    name: Annotated[str, Field(min_length=1, max_length=80, description="Review topic, e.g. 'LLMs in shared decision making'")],
    path: Annotated[
        str | None,
        Field(description="Folder for the project (for example inside an Obsidian vault); default ~/med-lit/<name>"),
    ] = None,
) -> dict[str, Any]:
    """Create a project: one folder per review, holding its wiki and (hidden) its data."""
    project = await _run(projects.create_project, name, path)
    return project.summary() | {"note": "Search with start_search(project=...) once the question is approved."}


@mcp.tool(annotations=READ)
async def list_projects() -> list[dict[str, Any]]:
    """List known projects with their folders. available=false means the folder moved or is missing."""
    return await _run(projects.list_projects)


@mcp.tool(annotations=LOCAL)
async def open_project(path: Annotated[str, Field(description="Folder of an existing project")]) -> dict[str, Any]:
    """Register an existing project folder, for example after it was moved or copied from elsewhere."""
    project = await _run(projects.open_project, path)
    return project.summary()


@mcp.tool(annotations=READ)
async def guide(topic: Literal[tuple(GUIDES)]) -> str:  # type: ignore[valid-type]
    """Rules and how-to for a stage: workflow, question, screening, fetch, wiki, extraction,
    duplicates, synthesis or ontology."""
    return GUIDES[topic]


@mcp.tool(annotations=READ)
async def validate_question(
    question: ResearchQuestion, sources: list[Source] | None = None
) -> dict[str, Any]:
    """Check a drafted PICO/PCC search question and return a question_id for start_search.
    Show the result to the researcher and wait for approval. Rules: guide("question")."""
    return await _run(search.validate_question, _question(question), sources)


@mcp.tool(annotations=NETWORK)
async def start_search(
    question_id: Annotated[str, Field(description="From validate_question, after the researcher approved it")],
    project: ProjectName = None,
    limit_per_source: Annotated[int, Field(ge=1, le=200)] = 20,
    wait_seconds: Annotated[int, Field(ge=0, le=240)] = 45,
) -> dict[str, Any]:
    """Search the literature databases with an approved question; creates a run in the project.
    If search_status is 'running', call resume_search(run_id)."""

    def start() -> dict[str, Any]:
        draft = search.load_draft(question_id)
        return search.start_search(
            projects.get_project(project), draft["question"], draft["sources"],
            limit_per_source=limit_per_source, wait_seconds=wait_seconds,
        )

    return await _run(start)


@mcp.tool(annotations=NETWORK)
async def resume_search(
    run_id: RunId,
    wait_seconds: Annotated[int, Field(ge=0, le=240)] = 45,
    retry_failed_sources: Annotated[
        bool,
        Field(description="For a completed run, search again only the sources listed in source_failures and add their new records"),
    ] = False,
) -> dict[str, Any]:
    """Check a running search, retry a failed one, or retry just the failed sources of a completed run."""
    return await _run(search.resume_search, run_id, wait_seconds, retry_failed_sources=retry_failed_sources)


@mcp.tool(annotations=LOCAL)
async def set_screening_criteria(
    run_id: RunId,
    include: Annotated[list[str], Field(description="Inclusion criteria in the researcher's words")],
    exclude: Annotated[list[str], Field(description="Exclusion criteria in the researcher's words")],
    replace: Annotated[bool, Field(description="Required to change existing criteria; starts a new revision and re-screens everything")] = False,
) -> dict[str, Any]:
    """Save the researcher's inclusion and exclusion criteria for screening. Rules: guide("screening")."""
    return await _run(screening.set_criteria, run_id, include, exclude, replace=replace)


@mcp.tool(annotations=LOCAL)
async def next_screening_batch(run_id: RunId, batch_size: Annotated[int, Field(ge=1, le=25)] = 10) -> dict[str, Any]:
    """Get the next unscreened titles/abstracts with the criteria to judge them against."""
    return await _run(screening.next_batch, run_id, batch_size)


@mcp.tool(annotations=LOCAL)
async def record_screening_decisions(
    run_id: RunId,
    revision: Annotated[int, Field(description="revision from next_screening_batch")],
    decisions: Annotated[list[ScreeningDecision], Field(min_length=1, max_length=25)],
    ctx: Context,
) -> dict[str, Any]:
    """Record screening decisions; include/exclude need a verbatim quote as evidence."""
    return await _run(
        screening.record_decisions,
        run_id,
        revision,
        [decision.model_dump() for decision in decisions],
        client=_client(ctx),
    )


@mcp.tool(annotations=LOCAL)
async def review_article(
    run_id: RunId,
    uid: str,
    decision: ReviewDecision,
    reason: Annotated[str, Field(min_length=1, max_length=1000)],
    ctx: Context,
) -> dict[str, Any]:
    """Record the researcher's own include/exclude decision and reason for one article."""
    return await _run(screening.review, run_id, uid, decision, reason, client=_client(ctx))


@mcp.tool(annotations=NETWORK)
async def fetch_articles(
    run_id: RunId,
    uids: Annotated[list[str] | None, Field(description="Specific included articles; default is the next pending ones")] = None,
    max_items: Annotated[int, Field(ge=1, le=20)] = 5,
    retry_failed: Annotated[bool, Field(description="Also retry articles whose fetch failed")] = False,
    retry_abstract_only: Annotated[bool, Field(description="Also retry articles that only have an abstract")] = False,
) -> dict[str, Any]:
    """Fetch full text of included articles (PMC, Unpaywall open access, else abstract); repeat
    while remaining > 0. Rules: guide("fetch")."""
    return await _run(
        fetch.fetch_batch,
        run_id,
        uids,
        max_items=max_items,
        retry_failed=retry_failed,
        retry_abstract_only=retry_abstract_only,
    )


@mcp.tool(annotations=READ)
async def wiki_tasks(run_id: RunId) -> dict[str, Any]:
    """Start here to build or update the wiki: the current step as self-contained tasks, each sized
    for one fresh subagent. Rules: guide("wiki")."""
    return await _run(wiki.work_plan, run_id)


@mcp.tool(annotations=LOCAL, structured_output=False)
async def next_wiki_article(
    run_id: RunId,
    max_pages: Annotated[int | None, Field(ge=1, le=50, description="Limit pages read for the next article")] = None,
    uid: Annotated[str | None, Field(description="Serve only this article's pages (for a subagent that owns one article)")] = None,
) -> list[TextContent]:
    """Get the next page of article text to extract wiki entities from (a JSON header plus the page)."""
    return _page(await _run(wiki.next_article, run_id, max_pages, uid))


@mcp.tool(annotations=READ, structured_output=False)
async def get_article_page(run_id: RunId, uid: str, page: Annotated[int, Field(ge=0)]) -> list[TextContent]:
    """Re-read one page of a fetched article (pages are numbered from 0)."""
    return _page(await _run(wiki.get_page, run_id, uid, page))


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True, openWorldHint=False))
async def record_extraction(
    run_id: RunId,
    uid: str,
    content_sha256: str,
    page: Annotated[int, Field(ge=0)],
    entities: Annotated[list[ExtractedEntity], Field(max_length=30)],
    relationships: Annotated[list[ExtractedRelationship], Field(max_length=40)],
    ctx: Context,
) -> dict[str, Any]:
    """Record the entities and relationships extracted from one page (re-recording replaces it).
    Rules: guide("extraction")."""
    return await _run(
        wiki.record_extraction,
        run_id,
        uid,
        content_sha256,
        page,
        [entity.model_dump() for entity in entities],
        [relation.model_dump() for relation in relationships],
        client=_client(ctx),
    )


@mcp.tool(annotations=READ)
async def find_entities(
    query: str,
    project: ProjectName = None,
    entity_type: EntityType | None = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> list[dict[str, Any]]:
    """Search a project's wiki entities by name, alias, acronym, or spelling variant."""
    return await _run(lambda: wiki.find_entities(projects.get_project(project), query, entity_type, limit))


@mcp.tool(annotations=READ)
async def list_duplicate_candidates(
    project: ProjectName = None,
    run_id: Annotated[RunId | None, Field(description="Only pairs involving this run's articles")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """List entity pairs that may be the same thing, with an example mention of each."""
    return await _run(lambda: wiki.list_duplicate_candidates(projects.get_project(project), run_id, limit))


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True, openWorldHint=False))
async def resolve_duplicates(
    decisions: Annotated[list[DuplicateDecision], Field(min_length=1, max_length=50)],
    project: ProjectName = None,
) -> dict[str, Any]:
    """Merge or keep distinct pairs of possible duplicate entities. Rules: guide("duplicates")."""
    payload = [decision.model_dump() for decision in decisions]
    return await _run(lambda: wiki.resolve_duplicates(projects.get_project(project), payload))


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True, openWorldHint=False))
async def merge_entities(
    keep_id: int,
    merge_ids: Annotated[list[int], Field(max_length=10, description="Entities folded into keep_id; empty to only rename or retype")],
    reason: Annotated[str, Field(min_length=1, max_length=400)],
    project: ProjectName = None,
    canonical_name: str | None = None,
    entity_type: EntityType | None = None,
) -> dict[str, Any]:
    """Merge entities into one, and/or rename or retype an entity."""
    return await _run(
        lambda: wiki.merge_entities(
            projects.get_project(project), keep_id, merge_ids,
            canonical_name=canonical_name, entity_type=entity_type, reason=reason,
        )
    )


@mcp.tool(annotations=LOCAL)
async def next_synthesis(
    project: ProjectName = None,
    run_id: Annotated[RunId | None, Field(description="Only entities from this run's articles")] = None,
    min_sources: Annotated[int, Field(ge=1, le=10, description="Entities need at least this many source articles")] = 2,
) -> dict[str, Any]:
    """Get the next entity whose wiki page needs writing, with its evidence from the project."""

    def pick() -> dict[str, Any]:
        owner = projects.locate(run_id)[0] if run_id and project is None else projects.get_project(project)
        return wiki.next_synthesis(owner, run_id, min_sources)

    return await _run(pick)


@mcp.tool(annotations=LOCAL)
async def record_synthesis(
    entity_id: int,
    input_digest: str,
    summary: Annotated[str, Field(min_length=1, max_length=800, description="2-3 sentence overview")],
    synthesis: Annotated[str, Field(min_length=200, max_length=20000, description="Markdown article citing sources as [uid] and linking entities as [[Name]]")],
    key_aspects: Annotated[list[str], Field(min_length=1, max_length=10)],
    related_entities: Annotated[list[RelatedEntity], Field(max_length=30)],
    ctx: Context,
    project: ProjectName = None,
) -> dict[str, Any]:
    """Save an entity page written only from next_synthesis evidence. Rules: guide("synthesis")."""
    return await _run(
        lambda **kwargs: wiki.record_synthesis(projects.get_project(project), **kwargs),
        entity_id=entity_id,
        input_digest=input_digest,
        summary=summary,
        synthesis=synthesis,
        key_aspects=key_aspects,
        related_entities=[entity.model_dump() for entity in related_entities],
        client=_client(ctx),
    )


@mcp.tool(annotations=LOCAL)
async def export_wiki(project: ProjectName = None) -> dict[str, Any]:
    """Rewrite every Markdown page of a project's wiki (entities, sources, index, log) from its database."""
    return await _run(lambda: wiki_export.export_wiki(projects.get_project(project)))


@mcp.tool(annotations=READ)
async def list_runs(project: ProjectName = None, limit: Annotated[int, Field(ge=1, le=100)] = 20) -> list[dict[str, Any]]:
    """List a project's searches (runs), newest first."""
    return await _run(lambda: runs.list_runs(projects.get_project(project), limit))


@mcp.tool(annotations=READ)
async def get_run_status(run_id: RunId) -> dict[str, Any]:
    """Show a run's progress in every stage and the available next steps."""

    def status() -> dict[str, Any]:
        project, path = projects.locate(run_id)
        manifest = runs.load_manifest(run_id)
        from .store import database

        with database(project.db) as conn:
            duplicates = conn.execute("SELECT COUNT(*) FROM kg_duplicate_candidates WHERE status='pending'").fetchone()[0]
            stale = conn.execute("SELECT COUNT(*) FROM kg_syntheses WHERE stale=1").fetchone()[0]
        return project.summary() | runs.summary(manifest) | {
            "question": runs.question_text(path),
            "pending_duplicates": duplicates,
            "stale_syntheses": stale,
            "available_next_steps": runs.next_steps(manifest),
        }

    return await _run(status)


@mcp.tool(annotations=READ)
async def list_articles(
    run_id: RunId,
    decision: DecisionFilter | None = None,
    fetch_status: FetchStatus | None = None,
    wiki_status: WikiStatus | None = None,
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """List a run's articles with their screening, fetch, and wiki status."""
    return await _run(runs.list_articles, run_id, decision=decision, fetch=fetch_status, wiki=wiki_status, offset=offset, limit=limit)


@mcp.prompt()
def plan_search(question: str) -> str:
    """Draft and validate a structured search for a research question."""
    return (
        f"Research question: {question}\n\nDraft a PICO or PCC question in my terms: one group per "
        "separately required facet, only true synonyms within a group, no ambiguous bare acronyms, "
        "and PCC context only for a setting. Suggest candidate MeSH headings. Call "
        "validate_question and show me the normalized question, sources, date range, and warnings. "
        "Do not start the search until I approve it."
    )


@mcp.prompt()
def screen_run(run_id: str) -> str:
    """Screen a run's search results against criteria."""
    return (
        f"Screen run {run_id}. If no criteria are set, ask me for inclusion and exclusion criteria "
        "first. Then repeat next_screening_batch and record_screening_decisions until nothing "
        "remains, and summarize the include/exclude/uncertain counts."
    )


@mcp.prompt()
def build_wiki(run_id: str) -> str:
    """Build the evidence-linked wiki for a run's fetched articles."""
    return f"Build the wiki for run {run_id}: call wiki_tasks and follow it until the wiki is exported."


STAGE_TOOLS = {
    "search": ("validate_question", "start_search", "resume_search"),
    "screening": ("set_screening_criteria", "next_screening_batch", "record_screening_decisions", "review_article"),
    "fetch": ("fetch_articles",),
    "wiki": (
        "wiki_tasks", "next_wiki_article", "get_article_page", "record_extraction", "find_entities",
        "list_duplicate_candidates", "resolve_duplicates", "merge_entities", "next_synthesis",
        "record_synthesis", "export_wiki",
    ),
}
STAGE_PROMPTS = {"screening": ("screen_run",), "wiki": ("build_wiki",)}


def apply_stages() -> tuple[str, ...]:
    """Hide the tools of stages left out of MED_LIT_STAGES (project and status tools always stay)."""
    enabled = config.enabled_stages()
    for stage in config.STAGES:
        if stage not in enabled:
            for name in STAGE_TOOLS[stage]:
                mcp._tool_manager._tools.pop(name, None)
            for name in STAGE_PROMPTS.get(stage, ()):
                mcp._prompt_manager._prompts.pop(name, None)
    return enabled


def check() -> int:
    """Report configuration without starting stdio (safe to print)."""
    from .store import MIGRATIONS

    report = {
        "version": __version__,
        "projects_registry": str(config.state_dir() / "projects.json"),
        "new_projects_in": str(config.projects_dir()),
        "projects": projects.list_projects(),
        "database_schema": len(MIGRATIONS),
        "ncbi_email": bool(config.ncbi_email()),
        "ncbi_api_key": bool(config.ncbi_api_key()),
        "semantic_scholar_api_key": search.semantic_scholar_key(),
        "stages": list(apply_stages()),
        "tools": len(mcp._tool_manager.list_tools()),
    }
    print(json.dumps(report, indent=2))  # noqa: T201
    if not report["ncbi_email"]:
        print("warning: NCBI_EMAIL is not set; pubmed/pmc search and fetch need it", file=sys.stderr)  # noqa: T201
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="med-lit-mcp", description="med-lit-mcp stdio MCP server")
    parser.add_argument("--version", action="version", version=f"med-lit-mcp {__version__}")
    parser.add_argument("--check", action="store_true", help="Print configuration and exit")
    args = parser.parse_args(argv)
    if args.check:
        raise SystemExit(check())
    apply_stages()
    mcp.run("stdio")
