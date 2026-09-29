"""Durable run manifests, file locks, and the per-project SQLite database."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUN_ID = re.compile(r"^[0-9a-f]{32}$")
RUN_FILE = "run.json"
MANIFEST_VERSION = 1


def now() -> str:
    return datetime.now(UTC).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - Windows
        raise ValueError("med-lit-mcp needs Linux or macOS (fcntl file locks)") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@contextmanager
def locked_run(path: Path, *, write: bool = True) -> Iterator[dict[str, Any]]:
    """Hold the run lock; writers must call save_run themselves."""
    with file_lock(path / ".lock"):
        manifest = read_json(path / RUN_FILE)
        if write and manifest.get("schema_version") != MANIFEST_VERSION:
            raise ValueError(f"Run {manifest.get('run_id')} uses an unsupported format and is read-only")
        yield manifest


def save_run(path: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = now()
    atomic_json(path / RUN_FILE, manifest)


def question_text(path: Path) -> str | None:
    try:
        return read_json(path / "question.json").get("question")
    except (OSError, ValueError):
        return None


MIGRATIONS = [
    """
    CREATE TABLE articles (
      uid TEXT PRIMARY KEY, title TEXT, authors_json TEXT, first_author TEXT, journal TEXT,
      pub_date TEXT, doi TEXT, url TEXT, abstract TEXT, full_text TEXT NOT NULL,
      content_type TEXT NOT NULL CHECK (content_type IN ('full_text', 'abstract_only')),
      content_sha256 TEXT NOT NULL, source_url TEXT, fetch_method TEXT, fetched_at TEXT,
      kg_sha256 TEXT, kg_page_count INTEGER, kg_completed_at TEXT,
      page_name TEXT UNIQUE COLLATE NOCASE, updated_at TEXT NOT NULL);
    CREATE TABLE source_snapshots (
      run_id TEXT NOT NULL, article_uid TEXT NOT NULL REFERENCES articles(uid) ON DELETE CASCADE,
      source_url TEXT, content_type TEXT NOT NULL, content_sha256 TEXT NOT NULL,
      fetched_at TEXT NOT NULL, search_record_json TEXT NOT NULL,
      PRIMARY KEY (run_id, article_uid));
    CREATE TABLE kg_entities (
      id INTEGER PRIMARY KEY, canonical_name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
      entity_type TEXT NOT NULL CHECK (entity_type IN ('CONDITION', 'INTERVENTION', 'TECHNOLOGY',
        'METHOD', 'GUIDELINE', 'DATASET', 'CONCEPT', 'PERSON', 'ORGANIZATION', 'PLACE')),
      description TEXT, page_name TEXT UNIQUE COLLATE NOCASE,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE kg_aliases (
      alias_key TEXT PRIMARY KEY, alias TEXT NOT NULL,
      entity_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
      source TEXT NOT NULL CHECK (source IN ('canonical', 'mention', 'acronym', 'merge', 'agent')),
      created_at TEXT NOT NULL);
    CREATE INDEX kg_aliases_entity ON kg_aliases(entity_id);
    CREATE TABLE kg_extraction_pages (
      article_uid TEXT NOT NULL REFERENCES articles(uid) ON DELETE CASCADE,
      content_sha256 TEXT NOT NULL, page_index INTEGER NOT NULL, page_count INTEGER NOT NULL,
      payload_json TEXT NOT NULL, client TEXT, recorded_at TEXT NOT NULL,
      PRIMARY KEY (article_uid, content_sha256, page_index));
    CREATE TABLE kg_mentions (
      id INTEGER PRIMARY KEY,
      article_uid TEXT NOT NULL REFERENCES articles(uid) ON DELETE CASCADE,
      entity_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
      page_index INTEGER NOT NULL, mention_text TEXT NOT NULL, context TEXT NOT NULL,
      description TEXT, role TEXT CHECK (role IN ('population', 'intervention', 'comparator',
        'outcome', 'concept', 'context')),
      UNIQUE (article_uid, entity_id, page_index, mention_text));
    CREATE INDEX kg_mentions_entity ON kg_mentions(entity_id);
    CREATE TABLE kg_relationships (
      id INTEGER PRIMARY KEY,
      source_entity_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
      target_entity_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
      relationship_type TEXT NOT NULL, detail TEXT,
      UNIQUE (source_entity_id, target_entity_id, relationship_type));
    CREATE TABLE kg_relationship_evidence (
      relationship_id INTEGER NOT NULL REFERENCES kg_relationships(id) ON DELETE CASCADE,
      article_uid TEXT NOT NULL REFERENCES articles(uid) ON DELETE CASCADE,
      page_index INTEGER NOT NULL, evidence TEXT NOT NULL, detail TEXT,
      PRIMARY KEY (relationship_id, article_uid, page_index));
    CREATE TABLE kg_syntheses (
      entity_id INTEGER PRIMARY KEY REFERENCES kg_entities(id) ON DELETE CASCADE,
      summary TEXT NOT NULL, synthesis TEXT NOT NULL, key_aspects_json TEXT NOT NULL,
      related_entities_json TEXT NOT NULL, input_digest TEXT NOT NULL,
      source_article_count INTEGER NOT NULL, stale INTEGER NOT NULL DEFAULT 0,
      version INTEGER NOT NULL, client TEXT, compiled_at TEXT NOT NULL);
    CREATE TABLE kg_duplicate_candidates (
      entity_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
      candidate_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
      score REAL NOT NULL, reasons_json TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('pending', 'merged', 'distinct')),
      created_at TEXT NOT NULL, PRIMARY KEY (entity_id, candidate_id));
    CREATE TABLE kg_distinct_pairs (
      a INTEGER NOT NULL, b INTEGER NOT NULL, reason TEXT, decided_at TEXT NOT NULL,
      PRIMARY KEY (a, b), CHECK (a < b));
    CREATE TABLE kg_merge_log (
      id INTEGER PRIMARY KEY, kept_id INTEGER NOT NULL, merged_id INTEGER NOT NULL,
      merged_name TEXT NOT NULL, reason TEXT, merged_at TEXT NOT NULL);
    CREATE VIEW kg_entity_stats AS
      SELECT e.id AS entity_id, COUNT(m.id) AS mention_count,
             COUNT(DISTINCT m.article_uid) AS source_count
      FROM kg_entities e LEFT JOIN kg_mentions m ON m.entity_id = e.id GROUP BY e.id;
    """,
]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    # Table rebuilds in migrations need foreign keys off; the setting cannot change mid-transaction.
    conn.execute("PRAGMA foreign_keys=OFF")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for index in range(version, len(MIGRATIONS)):
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Another process may have migrated while this one waited for the lock.
            if conn.execute("PRAGMA user_version").fetchone()[0] > index:
                conn.execute("COMMIT")
                continue
            for statement in MIGRATIONS[index].split(";"):
                if statement.strip():
                    conn.execute(statement)
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise sqlite3.IntegrityError(f"Migration {index + 1} left dangling references")
            conn.execute(f"PRAGMA user_version={index + 1}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@contextmanager
def database(path: Path) -> Iterator[sqlite3.Connection]:
    """Open a project database (pass project.db)."""
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()
