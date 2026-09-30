"""Agent-driven knowledge graph: paged extraction, lexical entity resolution, synthesis."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from .matching import (
    AliasScanner,
    candidates,
    contains_verbatim,
    find_acronym_definitions,
    fold,
    looks_like_acronym,
    name_key,
    split_acronym,
)
from .ontology import related_types
from .projects import Project, locate, run_dir
from .runs import is_eligible, load_manifest
from .settings import load_settings
from .store import database, locked_run, now, question_text, save_run, transaction

PAGE_CHARS = 12_000
MENTION_LIMIT = 30
MENTION_CHARS = 9_000
CITATION = re.compile(r"\[([a-z][a-z0-9_-]*:[A-Za-z0-9._/-]+)\]")
WIKI_LINK = re.compile(r"\[\[([^\[\]|]+)(?:\|[^\[\]]*)?\]\]")
EXCLUDED_NAMES = frozenset(
    """article|articles|author|authors|cc by|cc by 4.0|copyright|creative commons|
    creative commons attribution license|creative commons attribution license cc-by 4.0|
    creative commons license|epub|journal|open access|paper|papers|pmc|pmc-last-change|pmc-live|
    pmc-release|publication|publications|pubmed|research article|journal article|
    jmir publications inc.|nature publishing group|scientific reports|springer|source|sources|
    study|studies|this study|the study|authors' contributions|conflict of interest|
    funding|acknowledgements|acknowledgments|references|supplementary material|
    spss|ibm spss|ibm spss statistics|spss statistics|stata|sas|graphpad|graphpad prism|prism|nvivo|
    microsoft excel|excel|r software|r statistical software|jamovi|jasp|atlas.ti|maxqda|
    python software|redcap|qualtrics""".replace("\n", "")
    .split("|")
)
EXCLUDED_NAMES = frozenset(" ".join(name.split()) for name in EXCLUDED_NAMES)
MONTHS = (
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december",
)


def is_junk_name(name: str) -> bool:
    """Publication metadata that must never become an entity (port of the Rust filter)."""
    value = fold(name)
    if not value or value in EXCLUDED_NAMES:
        return True
    if re.fullmatch(r"(19|20)\d\d|2100", value):
        return True
    if re.fullmatch(r"(pmc|pubmed|pmid)\s*:?\s*\d+", value) or re.fullmatch(r"10\.\d{4,}/\S+", value):
        return True
    if value.startswith(("doi:", "license:", "volume ", "issue ", "https://", "http://")):
        return True
    if len(value) <= 40 and any(month in value for month in MONTHS):
        tokens = re.split(r"[^a-z0-9]+", value)
        has_year = any(re.fullmatch(r"(19|20)\d\d", token) for token in tokens)
        has_day = any(token.isdigit() and 1 <= int(token) <= 31 for token in tokens)
        if has_year and has_day:
            return True
    return False


def paginate(title: str, text: str, size: int = PAGE_CHARS) -> list[str]:
    """Deterministic pages at paragraph, then sentence, then hard boundaries."""
    units: list[str] = []
    for paragraph in f"Title: {title}\n\n{text}".split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= size:
            units.append(paragraph)
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            while len(sentence) > size:
                units.append(sentence[:size])
                sentence = sentence[size:]
            if sentence:
                units.append(sentence)
    pages: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + 2 + len(unit) > size:
            pages.append(current)
            current = unit
        else:
            current = f"{current}\n\n{unit}" if current else unit
    if current:
        pages.append(current)
    return pages or [f"Title: {title}"]


def _article(conn: sqlite3.Connection, uid: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM articles WHERE uid=?", (uid,)).fetchone()
    if row is None:
        raise ValueError(f"Article {uid} has not been fetched")
    return row


def _pages(row: sqlite3.Row) -> list[str]:
    return paginate(str(row["title"] or row["uid"]), str(row["full_text"]))


def _fetched_uids(manifest: dict[str, Any]) -> list[str]:
    revision = manifest.get("selection_revision")
    return [
        uid
        for uid, item in manifest["articles"].items()
        if is_eligible(item, revision) and item["fetch"] in ("full_text", "abstract_only")
    ]


def _mark_stale(conn: sqlite3.Connection, entity_ids: set[int]) -> None:
    for entity_id in entity_ids:
        conn.execute("UPDATE kg_syntheses SET stale=1 WHERE entity_id=?", (entity_id,))


def _reset_if_changed(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    """Drop graph data extracted from an older version of the article text."""
    old = conn.execute(
        "SELECT 1 FROM kg_extraction_pages WHERE article_uid=? AND content_sha256!=? LIMIT 1",
        (row["uid"], row["content_sha256"]),
    ).fetchone()
    if not old and (row["kg_sha256"] in (None, row["content_sha256"])):
        return
    with transaction(conn):
        touched = {r[0] for r in conn.execute("SELECT entity_id FROM kg_mentions WHERE article_uid=?", (row["uid"],))}
        conn.execute("DELETE FROM kg_mentions WHERE article_uid=?", (row["uid"],))
        conn.execute("DELETE FROM kg_relationship_evidence WHERE article_uid=?", (row["uid"],))
        conn.execute(
            "DELETE FROM kg_extraction_pages WHERE article_uid=? AND content_sha256!=?",
            (row["uid"], row["content_sha256"]),
        )
        conn.execute(
            "UPDATE articles SET kg_sha256=NULL, kg_page_count=NULL, kg_completed_at=NULL WHERE uid=?",
            (row["uid"],),
        )
        _mark_stale(conn, touched)


def withdraw_article(conn: sqlite3.Connection, uid: str, min_sources: int) -> set[int]:
    """Remove an article from the wiki after screening turned it into an exclude.

    Its mentions, evidence, extraction pages and stored text go; entity pages that cited it are
    rewritten (or lose their synthesis when too few sources remain). The run keeps its record and
    screening history, and the article is fetched again if it is ever included again."""
    with transaction(conn):
        touched = {r[0] for r in conn.execute("SELECT entity_id FROM kg_mentions WHERE article_uid=?", (uid,))}
        conn.execute("DELETE FROM kg_mentions WHERE article_uid=?", (uid,))
        conn.execute("DELETE FROM kg_relationship_evidence WHERE article_uid=?", (uid,))
        conn.execute(
            "DELETE FROM kg_relationships WHERE id NOT IN (SELECT DISTINCT relationship_id FROM kg_relationship_evidence)"
        )
        conn.execute("DELETE FROM kg_extraction_pages WHERE article_uid=?", (uid,))
        conn.execute("DELETE FROM articles WHERE uid=?", (uid,))
        _mark_stale(conn, touched)
        if touched:
            marks = ",".join("?" * len(touched))
            conn.execute(
                f"""DELETE FROM kg_syntheses WHERE entity_id IN ({marks}) AND entity_id IN
                    (SELECT entity_id FROM kg_entity_stats WHERE source_count < ?)""",
                [*touched, min_sources],
            )
    return touched


def _page_count(
    conn: sqlite3.Connection, row: sqlite3.Row, max_pages: int | None, *, override: bool = False
) -> int:
    """Pages to read: fixed when an article is first opened (from the setting), or by an explicit call."""
    total = len(_pages(row))
    count = row["kg_page_count"]
    if count is None or override:
        count = min(total, max_pages) if max_pages else total
        conn.execute("UPDATE articles SET kg_page_count=? WHERE uid=?", (count, row["uid"]))
    return int(count)


def _recorded_pages(conn: sqlite3.Connection, uid: str, digest: str) -> set[int]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT page_index FROM kg_extraction_pages WHERE article_uid=? AND content_sha256=?",
            (uid, digest),
        )
    }


def _page_header(
    conn: sqlite3.Connection, run_id: str, uids: list[str], row: sqlite3.Row, page: int,
    pages: list[str], count: int, recorded: set[int], remaining: int,
) -> dict[str, Any]:
    scanner = AliasScanner(conn.execute("SELECT alias_key, entity_id FROM kg_aliases").fetchall())
    hits = scanner.scan(pages[page])
    known = []
    for entity_id, _ in sorted(hits.items(), key=lambda item: -item[1])[:80]:
        entity = conn.execute("SELECT id, canonical_name, entity_type FROM kg_entities WHERE id=?", (entity_id,)).fetchone()
        if entity:
            known.append({"id": entity["id"], "name": entity["canonical_name"], "type": entity["entity_type"]})
    marks = ",".join("?" * len(uids))
    frequent = [
        {"id": r["id"], "name": r["canonical_name"], "type": r["entity_type"], "sources": r["n"]}
        for r in conn.execute(
            f"""SELECT e.id, e.canonical_name, e.entity_type, COUNT(DISTINCT m.article_uid) AS n
                FROM kg_mentions m JOIN kg_entities e ON e.id = m.entity_id
                WHERE m.article_uid IN ({marks}) GROUP BY e.id ORDER BY n DESC, e.id LIMIT 30""",
            uids,
        )
    ]
    header = {
        "run_id": run_id,
        "uid": row["uid"],
        "title": row["title"],
        "content_type": row["content_type"],
        "content_sha256": row["content_sha256"],
        "page": page,
        "page_count": count,
        "pages_recorded": sorted(recorded),
        "remaining_articles": remaining,
        "research_question": question_text(run_dir(run_id)),
        "known_entities": known,
        "frequent_entities": frequent,
        "instructions": (
            "Extract 5-18 substantive entities from this page with record_extraction. Each mention "
            "and relationship evidence must be copied verbatim from the page. Reuse a known entity's "
            "name or pass its id as entity_id. Never extract publication metadata, statistics software "
            "(SPSS, Stata, R, GraphPad Prism), or places and institutions named only as where the study "
            "was done or who funded it. An empty list is valid for a page with nothing substantive."
        ),
    }
    if row["content_type"] == "abstract_only":
        header["notice"] = "Only the abstract is available for this article; never describe it as full text."
    return header


def next_article(run_id: str, max_pages: int | None = None, uid: str | None = None) -> dict[str, Any]:
    """Return {header, text} for the next unextracted page, or {done: true}.

    With uid, only that article's pages are served, so one worker can own one article."""
    project, _ = locate(run_id)
    manifest = load_manifest(run_id)
    run_uids = _fetched_uids(manifest)
    if not run_uids:
        raise ValueError("No fetched included articles; run fetch_articles first")
    if uid is not None and uid not in run_uids:
        raise ValueError(f"{uid} is not a fetched, included article in this run")
    uids = [uid] if uid is not None else run_uids
    explicit = max_pages is not None
    max_pages = max_pages if explicit else load_settings(project.root).wiki.max_pages
    with database(project.db) as conn:
        pending: list[tuple[sqlite3.Row, int, list[int]]] = []
        for candidate in uids:
            row = conn.execute("SELECT * FROM articles WHERE uid=?", (candidate,)).fetchone()
            if row is None:
                continue
            _reset_if_changed(conn, row)
            row = _article(conn, candidate)
            count = _page_count(conn, row, max_pages, override=explicit and not pending)
            todo = sorted(set(range(count)) - _recorded_pages(conn, candidate, row["content_sha256"]))
            if todo:
                pending.append((row, count, todo))
                if len(pending) > 1:
                    break
        if not pending:
            if uid is not None:
                return {"done": True, "run_id": run_id, "uid": uid, "note": "This article is fully extracted"}
            _finish_articles(run_id)
            return {"done": True, "run_id": run_id, "next": "wiki_tasks(run_id) for the next step"}
        row, count, todo = pending[0]
        pages = _pages(row)
        recorded = _recorded_pages(conn, row["uid"], row["content_sha256"])
        remaining = sum(
            1
            for other in run_uids
            if (r := conn.execute("SELECT kg_sha256, content_sha256 FROM articles WHERE uid=?", (other,)).fetchone())
            and r["kg_sha256"] != r["content_sha256"]
        )
        header = _page_header(conn, run_id, run_uids, row, todo[0], pages, count, recorded, remaining)
        return {"header": header, "text": pages[todo[0]]}


