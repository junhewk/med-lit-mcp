"""Bot projects: a frozen question and criteria, searched on a schedule (a Hermes cron job).

A bot project has one standing run that every scheduled run adds to. One scheduled run is:

    bot_start   lock the project and search the look-back window; articles new to the standing
                run wait as candidates
    bot_next    the next piece of work for the agent: triage the candidates' titles (the best-scored
                bot.max_new_articles, at most 20, are kept), screen them, fetch, wiki tasks, until
                the run's capped work is done
    bot_finish  refresh the wiki, write updates/<date>.md and release the lock; the returned
                message is the run's report, or [SILENT] when nothing happened

The question and criteria change only through `med-lit-mcp setup bot --edit`, which records each
version: new criteria re-screen everything collected (articles that become excludes are withdrawn
from the wiki), and a new question backfills from the bot's start date.
"""

from __future__ import annotations

import copy
import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import screening, search, triage, wiki
from .projects import Project, create_project, get_project, new_run_dir
from .platforms import process_alive
from .runs import is_eligible
from .settings import apply_changes, load_settings, write_settings
from .store import (
    MANIFEST_VERSION,
    RUN_FILE,
    atomic_json,
    atomic_text,
    database,
    file_lock,
    locked_run,
    now,
    read_json,
    save_run,
)

BOT_FILE = "bot.json"
UPDATES = "updates"
SILENT = "[SILENT]"
# A run holds its project while the server process that started it is alive and still writing.
# Hermes stops a cron run after 10 idle minutes, so an hour without writes means it is gone.
STALE = timedelta(hours=1)
SEARCH_WAIT = 200  # seconds a bot tool waits for its search before handing back
MAX_TASK_REPEATS = 2  # the same wiki task handed out three times with no progress ends the run


def cron_prompt(name: str) -> str:
    """The self-contained instruction a scheduled job runs."""
    return (
        f'You are the med-lit literature bot for the project "{name}". Work only with the med-lit tools.\n'
        f'1. Call bot_start(project="{name}"). If it reports busy, reply with its message and stop.\n'
        f'2. Call bot_next(project="{name}") and do exactly what its "do" says, then call bot_next again. '
        'When it gives a wiki "task", call delegate_task with that task text as the goal, wait for the '
        "subagent to finish, then call bot_next again.\n"
        f'3. When bot_next says finish, call bot_finish(project="{name}") and reply with its "message" '
        "exactly, nothing else.\n"
        "Keep calling tools until bot_finish: never end your reply before it, and never reply with a plan or "
        "a note to yourself. If you cannot go on (a tool keeps failing), reply with [CRON_FAILURE] on the "
        "first line, then say why.\n"
        "Triage: score each title alone 0-3 by what the study is about, not by shared words. "
        "Screening: judge only the title and abstract against the criteria; include and exclude need a "
        "verbatim quote as evidence; when unsure choose uncertain, and the researcher will decide."
    )


def _state_path(project: Project) -> Path:
    return project.work / BOT_FILE


@contextmanager
def _state(project: Project) -> Iterator[dict[str, Any]]:
    path = _state_path(project)
    with file_lock(path.with_suffix(".lock")):
        state = read_json(path) if path.is_file() else {}
        before = copy.deepcopy(state)
        yield state
        if state != before:
            atomic_json(path, state)


def read_state(project: Project) -> dict[str, Any]:
    path = _state_path(project)
    if not path.is_file():
        raise ValueError(f"'{project.name}' is not a bot project; create one with `uvx med-lit-mcp setup bot`")
    return read_json(path)


def _bot_project(name: str | None) -> Project:
    project = get_project(name)
    if project.mode != "bot":
        raise ValueError(f"'{project.name}' is an interactive project; bot tools work only on bot projects")
    return project


