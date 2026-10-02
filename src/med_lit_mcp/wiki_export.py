"""Markdown export of a project's knowledge graph (SQLite owns the data; Markdown is a view).

Pages get human-readable file names ("entities/Shared decision making.md",
"sources/Guirgus 2026 - Assessing Artificial Intelligence in Patient Education.md"). A name is
assigned once and stored, so it stays stable between exports; the entity or article id lives in
the front matter.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import __version__
from .matching import name_key
from .ontology import ONTOLOGY_VERSION, graph_type
from .projects import Project
from .store import (
    RUN_FILE,
    atomic_json,
    atomic_text,
    database,
    now,
    read_json,
    transaction,
)

GENERATOR = "generator: med-lit-mcp"
GRAPH_FILE = "sgb-export.json"
GRAPH_FORMAT = "med-lit-sgb/1"
CITATION = re.compile(r"\[([a-z][a-z0-9_-]*:[A-Za-z0-9._/-]+)\](?!\()")
WIKI_LINK = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]*))?\]\]")
UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]+')


def _file_name(text: str, limit: int = 100) -> str:
    """A file name that is valid on every platform and inside Obsidian links."""
    cleaned = " ".join(UNSAFE.sub(" ", text).split()).strip(" .")
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rsplit(" ", 1)[0].rstrip(" .,;-")
    return cleaned


def _assign(conn: sqlite3.Connection, table: str, key: str, value: Any, candidates: list[str]) -> str:
    row = conn.execute(f"SELECT page_name FROM {table} WHERE {key}=?", (value,)).fetchone()
    if row and row[0]:
        return row[0]
    for candidate in [c for c in candidates if c]:
        taken = conn.execute(
            f"SELECT 1 FROM {table} WHERE page_name=? COLLATE NOCASE AND {key}!=?",
            (candidate, value),
        ).fetchone()
        if not taken:
            conn.execute(f"UPDATE {table} SET page_name=? WHERE {key}=?", (candidate, value))
            return candidate
    raise ValueError(f"No free page name for {value}")


def _relative(folder: str, page_name: str) -> str:
    return f"{folder}/{page_name}.md"


def entity_file(conn: sqlite3.Connection, entity_id: int) -> str:
    entity = conn.execute("SELECT canonical_name, entity_type FROM kg_entities WHERE id=?", (entity_id,)).fetchone()
    base = _file_name(entity["canonical_name"]) or f"Entity {entity_id}"
    kind = entity["entity_type"].lower()
    name = _assign(conn, "kg_entities", "id", entity_id, [base, f"{base} ({kind})", f"{base} ({entity_id})"])
    return _relative("entities", name)


def source_file(conn: sqlite3.Connection, uid: str) -> str:
    article = conn.execute("SELECT first_author, pub_date, title FROM articles WHERE uid=?", (uid,)).fetchone()
    fallback = _file_name(uid.replace(":", " "))
    if article is None:
        return _relative("sources", fallback)
    surname = (article["first_author"] or "").split()[-1:] or [""]
    year = str(article["pub_date"] or "")[:4]
    prefix = " ".join(part for part in (surname[0], year) if part)
    title = _file_name(article["title"] or "", 70)
    base = _file_name(f"{prefix} - {title}" if prefix and title else prefix or title) or fallback
    name = _assign(conn, "articles", "uid", uid, [base, f"{base} ({fallback})"])
    return _relative("sources", name)


def _link(label: str, target: str, prefix: str) -> str:
    return f"[{label}]({quote(prefix + target)})"


def _front_matter(values: dict[str, Any]) -> str:
    lines = ["---", GENERATOR]
    lines += [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in values.items()]
    return "\n".join(lines + ["---", ""])


def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]").replace("\n", " ")


def _link_text(conn: sqlite3.Connection, text: str, prefix: str) -> str:
    def entity(match: re.Match[str]) -> str:
        label = match.group(2) or match.group(1)
        row = conn.execute("SELECT entity_id FROM kg_aliases WHERE alias_key=?", (name_key(match.group(1)),)).fetchone()
        return _link(label, entity_file(conn, row[0]), prefix) if row else label

    text = WIKI_LINK.sub(entity, text)
    return CITATION.sub(lambda m: _link(m.group(1), source_file(conn, m.group(1)), prefix), text)


def _generated(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8") as handle:
            return handle.readline().strip() == "---" and handle.readline().strip() == GENERATOR
    except OSError:
        return False


def _write(path: Path, content: str) -> str:
    """Write a generated page; a file med-lit-mcp did not generate is never overwritten."""
    if path.exists():
        if not _generated(path):
            return "protected"
        try:
            if path.read_text(encoding="utf-8") == content:
                return "unchanged"
        except OSError:
            pass
    atomic_text(path, content)
    return "written"


def render_entity(conn: sqlite3.Connection, entity_id: int) -> tuple[str, str]:
    entity = conn.execute("SELECT * FROM kg_entities WHERE id=?", (entity_id,)).fetchone()
    aliases = [r[0] for r in conn.execute("SELECT alias FROM kg_aliases WHERE entity_id=? ORDER BY alias", (entity_id,))]
    synthesis = conn.execute("SELECT * FROM kg_syntheses WHERE entity_id=?", (entity_id,)).fetchone()
    sources = conn.execute(
        """SELECT a.uid, a.title, a.pub_date, a.content_type,
                  GROUP_CONCAT(DISTINCT m.role) AS roles
           FROM kg_mentions m JOIN articles a ON a.uid = m.article_uid WHERE m.entity_id=?
           GROUP BY a.uid ORDER BY a.pub_date DESC, a.uid""",
        (entity_id,),
    ).fetchall()
    name = entity["canonical_name"]
    meta = {
        "type": "entity",
        "entity_id": entity_id,
        "entity_type": entity["entity_type"],
        "sgb_type": graph_type(entity["entity_type"]),
        "ontology": f"med-lit/{ONTOLOGY_VERSION}",
        "aliases": [alias for alias in aliases if alias != name][:12],
        "sources": len(sources),
        "version": synthesis["version"] if synthesis else 0,
        "stale": bool(synthesis["stale"]) if synthesis else None,
        "compiled_at": synthesis["compiled_at"] if synthesis else None,
    }
    lines = [_front_matter(meta), f"# {name}", ""]
    if synthesis:
        lines += [_link_text(conn, synthesis["summary"], "../"), ""]
    elif entity["description"]:
        lines += [entity["description"], ""]
    lines += [f"- Type: {entity['entity_type']}", f"- Source articles: {len(sources)}"]
    if meta["aliases"]:
        lines.append(f"- Also known as: {', '.join(meta['aliases'])}")
    lines.append("")
    if synthesis:
        if synthesis["stale"]:
            lines += ["> New evidence has been added since this synthesis was written.", ""]
        aspects = json.loads(synthesis["key_aspects_json"])
        if aspects:
            lines += ["## Key Aspects", "", *[f"- {aspect}" for aspect in aspects], ""]
        lines += ["## Synthesis", "", _link_text(conn, synthesis["synthesis"], "../"), ""]
        related = [entry for entry in json.loads(synthesis["related_entities_json"]) if entry.get("entity_id")]
        links = []
        for entry in related:
            row = conn.execute("SELECT id, canonical_name FROM kg_entities WHERE id=?", (entry["entity_id"],)).fetchone()
            if row:
                links.append(f"- {_link(row[1], entity_file(conn, row[0]), '../')}")
        if links:
            lines += ["## Related Entities", "", *links, ""]
    else:
        mentions = conn.execute(
            "SELECT article_uid, context FROM kg_mentions WHERE entity_id=? ORDER BY id LIMIT 5", (entity_id,)
        ).fetchall()
        lines += ["## Mentions", ""]
        lines += [
            f"- “{row['context']}” ({_link(row['article_uid'], source_file(conn, row['article_uid']), '../')})"
            for row in mentions
        ]
        lines += ["", "_No synthesis yet: this entity has too few sources or is waiting for next_synthesis._", ""]
    lines += _relationship_lines(conn, entity_id)
    lines += ["## Source Articles", ""]
    for row in sources[:100]:
        notes = [f"as {', '.join(sorted(row['roles'].split(',')))}"] if row["roles"] else []
        if row["content_type"] == "abstract_only":
            notes.append("abstract only")
        suffix = f" ({'; '.join(notes)})" if notes else ""
        lines.append(f"- {_link(_escape(row['title'] or row['uid']), source_file(conn, row['uid']), '../')}{suffix}")
    return entity_file(conn, entity_id), "\n".join(lines).rstrip() + "\n"


def _relationship_lines(conn: sqlite3.Connection, entity_id: int, limit: int = 30) -> list[str]:
    """Evidence-backed relationships, strongest first, in Simple Graph Builder's '- verb [[Entity]]' shape."""
    rows = conn.execute(
        """SELECT r.id, r.relationship_type, r.source_entity_id, r.target_entity_id,
                  COUNT(DISTINCT ev.article_uid) AS sources
           FROM kg_relationships r JOIN kg_relationship_evidence ev ON ev.relationship_id = r.id
           WHERE r.source_entity_id=? OR r.target_entity_id=?
           GROUP BY r.id ORDER BY sources DESC, r.id LIMIT ?""",
        (entity_id, entity_id, limit),
    ).fetchall()
    outgoing, incoming = [], []
    for row in rows:
        outward = row["source_entity_id"] == entity_id
        other = conn.execute(
            "SELECT id, canonical_name FROM kg_entities WHERE id=?",
            (row["target_entity_id"] if outward else row["source_entity_id"],),
        ).fetchone()
        evidence = conn.execute(
            "SELECT article_uid, evidence FROM kg_relationship_evidence WHERE relationship_id=? ORDER BY article_uid LIMIT 1",
            (row["id"],),
        ).fetchone()
        link = _link(other["canonical_name"], entity_file(conn, other["id"]), "../")
        cite = _link(evidence["article_uid"], source_file(conn, evidence["article_uid"]), "../")
        quote_text = f" — “{evidence['evidence']}” ({cite})"
        count = f" · {row['sources']} sources" if row["sources"] > 1 else ""
        if outward:
            outgoing.append(f"- {row['relationship_type']} {link}{count}{quote_text}")
        else:
            incoming.append(f"- {link} {row['relationship_type']} this{count}{quote_text}")
    return ["## Relationships", "", *outgoing, *incoming, ""] if rows else []


