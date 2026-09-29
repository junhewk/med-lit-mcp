"""Structured literature search: question validation, background retrieval, result import."""

from __future__ import annotations

import html
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .config import ncbi_email, state_dir
from .fetch import safe_http
from .medsearch.cli import _years_ago
from .medsearch.models import Question, ValidationError
from .projects import Project, new_run_dir, run_dir
from .runs import summary
from .store import (
    MANIFEST_VERSION,
    RUN_ID,
    atomic_json,
    atomic_text,
    locked_run,
    now,
    read_json,
    save_run,
)

SOURCES = ("pubmed", "pmc", "openalex", "semantic-scholar", "europepmc")
NCBI_SOURCES = {"pubmed", "pmc"}
RETRYABLE = ("pubmed", "pmc", "openalex", "semantic-scholar")
SEARCH_TIMEOUT = 30 * 60
QUESTION_COMPONENTS = {
    "PICO": (("population", "intervention"), ("comparison", "outcome")),
    "PCC": (("population", "concept"), ("context",)),
}
_ACTIVE: dict[str, subprocess.Popen[bytes]] = {}


def normalize_question(question: Any) -> dict[str, Any]:
    """Coerce a drafted question into the version 2 PICO/PCC shape."""
    if not isinstance(question, dict) or not question.get("question"):
        raise ValueError("Provide a structured PICO/PCC question with a question field")
    framework = str(question.get("framework", "")).upper()
    if framework not in QUESTION_COMPONENTS:
        raise ValueError("framework must be PICO or PCC")
    required, optional = QUESTION_COMPONENTS[framework]
    raw = question.get("components")
    # Models often return components as a list of {id|name, groups}.
    if isinstance(raw, list):
        raw = {str(item.get("id") or item.get("name") or ""): item for item in raw if isinstance(item, dict)}
    if not isinstance(raw, dict):
        raise ValueError("components must be an object keyed by component name")  # noqa: TRY004
    raw = {str(key).strip().lower(): value for key, value in raw.items()}
    components: dict[str, Any] = {}
    for name in (*required, *optional):
        block = raw.get(name)
        if not block:
            if name in required:
                raise ValueError(f"components.{name} is required for {framework}")
            continue
        groups = block.get("groups") if isinstance(block, dict) else None
        if not isinstance(groups, list) or not groups:
            raise ValueError(f"components.{name}.groups must be a non-empty array")
        normalized, labels = [], set()
        for group in groups:
            if not isinstance(group, dict) or not str(group.get("text", "")).strip():
                raise ValueError(f"components.{name} groups need text")
            label = " ".join(str(group.get("label") or group["text"]).split())
            unique, suffix = label, 2
            while unique.casefold() in labels:
                unique, suffix = f"{label} {suffix}", suffix + 1
            labels.add(unique.casefold())
            normalized.append({**{k: v for k, v in group.items() if v is not None}, "label": unique})
        components[name] = {"groups": normalized}
    filters = question.get("filters")
    filters = {k: v for k, v in filters.items() if v not in (None, [])} if isinstance(filters, dict) else {}
    return {
        "schema_version": "2",
        "framework": framework,
        "question": " ".join(str(question["question"]).split()),
        "components": components,
        "filters": filters,
    }


def normalize_sources(sources: list[str] | None) -> list[str] | None:
    if not sources:
        return None
    cleaned = list(dict.fromkeys(s.strip().lower().replace("_", "-") for s in sources if s.strip()))
    unknown = [s for s in cleaned if s not in SOURCES]
    if unknown:
        raise ValueError(f"Unknown sources: {', '.join(unknown)}; use {', '.join(SOURCES)}")
    if "europepmc" in cleaned and len(cleaned) > 1:
        raise ValueError("europepmc runs on its own; search it separately")
    return cleaned


def semantic_scholar_key() -> bool:
    return bool(os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or os.environ.get("S2_API_KEY"))


def default_sources() -> list[str]:
    """Keyless Semantic Scholar shares one public rate limit and mostly answers 429."""
    return ["pubmed", "pmc", "openalex", *(["semantic-scholar"] if semantic_scholar_key() else [])]