def bot_brief(project: Project) -> dict[str, Any]:
    try:
        state = read_state(project)
    except (OSError, ValueError):
        return {"status": "unknown"}
    last = (state.get("history") or [None])[-1]
    return {
        "status": state.get("status"),
        "schedule": state.get("schedule"),
        "running": bool(state.get("active")),
        "last_run": {k: last.get(k) for k in ("finished_at", "new_articles", "report")} if last else None,
    }


def _update_numbers(run: dict[str, Any] | None) -> list[int]:
    """The search updates a run covers (a run that finished a stopped one covers both)."""
    if not run:
        return []
    return run.get("updates") or ([run["update"]] if run.get("update") else [])


def latest_update(project: Project) -> int | None:
    """The newest bot update whose work the wiki may include: the running run's, else the last run's."""
    if project.mode != "bot":
        return None
    try:
        state = read_state(project)
    except (OSError, ValueError):
        return None
    last = (state.get("history") or [None])[-1]
    return max(_update_numbers(state.get("active")) or _update_numbers(last), default=None)


def _today() -> date:
    """The machine's local date: the day a scheduled run belongs to (05:30 KST is still yesterday in UTC)."""
    return datetime.now(UTC).astimezone().date()


def create_bot(
    name: str,
    question: dict[str, Any],
    sources: list[str],
    criteria: dict[str, list[str]],
    *,
    schedule: str,
    changes: dict[str, Any] | None = None,
    path: str | None = None,
) -> Project:
    """A new, empty bot project with its standing run, question version 1 and criteria revision 1."""
    if "europepmc" in sources:
        raise ValueError("Bots cannot search europepmc; choose pubmed, pmc, openalex, semantic-scholar or scopus")
    include, exclude = screening.normalize_criteria(criteria.get("include", []), criteria.get("exclude", [])).values()
    search.validate_question(question, sources, repair_sentences=True)  # a rejected question creates nothing
    project = create_project(name, path, mode="bot")
    if changes:
        write_settings(project.root, apply_changes(load_settings(project.root), changes))
    checked = search.validate_question(question, sources, project, repair_sentences=True)
    run_id, run_path = new_run_dir(project)
    atomic_json(run_path / "question.json", checked["normalized_question"])
    config = load_settings(project.root).search
    save_run(
        run_path,
        {
            "schema_version": MANIFEST_VERSION,
            "run_id": run_id,
            "created_at": now(),
            "search_status": "complete",
            "sources": checked["sources"],
            "limit_per_source": config.per_source,
            "preprint_allow": config.preprint_allow,
            "bot": True,
            "articles": {},
        },
    )
    screening.set_criteria(run_id, include, exclude)
    with _state(project) as state:
        state.update(
            version=1,
            run_id=run_id,
            created_at=now(),
            start_date=_today().isoformat(),
            schedule=schedule,
            status="active",
            hermes={},
            questions=[{"version": 1, "set_at": now(), "question": checked["normalized_question"], "sources": checked["sources"]}],
            criteria=[{"version": 1, "set_at": now(), "include": include, "exclude": exclude}],
            backfill=None,
            active=None,
            history=[],
        )
    return project


def _run_path(project: Project, state: dict[str, Any]) -> Path:
    path = project.runs / state["run_id"]
    if not (path / RUN_FILE).is_file():
        raise ValueError(f"The bot's standing run {state['run_id']} is missing from {project.runs}")
    return path


def _last_write(project: Project, state: dict[str, Any]) -> datetime:
    paths = [project.runs / state["run_id"] / RUN_FILE, project.db, project.db.with_name(project.db.name + "-wal")]
    stamps = [p.stat().st_mtime for p in paths if p.exists()]
    started = datetime.fromisoformat(state["active"]["started_at"]).timestamp()
    return datetime.fromtimestamp(max([started, *stamps]), UTC)


def _run_alive(project: Project, state: dict[str, Any]) -> bool:
    """Whether the run that holds the project is still going (not crashed, not hung)."""
    try:
        if not process_alive(int(state["active"]["pid"])):
            return False
    except ProcessLookupError:
        return False
    except (PermissionError, KeyError, TypeError, ValueError):
        pass
    return datetime.now(UTC) - _last_write(project, state) < STALE


