"""Agent-driven, auditable title/abstract screening."""

from __future__ import annotations

from typing import Any

from .matching import contains_verbatim
from .projects import run_dir
from .runs import summary
from .store import locked_run, now, save_run

VALID_DECISIONS = ("include", "exclude", "uncertain")
ABSTRACT_LIMIT = 6000
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


def next_batch(run_id: str, batch_size: int = 10) -> dict[str, Any]:
    path = run_dir(run_id)
    with locked_run(path) as manifest:
        revision = manifest.get("selection_revision")
        if revision is None:
            raise ValueError("Set screening criteria first with set_screening_criteria")
        items, changed = [], False
        pending = 0
        for uid, item in manifest["articles"].items():
            if (item.get("screening") or {}).get("revision") == revision:
                continue
            record = item["record"]
            title = str(record.get("title") or "").strip()
            abstract = str(record.get("abstract") or "").strip()
            if not title and not abstract:
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
            pending += 1
            if len(items) < batch_size:
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
        return {
            "run_id": run_id,
            "revision": revision,
            "criteria": manifest["selection_criteria"],
            "instructions": INSTRUCTIONS,
            "items": items,
            "remaining": pending,
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
        return {
            "run_id": run_id,
            "revision": revision,
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