def validate_question(question: dict[str, Any], sources: list[str] | None = None) -> dict[str, Any]:

    normalized = normalize_question(question)
    chosen = normalize_sources(sources)
    try:
        Question.from_dict(normalized)
    except ValidationError as exc:
        raise ValueError(f"Question rejected: {exc}") from exc
    warnings = []
    if not normalized["filters"].get("from_date"):
        start = _years_ago(date.today(), 3).isoformat()  # noqa: DTZ011 - same local date as the engine
        warnings.append(
            f"No from_date: the search covers publications from {start} (three-year default). "
            "Set filters.from_date to search earlier."
        )
    effective = chosen or default_sources()
    if not ncbi_email() and NCBI_SOURCES & set(effective):
        warnings.append("NCBI_EMAIL is not set, so pubmed and pmc will be skipped")
    if "semantic-scholar" in effective and not semantic_scholar_key():
        warnings.append(
            "semantic-scholar without SEMANTIC_SCHOLAR_API_KEY uses a shared public rate limit and usually fails with HTTP 429"
        )
    elif not chosen and not semantic_scholar_key():
        warnings.append("semantic-scholar is skipped by default until SEMANTIC_SCHOLAR_API_KEY is set")
    question_id = uuid.uuid4().hex
    atomic_json(
        _draft_path(question_id),
        {"question": normalized, "sources": effective, "warnings": warnings, "created_at": now()},
    )
    return {
        "question_id": question_id,
        "normalized_question": normalized,
        "sources": effective,
        "europepmc_query": europepmc_query(normalized),
        "warnings": warnings,
        "next": "Show this to the researcher; after approval call start_search(question_id).",
    }


def _draft_path(question_id: str) -> Path:
    if not RUN_ID.fullmatch(question_id):
        raise ValueError("Invalid question_id")
    return state_dir() / "questions" / f"{question_id}.json"


def load_draft(question_id: str) -> dict[str, Any]:
    """The question and sources exactly as validate_question showed them."""
    path = _draft_path(question_id)
    if not path.is_file():
        raise ValueError("Unknown question_id; call validate_question first")
    return read_json(path)


def record_uid(record: dict[str, Any]) -> str:
    if record.get("pmcid"):
        value = str(record["pmcid"]).upper()
        return f"pmc:{value if value.startswith('PMC') else 'PMC' + value}"
    if record.get("pmid"):
        return f"pubmed:{record['pmid']}"
    source = str(record.get("source") or "unknown")
    source_id = str(record.get("source_id") or "").strip()
    if not source_id:
        raise ValueError("Search record is missing source_id")
    return f"{source}:{source_id}"


def import_results(path: Path, manifest: dict[str, Any]) -> None:
    """Import a finished search; a retry of failed sources merges into the earlier results."""
    name = manifest.get("search_output", "search")
    output = path / name
    search_manifest = read_json(output / "manifest.json")
    if search_manifest.get("status") != "complete":
        raise ValueError(f"Search is incomplete; inspect {name}/manifest.json")
    results = output / "results.jsonl"
    if not results.exists():
        raise ValueError("Completed search has no results.jsonl")
    report = read_json(output / "summary.json")
    retried = manifest.get("retry_sources")
    if retried:
        failures = {k: v for k, v in manifest.get("source_failures", {}).items() if k not in retried}
        manifest["source_failures"] = failures | report.get("source_failures", {})
        for key in ("records_by_source", "records_filtered_by_source"):
            manifest[key] = manifest.get(key, {}) | report.get(key, {})
    else:
        manifest["source_failures"] = report.get("source_failures", {})
        manifest["records_by_source"] = report.get("records_by_source", {})
        manifest["records_filtered_by_source"] = report.get("records_filtered_by_source", {})
    articles = manifest.setdefault("articles", {})
    added = 0
    with results.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                uid = record_uid(record)
                if uid not in articles:
                    articles[uid] = {"record": record, "fetch": "pending", "wiki": "pending"}
                    added += 1
    if retried:
        manifest["last_retry"] = {"sources": retried, "new_articles": added, "finished_at": now()}
    manifest["search_status"] = "complete"
    for key in ("search_error", "search_pid", "search_output", "retry_sources"):
        manifest.pop(key, None)
    save_run(path, manifest)


def europepmc_query(question: dict[str, Any]) -> str:
    blocks = []
    for component in (question.get("components") or {}).values():
        for group in component.get("groups", []):
            quoted = []
            for term in [group.get("text", ""), *(group.get("synonyms") or [])]:
                clean = " ".join(str(term).replace('"', " ").split())
                if clean:
                    quoted.append(f'TITLE_ABS:"{clean}"')
            if quoted:
                blocks.append("(" + " OR ".join(quoted) + ")")
    if not blocks:
        raise ValueError("Europe PMC search needs structured question component groups")
    return " AND ".join(blocks)