def bot_start(name: str | None) -> dict[str, Any]:
    project = _bot_project(name)
    settings = load_settings(project.root)
    assert settings.bot is not None
    started = datetime.now(UTC)
    with _state(project) as state:
        if state.get("status") != "active":
            raise ValueError(f"This bot is {state.get('status')}; resume it with `uvx med-lit-mcp setup bot --edit`")
        active = state.get("active")
        if active and _run_alive(project, state):
            return {"busy": True, "message": SILENT, "note": f"A run of this bot started at {active['started_at']} is still in progress"}
        carried: dict[str, Any] = {}
        if active:
            # The run stopped before bot_finish: this run finishes its work and reports it too.
            state["history"].append(
                active | {"finished_at": now(), "outcome": "stopped before bot_finish; reported by the next run"}
            )
            carried = {
                "covers_from": active.get("covers_from", active["started_at"]),
                "updates": _update_numbers(active),
                "withdrawn": active.get("withdrawn", []),
                "stopped_runs": active.get("stopped_runs", 0) + 1,
            }
        window = _today() - timedelta(days=settings.bot.lookback_days)
        backfill = state.get("backfill")
        from_date = min(window, date.fromisoformat(backfill["from_date"])) if backfill else window
        state["active"] = {
            "started_at": started.isoformat(),
            "covers_from": started.isoformat(),
            "pid": os.getpid(),
            "from_date": from_date.isoformat(),
            "backfill": bool(backfill),
            "update": None,
            "updates": [],
            "withdrawn": [],
            "stopped_runs": 0,
        } | carried
        current = state["questions"][-1]
        run_path = _run_path(project, state)
    result: dict[str, Any]
    try:
        with locked_run(run_path, write=False) as manifest:
            number = len(manifest.get("updates", [])) + 1
        result = search.start_update(
            run_path,
            current["question"],
            settings.search.sources or current["sources"],
            from_date=from_date.isoformat(),
            per_source=settings.search.per_source,
            cap=min(settings.bot.max_new_articles, triage.SESSION_SIZE),
            wait_seconds=SEARCH_WAIT,
        )
        with _state(project) as state:
            state["active"]["update"] = number
            state["active"]["updates"] = [*state["active"].get("updates", []), number]
    except (OSError, ValueError) as exc:
        result = {"search_error": str(exc)[:300]}
    return project.summary() | {
        "run_id": state["run_id"],
        "window": f"published {from_date.isoformat()} or later" + (" (backfill for a new question)" if backfill else ""),
        "search": {k: result.get(k) for k in ("search_status", "search_error", "source_failures") if result.get(k)},
        "next": f"Call bot_next(project={project.name!r}) and follow it until it says finish.",
    }


def _finish_step(project: Project, reason: str) -> dict[str, Any]:
    return {"step": "finish", "reason": reason, "do": f"Call bot_finish(project={project.name!r}) and reply with its message exactly."}


def _withdraw_excluded(project: Project, path: Path, min_sources: int) -> list[str]:
    """Articles in the wiki whose current decision is no longer include."""
    withdrawn = []
    with locked_run(path) as manifest, database(project.db) as conn:
        revision = manifest.get("selection_revision")
        for uid, item in manifest["articles"].items():
            decided = (item.get("screening") or {}).get("revision") == revision
            if not decided or is_eligible(item, revision):
                continue
            if conn.execute("SELECT 1 FROM articles WHERE uid=?", (uid,)).fetchone() is None:
                continue
            wiki.withdraw_article(conn, uid, min_sources)
            item.update(fetch="pending", wiki="pending", withdrawn_at=now())
            for key in ("text_file", "content_sha256", "fetched_at"):
                item.pop(key, None)
            withdrawn.append(uid)
        if withdrawn:
            save_run(path, manifest)
    return withdrawn