def render_source(conn: sqlite3.Connection, uid: str) -> tuple[str, str]:
    article = conn.execute("SELECT * FROM articles WHERE uid=?", (uid,)).fetchone()
    authors = json.loads(article["authors_json"] or "[]")
    meta = {
        "type": "source",
        "uid": uid,
        "title": article["title"],
        "authors": authors[:20],
        "journal": article["journal"],
        "published": article["pub_date"],
        "doi": article["doi"],
        "url": article["source_url"] or article["url"],
        "content_type": article["content_type"],
        "fetched_at": article["fetched_at"],
    }
    lines = [_front_matter(meta), f"# {article['title'] or uid}", ""]
    if article["content_type"] == "abstract_only":
        lines += ["> Only the abstract was available; this source was not read in full.", ""]
    byline = ", ".join(authors[:6]) + (" et al." if len(authors) > 6 else "")
    details = [part for part in (byline, article["journal"], article["pub_date"]) if part]
    if details:
        lines += [" · ".join(details), ""]
    if article["abstract"]:
        lines += ["## Abstract", "", article["abstract"], ""]
    entities = conn.execute(
        """SELECT e.id, e.canonical_name, e.entity_type, GROUP_CONCAT(DISTINCT m.role) AS roles
           FROM kg_mentions m JOIN kg_entities e ON e.id = m.entity_id WHERE m.article_uid=?
           GROUP BY e.id ORDER BY e.canonical_name""",
        (uid,),
    ).fetchall()
    if entities:
        lines += ["## Entities", ""]
        for row in entities:
            role = f" · {', '.join(sorted(row['roles'].split(',')))}" if row["roles"] else ""
            lines.append(f"- {_link(row[1], entity_file(conn, row[0]), '../')} · {row[2]}{role}")
    return source_file(conn, uid), "\n".join(lines).rstrip() + "\n"