def search_europepmc(path: Path, question: dict[str, Any], limit: int) -> None:
    query = europepmc_query(question)
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(
        {"query": query, "format": "json", "resultType": "core", "pageSize": limit}
    )
    response = json.loads(safe_http(url, timeout=45))
    records = []
    for item in response.get("resultList", {}).get("result", []):
        source_id = str(item.get("id") or "").strip()
        if not source_id:
            continue
        abstract = html.unescape(re.sub(r"<[^>]+>", " ", str(item.get("abstractText") or "")))
        records.append(
            {
                "source": "europepmc",
                "source_id": source_id,
                "pmid": item.get("pmid"),
                "pmcid": item.get("pmcid"),
                "doi": item.get("doi"),
                "title": item.get("title"),
                "abstract": " ".join(abstract.split()),
                "authors": [p.strip() for p in str(item.get("authorString") or "").split(",") if p.strip()],
                "journal": item.get("journalTitle"),
                "publication_date": item.get("firstPublicationDate") or item.get("pubYear"),
                "url": f"https://europepmc.org/article/{item.get('source', 'MED')}/{source_id}",
            }
        )
    output = path / "search"
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "strategy.json", {"source": "europepmc", "query": query, "url": url})
    atomic_json(output / "manifest.json", {"status": "complete", "source": "europepmc"})
    atomic_json(
        output / "summary.json",
        {
            "source_failures": {},
            "records_by_source": {"europepmc": len(records)},
            "records_filtered_by_source": {"europepmc": 0},
            "total_matches": response.get("hitCount"),
        },
    )
    atomic_text(output / "results.jsonl", "".join(json.dumps(r) + "\n" for r in records))


def search_argv(path: Path, manifest: dict[str, Any]) -> list[str]:
    command = [sys.executable, "-m", "med_lit_mcp.medsearch"]
    output = path / manifest.get("search_output", "search")
    sources = manifest.get("retry_sources") or manifest.get("sources")
    if (output / "strategy.json").is_file() and (output / "manifest.json").is_file():
        return [*command, "search", str(output)]
    argv = [
        *command, "run", str(path / "question.json"), "--output", str(output),
        "--limit-per-source", str(manifest["limit_per_source"]),
    ]
    if sources:
        argv += ["--sources", ",".join(sources)]
    return argv


def _launch(path: Path, manifest: dict[str, Any]) -> None:
    argv = search_argv(path, manifest)
    # stdin/stdout must never touch the MCP stdio pipe.
    with (path / "search.stdout.log").open("ab") as out, (path / "search.stderr.log").open("wb") as err:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err, cwd=path, start_new_session=True
        )
    _ACTIVE[manifest["run_id"]] = process
    manifest.update(search_status="running", search_pid=process.pid, search_started_at=now())
    manifest.pop("search_error", None)
    save_run(path, manifest)


def _alive(run_id: str, pid: int | None) -> tuple[bool, int | None]:
    process = _ACTIVE.get(run_id)
    if process is not None:
        code = process.poll()
        return code is None, code
    if not pid:
        return False, None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, None
    except PermissionError:
        return True, None
    return True, None


def _stderr_tail(path: Path) -> str | None:
    try:
        lines = [line.strip() for line in (path / "search.stderr.log").read_text(errors="replace").splitlines()]
    except OSError:
        return None
    lines = [line for line in lines if line]
    return lines[-1][:400] if lines else None