def _syntheses_since(project: Project, started_at: str) -> list[dict[str, Any]]:
    with database(project.db) as conn:
        return [
            {"entity": r[0], "version": r[1]}
            for r in conn.execute(
                """SELECT e.canonical_name, y.version FROM kg_syntheses y JOIN kg_entities e ON e.id = y.entity_id
                   WHERE y.compiled_at >= ? ORDER BY y.compiled_at""",
                (started_at,),
            )
        ]


def synthesis_allowance(project: Project) -> int | None:
    """Entity pages the running bot run may still write, or None outside a bot run."""
    if project.mode != "bot":
        return None
    try:
        active = read_state(project).get("active")
    except (OSError, ValueError):
        return None
    if not active:
        return None
    settings = load_settings(project.root)
    assert settings.bot is not None
    return max(0, settings.bot.max_syntheses - len(_syntheses_since(project, active["started_at"])))


def bot_next(name: str | None) -> dict[str, Any]:
    project = _bot_project(name)
    settings = load_settings(project.root)
    assert settings.bot is not None
    state = read_state(project)
    active = state.get("active")
    if not active:
        raise ValueError(f"No bot run is in progress; call bot_start(project={project.name!r}) first")
    path = _run_path(project, state)
    run_id = state["run_id"]
    with locked_run(path) as manifest:
        search.poll(path, manifest)
        running = manifest["search_status"] == "running"
        if screening.requeue_vague(manifest):
            save_run(path, manifest)
    if running and search._wait(path, SEARCH_WAIT)["search_status"] == "running":
        return {"step": "wait", "do": f"The search is still running; call bot_next(project={project.name!r}) again."}
    cap = min(settings.bot.max_new_articles, triage.SESSION_SIZE)
    triage_step = {
        "step": "triage",
        "run_id": run_id,
        "do": (
            f"Call next_triage_batch(run_id='{run_id}'), score every title 0-3 as it says, and call "
            f"record_triage_scores(run_id='{run_id}', revision=<its revision>, scores=[...]). Repeat until "
            f"remaining is 0, then call bot_next(project={project.name!r})."
        ),
    }
    with locked_run(path) as manifest:
        if manifest.get("candidates"):
            if triage.needed(manifest, cap):
                return triage_step | {"candidates": len(manifest["candidates"]), "keep": cap}
            triage.select_candidates(manifest, cap)
            save_run(path, manifest)
    session = screening.prepare_session(path, active["started_at"])
    if session["triage"]:
        return triage_step | {"pending": session["pending"]}
    if session["left"]:
        return {
            "step": "screen",
            "run_id": run_id,
            "pending": session["left"],
            "do": (
                f"Call next_screening_batch(run_id='{run_id}') and record_screening_decisions(run_id='{run_id}', "
                "revision=<its revision>, decisions=[...]) for its items. Repeat until it reports the session "
                f"complete, then call bot_next(project={project.name!r})."
            ),
        }
    if session["pending"]:
        return _finish_step(
            project, f"this run's articles are screened; {session['pending']} more wait for the next runs"
        )
    manifest = read_json(path / RUN_FILE)
    revision = manifest.get("selection_revision")
    withdrawn = _withdraw_excluded(project, path, settings.wiki.min_sources)
    if withdrawn:
        with _state(project) as fresh:
            fresh["active"]["withdrawn"] = fresh["active"].get("withdrawn", []) + withdrawn
    manifest = read_json(path / RUN_FILE)
    to_fetch = sum(
        is_eligible(item, revision) and item["fetch"] == "pending" for item in manifest["articles"].values()
    )
    if to_fetch:
        return {
            "step": "fetch",
            "run_id": run_id,
            "pending": to_fetch,
            "do": (
                f"Call fetch_articles(run_id='{run_id}') repeatedly until remaining is 0, then call "
                f"bot_next(project={project.name!r})."
            ),
        }
    allowance = synthesis_allowance(project) or 0
    try:
        plan = wiki.work_plan(run_id, max_syntheses=allowance)
    except ValueError:
        return _finish_step(project, "no included articles with text yet")
    if plan["step"] == "export":
        reason = "all work done" if allowance > 0 else "safety limit on entity pages (bot.max_syntheses) reached; the rest carry over"
        return _finish_step(project, reason)
    task = plan["tasks"][0]
    with database(project.db) as conn:
        progress = conn.execute(
            """SELECT (SELECT COUNT(*) FROM kg_extraction_pages), (SELECT COALESCE(SUM(version), 0) FROM kg_syntheses),
                      (SELECT COUNT(*) FROM kg_duplicate_candidates WHERE status='pending')"""
        ).fetchone()
    fingerprint = hashlib.sha256(f"{task}|{tuple(progress)}".encode()).hexdigest()
    with _state(project) as fresh:
        last = fresh["active"].get("last_task") or {}
        repeats = last.get("repeats", 0) + 1 if last.get("fingerprint") == fingerprint else 0
        fresh["active"]["last_task"] = {"fingerprint": fingerprint, "repeats": repeats}
    if repeats >= MAX_TASK_REPEATS:
        return _finish_step(project, "a wiki task made no progress after repeated attempts; it carries over to the next run")
    return {
        "step": "wiki",
        "stage": plan["step"],
        "task": task,
        "tasks_in_stage": len(plan["tasks"]),
        "do": (
            "Call delegate_task with the text of `task`, unchanged, as the goal. When the subagent has "
            f"finished, call bot_next(project={project.name!r})."
        ),
    }