def render_index(project: Project, conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """SELECT e.id, e.canonical_name, e.entity_type, s.source_count, y.summary
           FROM kg_entities e JOIN kg_entity_stats s ON s.entity_id = e.id
           JOIN kg_syntheses y ON y.entity_id = e.id ORDER BY e.entity_type, s.source_count DESC, e.canonical_name"""
    ).fetchall()
    stubs = conn.execute(
        "SELECT COUNT(*) FROM kg_entities e WHERE NOT EXISTS (SELECT 1 FROM kg_syntheses y WHERE y.entity_id = e.id)"
    ).fetchone()[0]
    sources = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    lines = [
        _front_matter({"type": "index", "project": project.name, "ontology": f"med-lit/{ONTOLOGY_VERSION}"}),
        f"# {project.name}",
        "",
        (
            f"{len(rows)} synthesized entities, {stubs} entities without a synthesis, "
            f"{sources} source articles. See [the update log](log.md)."
        ),
        "",
    ]
    current = None
    for row in rows:
        if row["entity_type"] != current:
            current = row["entity_type"]
            lines += ["", f"## {current.replace('_', ' ').title()}", ""]
        summary = " ".join((row["summary"] or "").split())
        lines.append(
            f"- {_link(row['canonical_name'], entity_file(conn, row['id']), '')} · "
            f"{row['source_count']} sources · {summary[:200]}"
        )
    return "\n".join(lines).rstrip() + "\n"


