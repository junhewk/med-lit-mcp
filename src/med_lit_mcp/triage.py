"""Rank articles by the agent's reading of their titles, so each session screens the most promising 20.

Search ranking matches words, so it cannot see what a concept does in a study (continuous glucose
monitoring as the intervention, or only as a measurement), and that is what decides inclusion. Before
screening, the agent scores every title 0-3 against the question and criteria; screening then takes
the best-scored articles, 20 per session, ties broken by the search's relevance rank.

A bot triages its new articles while they are still candidates, then keeps the best-scored ones up to
its cap; an interactive run triages its unscreened articles.
"""

from __future__ import annotations

from typing import Any

from .projects import run_dir
from .settings import MAX_SESSION
from .store import locked_run, now, read_json, save_run

SESSION_SIZE = MAX_SESSION  # articles screened per session (a bot run, or one interactive round)
TRIAGE_CHARS = 20_000  # characters of titles per batch, under clients' tool-result limits
SCALE = {
    3: "clearly meets the criteria",
    2: "probably meets them",
    1: "related topic, but probably does not meet them",
    0: "unrelated",
}
INSTRUCTIONS = (
    "Score each title alone, without an abstract, for how likely its article meets the criteria, on "
    "the scale given. Judge what the study is about, not shared words: a study that only uses the "
    "intervention as a measuring tool is unrelated to a question about the intervention. Score every "
    "item, then submit the scores with record_triage_scores."
)


def _pool(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A bot update's candidates while they wait for its cap, otherwise the unscreened articles."""
    if manifest.get("candidates"):
        return manifest["candidates"]
    revision = manifest.get("selection_revision")
    return {
        uid: item
        for uid, item in manifest["articles"].items()
        if (item.get("screening") or {}).get("revision") != revision
    }


def score(item: dict[str, Any], revision: int | None) -> int | None:
    triage = item.get("triage") or {}
    return triage.get("score") if triage.get("revision") == revision else None


def order_key(item: dict[str, Any], revision: int | None) -> tuple[int, float]:
    """Best triage score first (untriaged last), then the search's relevance rank."""
    value = score(item, revision)
    rank = item.get("rank")
    return (-(value if value is not None else -1), rank if rank is not None else float("inf"))


def untriaged(manifest: dict[str, Any]) -> list[str]:
    revision = manifest.get("selection_revision")
    return [uid for uid, item in _pool(manifest).items() if score(item, revision) is None]


def needed(manifest: dict[str, Any], size: int = SESSION_SIZE) -> bool:
    """Triage decides the order only when more articles wait than one session takes."""
    return len(_pool(manifest)) > size and bool(untriaged(manifest))


def next_batch(run_id: str) -> dict[str, Any]:
    path = run_dir(run_id)
    with locked_run(path, write=False) as manifest:
        revision = manifest.get("selection_revision")
        if revision is None:
            raise ValueError("Set screening criteria first with set_screening_criteria")
        pool = _pool(manifest)
        waiting = sorted(untriaged(manifest), key=lambda uid: order_key(pool[uid], revision))
        items, size = [], 0
        for uid in waiting:
            title = " ".join(str(pool[uid]["record"].get("title") or "").split()) or "(no title)"
            if items and size + len(title) > TRIAGE_CHARS:
                break
            items.append({"uid": uid, "title": title})
            size += len(title)
        question = read_json(path / "question.json").get("question") if (path / "question.json").exists() else None
        return {
            "run_id": run_id,
            "revision": revision,
            "question": question,
            "criteria": manifest["selection_criteria"],
            "scale": SCALE,
            "instructions": INSTRUCTIONS,
            "items": items,
            "remaining": len(waiting),
        }


def record_scores(run_id: str, revision: int, scores: list[dict[str, Any]]) -> dict[str, Any]:
    path = run_dir(run_id)
    with locked_run(path) as manifest:
        if revision != manifest.get("selection_revision"):
            raise ValueError(f"Criteria are at revision {manifest.get('selection_revision')}; call next_triage_batch again")
        pool = _pool(manifest)
        recorded, rejected = 0, []
        for entry in scores:
            item = pool.get(entry["uid"])
            if item is None:
                rejected.append({"uid": entry["uid"], "error": "not waiting for triage"})
                continue
            item["triage"] = {"score": int(entry["score"]), "revision": revision, "at": now()}
            recorded += 1
        save_run(path, manifest)
        return {"run_id": run_id, "recorded": recorded, "rejected": rejected, "remaining": len(untriaged(manifest))}


def select_candidates(manifest: dict[str, Any], cap: int) -> int:
    """Keep a bot update's best-triaged candidates up to its cap; the rest are reported and may be found again."""
    candidates = manifest.pop("candidates", {})
    revision = manifest.get("selection_revision")
    ordered = sorted(candidates, key=lambda uid: order_key(candidates[uid], revision))
    kept, dropped = ordered[:cap], ordered[cap:]
    for uid in kept:
        entry = candidates[uid]
        manifest["articles"][uid] = {
            "record": entry["record"], "fetch": "pending", "wiki": "pending", "rank": entry.get("rank"),
            "added_in_update": entry["update"], **({"triage": entry["triage"]} if "triage" in entry else {}),
        }
    for update in manifest.get("updates", []):
        if update.get("number") in {candidates[uid]["update"] for uid in candidates}:
            update["new_articles"] = sum(candidates[uid]["update"] == update["number"] for uid in kept)
            update["dropped_over_cap"] = [uid for uid in dropped if candidates[uid]["update"] == update["number"]]
    return len(kept)