def _report_path(project: Project) -> Path:
    folder = project.root / UPDATES
    stem = _today().isoformat()
    path, number = folder / f"{stem}.md", 2
    while path.exists():
        path, number = folder / f"{stem}-{number}.md", number + 1
    return path


def _render_report(project: Project, facts: dict[str, Any]) -> str:
    lines = [
        "---",
        "generator: med-lit-bot",
        f"date: {facts['date']}",
        "---",
        "",
        f"# {project.name}: update {facts['date']}",
        "",
        f"- Window: articles published {facts['from_date']} or later" + (" (backfill for a new question)" if facts["backfill"] else ""),
        f"- New articles: {facts['new_articles']}"
        + (f"; {facts['dropped_over_cap']} more were over the cap of {facts['cap']} and may be picked up later" if facts["dropped_over_cap"] else ""),
        f"- Screened: {facts['screened']} (included {facts['included']}, excluded {facts['excluded']}, uncertain {facts['uncertain']})",
        f"- Fetched: {facts['fetched']}",
        f"- Entity pages written or updated: {len(facts['syntheses'])}",
    ]
    if facts["syntheses"]:
        lines += ["", "## Entity pages", ""]
        lines += [f"- [[{s['entity']}]]" + (" (updated)" if s["version"] > 1 else " (new)") for s in facts["syntheses"]]
    if facts["new_included"]:
        lines += ["", "## Newly included", ""]
        lines += [f"- {a['title']} [{a['uid']}]" for a in facts["new_included"]]
    if facts["awaiting_review"]:
        lines += ["", f"## Waiting for your decision ({len(facts['awaiting_review'])})", ""]
        lines += [f"- {a['title']} [{a['uid']}]: {a['reason']}" for a in facts["awaiting_review"][:30]]
        lines += ["", "Decide them in a chat with review_article; the bot never decides uncertain articles."]
    if facts["stopped_runs"]:
        since = facts["covers_from"][:16].replace("T", " ")
        lines.insert(7, f"- Includes the work of {facts['stopped_runs']} earlier run(s) that stopped before finishing (since {since} UTC)")
    if facts["no_identifier"]:
        lines += ["", f"## Skipped: no DOI or PMID ({len(facts['no_identifier'])})", ""]
        lines += [f"- {entry['title']} [{entry['uid']}]: {entry['reason']}" for entry in facts["no_identifier"][:20]]
        lines += [
            "",
            (
                "They are left out. Each is looked up again when a later search finds it, in case a DOI has "
                "been registered; to include one now, ask for it by name in a chat (add_skipped_articles)."
            ),
        ]
    if facts["withdrawn"]:
        lines += ["", "## Withdrawn from the wiki (now excluded)", ""]
        lines += [f"- [{uid}]" for uid in facts["withdrawn"]]
    problems = []
    if facts.get("search_error"):
        problems.append(f"Search failed: {facts['search_error']}")
    problems += [f"{source} failed: {error}" for source, error in (facts.get("source_failures") or {}).items()]
    if facts.get("truncated_sources"):
        problems.append(
            f"More records matched than were retrieved from {', '.join(facts['truncated_sources'])}; "
            "raise search.per_source with `setup bot --edit` if new articles may be missed."
        )
    left = facts["left"]
    if any(left.values()):
        problems.append(
            "Carried over: " + ", ".join(f"{count} {what}" for what, count in left.items() if count)
        )
    if problems:
        lines += ["", "## Notes", ""] + [f"- {p}" for p in problems]
    return "\n".join(lines) + "\n"


