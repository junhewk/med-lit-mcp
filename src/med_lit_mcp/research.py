"""Research modes: an interactive review from the researcher's question to a report that answers it.

research_next is the driver. From the project's settings and the run's stored state it returns the
next step, with `do` naming the tools to call, so the agent never has to remember where a review
stands. Where the researcher decides, the step is a checkpoint (`ask_researcher: true`): the agent
shows what the step lists, asks, and stops until the researcher answers. The checkpoints are:

    parameters   the mode (basic, full or deep) and the search settings: years, sources, records per source,
                 preprints, languages, publication types, how many articles to screen, fetch limit
    question     the drafted PICO/PCC question with the settings validate_question applied
    criteria     inclusion and exclusion criteria drafted from the approved question, each with the
                 reason it follows from the question
    screening    after each session of 20, when no max_screened was set: screen on, or stop here
    uncertain    articles the agent could not decide: the researcher decides them, or leaves them out

basic screens titles and abstracts and reports from the abstracts. full also fetches full text and
builds the evidence wiki, and its report can quote the full text. deep runs full mode in rounds, like a
bot's runs in one session: after each round the agent judges whether the evidence answers the
question (record_evaluation); if not, its revised search adds the best new articles and the round repeats.

The report (next_report, record_report) is written by the agent and checked here: every [uid] it
cites must be an included article, and every key finding carries a verbatim quote from that
article's stored text. The methods section (search, settings, counts, criteria and their reasons,
reasons for exclusion) is rendered from the run's record, never written by the model.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Any

from . import screening, search, triage, wiki
from .config import enabled_stages
from .limits import cut_to_fit, encoded_size, fill
from .matching import contains_verbatim
from .projects import Project, get_project, locate, run_dir
from .runs import (
    is_eligible,
    list_runs,
    require_selection_complete,
    screening_stopped,
    summary,
)
from .settings import ResearchSettings, load_settings, settings_path
from .store import RUN_FILE, atomic_text, database, locked_run, now, read_json, save_run
from .wiki_export import CITATION, GENERATOR

REPORTS = "reports"
MODES = {
    "basic": "titles and abstracts are screened, and the report is written from the abstracts",
    "full": "the included articles' full text is fetched and the evidence wiki built; the report quotes the full text",
    "deep": "full mode in rounds: after each round the evidence is judged, and a revised search adds new articles until it is enough",
}
FULL_TEXT = ("full", "deep")  # modes that fetch full text and build the wiki
CONFIRMATIONS = ("stop_screening", "leave_uncertain")
SHOWN_UNCERTAIN = 30
REPORT_ABSTRACT = 4000
REPORT_ROLES = 20
REPORT_RELATIONS = 10
REPORT_ENTITIES = 12
REPORT_SUMMARY = 300

REPORT_RULES = """\
Write for the researcher who asked, and for a colleague who did not see the review: plain language,
short paragraphs, every abbreviation spelled out on first use. Never write criterion codes (I1, E2),
tool names, run ids or uids in the text: cite a study only as [uid], and the report numbers it [1], [2]
with a reference list.
- Use only the articles next_report returns: no outside knowledge, no studies that are not listed.
- answer: the direct answer in 3-6 sentences, first sentence first: what the studies show on the question,
  then how much evidence there is (how many studies, which designs and settings). If the included studies
  cannot answer the question, say so plainly and say what they do show.
- body: '## ' sections a reader can follow, for example the uses or findings by theme ("## Tools patients
  use before a consultation"), then "## Where the studies agree and differ" and "## What is still unknown".
  Each statement about a study cites it as [uid]; name what a study did (design, size, setting) the first
  time it appears.
- findings: one to three per included study that bears on the question, each with the study's uid, the
  finding in your words, and a quote copied verbatim from that study's text (title, abstract or, in full
  or deep mode, a full-text page read with get_article_page) that shows it. They go in the appendix; a
  finding whose quote is not in the stored text is rejected.
- limitations: what limits this answer that the evidence itself shows, in plain words (few or small
  studies, simulated settings, one country). The report already lists `limitations_added`, the limits of
  the search and screening from the review's record: do not repeat them.
- Do not write how the search was done, the criteria or the list of studies: the report adds them from
  the review's record.
