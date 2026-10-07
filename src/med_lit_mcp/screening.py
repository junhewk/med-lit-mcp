"""Agent-driven, auditable title/abstract screening."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import triage
from .matching import contains_verbatim
from .projects import run_dir
from .runs import summary
from .store import locked_run, now, save_run

VALID_DECISIONS = ("include", "exclude", "uncertain")
ABSTRACT_LIMIT = 6000
# Clients cap one tool result: Hermes moves an MCP result over 50,000 characters to a file and shows the
# model only a pointer (a 25-item batch measured 60,716). A batch therefore stops at this many characters
# of titles and abstracts, however many items were asked for.
BATCH_CHARS = 20_000
MAX_CORRECTIONS = 1
INSTRUCTIONS = (
    "Screen each item using only its title and abstract against the criteria. A clear inclusion "
    "match is include; a clear exclusion match is exclude; missing or ambiguous information is "
    "uncertain. Never infer unavailable facts. For include/exclude, evidence must be an exact "
    "short quote from the title or abstract. For uncertain, the reason must name the criterion "
    "that cannot be judged and what the title/abstract leaves open (e.g. 'does not say whether the "
    "virtual patient uses a large language model'), never just 'ambiguous'. Submit all items with "
    "record_screening_decisions using this revision."
)


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
    decision: str, reason: str, evidence: str, title: str, abstract: str
) -> str | None:
    """Return why a decision cannot be accepted as stated, or None."""
    if decision not in VALID_DECISIONS:
        return "decision must be include, exclude, or uncertain"
    if not reason.strip():
        return "reason is empty"
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


def set_criteria(
    run_id: str, include: list[str], exclude: list[str], *, replace: bool = False
) -> dict[str, Any]:
    criteria = normalize_criteria(include, exclude)
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
            save_run(path, manifest)
        result = summary(manifest)
        return {
            "run_id": run_id,
            "revision": manifest["selection_revision"],
            "criteria": criteria,
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


def next_batch(run_id: str, batch_size: int = 10, *, new_round: bool = False) -> dict[str, Any]:
    path = run_dir(run_id)
    with locked_run(path) as manifest:
        revision = manifest.get("selection_revision")
        if revision is None:
            raise ValueError("Set screening criteria first with set_screening_criteria")
        changed = requeue_vague(manifest) > 0
        pending, marked = _pending(manifest, revision)
        changed |= marked
        left = _session_left(manifest, revision, pending)
        base = {"run_id": run_id, "revision": revision, "criteria": manifest["selection_criteria"]}
        if pending and not left:
            if left is not None and not new_round:
                if changed:
                    save_run(path, manifest)
                return base | {
                    "items": [],
                    "remaining": len(pending),
                    "session_complete": True,
                    "message": (
                        f"This session's {len(manifest['screening_round']['uids'])} articles are screened. Report "
                        f"them to the researcher and stop; {len(pending)} articles wait for later sessions, which "
                        "start with next_screening_batch(new_round=true) when the researcher asks."
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
        items, size = [], 0
        for uid in (left or [])[:batch_size]:
            record = manifest["articles"][uid]["record"]
            title = str(record.get("title") or "").strip()
            abstract = str(record.get("abstract") or "").strip()
            length = len(title) + min(len(abstract), ABSTRACT_LIMIT)
            if items and size + length > BATCH_CHARS:
                break
            size += length
            items.append(
                {
                    "uid": uid,
                    "title": title,
                    "abstract": abstract[:ABSTRACT_LIMIT],
                    "abstract_truncated": len(abstract) > ABSTRACT_LIMIT,
                    "journal": record.get("journal"),
                    "year": str(record.get("publication_date") or "")[:4] or None,
                    "publication_types": record.get("publication_types") or [],
                }
            )
        if changed:
            save_run(path, manifest)
        return base | {
            "instructions": INSTRUCTIONS,
            "items": items,
            "session_left": len(left or []),
            "remaining": len(pending),
        }


def record_decisions(
    run_id: str, revision: int, decisions: list[dict[str, str]], *, client: str | None = None
) -> dict[str, Any]:
    path = run_dir(run_id)
    with locked_run(path) as manifest:
        current_revision = manifest.get("selection_revision")
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
            error = validate_decision(
                decision, reason, evidence, str(record.get("title") or ""), str(record.get("abstract") or "")
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
            "session_left": len(left) if left is not None else None,
            "recorded": len(recorded),
            "downgraded": downgraded,
            "rejected": rejected,
            "remaining": result["selection"]["pending"],
            "selection": result["selection"],
            "note": (
                "Downgraded items are stored as uncertain; resubmit each once, fixing its error (a "
                "verbatim quote, or a specific reason), or leave them for the researcher."
                if downgraded
                else None
            ),
        }


def review(run_id: str, uid: str, decision: str, reason: str, *, client: str | None = None) -> dict[str, Any]:
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
        }
        save_run(path, manifest)
        return summary(manifest)