def get_page(run_id: str, uid: str, page: int) -> dict[str, Any]:
    project, _ = locate(run_id)
    manifest = load_manifest(run_id)
    uids = _fetched_uids(manifest)
    if uid not in uids:
        raise ValueError(f"{uid} is not a fetched, included article in this run")
    with database(project.db) as conn:
        row = _article(conn, uid)
        pages = _pages(row)
        count = int(row["kg_page_count"] or len(pages))
        if not 0 <= page < len(pages):
            raise ValueError(f"page must be between 0 and {len(pages) - 1}")
        recorded = _recorded_pages(conn, uid, row["content_sha256"])
        header = _page_header(conn, run_id, uids, row, page, pages, count, recorded, 0)
        return {"header": header, "text": pages[page]}


def _add_alias(conn: sqlite3.Connection, alias: str, entity_id: int, source: str) -> None:
    key = name_key(alias)
    if key:
        conn.execute(
            "INSERT OR IGNORE INTO kg_aliases (alias_key, alias, entity_id, source, created_at) VALUES (?, ?, ?, ?, ?)",
            (key, alias, entity_id, source, now()),
        )


def _lookup(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT e.id, e.entity_type, e.name_key FROM kg_aliases a JOIN kg_entities e ON e.id = a.entity_id
           WHERE a.alias_key=?""",
        (key,),
    ).fetchone()


def _resolve(
    conn: sqlite3.Connection, entity: dict[str, Any], definitions: dict[str, str]
) -> tuple[int, str, bool]:
    """Return (entity_id, action, type_differs); only exact keys or explicit links merge."""
    name, entity_type = entity["name"], entity["entity_type"]
    if entity.get("entity_id"):
        row = conn.execute("SELECT id, entity_type FROM kg_entities WHERE id=?", (entity["entity_id"],)).fetchone()
        if row is None:
            raise ValueError(f"entity_id {entity['entity_id']} does not exist")
        _add_alias(conn, name, row["id"], "agent")
        return row["id"], "linked_id", row["entity_type"] != entity_type
    split = split_acronym(name)
    canonical, extra = (split[0], [split[1]]) if split else (name, [])
    key = name_key(canonical)
    lookups = [(key, "exact")] + [(name_key(alias), "acronym") for alias in extra]
    if looks_like_acronym(name) and key in definitions:
        lookups.append((name_key(definitions[key]), "acronym"))
    lookups += [(name_key(acronym), "acronym") for acronym, expansion in definitions.items() if name_key(expansion) == key]
    names = [canonical, *extra]
    for lookup_key, action in lookups:
        row = _lookup(conn, lookup_key)
        if row:
            for alias in names:
                _add_alias(conn, alias, row["id"], "mention")
            if action == "exact" and row["name_key"] != lookup_key:
                action = "alias"
            return row["id"], action, row["entity_type"] != entity_type
    if looks_like_acronym(name) and key in definitions:
        canonical, names = definitions[key], [definitions[key], name]
        key = name_key(canonical)
    stamp = now()
    cursor = conn.execute(
        """INSERT INTO kg_entities (canonical_name, name_key, entity_type, description, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (canonical, key, entity_type, entity["description"], stamp, stamp),
    )
    entity_id = int(cursor.lastrowid)
    _add_alias(conn, canonical, entity_id, "canonical")
    for alias in names[1:]:
        _add_alias(conn, alias, entity_id, "acronym")
    for acronym, expansion in definitions.items():
        if name_key(expansion) == key:
            _add_alias(conn, acronym.upper(), entity_id, "acronym")
    return entity_id, "created", False


def _context(page: str, mention: str) -> str:
    for sentence in re.split(r"(?<=[.!?])\s+|\n\n", page):
        if contains_verbatim(sentence, mention):
            sentence = " ".join(sentence.split())
            if len(sentence) <= 600:
                return sentence
            at = max(0, fold(sentence).find(fold(mention)) - 250)
            return "…" + sentence[at : at + 500] + "…"
    return mention


def _queue_duplicates(conn: sqlite3.Connection, entity_id: int) -> list[dict[str, Any]]:
    entity = conn.execute("SELECT * FROM kg_entities WHERE id=?", (entity_id,)).fetchone()
    kinds = related_types(entity["entity_type"])
    marks = ",".join("?" * len(kinds))
    pool = [
        (r["entity_id"], r["alias_key"])
        for r in conn.execute(
            f"""SELECT a.entity_id, a.alias_key FROM kg_aliases a JOIN kg_entities e ON e.id = a.entity_id
                WHERE e.entity_type IN ({marks}) AND a.entity_id!=?""",
            (*kinds, entity_id),
        )
    ]
    distinct = {
        r[0] if r[1] == entity_id else r[1]
        for r in conn.execute("SELECT a, b FROM kg_distinct_pairs WHERE a=? OR b=?", (entity_id, entity_id))
    }
    found = []
    for candidate_id, score, reasons in candidates(entity["name_key"], pool):
        if candidate_id in distinct:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO kg_duplicate_candidates
               (entity_id, candidate_id, score, reasons_json, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)""",
            (entity_id, candidate_id, score, json.dumps(reasons), now()),
        )
        other = conn.execute("SELECT canonical_name, entity_type FROM kg_entities WHERE id=?", (candidate_id,)).fetchone()
        found.append({"id": candidate_id, "name": other["canonical_name"], "type": other["entity_type"], "score": score, "reasons": reasons})
    return found


def record_extraction(
    run_id: str,
    uid: str,
    content_sha256: str,
    page: int,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    *,
    client: str | None = None,
) -> dict[str, Any]:
    project, path = locate(run_id)
    with locked_run(path) as manifest:
        if uid not in _fetched_uids(manifest):
            raise ValueError(f"{uid} is not a fetched, included article in this run")
        with database(project.db) as conn:
            row = _article(conn, uid)
            if row["content_sha256"] != content_sha256:
                raise ValueError("Article text changed; call next_wiki_article again")
            pages = _pages(row)
            count = int(row["kg_page_count"] or len(pages))
            if not 0 <= page < count:
                raise ValueError(f"page must be between 0 and {count - 1}")
            text = pages[page]
            title = str(row["title"] or "")
            definitions = find_acronym_definitions(str(row["full_text"]))
            accepted, rejected, duplicates = [], [], []
            with transaction(conn):
                touched = {
                    r[0]
                    for r in conn.execute(
                        "SELECT entity_id FROM kg_mentions WHERE article_uid=? AND page_index=?", (uid, page)
                    )
                }
                conn.execute("DELETE FROM kg_mentions WHERE article_uid=? AND page_index=?", (uid, page))
                conn.execute("DELETE FROM kg_relationship_evidence WHERE article_uid=? AND page_index=?", (uid, page))
                ids: dict[str, int] = {}
                created: list[int] = []
                for index, entity in enumerate(entities):
                    name = " ".join(entity["name"].split())
                    mention = " ".join(entity["mention"].split())
                    reason = None
                    if is_junk_name(name):
                        reason = "publication metadata is not an entity"
                    elif not (contains_verbatim(text, mention) or contains_verbatim(title, mention)):
                        reason = "mention is not a verbatim quote from this page"
                    if reason:
                        rejected.append({"kind": "entity", "index": index, "name": name, "reason": reason})
                        continue
                    entity_id, action, type_differs = _resolve(conn, {**entity, "name": name}, definitions)
                    if action == "created":
                        created.append(entity_id)
                    conn.execute(
                        """INSERT OR IGNORE INTO kg_mentions
                           (article_uid, entity_id, page_index, mention_text, context, description, role)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (uid, entity_id, page, mention, _context(text, mention), entity["description"], entity.get("role")),
                    )
                    touched.add(entity_id)
                    split = split_acronym(name)
                    for alias in (name, mention, *(split or ())):
                        ids.setdefault(name_key(alias), entity_id)
                    canonical = conn.execute("SELECT canonical_name FROM kg_entities WHERE id=?", (entity_id,)).fetchone()[0]
                    ids.setdefault(name_key(canonical), entity_id)
                    accepted.append(
                        {
                            "name": name, "entity_id": entity_id, "canonical_name": canonical, "action": action,
                            "type_differs": type_differs, "role": entity.get("role"),
                        }
                    )
                recorded_relationships = 0
                for index, relation in enumerate(relationships):
                    source = ids.get(name_key(relation["source"]))
                    target = ids.get(name_key(relation["target"]))
                    evidence = " ".join(relation["evidence"].split())
                    reason = None
                    if source is None or target is None:
                        reason = "source and target must be accepted entities from this call"
                    elif source == target:
                        reason = "a relationship needs two different entities"
                    elif not contains_verbatim(text, evidence):
                        reason = "evidence is not a verbatim quote from this page"
                    if reason:
                        rejected.append({"kind": "relationship", "index": index, "name": relation["relationship"], "reason": reason})
                        continue
                    kind = " ".join(relation["relationship"].casefold().split())
                    conn.execute(
                        """INSERT INTO kg_relationships (source_entity_id, target_entity_id, relationship_type, detail)
                           VALUES (?, ?, ?, ?) ON CONFLICT (source_entity_id, target_entity_id, relationship_type) DO NOTHING""",
                        (source, target, kind, relation.get("detail") or None),
                    )
                    relationship_id = conn.execute(
                        "SELECT id FROM kg_relationships WHERE source_entity_id=? AND target_entity_id=? AND relationship_type=?",
                        (source, target, kind),
                    ).fetchone()[0]
                    conn.execute(
                        """INSERT OR REPLACE INTO kg_relationship_evidence
                           (relationship_id, article_uid, page_index, evidence, detail) VALUES (?, ?, ?, ?, ?)""",
                        (relationship_id, uid, page, evidence, relation.get("detail") or None),
                    )
                    recorded_relationships += 1
                conn.execute(
                    """INSERT OR REPLACE INTO kg_extraction_pages
                       (article_uid, content_sha256, page_index, page_count, payload_json, client, recorded_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (uid, content_sha256, page, count, json.dumps({"entities": entities, "relationships": relationships}), client, now()),
                )
                _mark_stale(conn, touched)
                for entity_id in created:
                    found = _queue_duplicates(conn, entity_id)
                    if found:
                        name = next(item["canonical_name"] for item in accepted if item["entity_id"] == entity_id)
                        duplicates.append({"entity_id": entity_id, "name": name, "candidates": found})
            remaining_pages = sorted(set(range(count)) - _recorded_pages(conn, uid, content_sha256))
            status = manifest["articles"][uid]["wiki"]
            if not remaining_pages:
                conn.execute(
                    "UPDATE articles SET kg_sha256=?, kg_completed_at=? WHERE uid=?", (content_sha256, now(), uid)
                )
                has_mentions = conn.execute("SELECT 1 FROM kg_mentions WHERE article_uid=? LIMIT 1", (uid,)).fetchone()
                status = "kg_complete" if has_mentions else "no_entities"
                manifest["articles"][uid]["wiki"] = status
                manifest["articles"][uid].pop("wiki_completed_at", None)
                save_run(path, manifest)
        return {
            "uid": uid,
            "page": page,
            "accepted": accepted,
            "rejected": rejected,
            "relationships_recorded": recorded_relationships,
            "possible_duplicates": duplicates,
            "pages_remaining": remaining_pages,
            "article_wiki_status": status,
            "note": (
                "Resolve possible_duplicates with resolve_duplicates (merge or distinct) before synthesis."
                if duplicates
                else None
            ),
        }


def _entity_brief(conn: sqlite3.Connection, entity_id: int) -> dict[str, Any]:
    entity = conn.execute("SELECT * FROM kg_entities WHERE id=?", (entity_id,)).fetchone()
    if entity is None:
        raise ValueError(f"Entity {entity_id} does not exist")
    stats = conn.execute("SELECT * FROM kg_entity_stats WHERE entity_id=?", (entity_id,)).fetchone()
    aliases = [r[0] for r in conn.execute("SELECT alias FROM kg_aliases WHERE entity_id=? ORDER BY alias", (entity_id,))]
    return {
        "id": entity_id,
        "name": entity["canonical_name"],
        "type": entity["entity_type"],
        "description": entity["description"],
        "aliases": aliases[:12],
        "source_count": stats["source_count"] if stats else 0,
    }


def find_entities(
    project: Project, query: str, entity_type: str | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    key = name_key(query)
    if not key:
        raise ValueError("query is empty")
    with database(project.db) as conn:
        sql = "SELECT a.entity_id, a.alias_key FROM kg_aliases a JOIN kg_entities e ON e.id = a.entity_id"
        rows = conn.execute(sql + (" WHERE e.entity_type=?" if entity_type else ""), (entity_type,) if entity_type else ()).fetchall()
        scored: dict[int, float] = {}
        for entity_id, alias_key in rows:
            if key in alias_key:
                scored[entity_id] = max(scored.get(entity_id, 0), 0.8 if key != alias_key else 1.0)
        for entity_id, score, _ in candidates(key, [(r[0], r[1]) for r in rows], threshold=0.5, limit=limit):
            scored[entity_id] = max(scored.get(entity_id, 0), score)
        ranked = sorted(scored.items(), key=lambda item: -item[1])[:limit]
        return [_entity_brief(conn, entity_id) | {"score": round(score, 3)} for entity_id, score in ranked]


def merge_entities(
    project: Project,
    keep_id: int,
    merge_ids: list[int],
    *,
    canonical_name: str | None = None,
    entity_type: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    with database(project.db) as conn, transaction(conn):
        if conn.execute("SELECT 1 FROM kg_entities WHERE id=?", (keep_id,)).fetchone() is None:
            raise ValueError(f"Entity {keep_id} does not exist")
        merged = []
        for merge_id in dict.fromkeys(merge_ids):
            if merge_id == keep_id:
                continue
            other = conn.execute("SELECT * FROM kg_entities WHERE id=?", (merge_id,)).fetchone()
            if other is None:
                raise ValueError(f"Entity {merge_id} does not exist")
            conn.execute("UPDATE OR IGNORE kg_mentions SET entity_id=? WHERE entity_id=?", (keep_id, merge_id))
            conn.execute(
                "UPDATE kg_aliases SET entity_id=?, source=CASE WHEN source='canonical' THEN 'merge' ELSE source END WHERE entity_id=?",
                (keep_id, merge_id),
            )
            for relation in conn.execute(
                "SELECT * FROM kg_relationships WHERE source_entity_id=? OR target_entity_id=?", (merge_id, merge_id)
            ).fetchall():
                source = keep_id if relation["source_entity_id"] == merge_id else relation["source_entity_id"]
                target = keep_id if relation["target_entity_id"] == merge_id else relation["target_entity_id"]
                if source == target:
                    conn.execute("DELETE FROM kg_relationships WHERE id=?", (relation["id"],))
                    continue
                existing = conn.execute(
                    """SELECT id FROM kg_relationships WHERE source_entity_id=? AND target_entity_id=?
                       AND relationship_type=? AND id!=?""",
                    (source, target, relation["relationship_type"], relation["id"]),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE OR IGNORE kg_relationship_evidence SET relationship_id=? WHERE relationship_id=?",
                        (existing[0], relation["id"]),
                    )
                    conn.execute("DELETE FROM kg_relationships WHERE id=?", (relation["id"],))
                else:
                    conn.execute(
                        "UPDATE kg_relationships SET source_entity_id=?, target_entity_id=? WHERE id=?",
                        (source, target, relation["id"]),
                    )
            for a, b in conn.execute("SELECT a, b FROM kg_distinct_pairs WHERE a=? OR b=?", (merge_id, merge_id)).fetchall():
                partner = b if a == merge_id else a
                conn.execute("DELETE FROM kg_distinct_pairs WHERE a=? AND b=?", (a, b))
                if partner != keep_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO kg_distinct_pairs (a, b, reason, decided_at) VALUES (?, ?, 'carried over by merge', ?)",
                        (min(partner, keep_id), max(partner, keep_id), now()),
                    )
            conn.execute(
                "INSERT INTO kg_merge_log (kept_id, merged_id, merged_name, reason, merged_at) VALUES (?, ?, ?, ?, ?)",
                (keep_id, merge_id, other["canonical_name"], reason, now()),
            )
            conn.execute("DELETE FROM kg_entities WHERE id=?", (merge_id,))
            merged.append({"id": merge_id, "name": other["canonical_name"]})
        if canonical_name:
            canonical_name = " ".join(canonical_name.split())
            key = name_key(canonical_name)
            owner = _lookup(conn, key)
            if owner and owner["id"] != keep_id:
                raise ValueError(f"'{canonical_name}' already names entity {owner['id']}; merge it instead")
            conn.execute(
                "UPDATE kg_entities SET canonical_name=?, name_key=?, page_name=NULL, updated_at=? WHERE id=?",
                (canonical_name, key, now(), keep_id),
            )
            _add_alias(conn, canonical_name, keep_id, "agent")
        if entity_type:
            conn.execute("UPDATE kg_entities SET entity_type=?, updated_at=? WHERE id=?", (entity_type, now(), keep_id))
        _mark_stale(conn, {keep_id})
        return _entity_brief(conn, keep_id) | {"merged": merged}


def list_duplicate_candidates(project: Project, run_id: str | None = None, limit: int = 20) -> dict[str, Any]:
    with database(project.db) as conn:
        rows = conn.execute(
            "SELECT * FROM kg_duplicate_candidates WHERE status='pending' ORDER BY score DESC"
        ).fetchall()
        allowed: set[int] | None = None
        if run_id:
            uids = _fetched_uids(load_manifest(run_id))
            marks = ",".join("?" * len(uids))
            allowed = {r[0] for r in conn.execute(f"SELECT DISTINCT entity_id FROM kg_mentions WHERE article_uid IN ({marks})", uids)}
        pairs = []
        for row in rows:
            if allowed is not None and row["entity_id"] not in allowed and row["candidate_id"] not in allowed:
                continue
            pair = {"score": row["score"], "reasons": json.loads(row["reasons_json"])}
            for side, entity_id in (("entity", row["entity_id"]), ("candidate", row["candidate_id"])):
                context = conn.execute(
                    "SELECT context FROM kg_mentions WHERE entity_id=? ORDER BY id LIMIT 1", (entity_id,)
                ).fetchone()
                pair[side] = _entity_brief(conn, entity_id) | {"example": context[0] if context else None}
            pairs.append(pair)
        return {"total": len(pairs), "pairs": pairs[:limit]}


def resolve_duplicates(project: Project, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    for decision in decisions:
        entity_id, candidate_id = decision["entity_id"], decision["candidate_id"]
        if decision["action"] == "merge":
            keep, other = (candidate_id, entity_id) if decision.get("keep", "candidate") == "candidate" else (entity_id, candidate_id)
            results.append({"action": "merge", **merge_entities(project, keep, [other], reason=decision.get("reason", ""))})
            continue
        with database(project.db) as conn, transaction(conn):
            conn.execute(
                "INSERT OR IGNORE INTO kg_distinct_pairs (a, b, reason, decided_at) VALUES (?, ?, ?, ?)",
                (min(entity_id, candidate_id), max(entity_id, candidate_id), decision.get("reason"), now()),
            )
            conn.execute(
                """UPDATE kg_duplicate_candidates SET status='distinct'
                   WHERE (entity_id=? AND candidate_id=?) OR (entity_id=? AND candidate_id=?)""",
                (entity_id, candidate_id, candidate_id, entity_id),
            )
        results.append({"action": "distinct", "entity_id": entity_id, "candidate_id": candidate_id})
    with database(project.db) as conn:
        pending = conn.execute("SELECT COUNT(*) FROM kg_duplicate_candidates WHERE status='pending'").fetchone()[0]
    return {"results": results, "pending_duplicates": pending}


def _coverage(conn: sqlite3.Connection, entity_id: int) -> tuple[set[int], set[str]]:
    """Mentions and sources an entity's current page was written from."""
    covered = {r[0] for r in conn.execute("SELECT mention_id FROM kg_synthesis_mentions WHERE entity_id=?", (entity_id,))}
    row = conn.execute("SELECT sources_json FROM kg_syntheses WHERE entity_id=?", (entity_id,)).fetchone()
    return covered, set(json.loads(row[0] or "[]")) if row else set()


def _pending(conn: sqlite3.Connection, entity_id: int) -> dict[str, Any]:
    """What a written page does not reflect yet: new mentions and withdrawn sources.

    An update is due for new mentions that say something about the entity; a mention whose role
    is only the study's setting ("context") does not by itself change the page."""
    covered, cited = _coverage(conn, entity_id)
    rows = conn.execute("SELECT id, article_uid, role FROM kg_mentions WHERE entity_id=?", (entity_id,)).fetchall()
    new = [row for row in rows if row["id"] not in covered]
    removed = sorted(cited - {row["article_uid"] for row in rows})
    substantive = [row for row in new if row["role"] != "context"]
    return {"new": new, "substantive": len(substantive), "removed": removed, "due": bool(substantive or removed)}


def _absorb(conn: sqlite3.Connection, entity_id: int, pending: dict[str, Any]) -> None:
    """Mark setting-only new mentions as covered: the page's source list shows them already."""
    with transaction(conn):
        conn.executemany(
            "INSERT OR IGNORE INTO kg_synthesis_mentions (entity_id, mention_id) VALUES (?, ?)",
            [(entity_id, row["id"]) for row in pending["new"]],
        )
        sources = sorted({r[0] for r in conn.execute("SELECT DISTINCT article_uid FROM kg_mentions WHERE entity_id=?", (entity_id,))})
        conn.execute("UPDATE kg_syntheses SET stale=0, sources_json=? WHERE entity_id=?", (json.dumps(sources), entity_id))


def synthesis_context(conn: sqlite3.Connection, entity_id: int) -> dict[str, Any]:
    """The evidence for writing a new entity page, or for updating a written one.

    mode "new": all mentions (up to the limits). mode "update": the current page in full plus only
    the mentions added since it was written, and the sources withdrawn since."""
    brief = _entity_brief(conn, entity_id)
    current = conn.execute("SELECT * FROM kg_syntheses WHERE entity_id=?", (entity_id,)).fetchone()
    covered, cited = _coverage(conn, entity_id) if current else (set(), set())
    rows = conn.execute(
        """SELECT m.id, m.article_uid, m.page_index, m.mention_text, m.context, m.description, m.role,
                  a.title, a.pub_date, a.content_type
           FROM kg_mentions m JOIN articles a ON a.uid = m.article_uid
           WHERE m.entity_id=? ORDER BY a.pub_date DESC, m.article_uid, m.page_index, m.id""",
        (entity_id,),
    ).fetchall()
    by_article: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_article.setdefault(row["article_uid"], []).append(row)
    sources = [
        {
            "uid": uid,
            "title": group[0]["title"],
            "year": str(group[0]["pub_date"] or "")[:4] or None,
            "content_type": group[0]["content_type"],
            "roles": sorted({row["role"] for row in group if row["role"]}),
            **({"new": uid not in cited} if current else {}),
        }
        for uid, group in by_article.items()
    ]
    pool: dict[str, list[sqlite3.Row]] = {}
    for uid, group in by_article.items():
        fresh = [row for row in group if row["id"] not in covered]
        if fresh:
            pool[uid] = fresh
    mentions, used = [], 0
    for depth in range(max((len(group) for group in pool.values()), default=0)):
        for group in pool.values():
            if depth < len(group) and len(mentions) < MENTION_LIMIT:
                row = group[depth]
                size = len(row["context"]) + len(row["description"] or "")
                if used + size > MENTION_CHARS and mentions:
                    continue
                used += size
                mentions.append(
                    {
                        "id": row["id"], "uid": row["article_uid"], "page": row["page_index"], "role": row["role"],
                        "context": row["context"], "description": row["description"],
                    }
                )
    removed = sorted(cited - set(by_article)) if current else []
    relations = []
    for row in conn.execute(
        """SELECT r.id, r.relationship_type, r.source_entity_id, r.target_entity_id, r.detail,
                  COUNT(DISTINCT ev.article_uid) AS sources, MIN(ev.evidence) AS evidence
           FROM kg_relationships r JOIN kg_relationship_evidence ev ON ev.relationship_id = r.id
           WHERE r.source_entity_id=? OR r.target_entity_id=?
           GROUP BY r.id ORDER BY sources DESC, r.id LIMIT 20""",
        (entity_id, entity_id),
    ):
        outgoing = row["source_entity_id"] == entity_id
        neighbour = conn.execute(
            "SELECT canonical_name, entity_type FROM kg_entities WHERE id=?",
            (row["target_entity_id"] if outgoing else row["source_entity_id"],),
        ).fetchone()
        relations.append(
            {
                "id": row["id"],
                "statement": (
                    f"{brief['name']} {row['relationship_type']} {neighbour[0]}"
                    if outgoing
                    else f"{neighbour[0]} {row['relationship_type']} {brief['name']}"
                ),
                "neighbour": neighbour[0],
                "neighbour_type": neighbour[1],
                "source_count": row["sources"],
                "detail": row["detail"],
                "evidence": row["evidence"],
            }
        )
    digest = hashlib.sha256(
        json.dumps(
            {
                "mentions": [(m["id"], m["context"], m["role"]) for m in mentions],
                "relations": [(r["id"], r["source_count"]) for r in relations],
                "aliases": brief["aliases"],
                "sources": [s["uid"] for s in sources],
                "removed": removed,
                "version": current["version"] if current else 0,
            }
        ).encode()
    ).hexdigest()
    context = brief | {
        "mode": "update" if current else "new",
        "input_digest": digest,
        "sources": sources,
        "mentions": mentions,
        "relationships": relations,
        "version": current["version"] if current else 0,
    }
    if current:
        context |= {
            "removed_sources": removed,
            "current_summary": current["summary"],
            "current_key_aspects": json.loads(current["key_aspects_json"]),
            "current_synthesis": current["synthesis"],
            "current_sections": [heading for heading, _ in _sections(current["synthesis"]) if heading],
        }
    return context


def _synthesis_queue(conn: sqlite3.Connection, uids: list[str] | None, min_sources: int) -> tuple[list[int], int]:
    """Entities whose page is due: new pages first (most sources first), then updates (most new
    evidence first). Written pages whose only new evidence names the setting are marked current."""
    rows = conn.execute(
        """SELECT e.id, e.canonical_name, s.source_count, y.entity_id IS NOT NULL AS written,
                  EXISTS (SELECT 1 FROM kg_duplicate_candidates d WHERE d.status='pending'
                          AND (d.entity_id=e.id OR d.candidate_id=e.id)) AS blocked
           FROM kg_entities e JOIN kg_entity_stats s ON s.entity_id = e.id
           LEFT JOIN kg_syntheses y ON y.entity_id = e.id
           WHERE e.entity_type != 'PERSON'
             AND ((y.entity_id IS NULL AND s.source_count >= ?) OR y.stale = 1)
           ORDER BY s.source_count DESC, e.id""",
        (min_sources,),
    ).fetchall()
    allowed = None
    if uids is not None:
        marks = ",".join("?" * len(uids))
        allowed = {r[0] for r in conn.execute(f"SELECT DISTINCT entity_id FROM kg_mentions WHERE article_uid IN ({marks})", uids)}
    new, updates, blocked = [], [], 0
    for row in rows:
        if is_junk_name(row["canonical_name"]) or (allowed is not None and row["id"] not in allowed):
            continue
        if row["written"]:
            pending = _pending(conn, row["id"])
            if not pending["due"]:
                _absorb(conn, row["id"], pending)
                continue
        if row["blocked"]:
            blocked += 1
        elif row["written"]:
            updates.append((-(pending["substantive"] + len(pending["removed"])), row["id"]))
        else:
            new.append(row["id"])
    return new + [entity_id for _, entity_id in sorted(updates)], blocked


NEW_PAGE = (
    "Write from these mentions only, in a neutral encyclopedic tone, describing how the gathered "
    "articles depict the entity. Sections (as '## ' headings): Overview, How Gathered Articles Depict It, "
    "Recurring Themes, Tensions and Limitations, Relationships. Mentions and sources carry the entity's "
    "PICO/PCC role in each study (for example intervention or outcome); use them to say how studies used "
    "it. Cite sources inline as [uid]; link other entities as [[Name]]. Call record_synthesis with "
    "summary, synthesis, key_aspects and related_entities, passing input_digest back."
)
UPDATE_PAGE = (
    "This entity already has a page (current_synthesis). Update it; do not rewrite it. `mentions` are only "
    "the evidence added since the page was written (sources marked new: true); removed_sources were "
    "withdrawn from the review. Change only the sections this evidence affects: add what the new "
    "sources show, citing them as [uid], and delete every statement citing a removed source. Keep all "
    "other text as it is. Call record_synthesis with `sections`: only the changed sections, as "
    "{heading: full new text of that section} (use the current_sections headings; a new heading adds a "
    "section). Pass summary or key_aspects only if they change. Pass input_digest back."
)


def next_synthesis(
    project: Project, run_id: str | None = None, min_sources: int | None = None
) -> dict[str, Any]:
    min_sources = min_sources or load_settings(project.root).wiki.min_sources
    uids = None
    if run_id:
        owner, path = locate(run_id)
        if owner.id != project.id:
            raise ValueError(f"Run {run_id} belongs to project '{owner.name}'")
        with locked_run(path) as manifest:
            uids = _fetched_uids(manifest)
            if manifest.get("wiki_min_sources") != min_sources:
                manifest["wiki_min_sources"] = min_sources
                save_run(path, manifest)
    from .bot import synthesis_allowance

    if synthesis_allowance(project) == 0:
        return {"done": True, "note": "This bot run reached its safety limit on entity pages (bot.max_syntheses); the rest wait for the next run"}
    with database(project.db) as conn:
        queue, blocked = _synthesis_queue(conn, uids, min_sources)
        if not queue:
            if run_id:
                _finish_articles(run_id)
            return {
                "done": True,
                "skipped_pending_duplicates": blocked,
                "note": "Resolve duplicates to unblock skipped entities" if blocked else "Call export_wiki to refresh all pages",
            }
        context = synthesis_context(conn, queue[0])
        return {"project": project.name} | context | {
            "remaining": len(queue),
            "skipped_pending_duplicates": blocked,
            "instructions": UPDATE_PAGE if context["mode"] == "update" else NEW_PAGE,
        }


def _sections(text: str) -> list[tuple[str, str]]:
    """(heading, block) pairs of a page's '## ' sections; the first pair holds any preamble."""
    parts = re.split(r"(?m)^(?=## )", text)
    result = []
    for part in parts:
        first = part.split("\n", 1)[0]
        heading = first[3:].strip() if first.startswith("## ") else ""
        result.append((heading, part))
    return result


def _patch_sections(text: str, sections: dict[str, str]) -> str:
    """Replace the named sections of a page (matched case-insensitively); new headings are added
    at the end and an empty text removes a section."""
    parts = _sections(text)
    index = {heading.casefold(): number for number, (heading, _) in enumerate(parts) if heading}
    for name, body in sections.items():
        heading = name.strip().lstrip("#").strip()
        if not heading:
            raise ValueError("Every section needs a heading")
        body = body.strip()
        first, _, rest = body.partition("\n")
        if first.startswith("#") and first.lstrip("#").strip().casefold() == heading.casefold():
            body = rest.strip()
        block = f"## {heading}\n\n{body}\n\n" if body else ""
        if heading.casefold() in index:
            number = index[heading.casefold()]
            parts[number] = (parts[number][0], block)
        elif body:
            parts.append((heading, block))
    return "".join(block if block.endswith("\n") or not block else block + "\n" for _, block in parts).strip() + "\n"


def record_synthesis(
    project: Project,
    entity_id: int,
    input_digest: str,
    summary: str | None = None,
    synthesis: str | None = None,
    key_aspects: list[str] | None = None,
    related_entities: list[dict[str, Any]] | None = None,
    *,
    sections: dict[str, str] | None = None,
    client: str | None = None,
) -> dict[str, Any]:
    """Save a new page, or an update: changed `sections` patched into the current page (or a full
    `synthesis`). The page then covers the evidence its context showed."""
    from .bot import synthesis_allowance
    from .wiki_export import export_entity

    if synthesis_allowance(project) == 0:
        raise ValueError("This bot run reached its safety limit on entity pages (bot.max_syntheses); stop and report done")

    with database(project.db) as conn:
        context = synthesis_context(conn, entity_id)
        if context["input_digest"] != input_digest:
            raise ValueError("The entity's evidence changed; call next_synthesis again")
        current = conn.execute("SELECT * FROM kg_syntheses WHERE entity_id=?", (entity_id,)).fetchone()
        if current is None:
            if sections or not (summary and synthesis and key_aspects):
                raise ValueError("A new page needs summary, synthesis and key_aspects (sections are for updates)")
            text = synthesis
        else:
            if sections and synthesis:
                raise ValueError("Give either sections (the changed ones) or a full synthesis, not both")
            if not sections and not synthesis:
                raise ValueError("Give the changed sections as {heading: text}")
            text = _patch_sections(current["synthesis"], sections) if sections else synthesis
            summary = summary or current["summary"]
            key_aspects = key_aspects or json.loads(current["key_aspects_json"])
        sources = {source["uid"] for source in context["sources"]}
        cited = set(CITATION.findall(text))
        if not cited:
            raise ValueError("Cite at least one source inline as [uid], e.g. [pmc:PMC123]")
        unknown = sorted(cited - sources)
        if unknown:
            where = sorted({h or "(top)" for h, block in _sections(text) if any(f"[{uid}]" in block for uid in unknown)})
            withdrawn = [uid for uid in unknown if uid in context.get("removed_sources", [])]
            note = f" ({', '.join(withdrawn)} withdrawn from the review)" if withdrawn else ""
            raise ValueError(
                f"Citations are not sources of this entity: {', '.join(unknown)}{note}; fix sections: {', '.join(where)}"
            )
        warnings = []
        for name in WIKI_LINK.findall(text):
            if not _lookup(conn, name_key(name)):
                warnings.append(f"[[{name}]] does not match a known entity and will be plain text")
        if related_entities is None and current is not None:
            related = json.loads(current["related_entities_json"])
        else:
            related = []
            for entry in related_entities or []:
                found = _lookup(conn, name_key(entry["name"]))
                related.append(entry | {"entity_id": found["id"] if found else None})
        with transaction(conn):
            conn.execute(
                """INSERT INTO kg_syntheses (entity_id, summary, synthesis, key_aspects_json, related_entities_json,
                     input_digest, source_article_count, stale, version, client, compiled_at, sources_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?, ?)
                   ON CONFLICT(entity_id) DO UPDATE SET summary=excluded.summary, synthesis=excluded.synthesis,
                     key_aspects_json=excluded.key_aspects_json, related_entities_json=excluded.related_entities_json,
                     input_digest=excluded.input_digest, source_article_count=excluded.source_article_count,
                     stale=0, version=kg_syntheses.version + 1, client=excluded.client,
                     compiled_at=excluded.compiled_at, sources_json=excluded.sources_json""",
                (
                    entity_id, summary.strip(), text.strip(), json.dumps(key_aspects, ensure_ascii=False),
                    json.dumps(related, ensure_ascii=False), input_digest, len(sources), client, now(),
                    json.dumps(sorted(sources)),
                ),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO kg_synthesis_mentions (entity_id, mention_id) VALUES (?, ?)",
                [(entity_id, mention["id"]) for mention in context["mentions"]],
            )
            # Evidence beyond this context's limits is still uncovered: the page stays due.
            if _pending(conn, entity_id)["new"]:
                conn.execute("UPDATE kg_syntheses SET stale=1 WHERE entity_id=?", (entity_id,))
        page = export_entity(project, conn, entity_id)
        version = conn.execute("SELECT version FROM kg_syntheses WHERE entity_id=?", (entity_id,)).fetchone()[0]
    return {"entity_id": entity_id, "mode": context["mode"], "version": version, "page": str(page), "warnings": warnings}


def _finish_articles(run_id: str) -> None:
    """Mark kg_complete articles complete once every eligible entity has a fresh synthesis."""
    project, path = locate(run_id)
    with locked_run(path) as manifest, database(project.db) as conn:
        min_sources = int(manifest.get("wiki_min_sources") or load_settings(project.root).wiki.min_sources)
        changed = False
        for uid in _fetched_uids(manifest):
            item = manifest["articles"][uid]
            if item["wiki"] != "kg_complete":
                continue
            waiting = conn.execute(
                """SELECT e.canonical_name FROM kg_mentions m JOIN kg_entities e ON e.id = m.entity_id
                   JOIN kg_entity_stats s ON s.entity_id = e.id LEFT JOIN kg_syntheses y ON y.entity_id = e.id
                   WHERE m.article_uid=? AND e.entity_type != 'PERSON' AND s.source_count >= ?
                     AND (y.entity_id IS NULL OR y.stale = 1)""",
                (uid, min_sources),
            ).fetchall()
            if not any(not is_junk_name(row[0]) for row in waiting):
                item["wiki"] = "complete"
                item["wiki_completed_at"] = now()
                changed = True
        if changed:
            save_run(path, manifest)
    if changed:
        from .wiki_export import write_log

        write_log(project)


SYNTHESIS_BATCH = 5


def work_plan(run_id: str, max_syntheses: int | None = None) -> dict[str, Any]:
    """Self-contained tasks for the current wiki step, each small enough for one fresh subagent.

    max_syntheses caps the entity pages handed out in this plan (a bot run's allowance)."""
    project, _ = locate(run_id)
    manifest = load_manifest(run_id)
    uids = _fetched_uids(manifest)
    if not uids:
        raise ValueError("No fetched included articles; run fetch_articles first")
    settings = load_settings(project.root).wiki
    min_sources = int(manifest.get("wiki_min_sources") or settings.min_sources)
    with database(project.db) as conn:
        articles = []
        for uid in uids:
            row = conn.execute("SELECT * FROM articles WHERE uid=?", (uid,)).fetchone()
            if row is None or row["kg_sha256"] == row["content_sha256"]:
                continue
            pages = int(row["kg_page_count"] or len(_pages(row)))
            done = len(_recorded_pages(conn, uid, row["content_sha256"]))
            articles.append((uid, row["title"] or uid, pages - done))
        pending_duplicates = conn.execute(
            "SELECT COUNT(*) FROM kg_duplicate_candidates WHERE status='pending'"
        ).fetchone()[0]
        queue, _ = _synthesis_queue(conn, uids, min_sources)
    common = (
        "Use only the text the tools return: quote mentions and evidence verbatim, never add outside "
        "knowledge, and never extract publication metadata. When finished, reply with one line only."
    )
    if articles:
        tasks = [
            (
                f"med-lit wiki extraction, project {project.name!r}, run {run_id}, article {uid} ({title[:120]}; {left} page(s) left). "
                f"Repeat: call next_wiki_article(run_id='{run_id}', uid='{uid}'), extract 5-18 substantive "
                "entities and their relationships from that page, and call record_extraction with the "
                "header's uid, content_sha256 and page. Reuse known_entities names or ids, and give each "
                "entity its PICO/PCC role in this article's study when it has one. Stop when "
                "next_wiki_article reports done. " + common + " Reply format: 'uid: N entities, M relationships, K rejected'."
            )
            for uid, title, left in articles
        ]
        step = "extract"
    elif pending_duplicates:
        tasks = [
            (
                f"med-lit duplicate review, project {project.name!r}. Call "
                f"list_duplicate_candidates(project={project.name!r}, run_id='{run_id}') "
                f"and decide every pair with resolve_duplicates(project={project.name!r}, ...): merge only acronym or spelling variants of "
                "the same thing; keep generic and specific terms distinct. Repeat until none are pending. "
                + common
            )
        ]
        step = "duplicates"
    elif queue and max_syntheses != 0:
        total = len(queue) if max_syntheses is None else min(len(queue), max_syntheses)
        sizes = [min(SYNTHESIS_BATCH, total - start) for start in range(0, total, SYNTHESIS_BATCH)]
        tasks = [
            (
                f"med-lit wiki synthesis, project {project.name!r}, run {run_id}. Up to {size} times: "
                f"call next_synthesis(project={project.name!r}, run_id='{run_id}', min_sources={min_sources}); "
                "stop if it reports done. "
                "Follow its instructions: for mode 'new', write a neutral page describing how the gathered "
                "sources depict the entity; for mode 'update', change only the sections the new evidence "
                "affects and send just those as `sections`. Cite sources inline as [uid], link entities as "
                f"[[Name]], and call record_synthesis(project={project.name!r}, ...) with the same entity_id "
                "and input_digest. " + common
            )
            for size in sizes
        ]
        step = "synthesize"
    else:
        return {
            "step": "export",
            "tasks": [],
            "next": f"Call export_wiki(project={project.name!r}); the wiki is the folder {project.root}.",
        }
    return {
        "step": step,
        "tasks": tasks,
        "how": (
            "Give each task, verbatim, to its own fresh subagent, one after another (not in parallel). "
            f"If you cannot delegate, carry out at most {settings.tasks_per_conversation} tasks yourself in "
            "order, then stop and tell the researcher to continue in a new conversation (the work is saved). "
            "Call wiki_tasks again when they are finished to get the next step."
        ),
    }