def bot_finish(name: str | None) -> dict[str, Any]:
    from .wiki_export import export_wiki

    project = _bot_project(name)
    settings = load_settings(project.root)
    assert settings.bot is not None
    state = read_state(project)
    active = state.get("active")
    if not active:
        return {"message": SILENT, "note": "No bot run was in progress"}
    path = _run_path(project, state)
    started = active.get("covers_from", active["started_at"])  # includes runs that stopped early
    try:
        export_wiki(project)
    except (OSError, ValueError) as exc:
        export_error = str(exc)[:300]
    else:
        export_error = None
    manifest = read_json(path / RUN_FILE)
    revision = manifest.get("selection_revision")
    numbers = set(_update_numbers(active))
    covered = [u for u in manifest.get("updates", []) if u.get("number") in numbers]
    update = covered[-1] if covered else {}
    unidentified = {uid for u in covered for uid in u.get("no_identifier", [])}
    screened = [
        (uid, item) for uid, item in manifest["articles"].items()
        if (item.get("screening") or {}).get("revision") == revision and item["screening"].get("reviewed_at", "") >= started
    ]
    decisions = {d: [(u, i) for u, i in screened if i["screening"]["decision"] == d] for d in ("include", "exclude", "uncertain")}
    awaiting = [
        {"uid": uid, "title": str(item["record"].get("title") or uid)[:160], "reason": item["screening"].get("reason", "")[:200]}
        for uid, item in manifest["articles"].items()
        if (item.get("screening") or {}).get("revision") == revision and item["screening"]["decision"] == "uncertain"
    ]
    with database(project.db) as conn:
        uids = [uid for uid, item in manifest["articles"].items() if is_eligible(item, revision)]
        unextracted = sum(
            1 for uid in uids
            if (row := conn.execute("SELECT kg_sha256, content_sha256 FROM articles WHERE uid=?", (uid,)).fetchone())
            and row[0] != row[1]
        )
        queue, _ = wiki._synthesis_queue(conn, uids or None, settings.wiki.min_sources)
    facts = {
        "finished_at": now(),
        "date": _today().isoformat(),
        "from_date": active["from_date"],
        "backfill": active.get("backfill"),
        "new_articles": sum(u.get("new_articles", 0) for u in covered),
        "stopped_runs": active.get("stopped_runs", 0),
        "covers_from": started,
        "dropped_over_cap": len(update.get("dropped_over_cap", [])),
        "cap": update.get("cap"),
        "no_identifier": [
            entry for entry in manifest.get("skipped_no_identifier", []) if entry["uid"] in unidentified
        ],
        "search_error": "; ".join(u["error"] for u in covered if u.get("error")) or export_error,
        "source_failures": {k: v for u in covered for k, v in (u.get("source_failures") or {}).items()},
        "truncated_sources": sorted({s for u in covered for s in u.get("truncated_sources") or []}),
        "screened": len(screened),
        "included": len(decisions["include"]),
        "excluded": len(decisions["exclude"]),
        "uncertain": len(decisions["uncertain"]),
        "new_included": [{"uid": u, "title": str(i["record"].get("title") or u)[:160]} for u, i in decisions["include"]],
        "fetched": sum(1 for item in manifest["articles"].values() if item.get("fetched_at", "") >= started),
        "syntheses": _syntheses_since(project, started),
        "awaiting_review": awaiting,
        "withdrawn": active.get("withdrawn", []),
        "left": {
            "to screen": sum((i.get("screening") or {}).get("revision") != revision for i in manifest["articles"].values()),
            "to fetch": sum(is_eligible(i, revision) and i["fetch"] == "pending" for i in manifest["articles"].values()),
            "to extract": unextracted,
            "entity pages to write": len(queue if uids else []),
        },
    }
    quiet = not (
        facts["new_articles"] or facts["screened"] or facts["syntheses"] or facts["withdrawn"] or facts["no_identifier"]
        or facts["stopped_runs"]
        or facts["search_error"] or facts["source_failures"]
    )
    report = None
    if not quiet:
        report = _report_path(project)
        atomic_text(report, _render_report(project, facts))
    with _state(project) as fresh:
        record = fresh["active"] | {
            "finished_at": facts["finished_at"],
            "new_articles": facts["new_articles"],
            "included": facts["included"],
            "syntheses": len(facts["syntheses"]),
            "report": str(report.relative_to(project.root)) if report else None,
            "outcome": "finished",
        }
        fresh["history"] = (fresh.get("history") or [])[-199:] + [record]
        if fresh["active"].get("backfill") and update and not update.get("error") and not update.get("dropped_over_cap"):
            fresh["backfill"] = None
        fresh["active"] = None
    if quiet:
        return {"message": SILENT, "note": "Nothing new this run"}
    summary = _render_report(project, facts).split("---\n", 2)[-1].strip()
    return {"message": f"{summary}\n\nReport: {report}", "report": str(report)}