def render_log(project: Project, conn: sqlite3.Connection) -> str:
    entries = []
    for path in project.runs.glob(f"*/{RUN_FILE}"):
        try:
            run = read_json(path)
            for uid, item in run["articles"].items():
                if item.get("wiki") == "complete" and item.get("wiki_completed_at"):
                    title = str(item["record"].get("title") or uid)
                    entries.append((item["wiki_completed_at"], run["run_id"], uid, title))
        except (OSError, KeyError, ValueError):
            continue
    entries.sort(reverse=True)
    lines = [_front_matter({"type": "log"}), f"# {project.name}: updates", ""]
    lines += [
        f"- {stamp} · Run `{run_id}` · {_link(_escape(title), source_file(conn, uid), '')} · `{uid}`"
        for stamp, run_id, uid, title in entries
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_log(project: Project) -> None:
    with database(project.db) as conn:
        _write(project.root / "log.md", render_log(project, conn))


# Entities with at least one mention: the ones the wiki shows (export_wiki deletes the rest).
LIVE_ENTITIES = "SELECT * FROM kg_entities e WHERE EXISTS (SELECT 1 FROM kg_mentions m WHERE m.entity_id = e.id) ORDER BY id"


def _write_pages(
    project: Project, conn: sqlite3.Connection, *, only_missing: bool
) -> tuple[dict[int, str | None], dict[str, str | None], list[str]]:
    """Write entity and source pages (every one, or only those without a generated page yet).

    Returns each entity's and article's page relative to the project root (None when a file of
    the researcher's own holds its name) and the paths that were not overwritten."""
    root = project.root
    rows = [("entities", r["id"], r["page_name"], render_entity) for r in conn.execute(LIVE_ENTITIES).fetchall()]
    rows += [("sources", r[0], r[1], render_source) for r in conn.execute("SELECT uid, page_name FROM articles ORDER BY uid")]
    pages: dict[str, dict[Any, str | None]] = {"entities": {}, "sources": {}}
    protected: list[str] = []
    for folder, key, page_name, render in rows:
        page = _relative(folder, page_name) if only_missing and page_name else None
        if page is None or not _generated(root / page):
            page, content = render(conn, key)
            if _write(root / page, content) == "protected":
                protected.append(str(Path(page)))
                page = None
        pages[folder][key] = page
    return pages["entities"], pages["sources"], protected


def render_graph(
    project: Project, conn: sqlite3.Connection, entity_pages: dict[int, str | None], source_pages: dict[str, str | None]
) -> dict[str, Any]:
    """The knowledge graph as Simple Graph Builder imports it (format: docs/ontology.md, "Graph export")."""
    from .bot import latest_update

    aliases: dict[int, list[dict[str, str]]] = {}
    for row in conn.execute(
        """SELECT a.entity_id, a.alias, a.source FROM kg_aliases a JOIN kg_entities e ON e.id = a.entity_id
           WHERE a.alias != e.canonical_name ORDER BY a.entity_id, a.alias, a.alias_key"""
    ):
        aliases.setdefault(row[0], []).append({"alias": row[1], "source": row[2]})
    entities = [
        {
            "id": row["id"],
            "name": row["canonical_name"],
            "type": row["entity_type"],
            "sgb_type": graph_type(row["entity_type"]),
            "description": row["description"],
            "aliases": aliases.get(row["id"], []),
            "page": entity_pages.get(row["id"]),
        }
        for row in conn.execute(LIVE_ENTITIES)
    ]
    articles = [
        {
            "uid": row["uid"],
            "title": row["title"],
            "doi": row["doi"],
            "published": row["pub_date"],
            "content_type": row["content_type"],
            "page": source_pages.get(row["uid"]),
        }
        for row in conn.execute("SELECT uid, title, doi, pub_date, content_type FROM articles ORDER BY uid")
    ]
    mentions = [
        {"article": row[0], "entity": row[1], "role": row[2]}
        for row in conn.execute(
            "SELECT DISTINCT article_uid, entity_id, role FROM kg_mentions ORDER BY article_uid, entity_id, role"
        )
    ]
    evidence: dict[int, list[dict[str, str]]] = {}
    for row in conn.execute(
        """SELECT relationship_id, article_uid, evidence FROM kg_relationship_evidence
           ORDER BY relationship_id, article_uid, page_index"""
    ):
        evidence.setdefault(row[0], []).append({"article": row[1], "quote": row[2]})
    relationships = [
        {
            "id": row["id"],
            "source": row["source_entity_id"],
            "target": row["target_entity_id"],
            "verb": row["relationship_type"],
            "detail": row["detail"] or None,
            "evidence": evidence.get(row["id"], []),
        }
        for row in conn.execute("SELECT * FROM kg_relationships ORDER BY id")
    ]
    merges = [
        {"kept": row[0], "merged": row[1], "name": row[2], "merged_at": row[3]}
        for row in conn.execute("SELECT kept_id, merged_id, merged_name, merged_at FROM kg_merge_log ORDER BY id")
    ]
    updated = conn.execute(
        """SELECT MAX(stamp) FROM (SELECT MAX(updated_at) AS stamp FROM articles
             UNION ALL SELECT MAX(updated_at) FROM kg_entities
             UNION ALL SELECT MAX(compiled_at) FROM kg_syntheses
             UNION ALL SELECT MAX(merged_at) FROM kg_merge_log)"""
    ).fetchone()[0]
    return {
        "format": GRAPH_FORMAT,
        "generator": f"med-lit-mcp {__version__}",
        "generated_at": now(),
        "project": {"id": project.id, "name": project.name, "ontology": f"med-lit/{ONTOLOGY_VERSION}"},
        "data_updated_at": updated,
        "bot_update": latest_update(project),
        "entities": entities,
        "articles": articles,
        "mentions": mentions,
        "relationships": relationships,
        "merges": merges,
    }


def write_graph(
    project: Project, conn: sqlite3.Connection, entity_pages: dict[int, str | None], source_pages: dict[str, str | None]
) -> bool:
    """Write .med-lit/sgb-export.json atomically; skipped (False) when only generated_at would change."""
    path = project.work / GRAPH_FILE
    graph = render_graph(project, conn, entity_pages, source_pages)
    try:
        if read_json(path) | {"generated_at": None} == graph | {"generated_at": None}:
            return False
    except (OSError, ValueError):
        pass
    atomic_json(path, graph)
    return True


def export_entity(project: Project, conn: sqlite3.Connection, entity_id: int) -> Path:
    """Write one entity's page and its sources, pages still missing, the index, log and graph export."""
    root = project.root
    # One transaction: the pages and the graph export come from the same state of the database.
    with transaction(conn):
        relative, content = render_entity(conn, entity_id)
        _write(root / relative, content)
        for (uid,) in conn.execute(
            "SELECT DISTINCT article_uid FROM kg_mentions WHERE entity_id=?", (entity_id,)
        ).fetchall():
            source_relative, source_content = render_source(conn, uid)
            _write(root / source_relative, source_content)
        entity_pages, source_pages, _ = _write_pages(project, conn, only_missing=True)
        _write(root / "index.md", render_index(project, conn))
        _write(root / "log.md", render_log(project, conn))
        write_graph(project, conn, entity_pages, source_pages)
    return root / relative


def export_wiki(project: Project) -> dict[str, Any]:
    from .wiki import _finish_articles

    for path in project.runs.glob(f"*/{RUN_FILE}"):
        try:
            if read_json(path).get("schema_version") == 1:
                _finish_articles(path.parent.name)
        except (OSError, ValueError):
            continue
    root = project.root
    # One transaction: the pages and the graph export come from the same state of the database.
    with database(project.db) as conn, transaction(conn):
        orphans = conn.execute(
            "DELETE FROM kg_entities WHERE id NOT IN (SELECT DISTINCT entity_id FROM kg_mentions) RETURNING id"
        ).fetchall()
        entity_pages, source_pages, protected = _write_pages(project, conn, only_missing=False)
        for name, content in (("index.md", render_index(project, conn)), ("log.md", render_log(project, conn))):
            if _write(root / name, content) == "protected":
                protected.append(name)
        # Before stale pages are removed, so the graph export never lists a missing page.
        graph_written = write_graph(project, conn, entity_pages, source_pages)
        synthesized = conn.execute("SELECT COUNT(*) FROM kg_syntheses").fetchone()[0]
    written = {root / page for page in [*entity_pages.values(), *source_pages.values()] if page}
    removed = 0
    for folder in ("entities", "sources"):
        for path in (root / folder).glob("*.md"):
            if path not in written and _generated(path):
                path.unlink()
                removed += 1
    return project.summary() | {
        "entity_pages": len(entity_pages),
        "synthesized_entities": synthesized,
        "source_pages": len(source_pages),
        "removed_files": removed,
        "removed_orphan_entities": len(orphans),
        "index": str(root / "index.md"),
        "graph": str(project.work / GRAPH_FILE),
        "graph_written": graph_written,
        "not_overwritten": protected,
    }
