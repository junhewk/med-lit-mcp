"""Agent-driven, auditable title/abstract screening."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import triage
from .limits import cut_to_fit, fill
from .matching import contains_verbatim
from .projects import run_dir
from .runs import summary
from .store import locked_run, now, save_run

VALID_DECISIONS = ("include", "exclude", "uncertain")
ABSTRACT_LIMIT = 6000
# Clients cap one tool result (pi at 20 KiB, Hermes at 50,000 characters), so a batch stops when the
# encoded response would pass limits.response_bytes(), however many items were asked for (limits.py).
ABSTRACT_CUT_NOTE = "This record alone exceeds the size limit for one tool result; the abstract was cut at a word boundary."
MAX_CORRECTIONS = 1
INSTRUCTIONS = (
    "Screen each item using only its title and abstract against the criteria. A clear inclusion "
    "match is include; a clear exclusion match is exclude; missing or ambiguous information is "
    "uncertain. Never infer unavailable facts. For include/exclude, evidence must be an exact "
    "short quote from the title or abstract. For uncertain, the reason must name the criterion "
    "that cannot be judged and what the title/abstract leaves open (e.g. 'does not say whether the "
    "virtual patient uses a large language model'), never just 'ambiguous'. For exclude, criterion "
    "is the code of the criterion the decision rests on: an exclusion criterion it meets (E1) or an "
    "inclusion criterion it fails (I2). Submit all items with record_screening_decisions using this revision."
)


def criterion_codes(criteria: dict[str, list[str]] | None) -> dict[str, str]:
    """I1, I2 … for the inclusion criteria and E1, E2 … for the exclusion criteria, in their order."""
    criteria = criteria or {}
    return {f"{prefix}{number}": text for key, prefix in (("include", "I"), ("exclude", "E"))
            for number, text in enumerate(criteria.get(key) or [], 1)}


def research_mode(run_id: str) -> str | None:
    """The research mode (basic, full, deep) of the run's project, None when no mode is chosen."""
    from .projects import locate
    from .settings import load_settings

    research = load_settings(locate(run_id)[0].root).research
    return research.mode if research else None


def normalize_criteria(include: list[str], exclude: list[str]) -> dict[str, list[str]]:
    criteria = {
        "include": [" ".join(x.split()) for x in include if x.strip()],
        "exclude": [" ".join(x.split()) for x in exclude if x.strip()],
    }
    if not criteria["include"] and not criteria["exclude"]:
        raise ValueError("Provide at least one inclusion or exclusion criterion")
    return criteria


# Words that say a decision is hard without saying why.
GENERIC_REASON_WORDS = frozenset(
    ["a", "an", "and", "are", "as", "be", "borderline", "by", "case", "check", "could", "decide", "decision", "for", "further", "human", "in", "insufficient", "is", "it", "manual", "may", "might", "more", "need", "needed", "needs", "not", "of", "or", "possibly", "record", "relevant", "requires", "required", "researcher", "review", "reviewer", "should", "sure", "the", "this", "to", "unclear", "uncertain", "ambiguous", "ambiguity", "whether", "article", "study", "paper", "information", "clear"]
)


def _specific(reason: str) -> bool:
    words = [w for w in "".join(c if c.isalnum() else " " for c in reason.casefold()).split() if len(w) > 2]
    return len([w for w in words if w not in GENERIC_REASON_WORDS]) >= 3


def requeue_vague(manifest: dict[str, Any]) -> int:
    """Send unspecific uncertain reasons back to screening (recorded before the check existed)."""
    revision = manifest.get("selection_revision")
    requeued = 0
    for item in manifest["articles"].values():
        screening = item.get("screening") or {}
        if (
            screening.get("revision") == revision
            and screening.get("method") == "agent"
            and screening.get("decision") == "uncertain"
            and "validation_error" not in screening
            and not _specific(screening.get("reason") or "")
        ):
            item.setdefault("screening_history", []).append(item.pop("screening") | {"requeued": "reason not specific"})
            requeued += 1
    return requeued


def validate_decision(
    decision: str, reason: str, evidence: str, title: str, abstract: str,
    *, criterion: str = "", codes: dict[str, str] | None = None, needs_criterion: bool = False,
) -> str | None:
    """Return why a decision cannot be accepted as stated, or None."""
    if decision not in VALID_DECISIONS:
        return "decision must be include, exclude, or uncertain"
    if not reason.strip():
        return "reason is empty"
    if criterion and criterion not in (codes or {}):
        return f"criterion {criterion!r} is not one of the criteria ({', '.join(codes or {}) or 'none'})"
    if decision == "exclude" and needs_criterion and not criterion:
        return "exclude needs the criterion it rests on: an exclusion code (E1) or a failed inclusion code (I1)"
    if decision == "uncertain" and not _specific(reason):
        return (
            "the reason must name the criterion that cannot be judged and what the title/abstract "
            "leaves open, not just that the case is ambiguous"
        )
    if decision != "uncertain":
        if not evidence.strip():
            return "include/exclude needs an evidence quote"
        if not contains_verbatim(f"{title}\n{abstract}", evidence):
            return "evidence is not a verbatim quote from the title or abstract"
    return None