- Cite only included studies; record_report rejects any other citation."""


def _project_and_run(name: str | None, run_id: str | None) -> tuple[Project, str | None]:
    if run_id:
        project, _ = locate(run_id)
    else:
        project = get_project(name)
        newest = [row for row in list_runs(project, 100) if not row["read_only"]]
        run_id = newest[0]["run_id"] if newest else None
    if project.mode == "bot":
        raise ValueError(
            f"'{project.name}' is a bot project: it runs on its schedule (bot_start, bot_next, bot_finish), "
            "and its reports are in updates/"
        )
    return project, run_id


def _research(project: Project) -> ResearchSettings:
    return load_settings(project.root).research or ResearchSettings()


def _checkpoint(step: str, do: str, **extra: Any) -> dict[str, Any]:
    return {"step": step, "ask_researcher": True, **extra, "do": do}


def _step(step: str, do: str, **extra: Any) -> dict[str, Any]:
    return {"step": step, "ask_researcher": False, **extra, "do": do}


def _parameters(project: Project) -> dict[str, Any]:
    settings = load_settings(project.root)
    search_settings = settings.search.model_dump()
    return {
        "modes": MODES,
        "research": (settings.research or ResearchSettings()).model_dump(),
        "search": search_settings | {
            "sources": search_settings["sources"] or f"default: {', '.join(search.default_sources())}",
            "years": search_settings["years"] or "last three years (default)",
        },
        "fetch": settings.fetch.model_dump(),
        "file": str(settings_path(project.root)),
    }


def _codes_with_reasons(manifest: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    reasons = manifest.get("selection_reasons") or {}
    return {
        code: {"criterion": text, "reason": reasons.get(code)}
        for code, text in screening.criterion_codes(manifest.get("selection_criteria")).items()
    }


def _confirm(path: Path, confirm: str, pending: int, uncertain: int) -> None:
    with locked_run(path) as manifest:
        revision = manifest.get("selection_revision")
        if revision is None:
            raise ValueError("Nothing to confirm yet: set the screening criteria first")
        if confirm == "stop_screening":
            if not pending:
                raise ValueError("Every article is screened; there is nothing to stop")
            manifest["screening_stopped"] = {
                "revision": revision, "at": now(), "unscreened": pending, "by": "researcher",
            }
        else:
            if not uncertain:
                raise ValueError("No uncertain articles are waiting")
            manifest["uncertain_left"] = {"revision": revision, "at": now(), "count": uncertain}
        save_run(path, manifest)


def _included(manifest: dict[str, Any]) -> list[str]:
    revision = manifest.get("selection_revision")
    return [uid for uid, item in manifest["articles"].items() if is_eligible(item, revision)]


def _included_digest(manifest: dict[str, Any]) -> str:
    return hashlib.sha256("\n".join(sorted(_included(manifest))).encode()).hexdigest()


def _report_current(manifest: dict[str, Any], mode: str) -> bool:
    report = manifest.get("report") or {}
    return bool(
        report
        and report.get("revision") == manifest.get("selection_revision")
        and report.get("mode") == mode
        and report.get("included_sha256") == _included_digest(manifest)
        # The methods and limitations state the screening counts, so more screening dates the report too.
        and report.get("selection", summary(manifest)["selection"]) == summary(manifest)["selection"]
    )


def research_next(name: str | None = None, run_id: str | None = None, confirm: str | None = None) -> dict[str, Any]:
    """The next step of an interactive review, from its settings and stored state."""
    if confirm is not None and confirm not in CONFIRMATIONS:
        raise ValueError(f"confirm must be one of {', '.join(CONFIRMATIONS)}")
    project, run_id = _project_and_run(name, run_id)
    research = _research(project)
    base = project.summary() | {"research_mode": research.mode}
    if research.mode is None:
        return base | _checkpoint(
            "parameters",
            (
                "Show the researcher the two modes and these settings in plain words, and ask which mode they "
                "want and whether to change any setting (years, sources, records per source, preprints, "
                "languages, publication types, how many articles to screen, fetch limit). Save their choice with "
                f'project_settings(project={project.name!r}, changes={{"research.mode": "basic", "full" or "deep", ...}}), '
                f"then call research_next(project={project.name!r})."
            ),
            parameters=_parameters(project),
        )
    if run_id is None:
        return base | _checkpoint(
            "question",
            (
                "Draft the search question from the researcher's words (guide('question')) and call "
                f"validate_question(project={project.name!r}, ...). Show the researcher the normalized question, "
                "sources, date range, settings and every warning or repair, and ask for approval; redraft and "
                "validate again for any change. Once approved, call start_search(question_id) and then "
                "research_next(run_id=<its run_id>)."
            ),
            parameters=_parameters(project),
        )
    path = run_dir(run_id)
    with locked_run(path) as manifest:
        search.poll(path, manifest)
        if screening.requeue_vague(manifest):
            save_run(path, manifest)
    manifest = read_json(path / RUN_FILE)
    base |= {"run_id": run_id}
    status = summary(manifest)
    if manifest["search_status"] == "running":
        return base | _step("wait", f"The search is still running: call resume_search(run_id='{run_id}'), then research_next(run_id='{run_id}').")
    if manifest["search_status"] == "failed":
        return base | _step(
            "search_failed",
            f"Tell the researcher the search failed ({status['search_error']}). Retry with resume_search(run_id='{run_id}'), then research_next(run_id='{run_id}').",
        )
    search_facts = {
        key: status[key] for key in (
            "candidates", "records_by_source", "source_failures", "already_known", "skipped_preprints", "skipped_no_identifier",
        )
    }
    revision = manifest.get("selection_revision")
    if revision is None:
        return base | _checkpoint(
            "criteria",
            (
                "Report the search results above (every failed source too). Then draft inclusion and exclusion "
                "criteria from the approved question, and give each the one-line reason it follows from the "
                "question (codes I1, I2 … for inclusion, E1, E2 … for exclusion, in order). "
                + (
                    "In deep mode every later round is screened against these same criteria, so draft them from the "
                    "research question itself, not from this round's search terms: a term the researcher narrowed "
                    "for this search (one model name, say) must not become a criterion. "
                    if research.mode == "deep" else ""
                )
                + "Show them to the "
                "researcher and ask them to approve or change them. Then call "
                f"set_screening_criteria(run_id='{run_id}', include=[...], exclude=[...], reasons={{'I1': ..., 'E1': ...}}) "
                f"with what they approved, and research_next(run_id='{run_id}')."
            ),
            question=read_json(path / "question.json").get("question"),
            search=search_facts,
        )
    if manifest.get("candidates"):
        # A deep round's search: its new articles wait as candidates until their titles are triaged, and the
        # best research.round_articles are kept (as a bot run keeps its cap).
        cap = research.round_articles
        if triage.needed(manifest, cap):
            return base | _step(
                "triage",
                (
                    f"This round's search found {len(manifest['candidates'])} new articles; the best {cap} by title are "
                    f"kept. Call next_triage_batch(run_id='{run_id}'), score every title 0-3 as it says, and call "
                    f"record_triage_scores(run_id='{run_id}', revision=<its revision>, scores=[...]); repeat until "
                    f"remaining is 0, then call research_next(run_id='{run_id}')."
                ),
                candidates=len(manifest["candidates"]), keep=cap,
            )
        with locked_run(path) as fresh:
            triage.select_candidates(fresh, cap)
            save_run(path, fresh)
        manifest = read_json(path / RUN_FILE)
    articles = manifest["articles"]
    pending = [uid for uid, item in articles.items() if (item.get("screening") or {}).get("revision") != revision]
    uncertain = [uid for uid, item in articles.items()
                 if (item.get("screening") or {}).get("revision") == revision and item["screening"]["decision"] == "uncertain"]
    deep = research.mode == "deep"
    if confirm == "stop_screening" and deep:
        raise ValueError("Deep mode screens each round's articles in full; the rounds and round_articles bound it")
    if confirm:
        _confirm(path, confirm, len(pending), len(uncertain))
        manifest = read_json(path / RUN_FILE)
    screened = len(articles) - len(pending)
    criteria = _codes_with_reasons(manifest)
    stopped = screening_stopped(manifest)
    # Deep mode ignores max_screened: each round keeps research.round_articles, and every round is screened.
    cap = None if deep else research.max_screened
    if stopped and (deep or stopped["by"] != "researcher") and not (cap and screened >= cap):
        with locked_run(path) as fresh:  # max_screened was raised or removed: screen on
            fresh.pop("screening_stopped", None)
            save_run(path, fresh)
        manifest = read_json(path / RUN_FILE)
    if pending and not screening_stopped(manifest):
        if cap and screened >= cap:
            with locked_run(path) as fresh:
                fresh["screening_stopped"] = {
                    "revision": revision, "at": now(), "unscreened": len(pending),
                    "by": f"research.max_screened ({research.max_screened})",
                }
                save_run(path, fresh)
        else:
            return base | _screening_step(project, run_id, manifest, pending, screened, research, criteria)
    manifest = read_json(path / RUN_FILE)
    left_open = manifest.get("uncertain_left") or {}
    if uncertain and not (left_open.get("revision") == revision and left_open.get("count", 0) >= len(uncertain)):
        shown = [
            {"uid": uid, "title": str(articles[uid]["record"].get("title") or uid)[:200],
             "reason": articles[uid]["screening"].get("reason", "")[:300]}
            for uid in uncertain[:SHOWN_UNCERTAIN]
        ]
        return base | _checkpoint(
            "uncertain",
            (
                f"{len(uncertain)} articles could not be decided from their title and abstract. Show them with "
                "their reasons and ask the researcher to decide each: record their decision and reason with "
                f"review_article(run_id='{run_id}', uid, decision, reason, criterion=<code for an exclusion>). "
                "If they choose to leave the rest undecided (they are then left out of the report), call "
                f"research_next(run_id='{run_id}', confirm='leave_uncertain'); otherwise call "
                f"research_next(run_id='{run_id}') when they are done."
            ),
            uncertain=shown,
            uncertain_total=len(uncertain),
            criteria=criteria,
        )
    included = _included(manifest)
    selection = summary(manifest)["selection"]
    stages = enabled_stages()
    full_text = research.mode in FULL_TEXT and "fetch" in stages
    if full_text and included:
        waiting = sum(articles[uid]["fetch"] == "pending" for uid in included)
        if waiting:
            return base | _step(
                "fetch",
                f"Call fetch_articles(run_id='{run_id}') until remaining is 0, then research_next(run_id='{run_id}'). Report articles that have only an abstract.",
                to_fetch=waiting,
            )
        if "wiki" in stages:
            try:
                plan = wiki.work_plan(run_id)
            except ValueError:
                plan = {"step": "export"}
            if plan["step"] != "export":
                return base | _step(
                    "wiki",
                    (
                        f"Build the evidence wiki: call wiki_tasks(run_id='{run_id}') and follow it (give each task to a "
                        "fresh subagent if you can), calling it again until it says export; then call "
                        f"export_wiki(project={project.name!r}) and research_next(run_id='{run_id}')."
                    ),
                    wiki_step=plan["step"],
                )
    if research.mode == "deep":
        rounds = _rounds(manifest)
        current = rounds[-1]
        if "evaluation" not in current:
            left = research.deep_rounds - current["round"]
            return base | _step(
                "evaluate",
                (
                    f"Round {current['round']} of at most {research.deep_rounds} is screened"
                    + (" and its full texts are in the wiki" if full_text else "")
                    + f". Judge whether the included studies answer the question (guide('evaluation')): call "
                    f"next_report(run_id='{run_id}') and, while remaining > 0, again with the offset it gives; then call "
                    f"record_evaluation(run_id='{run_id}', sufficient=..., assessment=..., gaps=[...], adequate=[uids]"
                    + (", question=<a revised search for the gaps, when not sufficient>" if left > 0 else "")
                    + f"). Tell the researcher in a line or two what you judged, then call research_next(run_id='{run_id}')."
                ),
                round=current["round"], rounds_left=left, included=len(included),
            )
    if not _report_current(manifest, research.mode):
        rewrite = bool(manifest.get("report"))
        return base | _step(
            "report",
            (
                ("The report is out of date (the screening, the included articles or the mode changed): write it again. " if rewrite else "")
                + f"Call next_report(run_id='{run_id}') and, while it reports remaining > 0, again with the offset "
                "it gives, until you have read every included article. Then write the report by its rules and "
                f"save it with record_report(run_id='{run_id}', ...); fix and resubmit anything it rejects. Finally "
                "reply to the researcher with the `message` record_report returns."
            ),
            selection=selection,
            note=None if full_text or research.mode == "basic" else "Fetch is not enabled on this server (MED_LIT_STAGES); the report uses abstracts.",
        )
    report = manifest["report"]
    return base | _step(
        "done",
        (
            "The report is written. If you have not yet replied with it, reply with its message; otherwise tell "
            "the researcher what they can do next: decide uncertain articles (review_article), screen the rest "
            f"(next_screening_batch(run_id='{run_id}', new_round=true), then research_next), change the criteria "
            "(set_screening_criteria with replace=true), switch to full or deep mode (project_settings research.mode) "
            "for full text and the wiki, or search again with a new question. The report is then written again."
        ),
        report=report["path"],
        message=report.get("message"),
        selection=selection,
    )


def _rounds(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """A deep review's rounds; the first is the approved search itself."""
    rounds = (manifest.get("deep") or {}).get("rounds") or []
    return rounds or [{"round": 1}]