# Changes made through `setup bot --edit`.


def set_question(project: Project, question: dict[str, Any], sources: list[str]) -> dict[str, Any]:
    """A new question version; the next run backfills from the bot's start date with it."""
    if "europepmc" in sources:
        raise ValueError("Bots cannot search europepmc")
    checked = search.validate_question(question, sources, project, repair_sentences=True)
    settings = load_settings(project.root)
    assert settings.bot is not None
    with _state(project) as state:
        version = len(state["questions"]) + 1
        state["questions"].append(
            {"version": version, "set_at": now(), "question": checked["normalized_question"], "sources": checked["sources"]}
        )
        start = date.fromisoformat(state["start_date"]) - timedelta(days=settings.bot.lookback_days)
        state["backfill"] = {"from_date": start.isoformat(), "question_version": version, "set_at": now()}
        atomic_json(_run_path(project, state) / "question.json", checked["normalized_question"])
    # Each run sets its own publication window, so the date-range warning does not apply.
    warnings = [w for w in checked["warnings"] if "from_date" not in w]
    return {"version": version, "backfill_from": start.isoformat(), "warnings": warnings}


def set_criteria(project: Project, include: list[str], exclude: list[str]) -> dict[str, Any]:
    """A new criteria revision; the next runs re-screen everything collected so far."""
    with _state(project) as state:
        result = screening.set_criteria(state["run_id"], include, exclude, replace=True)
        if result["changed"]:
            state["criteria"].append(
                {"version": len(state["criteria"]) + 1, "set_at": now(), **result["criteria"]}
            )
    return result


def set_status(project: Project, status: str) -> None:
    if status not in ("active", "paused", "archived"):
        raise ValueError("status must be active, paused or archived")
    with _state(project) as state:
        state["status"] = status
        state[f"{status}_at"] = now()


def set_hermes(project: Project, **values: Any) -> None:
    with _state(project) as state:
        state.setdefault("hermes", {}).update(values)
        if "schedule" in values:
            state["schedule"] = values["schedule"]