def _check_reasons(criteria: dict[str, list[str]], reasons: dict[str, str] | None, required: bool) -> dict[str, str]:
    codes = criterion_codes(criteria)
    reasons = {code.strip().upper(): " ".join(text.split()) for code, text in (reasons or {}).items() if text.strip()}
    unknown = sorted(set(reasons) - set(codes))
    if unknown:
        raise ValueError(f"reasons name unknown criteria {', '.join(unknown)}; the codes are {', '.join(codes)}")
    missing = [code for code in codes if code not in reasons]
    if required and missing:
        raise ValueError(
            "Give each criterion the reason it follows from the question (reasons={code: reason}); missing: "
            + ", ".join(f"{code} ({codes[code]})" for code in missing)
        )
    return reasons


def set_criteria(
    run_id: str, include: list[str], exclude: list[str], *, replace: bool = False,
    reasons: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Save criteria; a research project needs a reason for each one (reasons keyed I1 … E1 …)."""
    criteria = normalize_criteria(include, exclude)
    reasons = _check_reasons(criteria, reasons, required=research_mode(run_id) is not None)
    path = run_dir(run_id)
    with locked_run(path) as manifest:
        if manifest["search_status"] != "complete":
            raise ValueError("Search must complete before screening")
        current = manifest.get("selection_criteria")
        changed = current != criteria
        if current is not None and changed and not replace:
            raise ValueError(
                "Criteria differ from the current revision; pass replace=true to start a new "
                "revision (every article is screened again)"
            )
        if changed:
            revision = int(manifest.get("selection_revision") or 0) + 1
            manifest["selection_revision"] = revision
            manifest["selection_criteria"] = criteria
            manifest.setdefault("selection_history", []).append(
                {"revision": revision, "criteria": criteria, "created_at": now()}
            )
            for item in manifest["articles"].values():
                if "screening" in item:
                    item.setdefault("screening_history", []).append(item.pop("screening"))
        # Reasons belong to their revision: new criteria drop the old ones; the same criteria may gain them.
        reasons_changed = (changed or bool(reasons)) and manifest.get("selection_reasons", {}) != reasons
        if reasons_changed:
            manifest["selection_reasons"] = reasons
            if manifest.get("selection_history"):
                manifest["selection_history"][-1]["reasons"] = reasons
        if changed or reasons_changed:
            save_run(path, manifest)
        result = summary(manifest)
        return {
            "run_id": run_id,
            "revision": manifest["selection_revision"],
            "criteria": criteria,
            "codes": criterion_codes(criteria),
            "reasons": manifest.get("selection_reasons") or {},
            "changed": changed,
            "rescreen_required": changed and current is not None,
            "pending": result["selection"]["pending"],
        }


def _pending(manifest: dict[str, Any], revision: int) -> tuple[list[str], bool]:
    """Unscreened articles; one with no title or abstract is marked uncertain for the researcher instead."""
    pending, changed = [], False
    for uid, item in manifest["articles"].items():
        if (item.get("screening") or {}).get("revision") == revision:
            continue
        record = item["record"]
        if not str(record.get("title") or "").strip() and not str(record.get("abstract") or "").strip():
            item["screening"] = {
                "decision": "uncertain",
                "reason": "Search result has no title or abstract; review required",
                "evidence": "",
                "revision": revision,
                "method": "system",
                "reviewed_at": now(),
            }
            changed = True
            continue
        pending.append(uid)
    return pending, changed


def _session_left(manifest: dict[str, Any], revision: int, pending: list[str]) -> list[str] | None:
    """Unscreened articles of the current session, or None when no session is open at this revision."""
    session = manifest.get("screening_round")
    if not session or session.get("revision") != revision:
        return None
    waiting = set(pending)
    return [uid for uid in session["uids"] if uid in waiting]


def open_session(manifest: dict[str, Any], revision: int, pending: list[str], key: str | None = None) -> bool:
    """Start a session with the best-triaged SESSION_SIZE unscreened articles; False when triage must come first."""
    if triage.needed(manifest):
        return False
    ordered = sorted(pending, key=lambda uid: triage.order_key(manifest["articles"][uid], revision))
    manifest["screening_round"] = {
        "revision": revision, "uids": ordered[: triage.SESSION_SIZE], "started_at": now(), "key": key,
    }
    return True


def prepare_session(path: Path, key: str) -> dict[str, Any]:
    """Open the session of one bot run (key) if it has none; reports whether triage must come first."""
    with locked_run(path) as manifest:
        revision = manifest.get("selection_revision")
        pending, changed = _pending(manifest, revision)
        left = _session_left(manifest, revision, pending)
        needs_triage = False
        if pending and (left is None or (manifest.get("screening_round") or {}).get("key") != key):
            if open_session(manifest, revision, pending, key):
                changed = True
                left = _session_left(manifest, revision, pending)
            else:
                needs_triage = True
        if changed:
            save_run(path, manifest)
        return {"triage": needs_triage, "pending": len(pending), "left": len(left or [])}


def _with_next_step(run_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """In a research review, a screened session ends with the review's next step itself, as research_next
    would return it (counts, the checkpoint question, what to call for each answer). An agent that asks
    the researcher straight away, as Hermes does, then asks from the server's facts."""
    from .research import research_next

    result["next_step"] = research_next(run_id=run_id)
    result["next"] = (
        "The session is screened. next_step is the review's next step (the same as research_next): follow it; "
        "if it has ask_researcher=true, ask the researcher what it lists and stop."
    )
    return result


def next_batch(run_id: str, batch_size: int = 10, *, new_round: bool = False) -> dict[str, Any]:
    result = _next_batch(run_id, batch_size, new_round=new_round)
    if result.get("session_complete") and research_mode(run_id):
        result = _with_next_step(run_id, result)
    return result


def _next_batch(run_id: str, batch_size: int = 10, *, new_round: bool = False) -> dict[str, Any]:
    path = run_dir(run_id)
    with locked_run(path) as manifest:
        revision = manifest.get("selection_revision")
        if revision is None:
            raise ValueError("Set screening criteria first with set_screening_criteria")
        changed = requeue_vague(manifest) > 0
        if new_round and (manifest.get("screening_stopped") or {}).get("revision") == revision:
            manifest.pop("screening_stopped")  # the researcher asked to screen on after stopping
            changed = True
        pending, marked = _pending(manifest, revision)
        changed |= marked
        left = _session_left(manifest, revision, pending)
        base = {
            "run_id": run_id, "revision": revision, "criteria": manifest["selection_criteria"],
            "criterion_codes": criterion_codes(manifest["selection_criteria"]),
        }
        if pending and not left:
            if left is not None and not new_round:
                if changed:
                    save_run(path, manifest)
                done = f"This session's {len(manifest['screening_round']['uids'])} articles are screened. "
                return base | {
                    "items": [],
                    "remaining": len(pending),
                    "session_complete": True,
                    "message": (
                        done + "Follow next_step."
                        if research_mode(run_id)
                        else done + f"Report them to the researcher and stop; {len(pending)} articles wait for later "
                        "sessions, which start with next_screening_batch(new_round=true) when the researcher asks."
                    ),
                }
            if not open_session(manifest, revision, pending):
                if changed:
                    save_run(path, manifest)
                return base | {
                    "items": [],
                    "remaining": len(pending),
                    "triage_needed": True,
                    "message": (
                        f"{len(pending)} articles wait and a session screens {triage.SESSION_SIZE}: rank them first. "
                        "Repeat next_triage_batch and record_triage_scores until remaining is 0, then call "
                        "next_screening_batch again."
                    ),
                }
            changed = True
            left = _session_left(manifest, revision, pending) or []
        response = base | {
            "instructions": INSTRUCTIONS,
            "items": [],
            "session_left": len(left or []),
            "remaining": len(pending),
        }

        def build(uid: str) -> dict[str, Any]:
            record = manifest["articles"][uid]["record"]
            abstract = str(record.get("abstract") or "").strip()
            return {
                "uid": uid,
                "title": str(record.get("title") or "").strip(),
                "abstract": abstract[:ABSTRACT_LIMIT],
                "abstract_truncated": len(abstract) > ABSTRACT_LIMIT,
                "journal": record.get("journal"),
                "year": str(record.get("publication_date") or "")[:4] or None,
                "publication_types": record.get("publication_types") or [],
            }

        def shrink(item: dict[str, Any], limit: int) -> dict[str, Any] | None:
            # One record alone is over the size limit: send the start of its abstract and say so.
            # A quote from the delivered part is still verbatim.
            flagged = item | {"abstract_truncated": True, "abstract_cut": ABSTRACT_CUT_NOTE}
            return cut_to_fit(response, "items", flagged, "abstract", limit)

        response, _ = fill(response, "items", (left or [])[:batch_size], build, shrink=shrink)
        if changed:
            save_run(path, manifest)
        return response


def record_decisions(
    run_id: str, revision: int, decisions: list[dict[str, str]], *, client: str | None = None
) -> dict[str, Any]:
    result = _record_decisions(run_id, revision, decisions, client=client)
    if result.pop("session_done"):
        result = _with_next_step(run_id, result)
    return result


def _record_decisions(
    run_id: str, revision: int, decisions: list[dict[str, str]], *, client: str | None = None
) -> dict[str, Any]:
    path = run_dir(run_id)
    needs_criterion = research_mode(run_id) is not None
    with locked_run(path) as manifest:
        current_revision = manifest.get("selection_revision")
        codes = criterion_codes(manifest.get("selection_criteria"))
        if revision != current_revision:
            raise ValueError(
                f"Criteria are at revision {current_revision}; call next_screening_batch again"
            )
        recorded, downgraded, rejected = [], [], []
        for entry in decisions:
            uid = entry["uid"]
            item = manifest["articles"].get(uid)
            if item is None:
                rejected.append({"uid": uid, "error": "unknown article ID"})
                continue
            previous = item.get("screening") or {}
            corrections = 0
            if previous.get("revision") == revision:
                if not previous.get("validation_error") or previous.get("corrections", 0) >= MAX_CORRECTIONS:
                    rejected.append(
                        {"uid": uid, "error": "already recorded; use review_article for the researcher's decision"}
                    )
                    continue
                corrections = previous.get("corrections", 0) + 1
            record = item["record"]
            decision = entry["decision"]
            reason = " ".join(str(entry.get("reason") or "").split())
            evidence = " ".join(str(entry.get("evidence") or "").split())
            criterion = str(entry.get("criterion") or "").strip().upper()
            error = validate_decision(
                decision, reason, evidence, str(record.get("title") or ""), str(record.get("abstract") or ""),
                criterion=criterion, codes=codes, needs_criterion=needs_criterion,
            )
            screening = {
                "decision": decision if error is None else "uncertain",
                "reason": reason[:1000] or "No reason given",
                "evidence": evidence[:500] if error is None else "",
                "revision": revision,
                "method": "agent",
                "client": client,
                "reviewed_at": now(),
            }
            if criterion and error is None:
                screening["criterion"] = criterion
            if corrections:
                screening["corrections"] = corrections
            if error is not None:
                screening.update(
                    {"validation_error": error, "original_decision": decision, "original_evidence": evidence[:500]}
                )
                downgraded.append({"uid": uid, "original_decision": decision, "error": error})
            if previous:
                item.setdefault("screening_history", []).append(previous)
            item["screening"] = screening
            recorded.append(uid)
        save_run(path, manifest)
        result = summary(manifest)
        left = _session_left(manifest, revision, [u for u, i in manifest["articles"].items()
                                                  if (i.get("screening") or {}).get("revision") != revision])
        return {
            "run_id": run_id,
            "revision": revision,
            # Downgraded decisions may be resubmitted first; the next step follows once they are settled.
            "session_done": needs_criterion and left is not None and not left and not downgraded,
            "next": None,
            "session_left": len(left) if left is not None else None,
            "recorded": len(recorded),
            "downgraded": downgraded,
            "rejected": rejected,
            "remaining": result["selection"]["pending"],
            "selection": result["selection"],
            "note": (
                "Downgraded items are stored as uncertain; resubmit each once, fixing its error (a "
                "verbatim quote, a specific reason, or a criterion code), or leave them for the researcher."
                if downgraded
                else None
            ),
        }


def review(
    run_id: str, uid: str, decision: str, reason: str, *, criterion: str | None = None, client: str | None = None
) -> dict[str, Any]:
    if decision not in ("include", "exclude") or not reason.strip():
        raise ValueError("Review needs include or exclude and the researcher's reason")
    path = run_dir(run_id)
    with locked_run(path) as manifest:
        revision = manifest.get("selection_revision")
        if revision is None:
            raise ValueError("Set screening criteria before review")
        item = manifest["articles"].get(uid)
        if item is None:
            raise ValueError(f"Unknown article ID: {uid}")
        codes = criterion_codes(manifest.get("selection_criteria"))
        criterion = (criterion or "").strip().upper() or None
        if criterion and criterion not in codes:
            raise ValueError(f"criterion {criterion!r} is not one of the criteria ({', '.join(codes)})")
        if "screening" in item:
            item.setdefault("screening_history", []).append(item["screening"])
        item["screening"] = {
            "decision": decision,
            "reason": reason.strip()[:1000],
            "evidence": "",
            "revision": revision,
            "method": "manual",
            "client": client,
            "reviewed_at": now(),
        } | ({"criterion": criterion} if criterion else {})
        save_run(path, manifest)
        return summary(manifest)