EVALUATION_RULES = """\
Judge the included studies against the question, from next_report only.
- sufficient: true when each main part of the question (its population, concept or intervention, and
  setting) is answered by the included studies, or when a further search is unlikely to add studies that
  change the answer. Few studies is not by itself a reason to search again; a part of the question no
  study addresses is.
- assessment: 2-4 plain sentences for the report: what the studies cover, what they leave open, and why
  that is or is not enough. Cite studies as [uid].
- gaps: the parts of the question still unanswered, each in a few words.
- adequate: the uids of the included studies that directly answer the question.
- question (when not sufficient and rounds are left): a revised search aimed at the gaps, drafted by the
  rules of guide('question'): other words for the missing part, a related concept, a narrower or broader
  population. It must differ from the earlier searches. The criteria stay as they are; the new articles
  are screened against them."""


def _terms(question: dict[str, Any]) -> dict[str, frozenset[frozenset[str]]]:
    """What a question searches for: per component, its groups of terms (case and spacing ignored)."""
    def group(g: dict[str, Any]) -> frozenset[str]:
        return frozenset(" ".join(str(t).casefold().split()) for t in [g.get("text"), *g.get("synonyms", [])] if t)

    return {
        name: frozenset(group(g) for g in (block or {}).get("groups", []))
        for name, block in (question.get("components") or {}).items() if block
    }


