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

from .config import ncbi_api_key, ncbi_email, state_dir
from .fetch import _ncbi_pause, safe_http
from .medsearch.cli import _years_ago
from .medsearch.models import Question, ValidationError
from .medsearch.preprints import is_preprint
from .projects import Project, new_run_dir, run_dir
from .runs import summary
from .settings import SearchSettings, load_settings, year_filters
from .store import (
    MANIFEST_VERSION,
    RUN_FILE,
    RUN_ID,
    atomic_json,
    atomic_text,
    locked_run,
    now,
    read_json,
    save_run,
)

SOURCES = ("pubmed", "pmc", "openalex", "semantic-scholar", "scopus", "europepmc")
NCBI_SOURCES = {"pubmed", "pmc"}
RETRYABLE = ("pubmed", "pmc", "openalex", "semantic-scholar", "scopus")
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


def scopus_key() -> bool:
    return bool(os.environ.get("SCOPUS_API_KEY"))


def default_sources() -> list[str]:
    """Keyless Semantic Scholar shares one public rate limit and mostly answers 429; Scopus needs a key."""
    return [
        "pubmed", "pmc", "openalex",
        *(["semantic-scholar"] if semantic_scholar_key() else []),
        *(["scopus"] if scopus_key() else []),
    ]


def validate_question(
    question: dict[str, Any], sources: list[str] | None = None, project: Project | None = None
) -> dict[str, Any]:
    """Validate a question; with a project, fill in its settings (years, filters, sources)."""
    config = load_settings(project.root).search if project else SearchSettings()
    normalized = normalize_question(question)
    filters = normalized["filters"]
    if not filters.get("from_date") and not filters.get("to_date"):
        filters.update(year_filters(config.years))
    for key in ("languages", "publication_types"):
        if not filters.get(key) and getattr(config, key):
            filters[key] = list(getattr(config, key))
    # The project setting decides; the engine then filters preprints in each source's own query.
    filters.pop("exclude_preprints", None)
    if not config.preprint_allow:
        filters["exclude_preprints"] = True
    chosen = normalize_sources(sources) or normalize_sources(config.sources)
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
    if "scopus" in effective and not scopus_key():
        warnings.append("scopus needs SCOPUS_API_KEY (uvx med-lit-mcp keys set scopus), so it will fail")
    question_id = uuid.uuid4().hex
    applied = {
        "years": config.years or "last three years (default)",
        "per_source": config.per_source,
        "preprint_allow": config.preprint_allow,
    }
    atomic_json(
        _draft_path(question_id),
        {
            "question": normalized, "sources": effective, "warnings": warnings, "created_at": now(),
            "project": project.name if project else None, "per_source": config.per_source,
            "preprint_allow": config.preprint_allow,
        },
    )
    return {
        "question_id": question_id,
        "normalized_question": normalized,
        "sources": effective,
        "settings": applied,
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


TITLE_MATCH_MIN = 40  # a title shorter than this must match exactly, not as a prefix


def _title_key(title: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", html.unescape(str(title or "")).casefold()).split())


def titles_match(first: Any, second: Any) -> bool:
    """Same title, or one is the other plus a subtitle (for example ': A Meta-Analysis')."""
    a, b = _title_key(first), _title_key(second)
    if not a or not b:
        return False
    short, long = sorted((a, b), key=len)
    return short == long or (len(short) >= TITLE_MATCH_MIN and long.startswith(short + " "))


def _years_close(first: Any, second: Any) -> bool:
    a, b = str(first or "")[:4], str(second or "")[:4]
    return not (a.isdigit() and b.isdigit()) or abs(int(a) - int(b)) <= 1


def _pubmed_by_title(title: str, year: Any) -> dict[str, str] | None:
    email = ncbi_email()
    if not email:
        return None
    base = {"db": "pubmed", "retmode": "json", "tool": "med-lit-mcp", "email": email}
    if ncbi_api_key():
        base["api_key"] = ncbi_api_key()
    phrase = " ".join(re.sub(r'["\[\]]', " ", title).split())
    _ncbi_pause()
    found = json.loads(safe_http(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
        + urllib.parse.urlencode(base | {"term": f'"{phrase}"[Title]', "retmax": 3}), timeout=20,
    ))
    ids = (found.get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return None
    _ncbi_pause()
    summaries = json.loads(safe_http(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
        + urllib.parse.urlencode(base | {"id": ",".join(ids)}), timeout=20,
    )).get("result") or {}
    for pmid in ids:
        item = summaries.get(pmid) or {}
        if titles_match(item.get("title"), title) and _years_close(item.get("pubdate"), year):
            other = {entry.get("idtype"): entry.get("value") for entry in item.get("articleids") or []}
            return {"pmid": pmid, "doi": (other.get("doi") or "").lower() or None, "pmcid": other.get("pmc")}
    return None


def _crossref_by_title(title: str, year: Any) -> dict[str, str] | None:
    params = {"query.bibliographic": title, "rows": 5, "select": "DOI,title,issued"}
    if ncbi_email():
        params["mailto"] = ncbi_email()
    items = (json.loads(safe_http("https://api.crossref.org/works?" + urllib.parse.urlencode(params), timeout=20))
             .get("message") or {}).get("items") or []
    for item in items:
        issued = ((item.get("issued") or {}).get("date-parts") or [[None]])[0][0]
        if titles_match((item.get("title") or [""])[0], title) and _years_close(issued, year):
            return {"doi": str(item["DOI"]).lower()}
    return None


def find_identifiers(record: dict[str, Any]) -> tuple[dict[str, str] | None, str]:
    """A DOI or PMID for a record that came without one: PubMed by title, then Crossref.

    Only a strict title match (same title, or the same plus a subtitle) within a year counts."""
    title = str(record.get("title") or "").strip()
    if len(_title_key(title)) < 20:
        return None, "title too short to look up"
    year = record.get("year") or str(record.get("publication_date") or "")[:4]
    try:
        for lookup, method in ((_pubmed_by_title, "PubMed title match"), (_crossref_by_title, "Crossref title match")):
            found = lookup(title, year)
            if found:
                return {k: v for k, v in found.items() if v}, method
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return None, f"lookup failed: {str(exc)[:120]}"
    return None, "no DOI or PMID found in PubMed or Crossref"


def _record_keys(uid: str, record: dict[str, Any]) -> list[str]:
    """Identifiers that mark two records, possibly from different sources, as one article."""
    keys = [f"uid:{uid}"]
    doi = str(record.get("doi") or "").strip().lower().removeprefix("https://doi.org/")
    if doi:
        keys.append(f"doi:{doi}")
    if record.get("pmid"):
        keys.append(f"pmid:{str(record['pmid']).strip()}")
    if record.get("pmcid"):
        pmcid = str(record["pmcid"]).strip().upper()
        keys.append(f"pmcid:{pmcid if pmcid.startswith('PMC') else 'PMC' + pmcid}")
    return keys


def known_articles(runs: Path) -> dict[str, tuple[str, str]]:
    """Every article already in one of the project's runs: identifier key -> (run_id, uid)."""
    index: dict[str, tuple[str, str]] = {}
    for manifest_path in sorted(runs.glob(f"*/{RUN_FILE}")):
        try:
            other = read_json(manifest_path)
        except (OSError, ValueError):
            continue
        for uid, item in (other.get("articles") or {}).items():
            for key in _record_keys(uid, item.get("record") or {}):
                index.setdefault(key, (other["run_id"], uid))
    return index


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
    update = manifest.get("update")  # a bot's scheduled search into its standing run
    if update:
        pass  # failures and counts belong to this update, recorded below
    elif retried:
        failures = {k: v for k, v in manifest.get("source_failures", {}).items() if k not in retried}
        manifest["source_failures"] = failures | report.get("source_failures", {})
        for key in ("records_by_source", "records_filtered_by_source"):
            manifest[key] = manifest.get(key, {}) | report.get(key, {})
    else:
        manifest["source_failures"] = report.get("source_failures", {})
        manifest["records_by_source"] = report.get("records_by_source", {})
        manifest["records_filtered_by_source"] = report.get("records_filtered_by_source", {})
    articles = manifest.setdefault("articles", {})
    ranks = _ranks(output / "ranked-results.jsonl")
    skipped = manifest.setdefault("skipped_preprints", [])
    # Articles found by an earlier search of this project keep that search's decisions.
    known = known_articles(path.parent)
    already = manifest.setdefault("already_known", {})
    no_identifier = manifest.setdefault("skipped_no_identifier", [])
    unidentified = {entry["uid"] for entry in no_identifier}
    new_unidentified: list[str] = []
    added = 0
    fresh: list[tuple[str, dict[str, Any]]] = []
    with results.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                uid = record_uid(record)
                if uid in articles or uid in skipped or uid in already:
                    continue
                if not manifest.get("preprint_allow", False) and is_preprint(record):
                    skipped.append(uid)
                    continue
                if not (record.get("doi") or record.get("pmid") or record.get("pmcid")):
                    # Every article needs a DOI, PMID or PMCID: it is how one article is recognised
                    # across sources and searches, and how full text is found.
                    # Looked up again whenever a search finds it, in case a DOI was registered since.
                    found, how = find_identifiers(record)
                    if not found:
                        if uid not in unidentified:
                            no_identifier.append({"uid": uid, "title": str(record.get("title") or "")[:200], "reason": how})
                            unidentified.add(uid)
                            new_unidentified.append(uid)
                        continue
                    record.update({k: v for k, v in found.items() if not record.get(k)}, identifier_lookup=how)
                    uid = record_uid(record)
                    if uid in articles or uid in already:
                        continue
                match = next((known[key] for key in _record_keys(uid, record) if key in known), None)
                if match and match[0] == manifest["run_id"]:
                    continue  # the same article from another source in this run
                if match:
                    already[uid] = {"run_id": match[0], "uid": match[1]}
                    continue
                fresh.append((uid, record))
                for key in _record_keys(uid, record):
                    known.setdefault(key, (manifest["run_id"], uid))
    cap = (update or {}).get("cap")
    dropped: list[str] = []
    if cap is not None:
        # Keep the best-ranked new articles; the rest are reported and may be found again later.
        fresh.sort(key=lambda pair: (ranks.get(pair[0]) is None, ranks.get(pair[0]) or 0))
        fresh, dropped = fresh[:cap], [uid for uid, _ in fresh[cap:]]
    for uid, record in fresh:
        articles[uid] = {"record": record, "fetch": "pending", "wiki": "pending", "rank": ranks.get(uid)}
        if update:
            articles[uid]["added_in_update"] = update["number"]
        added += 1
    if update:
        engine = read_json(output / "manifest.json").get("sources") or {}
        manifest.setdefault("updates", []).append(
            update | {
                "finished_at": now(),
                "new_articles": added,
                "dropped_over_cap": dropped,
                "no_identifier": new_unidentified,
                "source_failures": report.get("source_failures", {}),
                "records_by_source": report.get("records_by_source", {}),
                "truncated_sources": sorted(k for k, v in engine.items() if isinstance(v, dict) and v.get("truncated")),
            }
        )
    if retried:
        manifest["last_retry"] = {"sources": retried, "new_articles": added, "finished_at": now()}
    manifest["search_status"] = "complete"
    for key in ("search_error", "search_pid", "search_output", "search_question", "retry_sources", "update"):
        manifest.pop(key, None)
    save_run(path, manifest)


def _ranks(path: Path) -> dict[str, int]:
    """Position of each record in the engine's relevance ranking (1 = first)."""
    if not path.is_file():
        return {}
    ranks: dict[str, int] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    ranks.setdefault(record_uid(json.loads(line)), len(ranks) + 1)
                except ValueError:
                    continue
    return ranks


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
    filters = question.get("filters") or {}
    start = filters.get("from_date") or _years_ago(date.today(), 3).isoformat()  # noqa: DTZ011 - as the engine
    blocks.append(f"FIRST_PDATE:[{start} TO {filters.get('to_date') or '*'}]")
    query = " AND ".join(blocks)
    return f"{query} NOT SRC:PPR" if filters.get("exclude_preprints") else query


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
        *command, "run", str(path / manifest.get("search_question", "question.json")), "--output", str(output),
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
    update = manifest.pop("update", None)
    manifest.pop("search_question", None)
    if update:
        manifest["search_status"] = "complete"
        manifest.setdefault("updates", []).append(update | {"error": error[:500], "finished_at": now()})
    elif retried:
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
            "run_id", "search_status", "search_error", "candidates", "already_known", "skipped_preprints",
            "skipped_no_identifier",
            "source_failures", "records_by_source", "records_filtered_by_source",
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
    limit_per_source: int | None = None,
    wait_seconds: float = 45,
) -> dict[str, Any]:
    config = load_settings(project.root).search
    checked = validate_question(question, sources, project)
    normalized, chosen = checked["normalized_question"], checked["sources"]
    limit_per_source = limit_per_source or config.per_source
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
        "preprint_allow": config.preprint_allow,
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


def start_update(
    path: Path,
    question: dict[str, Any],
    sources: list[str],
    *,
    from_date: str,
    per_source: int,
    cap: int | None,
    wait_seconds: float = 45,
) -> dict[str, Any]:
    """Search again into an existing run, from from_date on (open-ended), adding only new articles."""
    with locked_run(path) as manifest:
        poll(path, manifest)
        if manifest["search_status"] == "running":
            raise ValueError("A search is already running in this run")
        number = len(manifest.get("updates", [])) + 1
        windowed = normalize_question(question)
        windowed["filters"]["from_date"] = from_date
        windowed["filters"].pop("to_date", None)
        name = f"question-update-{number}.json"
        atomic_json(path / name, windowed)
        manifest.update(
            search_output=f"search-update-{number}", search_question=name, sources=sources,
            limit_per_source=per_source,
            update={"number": number, "from_date": from_date, "cap": cap, "started_at": now()},
        )
        _launch(path, manifest)
    return _wait(path, wait_seconds)


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
