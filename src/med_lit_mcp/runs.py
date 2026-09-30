"""Run summaries, listings, and shared stage preconditions."""

from __future__ import annotations

import json
from typing import Any

from .config import enabled_stages
from .projects import Project, run_dir
from .store import RUN_FILE, question_text, read_json

FETCH_STATES = ("pending", "full_text", "abstract_only", "failed", "skipped")
WIKI_STATES = ("pending", "kg_complete", "complete", "no_entities", "failed")


def is_eligible(item: dict[str, Any], revision: int | None) -> bool:
    screening = item.get("screening") or {}
    return bool(
        revision is not None
        and screening.get("revision") == revision
        and screening.get("decision") == "include"
    )


def summary(manifest: dict[str, Any]) -> dict[str, Any]:
    articles = manifest["articles"]
    revision = manifest.get("selection_revision")
    screened = [
        (item.get("screening") or {})
        for item in articles.values()
        if (item.get("screening") or {}).get("revision") == revision and revision is not None
    ]
    return {
        "run_id": manifest["run_id"],
        "search_status": manifest["search_status"],
        "search_error": manifest.get("search_error"),
        "candidates": len(articles),
        "selection_revision": revision,
        "selection_criteria": manifest.get("selection_criteria"),
        "selection": {
            status: sum(entry.get("decision") == status for entry in screened)
            for status in ("include", "exclude", "uncertain")
        }
        | {"pending": len(articles) - len(screened)},
        "source_failures": manifest.get("source_failures", {}),
        "records_by_source": manifest.get("records_by_source", {}),
        "records_filtered_by_source": manifest.get("records_filtered_by_source", {}),
        "skipped_preprints": len(manifest.get("skipped_preprints", [])),
        "fetch": {state: sum(item["fetch"] == state for item in articles.values()) for state in FETCH_STATES},
        "wiki": {state: sum(item["wiki"] == state for item in articles.values()) for state in WIKI_STATES},
    }


def require_selection_complete(manifest: dict[str, Any], stage: str) -> int:
    if manifest["search_status"] != "complete":
        raise ValueError(f"Search must complete before {stage}")
    revision = manifest.get("selection_revision")
    if revision is None:
        raise ValueError(f"Set screening criteria and screen articles before {stage}")
    pending = sum(
        (item.get("screening") or {}).get("revision") != revision for item in manifest["articles"].values()
    )
    if pending:
        raise ValueError(f"Screening is not finished ({pending} pending); continue next_screening_batch")
    return revision


def selected_uids(manifest: dict[str, Any], uids: list[str] | None) -> list[str]:
    revision = manifest.get("selection_revision")
    articles = manifest["articles"]
    if not uids:
        chosen = [uid for uid, item in articles.items() if is_eligible(item, revision)]
        if not chosen:
            raise ValueError("No included articles; review uncertain decisions with review_article")
        return chosen
    unknown = sorted(set(uids) - set(articles))
    if unknown:
        raise ValueError(f"Unknown article IDs: {', '.join(unknown)}")
    rejected = [uid for uid in uids if not is_eligible(articles[uid], revision)]
    if rejected:
        raise ValueError(f"Articles are not included in the current selection: {', '.join(rejected)}")
    return list(dict.fromkeys(uids))


def list_runs(project: Project, limit: int = 20) -> list[dict[str, Any]]:
    values = []
    for path in project.runs.glob(f"*/{RUN_FILE}"):
        try:
            manifest = read_json(path)
            item = summary(manifest)
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue
        values.append(
            {
                "run_id": item["run_id"],
                "created_at": manifest.get("created_at"),
                "question": question_text(path.parent),
                "search_status": item["search_status"],
                "candidates": item["candidates"],
                "selection": item["selection"],
                "read_only": manifest.get("schema_version") != 1,
            }
        )
    values.sort(key=lambda value: value["created_at"] or "", reverse=True)
    return values[:limit]


def load_manifest(run_id: str) -> dict[str, Any]:
    return read_json(run_dir(run_id) / RUN_FILE)


def list_articles(
    run_id: str,
    *,
    decision: str | None = None,
    fetch: str | None = None,
    wiki: str | None = None,
    uids: list[str] | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    manifest = load_manifest(run_id)
    revision = manifest.get("selection_revision")
    wanted = set(uids or [])
    rows = []
    for uid, item in manifest["articles"].items():
        screening = item.get("screening") or {}
        current = screening if screening.get("revision") == revision else {}
        if wanted and uid not in wanted:
            continue
        if decision and (current.get("decision") or "pending") != decision:
            continue
        if fetch and item["fetch"] != fetch:
            continue
        if wiki and item["wiki"] != wiki:
            continue
        record = item["record"]
        rows.append(
            {
                "uid": uid,
                "title": record.get("title"),
                "year": str(record.get("publication_date") or "")[:4] or None,
                "screening": current or None,
                "fetch": item["fetch"],
                "fetch_method": item.get("fetch_method"),
                "source_url": item.get("source_url") or record.get("url"),
                "wiki": item["wiki"],
                "error": item.get("error"),
            }
        )
    return {"run_id": run_id, "total": len(rows), "offset": offset, "items": rows[offset : offset + limit]}


def next_steps(manifest: dict[str, Any]) -> list[str]:
    item = summary(manifest)
    selection = item["selection"]
    if item["search_status"] == "running":
        return ["resume_search to check on the running search"]
    if item["search_status"] == "failed":
        return ["resume_search to retry the failed search"]
    if item["selection_revision"] is None:
        return ["set_screening_criteria once the researcher states inclusion/exclusion criteria"]
    if selection["pending"]:
        return ["next_screening_batch / record_screening_decisions until pending is 0"]
    steps = []
    if selection["uncertain"]:
        steps.append("review_article for uncertain articles, with the researcher's decision")
    if item["fetch"]["pending"] and selection["include"] and "fetch" in enabled_stages():
        steps.append("fetch_articles for included articles")
    fetched = item["fetch"]["full_text"] + item["fetch"]["abstract_only"]
    if "wiki" not in enabled_stages():
        return steps or ["nothing further is required"]
    if fetched and item["wiki"]["pending"]:
        steps.append("wiki_tasks to build the wiki")
    if item["wiki"]["kg_complete"]:
        steps.append("wiki_tasks to write entity pages")
    return steps or ["export_wiki, or nothing further is required"]