def record_evaluation(
    run_id: str, sufficient: bool, assessment: str, gaps: list[str], adequate: list[str],
    question: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a deep round's judgment; when the evidence is not enough and rounds are left, search again."""
    project, path = locate(run_id)
    research = _research(project)
    if research.mode != "deep":
        raise ValueError("record_evaluation is for deep mode; this review is in " + str(research.mode) + " mode")
    manifest = read_json(path / RUN_FILE)
    require_selection_complete(manifest, "the evaluation")
    rounds = _rounds(manifest)
    current = rounds[-1]
    if "evaluation" in current:
        raise ValueError(f"Round {current['round']} is already evaluated; call research_next(run_id='{run_id}')")
    included = set(_included(manifest))
    unknown = sorted(set(adequate) - included)
    if unknown:
        raise ValueError(f"adequate lists articles that are not included: {', '.join(unknown)}")
    cited = sorted({m.group(1) for m in CITATION.finditer(assessment)} - included)
    if cited:
        raise ValueError(f"the assessment cites articles that are not included: {', '.join(cited)}")
    evaluation = {
        "sufficient": sufficient, "assessment": " ".join(assessment.split()), "gaps": [" ".join(g.split()) for g in gaps if g.strip()],
        "adequate": list(dict.fromkeys(adequate)), "included": len(included), "at": now(),
    }
    left = research.deep_rounds - current["round"]
    result: dict[str, Any] = {"run_id": run_id, "round": current["round"], "sufficient": sufficient}
    launch = None
    if not sufficient and left > 0:
        if question is None:
            raise ValueError("The evidence is not sufficient and rounds are left: pass question, a revised search for the gaps")
        checked = search.validate_question(question, None, project)
        normalized = checked["normalized_question"]
        earlier = [read_json(path / "question.json"), *(r["normalized_question"] for r in rounds if r.get("normalized_question"))]
        if any(_terms(normalized) == _terms(q) for q in earlier):
            raise ValueError("This search is the same as an earlier round's; revise it for the gaps")
        launch = normalized
        result["warnings"] = checked["warnings"]
    elif not sufficient:
        evaluation["budget_reached"] = True
    with locked_run(path) as fresh:
        state = fresh.setdefault("deep", {})
        stored = state.setdefault("rounds", [{"round": 1}])
        stored[-1]["evaluation"] = evaluation
        if evaluation.get("budget_reached"):
            stored[-1]["budget_reached"] = True
        save_run(path, fresh)
    if launch is None:
        result["next"] = f"Call research_next(run_id='{run_id}'): the report comes next."
        return result
    sources = manifest.get("sources") or search.default_sources()
    status = search.start_update(
        path, launch, sources, from_date=None, per_source=int(manifest.get("limit_per_source") or 20),
        cap=research.round_articles, wait_seconds=30, kind="deep",
    )
    with locked_run(path) as fresh:
        number = (fresh.get("update") or {}).get("number") or len(fresh.get("updates", []))
        fresh["deep"]["rounds"].append({
            "round": current["round"] + 1, "question": launch.get("question"), "normalized_question": launch,
            "update": number, "started_at": now(),
        })
        save_run(path, fresh)
    return result | {
        "search": {k: status.get(k) for k in ("search_status", "search_error", "source_failures") if status.get(k)},
        "next": f"Round {current['round'] + 1} has started. Call research_next(run_id='{run_id}') and follow it.",
    }


def _screening_step(
    project: Project, run_id: str, manifest: dict[str, Any], pending: list[str], screened: int,
    research: ResearchSettings, criteria: dict[str, Any],
) -> dict[str, Any]:
    revision = manifest["selection_revision"]
    session = manifest.get("screening_round") or {}
    left = [uid for uid in session.get("uids", []) if uid in set(pending)] if session.get("revision") == revision else None
    screen = (
        f"Repeat next_screening_batch(run_id='{run_id}'{{extra}}) and record_screening_decisions(run_id='{run_id}', "
        "revision=<its revision>, decisions=[...]) until it reports the session complete. Each exclude names the "
        f"criterion it rests on (criterion code). Then call research_next(run_id='{run_id}')."
    )
    if left is None and triage.needed(manifest):
        return _step(
            "triage",
            (
                f"Call next_triage_batch(run_id='{run_id}'), score every title 0-3 as it says, and call "
                f"record_triage_scores(run_id='{run_id}', revision=<its revision>, scores=[...]); repeat until "
                f"remaining is 0, then call research_next(run_id='{run_id}')."
            ),
            pending=len(pending),
        )
    if left is None or left:
        return _step("screen", screen.format(extra=""), pending=len(pending), criteria=criteria)
    counts = summary(manifest)["selection"]
    if research.max_screened or research.mode == "deep":
        return _step(
            "screen",
            screen.format(extra=", new_round=true")
            + (f" (The researcher set max_screened {research.max_screened}; {screened} are screened.)" if research.max_screened else " (Deep mode screens each round's articles without asking.)"),
            pending=len(pending), criteria=criteria,
        )
    return _checkpoint(
        "screening",
        (
            f"This session is screened: tell the researcher the counts ({screened} screened, {len(pending)} not yet, best "
            "first by title) and ask whether to screen the next 20 or stop. To go on, "
            + screen.format(extra=", new_round=true")
            + f" To stop, call research_next(run_id='{run_id}', confirm='stop_screening'); the report then states how many were not screened."
        ),
        selection=counts,
    )


# The report.


def _article_text(conn: Any, uid: str, record: dict[str, Any]) -> tuple[str, str]:
    """The stored text a finding may quote, and what it is (abstract, full_text, abstract_only)."""
    row = conn.execute("SELECT full_text, content_type FROM articles WHERE uid=?", (uid,)).fetchone()
    title, abstract = str(record.get("title") or ""), str(record.get("abstract") or "")
    if row is None:
        return f"{title}\n{abstract}", "abstract"
    return f"{title}\n{abstract}\n{row[0]}", row[1]


def _wiki_evidence(conn: Any, uid: str) -> dict[str, Any]:
    roles = [
        {"entity": r[0], "type": r[1], "role": r[2], "mention": r[3]}
        for r in conn.execute(
            """SELECT e.canonical_name, e.entity_type, m.role, m.mention_text FROM kg_mentions m
               JOIN kg_entities e ON e.id = m.entity_id WHERE m.article_uid=? AND m.role IS NOT NULL
               ORDER BY m.page_index, m.id LIMIT ?""",
            (uid, REPORT_ROLES),
        )
    ]
    relations = [
        {"source": r[0], "relationship": r[1], "target": r[2], "evidence": r[3]}
        for r in conn.execute(
            """SELECT s.canonical_name, r.relationship_type, t.canonical_name, v.evidence
               FROM kg_relationship_evidence v JOIN kg_relationships r ON r.id = v.relationship_id
               JOIN kg_entities s ON s.id = r.source_entity_id JOIN kg_entities t ON t.id = r.target_entity_id
               WHERE v.article_uid=? ORDER BY v.page_index LIMIT ?""",
            (uid, REPORT_RELATIONS),
        )
    ]
    return {"roles": roles, "relationships": relations}


def _entity_summaries(conn: Any, uids: list[str]) -> list[dict[str, Any]]:
    if not uids:
        return []
    marks = ",".join("?" * len(uids))
    return [
        {"entity": r[0], "type": r[1], "sources": r[2], "summary": r[3][:REPORT_SUMMARY]}
        for r in conn.execute(
            f"""SELECT e.canonical_name, e.entity_type, COUNT(DISTINCT m.article_uid) AS n, y.summary
                FROM kg_mentions m JOIN kg_entities e ON e.id = m.entity_id JOIN kg_syntheses y ON y.entity_id = e.id
                WHERE m.article_uid IN ({marks}) GROUP BY e.id ORDER BY n DESC, e.canonical_name LIMIT ?""",
            (*uids, REPORT_ENTITIES),
        )
    ]


def _round_facts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    updates = {u.get("number"): u for u in manifest.get("updates", [])}
    facts = []
    for r in _rounds(manifest):
        q = r.get("normalized_question") or {}
        facts.append({
            "round": r["round"], "question": r.get("question"),
            "components": {
                name: [" OR ".join([g["text"], *g.get("synonyms", [])]) for g in block.get("groups", [])]
                for name, block in (q.get("components") or {}).items() if block
            },
            "new_articles": (updates.get(r.get("update")) or {}).get("new_articles", 0),
            "dropped": len((updates.get(r.get("update")) or {}).get("dropped_over_cap", [])),
            "evaluation": r.get("evaluation"), "budget_reached": r.get("budget_reached", False),
        })
    return facts


def _methods(project: Project, path: Path, manifest: dict[str, Any], mode: str) -> dict[str, Any]:
    """The facts of the search and screening, from the run's record."""
    question = read_json(path / "question.json")
    revision = manifest.get("selection_revision")
    articles = manifest["articles"]
    current = [item["screening"] for item in articles.values() if (item.get("screening") or {}).get("revision") == revision]
    excluded = [s for s in current if s["decision"] == "exclude"]
    status = summary(manifest)
    return {
        "mode": mode,
        "question": question.get("question"),
        "framework": question.get("framework"),
        "components": {
            name: [" OR ".join([group["text"], *group.get("synonyms", [])]) for group in block.get("groups", [])]
            for name, block in (question.get("components") or {}).items() if block
        },
        "filters": question.get("filters") or {},
        "searched_at": manifest.get("created_at"),
        "sources": manifest.get("sources"),
        "per_source": manifest.get("limit_per_source"),
        "preprint_allow": manifest.get("preprint_allow"),
        "records_by_source": status["records_by_source"],
        "source_failures": status["source_failures"],
        "candidates": status["candidates"],
        "already_known": status["already_known"],
        "skipped_preprints": status["skipped_preprints"],
        "skipped_no_identifier": status["skipped_no_identifier"],
        "criteria": _codes_with_reasons(manifest),
        "screened": len(current),
        "by_researcher": sum(s.get("method") == "manual" for s in current),
        "selection": status["selection"],
        "screening_stopped": screening_stopped(manifest),
        "exclusion_reasons": dict(Counter(s.get("criterion") or "not stated" for s in excluded).most_common()),
        "triaged": any(triage.score(item, revision) is not None for item in articles.values()),
        "rounds": _round_facts(manifest) if mode == "deep" else None,
        "fetch": dict(Counter(articles[uid]["fetch"] for uid in _included(manifest))) if mode in FULL_TEXT else None,
    }


def next_report(run_id: str, offset: int = 0) -> dict[str, Any]:
    """The question, the methods facts and the included articles' evidence, in portions that fit one result.

    The rules, methods and entity summaries come with the first portion only; later portions carry articles."""
    project, path = locate(run_id)
    if project.mode == "bot":
        raise ValueError("A bot project reports each run in updates/; research reports are for interactive projects")
    mode = _research(project).mode or "basic"
    manifest = read_json(path / RUN_FILE)
    require_selection_complete(manifest, "the report")
    included = sorted(_included(manifest), key=lambda uid: (manifest["articles"][uid].get("rank") is None, manifest["articles"][uid].get("rank") or 0))
    with database(project.db) as conn:
        response: dict[str, Any] = {"run_id": run_id, "mode": mode, "included_total": len(included), "offset": offset}
        if offset == 0:
            facts = _methods(project, path, manifest, mode)
            response |= {
                "question": read_json(path / "question.json").get("question"),
                "rules": REPORT_RULES,
                "methods": facts,
                "limitations_added": record_limits(facts),
            }
            if mode in FULL_TEXT:
                response["entities"] = _entity_summaries(conn, included)
        if mode in FULL_TEXT:
            response["note"] = f"To quote or check a full-text passage, read it with get_article_page(run_id='{run_id}', uid, page)."
        # Room for the fields added once the portion is known.
        response |= {"remaining": len(included), "next": f"Call next_report(run_id='{run_id}', offset={len(included)}) for the next articles."}

        def build(uid: str) -> dict[str, Any]:
            item = manifest["articles"][uid]
            record = item["record"]
            _, kind = _article_text(conn, uid, record)
            entry: dict[str, Any] = {
                "uid": uid,
                "title": str(record.get("title") or "").strip(),
                "year": str(record.get("publication_date") or "")[:4] or None,
                "journal": record.get("journal"),
                "authors": (record.get("authors") or [])[:3],
                "publication_types": record.get("publication_types") or [],
                "text": kind,
                "screening": {k: item["screening"].get(k) for k in ("reason", "evidence", "method")},
                "abstract": str(record.get("abstract") or "").strip()[:REPORT_ABSTRACT],
            }
            if mode in FULL_TEXT and kind != "abstract":
                pages = conn.execute("SELECT kg_page_count FROM articles WHERE uid=?", (uid,)).fetchone()
                entry |= _wiki_evidence(conn, uid) | {"pages": pages[0] if pages else None}
            return entry

        def shrink(item: dict[str, Any], limit: int) -> dict[str, Any] | None:
            # One article alone is too large: fewer wiki rows first, then a shorter abstract.
            for keep in (10, 5, 0):
                smaller = item | {"evidence_cut": True}
                for key in ("roles", "relationships"):
                    if key in item:
                        smaller[key] = item[key][:keep]
                if encoded_size(response | {"articles": [smaller]}) <= limit:
                    return smaller
            return cut_to_fit(response, "articles", smaller | {"abstract_cut": True}, "abstract", limit)

        response, taken = fill(response, "articles", included[offset:], build, shrink=shrink)
    remaining = len(included) - offset - taken
    response["remaining"] = remaining
    response["next"] = (
        f"Call next_report(run_id='{run_id}', offset={offset + taken}) for the next articles."
        if remaining > 0
        else f"Every included article is listed. Write the report by the rules and call record_report(run_id='{run_id}', ...)."
    )
    return response


def _report_path(project: Project, run_id: str) -> Path:
    """A new file for each report; an earlier report of the same day keeps its file (as bot updates do)."""
    folder, stem = project.root / REPORTS, f"{now()[:10]} {run_id[:8]}"
    path, number = folder / f"{stem}.md", 2
    while path.exists():
        path, number = folder / f"{stem}-{number}.md", number + 1
    return path


def _cell(text: Any) -> str:
    return " ".join(str(text or "").split()).replace("|", "\\|")


def _n(count: int, word: str, plural: str | None = None) -> str:
    return f"{count} {word if count == 1 else plural or word + 's'}"


def _was(count: int) -> str:
    return "was" if count == 1 else "were"


def record_limits(facts: dict[str, Any]) -> list[str]:
    """The limits the review's record shows, in plain words; the report adds them itself, so the agent need not."""
    selection, stopped, notes = facts["selection"], facts["screening_stopped"], []
    if facts["mode"] == "basic":
        notes.append("The answer rests on the studies' abstracts; their full texts were not read.")
    else:
        fetched = facts.get("fetch") or {}
        abstract_only = fetched.get("abstract_only", 0) + fetched.get("failed", 0) + fetched.get("pending", 0)
        if abstract_only:
            notes.append(f"{_n(abstract_only, 'included study', 'included studies')} had no openly available full text and {_was(abstract_only)} read from the abstract only.")
        if fetched.get("skipped"):
            notes.append(f"{_n(fetched['skipped'], 'included study', 'included studies')} {_was(fetched['skipped'])} beyond the full-text limit set for this review and read from the abstract only.")
    if stopped and selection["pending"]:
        notes.append(f"Screening stopped before the end: {_n(selection['pending'], 'lower-ranked article')} {_was(selection['pending'])} not checked, and some may be relevant.")
    if selection["uncertain"]:
        notes.append(f"{_n(selection['uncertain'], 'article')} could not be judged from the abstract and {'is' if selection['uncertain'] == 1 else 'are'} not part of this answer.")
    if facts["source_failures"]:
        notes.append("Some sources could not be searched: " + ", ".join(SOURCE_NAMES.get(s, s) for s in facts["source_failures"]) + ".")
    sources = len(facts["sources"] or [])
    notes.append(
        f"Only the first {facts['per_source']} records from each of the {_n(sources, 'source')} were taken, so the studies "
        "here are a sample of the literature, not all of it."
    )
    if facts.get("rounds") and facts["rounds"][-1].get("budget_reached"):
        notes.append("The review stopped at its round limit before the evidence was judged sufficient.")
    notes.append("This is not a systematic review: the studies' risk of bias and the certainty of the evidence were not assessed.")
    return notes


SOURCE_NAMES = {
    "pubmed": "PubMed", "pmc": "PubMed Central", "openalex": "OpenAlex", "semantic-scholar": "Semantic Scholar",
    "scopus": "Scopus", "europepmc": "Europe PMC",
}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


def _day(value: Any) -> str:
    """'2026-10-26…' as '26 October 2026'."""
    text = str(value or "")[:10]
    try:
        year, month, day = (int(part) for part in text.split("-"))
    except ValueError:
        return text
    return f"{day} {MONTHS[month - 1]} {year}"


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def _numbering(manifest: dict[str, Any], texts: list[str]) -> dict[str, int]:
    """Reference numbers by first citation; included studies nobody cited follow in search-rank order."""
    numbers: dict[str, int] = {}
    for text in texts:
        for match in CITATION.finditer(text):
            numbers.setdefault(match.group(1), len(numbers) + 1)
    for uid in sorted(_included(manifest), key=lambda u: (manifest["articles"][u].get("rank") is None, manifest["articles"][u].get("rank") or 0)):
        numbers.setdefault(uid, len(numbers) + 1)
    return numbers


def _cite(text: str, numbers: dict[str, int]) -> str:
    """[uid] → [n]; adjacent citations merge into one bracket ([1, 3])."""
    text = CITATION.sub(lambda m: f"[{numbers[m.group(1)]}]" if m.group(1) in numbers else m.group(0), text)
    return re.sub(r"\](\s*)\[(?=\d)", ", ", text)


def _reference(item: dict[str, Any], uid: str) -> str:
    record = item["record"]
    authors = record.get("authors") or []
    who = f"{authors[0]}{' et al.' if len(authors) > 1 else ''}" if authors else "Anonymous"
    year = str(record.get("publication_date") or "")[:4] or "n.d."
    title = " ".join(str(record.get("title") or uid).split()).rstrip(".")
    journal = (record.get("journal") or "").strip().rstrip(".")
    link = item.get("source_url") or record.get("url") or (f"https://doi.org/{record['doi']}" if record.get("doi") else "")
    return f"{who} ({year}). {title}." + (f" *{journal}*." if journal else "") + (f" {link}" if link else "") + f" `{uid}`"


def _how_done(facts: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """The methods as a few plain sentences, from the run's record."""
    filters, selection, stopped = facts["filters"], facts["selection"], facts["screening_stopped"]
    sources = _and([SOURCE_NAMES.get(s, s) for s in facts["sources"] or []])
    since = f"published from {_day(filters['from_date'])}" if filters.get("from_date") else "published in the last three years"
    until = f" to {_day(filters['to_date'])}" if filters.get("to_date") else ""
    extra = []
    if filters.get("languages"):
        extra.append("in " + _and(filters["languages"]))
    if filters.get("publication_types"):
        extra.append("of type " + _and(filters["publication_types"]))
    search = (
        f"We searched {sources} on {_day(facts['searched_at'])} for articles {since}{until}"
        + (", " + ", ".join(extra) if extra else "")
        + f", taking up to {facts['per_source']} records from each source"
        + ("" if facts["preprint_allow"] else " and leaving out preprints") + "."
    )
    rounds = facts.get("rounds") or []
    later = sum(r["new_articles"] for r in rounds[1:])
    found = f" This found {facts['candidates'] - later} distinct articles"
    found += f" (another {facts['already_known']} were already known from an earlier search)." if facts["already_known"] else "."
    paragraphs = [search + found]
    if len(rounds) > 1:
        added = "; ".join(
            f"round {r['round']} added {r['new_articles']} with a revised search"
            + (f" (the best {r['new_articles']} of {r['new_articles'] + r['dropped']} new articles by title)" if r.get("dropped") else "")
            for r in rounds[1:]
        )
        paragraphs.append(
            f"The review ran in {len(rounds)} rounds. After each round we judged whether the included studies answered "
            f"the question, and searched again where they did not: {added}."
        )
    screening = (
        f"Two-step screening followed. The titles were first ranked by how likely each study meets the eligibility "
        f"criteria; then the titles and abstracts of {'the ' + str(facts['screened']) + ' best-ranked' if selection['pending'] else 'all ' + str(facts['screened'])} "
        f"articles were checked against the criteria (listed in the appendix). {selection['include']} met them and "
        f"{selection['exclude']} did not"
        if facts["triaged"]
        else f"The titles and abstracts of {facts['screened']} articles were checked against the eligibility criteria "
        f"(listed in the appendix). {selection['include']} met them and {selection['exclude']} did not"
    )
    if selection["uncertain"]:
        screening += f"; {selection['uncertain']} could not be decided from the abstract and are left out"
    screening += "."
    if stopped and selection["pending"]:
        screening += f" Screening stopped there, so {selection['pending']} lower-ranked articles were not checked."
    if facts["by_researcher"]:
        screening += f" The researcher decided {_n(facts['by_researcher'], 'article')} that the abstract alone did not settle."
    paragraphs.append(screening)
    fetched = facts.get("fetch") or {}
    if fetched:
        full = fetched.get("full_text", 0)
        paragraphs.append(
            f"We then read the full text of the included studies where it was openly available ({full} of "
            f"{selection['include']}); the others were read from their abstracts. Statements were extracted from "
            "the text into the project's evidence wiki."
        )
    paragraphs.append("Every quotation in this report was checked word for word against the stored text of its study.")
    return paragraphs


def _appendix(facts: dict[str, Any], numbers: dict[str, int], findings: list[dict[str, str]]) -> list[str]:
    lines = ["## Appendix", ""]
    if findings:
        lines += ["### Key findings and their quotations", ""]
        lines += [f"- **[{numbers[f['uid']]}]** {' '.join(f['finding'].split())}  \n  > {f['quote']}" for f in findings]
        lines.append("")
    criteria = facts["criteria"]
    lines += ["### Eligibility criteria", "", "| | Criterion | Why |", "|---|---|---|"]
    lines += [f"| {'Include' if code.startswith('I') else 'Exclude'} | {_cell(c['criterion'])} | {_cell(c['reason'] or '—')} |" for code, c in criteria.items()]
    lines.append("")
    if facts["exclusion_reasons"]:
        lines += ["### Why articles were excluded", ""]
        for code, n in facts["exclusion_reasons"].items():
            text = (criteria.get(code, {}).get("criterion") or "").rstrip(". ")
            label = (f"Did not meet: {text}" if code.startswith("I") else text) if text else "Reason not recorded"
            lines.append(f"- {label}: {n}")
        lines.append("")
    lines += ["### Search", "", f"- Question ({facts['framework']}): {facts['question']}"]
    lines += [f"- {name.capitalize()}: " + " AND ".join(f"({g})" for g in groups) for name, groups in facts["components"].items()]
    for r in (facts.get("rounds") or [])[1:]:
        lines.append(f"- Round {r['round']} search: {r['question']}")
        lines += [f"  - {name.capitalize()}: " + " AND ".join(f"({g})" for g in groups) for name, groups in (r.get("components") or {}).items()]
    lines.append("- Records by source: " + (", ".join(f"{SOURCE_NAMES.get(s, s)} {n}" for s, n in facts["records_by_source"].items()) or "none"))
    if facts["source_failures"]:
        lines.append("- Sources that failed: " + ", ".join(f"{SOURCE_NAMES.get(s, s)} ({e})" for s, e in facts["source_failures"].items()))
    if facts["skipped_preprints"] or facts["skipped_no_identifier"]:
        lines.append(f"- Left out: {facts['skipped_preprints']} preprints, {facts['skipped_no_identifier']} without a DOI or PMID")
    return lines


def _render(
    project: Project, manifest: dict[str, Any], facts: dict[str, Any], answer: str, body: str,
    findings: list[dict[str, str]], limitations: list[str],
) -> str:
    articles = manifest["articles"]
    selection = facts["selection"]
    numbers = _numbering(manifest, [answer, body, *(f"[{f['uid']}]" for f in findings)])
    how = {"basic": "titles and abstracts", "full": "full texts where available", "deep": "full texts where available, in rounds"}
    lines = [
        "---", GENERATOR, "type: research-report", f"run_id: {manifest['run_id']}", f"mode: {facts['mode']}",
        f"date: {now()[:10]}", f"included: {selection['include']}", "---", "",
        f"# {facts['question']}", "",
        (
            f"*A literature review ({facts['mode']} mode: {how[facts['mode']]}), {_day(now())}. "
            f"{selection['include']} of {facts['screened']} screened articles met the criteria.*"
        ),
        "",
        "## In short", "", _cite(answer.strip(), numbers), "",
    ]
    if body.strip():
        lines += [_cite(body.strip(), numbers), ""]
    evaluation = (facts.get("rounds") or [{}])[-1].get("evaluation")
    if evaluation:
        lines += ["## Is the evidence enough?", "", _cite(evaluation["assessment"], numbers), ""]
        if evaluation.get("gaps"):
            lines += [f"- Still open: {_cite(g, numbers)}" for g in evaluation["gaps"]] + [""]
    lines += ["## How much to trust this", "", *[f"- {_cite(n, numbers)}" for n in [*limitations, *record_limits(facts)]], ""]
    lines += ["## How this review was done", "", *(line for paragraph in _how_done(facts, manifest) for line in (paragraph, ""))]
    lines += ["## References", ""]
    lines += [f"{n}. {_reference(articles[uid], uid)}" for uid, n in sorted(numbers.items(), key=lambda kv: kv[1]) if uid in articles]
    lines.append("")
    lines += _appendix(facts, numbers, findings)
    return "\n".join(lines).rstrip() + "\n"


def _message(facts: dict[str, Any], answer: str, numbers: dict[str, int], target: Path) -> str:
    selection = facts["selection"]
    counts = (
        f"{selection['include']} of {facts['screened']} screened articles met the criteria"
        + (f" ({selection['pending']} were not screened)" if selection["pending"] else "")
        + f"; {facts['candidates']} were found in {_and([SOURCE_NAMES.get(s, s) for s in facts['sources'] or []])}."
    )
    return f"{_cite(answer.strip(), numbers)}\n\n{counts}\n\nThe full report, with references: {target}"


def render_report(run_id: str) -> Path:
    """Write the run's current report again from its stored content, in the current format."""
    project, path = locate(run_id)
    manifest = read_json(path / RUN_FILE)
    report = manifest.get("report") or {}
    content = report.get("content")
    if not content:
        raise ValueError("This report was saved before its content was stored; write it again with record_report")
    facts = _methods(project, path, manifest, report["mode"])
    target = project.root / report["path"]
    atomic_text(target, _render(project, manifest, facts, **content))
    return target


def record_report(
    run_id: str, answer: str, body: str, findings: list[dict[str, str]], limitations: list[str],
) -> dict[str, Any]:
    """Check the report's citations and quotes, render it with the methods, and save it under reports/."""
    project, path = locate(run_id)
    if project.mode == "bot":
        raise ValueError("A bot project reports each run in updates/")
    mode = _research(project).mode or "basic"
    manifest = read_json(path / RUN_FILE)
    require_selection_complete(manifest, "the report")
    included = set(_included(manifest))
    problems = []
    cited = {m.group(1) for m in CITATION.finditer(f"{answer}\n{body}")}
    problems += [f"[{uid}] is not an included article of this run" for uid in sorted(cited - included)]
    if included and not cited:
        problems.append("the answer and body cite no included article; cite each study as [uid]")
    checked = []
    with database(project.db) as conn:
        for number, finding in enumerate(findings, 1):
            uid = finding["uid"]
            if uid not in included:
                problems.append(f"finding {number}: {uid} is not an included article")
                continue
            text, _ = _article_text(conn, uid, manifest["articles"][uid]["record"])
            if not contains_verbatim(text, finding["quote"]):
                problems.append(f"finding {number} ({uid}): the quote is not in the article's stored text; copy it verbatim")
                continue
            checked.append(finding | {"quote": " ".join(finding["quote"].split())})
    if problems:
        return {"saved": False, "problems": problems, "note": "Nothing was saved. Fix these and call record_report again."}
    facts = _methods(project, path, manifest, mode)
    target = _report_path(project, run_id)
    content = {"answer": answer, "body": body, "findings": checked, "limitations": limitations}
    atomic_text(target, _render(project, manifest, facts, **content))
    numbers = _numbering(manifest, [answer, body, *(f"[{f['uid']}]" for f in checked)])
    message = _message(facts, answer, numbers, target)
    relative = str(target.relative_to(project.root))
    with locked_run(path) as fresh:
        if fresh.get("report"):
            fresh.setdefault("report_history", []).append(fresh["report"])
        fresh["report"] = {
            "path": relative, "written_at": now(), "mode": mode, "revision": fresh.get("selection_revision"),
            "included_sha256": _included_digest(fresh), "selection": summary(fresh)["selection"],
            "findings": len(checked), "message": message, "content": content,
        }
        save_run(path, fresh)
    return {"saved": True, "path": str(target), "findings": len(checked), "message": message,
            "next": "Reply to the researcher with `message`."}


def report_brief(manifest: dict[str, Any]) -> dict[str, Any] | None:
    report = manifest.get("report")
    return {k: report.get(k) for k in ("path", "written_at", "mode")} if report else None