def poll(path: Path, manifest: dict[str, Any]) -> None:
    """Advance a running search: import results when done, fail it when the process died."""
    if manifest["search_status"] != "running":
        return
    run_id = manifest["run_id"]
    alive, code = _alive(run_id, manifest.get("search_pid"))
    if alive:
        started = datetime.fromisoformat(manifest.get("search_started_at") or now())
        if (datetime.now(UTC) - started).total_seconds() <= SEARCH_TIMEOUT:
            return
        try:
            os.killpg(manifest["search_pid"], signal.SIGTERM)
        except (OSError, KeyError, TypeError):
            pass
        _ACTIVE.pop(run_id, None)
        _fail(path, manifest, f"Search exceeded {SEARCH_TIMEOUT // 60} minutes and was stopped")
        return
    _ACTIVE.pop(run_id, None)
    try:
        import_results(path, manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        tail = _stderr_tail(path)
        status = f" with status {code}" if code is not None else ""
        _fail(path, manifest, f"search process exited{status}: {tail}" if tail else str(exc))


def _fail(path: Path, manifest: dict[str, Any], error: str) -> None:
    """A failed first search fails the run; a failed retry leaves the earlier results in place."""
    retried = manifest.pop("retry_sources", None)
    if retried:
        manifest["search_status"] = "complete"
        manifest["last_retry"] = {"sources": retried, "error": error[:500], "finished_at": now()}
    else:
        manifest["search_status"] = "failed"
        manifest["search_error"] = error[:500]
    manifest.pop("search_pid", None)
    manifest.pop("search_output", None)
    save_run(path, manifest)


def _status(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    result = summary(manifest)
    status = {
        key: result[key]
        for key in (
            "run_id", "search_status", "search_error", "candidates", "source_failures",
            "records_by_source", "records_filtered_by_source",
        )
    } | {"run_dir": str(path)}
    if manifest.get("last_retry"):
        status["last_retry"] = manifest["last_retry"]
    retryable = [source for source in status["source_failures"] if source in RETRYABLE]
    if manifest["search_status"] == "complete" and retryable:
        status["note"] = (
            f"{', '.join(retryable)} failed; the other sources' results are usable. Retry later with "
            "resume_search(run_id, retry_failed_sources=true); new records then need screening."
        )
    return status


def _wait(path: Path, wait_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + wait_seconds
    while True:
        with locked_run(path) as manifest:
            poll(path, manifest)
            if manifest["search_status"] != "running" or time.monotonic() >= deadline:
                result = _status(path, manifest)
                if result["search_status"] == "running":
                    result["note"] = "Search is still running; call resume_search with this run_id"
                return result
        time.sleep(1)


def _run_europepmc(path: Path, manifest: dict[str, Any], question: dict[str, Any]) -> None:
    try:
        search_europepmc(path, question, int(manifest["limit_per_source"]))
        import_results(path, manifest)
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        manifest["search_status"] = "failed"
        manifest["search_error"] = str(exc)[:500]
        save_run(path, manifest)


def start_search(
    project: Project,
    question: dict[str, Any],
    sources: list[str] | None = None,
    *,
    limit_per_source: int = 20,
    wait_seconds: float = 45,
) -> dict[str, Any]:
    checked = validate_question(question, sources)
    normalized, chosen = checked["normalized_question"], checked["sources"]
    if not 1 <= limit_per_source <= 200:
        raise ValueError("limit_per_source must be between 1 and 200")
    run_id, path = new_run_dir(project)
    atomic_json(path / "question.json", normalized)
    manifest = {
        "schema_version": MANIFEST_VERSION,
        "run_id": run_id,
        "created_at": now(),
        "search_status": "running",
        "sources": chosen,
        "limit_per_source": limit_per_source,
        "articles": {},
    }
    save_run(path, manifest)
    with locked_run(path) as manifest:
        if chosen == ["europepmc"]:
            _run_europepmc(path, manifest, normalized)
        else:
            _launch(path, manifest)
    result = _wait(path, wait_seconds)
    result["warnings"] = checked["warnings"]
    return project.summary() | result


def resume_search(run_id: str, wait_seconds: float = 45, *, retry_failed_sources: bool = False) -> dict[str, Any]:
    path = run_dir(run_id)
    with locked_run(path) as manifest:
        poll(path, manifest)
        if manifest["search_status"] == "complete":
            failed = [source for source in manifest.get("source_failures", {}) if source in RETRYABLE]
            if not (retry_failed_sources and failed):
                return _status(path, manifest)
            manifest["search_retries"] = int(manifest.get("search_retries") or 0) + 1
            manifest["search_output"] = f"search-retry-{manifest['search_retries']}"
            manifest["retry_sources"] = failed
            _launch(path, manifest)
        if manifest["search_status"] == "failed":
            question = normalize_question(read_json(path / "question.json"))
            atomic_json(path / "question.json", question)
            if manifest.get("sources") == ["europepmc"]:
                _run_europepmc(path, manifest, question)
            else:
                _launch(path, manifest)
    return _wait(path, wait_seconds)
