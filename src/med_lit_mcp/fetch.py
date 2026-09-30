"""Bounded, resumable retrieval of included articles."""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from . import __version__
from .config import ncbi_api_key, ncbi_email
from .projects import locate
from .runs import require_selection_complete, selected_uids, summary
from .settings import load_settings
from .store import atomic_text, database, locked_run, now, save_run, transaction

MAX_BODY = 10 * 1024 * 1024
REQUEST_TIMEOUT = 20
TIME_BUDGET = 30.0
PDF_ATTEMPTS = 2
MIN_PDF_TEXT = 2000
PMC_IN_URL = re.compile(r"/pmc/articles/(?:PMC)?(\d+)", re.IGNORECASE)
PMC_IN_OAI = re.compile(r"pubmedcentral\.nih\.gov:(\d+)$", re.IGNORECASE)
_last_ncbi_call = 0.0


def safe_http(url: str, timeout: float = REQUEST_TIMEOUT) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": f"med-lit-mcp/{__version__} (medical literature review)"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")
        data = response.read(MAX_BODY + 1)
        if len(data) > MAX_BODY:
            raise ValueError("Source exceeds 10 MB fetch limit")
        return data


def _ncbi_pause() -> None:
    """Stay under NCBI's 3 (or 10 with an API key) requests per second."""
    global _last_ncbi_call
    interval = 0.1 if ncbi_api_key() else 0.34
    wait = _last_ncbi_call + interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_ncbi_call = time.monotonic()


def pmc_body(xml: bytes) -> str:
    root = ET.fromstring(xml)
    if root.tag != "article":
        article = root.find(".//article")
        if article is None:
            raise ValueError("PMC response is not an article")
        root = article
    body = root.find("body")
    if body is None:
        raise ValueError("PMC article has no full-text body")
    paragraphs = [" ".join("".join(node.itertext()).split()) for node in body.iter("p")]
    return "\n\n".join(part for part in paragraphs if part)


Fetched = tuple[str, str, str, str, dict[str, Any]]


def _pmc_text(number: str) -> Fetched | None:
    """PMC article body as text: NCBI efetch first, Europe PMC as the fallback."""
    params = {"db": "pmc", "id": number, "rettype": "xml", "tool": "med-lit-mcp"}
    params["email"] = ncbi_email(required=True)
    if ncbi_api_key():
        params["api_key"] = ncbi_api_key()
    attempts = (
        (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode(params),
            f"https://pmc.ncbi.nlm.nih.gov/articles/PMC{number}/",
            "pmc_efetch_xml",
            True,
        ),
        (
            f"https://www.ebi.ac.uk/europepmc/webservices/rest/PMC{number}/fullTextXML",
            f"https://europepmc.org/articles/PMC{number}",
            "europepmc_fulltext_xml",
            False,
        ),
    )
    for fetch_url, source_url, method, is_ncbi in attempts:
        try:
            if is_ncbi:
                _ncbi_pause()
            text = pmc_body(safe_http(fetch_url))
            if text.strip():
                return text, "full_text", source_url, method, {"pmcid": f"PMC{number}"}
        except (OSError, urllib.error.URLError, ValueError, ET.ParseError):
            continue
    return None


def unpaywall_locations(doi: str) -> list[dict[str, Any]]:
    """Open-access copies of a DOI, best first; empty when Unpaywall knows none."""
    email = ncbi_email(required=True)
    url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi.strip(), safe='/')}?" + urllib.parse.urlencode(
        {"email": email}
    )
    try:
        data = json.loads(safe_http(url))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    return [location for location in data.get("oa_locations") or [] if isinstance(location, dict)]


def pdf_text(data: bytes) -> str:
    """Plain text of a PDF, one block per page, with line-break hyphenation undone."""
    if not data.startswith(b"%PDF-"):
        raise ValueError("Not a PDF (the host may require a browser or a login)")
    from pypdf import PdfReader

    blocks = []
    for page in PdfReader(io.BytesIO(data)).pages[:80]:
        raw = page.extract_text() or ""
        raw = re.sub(r"(\w)-\n(\w)", r"\1\2", raw)
        text = " ".join(raw.split())
        if text:
            blocks.append(text)
    return "\n\n".join(blocks)


def _open_access(doi: str) -> Fetched | None:
    """Full text via Unpaywall: a PMC copy when one exists, otherwise an open-access PDF."""
    try:
        locations = unpaywall_locations(doi)
    except (OSError, urllib.error.URLError, ValueError):
        return None

    def details(location: dict[str, Any]) -> dict[str, Any]:
        return {
            "via": "unpaywall",
            "doi": doi,
            "host_type": location.get("host_type"),
            "version": location.get("version"),
            "license": location.get("license"),
        }

    seen: set[str] = set()
    for location in locations:
        found = PMC_IN_URL.search(" ".join(str(location.get(k) or "") for k in ("url", "url_for_pdf", "url_for_landing_page")))
        found = found or PMC_IN_OAI.search(str(location.get("pmh_id") or ""))
        if found and found.group(1) not in seen:
            seen.add(found.group(1))
            result = _pmc_text(found.group(1))
            if result:
                text, content_type, source_url, method, extra = result
                return text, content_type, source_url, f"unpaywall_{method}", details(location) | extra
    attempts = 0
    for location in locations:
        pdf_url = location.get("url_for_pdf")
        if not pdf_url or attempts >= PDF_ATTEMPTS:
            continue
        attempts += 1
        try:
            text = pdf_text(safe_http(str(pdf_url)))
        except Exception:  # noqa: BLE001, S112 - blocked hosts and malformed PDFs just mean "try the next copy"
            continue
        if len(text) >= MIN_PDF_TEXT:
            return text, "full_text", str(pdf_url), "unpaywall_pdf", details(location)
    return None


def fetch_content(record: dict[str, Any], *, abstract_only: bool = False) -> Fetched:
    """Return (text, content_type, source_url, method, details): PMC, Unpaywall, then abstract."""
    number = str(record.get("pmcid") or "").upper().removeprefix("PMC")
    if not abstract_only and number.isdigit() and (result := _pmc_text(number)):
        return result
    doi = str(record.get("doi") or "").strip()
    if not abstract_only and doi and (result := _open_access(doi)):
        return result
    abstract = " ".join(str(record.get("abstract") or "").split())
    if abstract:
        return abstract, "abstract_only", str(record.get("url") or ""), "search_record_abstract", {}
    raise ValueError("No retrievable full text or abstract")


def save_article(
    conn: sqlite3.Connection,
    run_id: str,
    uid: str,
    record: dict[str, Any],
    fetched: tuple[str, str, str, str] | Fetched,
) -> tuple[str, str, str, str, str]:
    """Upsert the article, never replacing full text with an abstract; returns what is stored."""
    text, content_type, source_url, method = fetched[:4]
    authors = record.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    with transaction(conn):
        existing = conn.execute(
            "SELECT full_text, content_type, source_url, fetch_method FROM articles WHERE uid=?",
            (uid,),
        ).fetchone()
        if existing and existing["content_type"] == "full_text" and content_type == "abstract_only":
            text, content_type = existing["full_text"], "full_text"
            source_url, method = existing["source_url"], existing["fetch_method"]
        digest = hashlib.sha256(text.encode()).hexdigest()
        stamp = now()
        conn.execute(
            """INSERT INTO articles (uid, title, authors_json, first_author, journal, pub_date, doi,
                 url, abstract, full_text, content_type, content_sha256, source_url, fetch_method,
                 fetched_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(uid) DO UPDATE SET
                 title=COALESCE(excluded.title, articles.title),
                 authors_json=excluded.authors_json, first_author=excluded.first_author,
                 journal=COALESCE(excluded.journal, articles.journal),
                 pub_date=COALESCE(excluded.pub_date, articles.pub_date),
                 doi=COALESCE(excluded.doi, articles.doi), url=COALESCE(excluded.url, articles.url),
                 abstract=COALESCE(excluded.abstract, articles.abstract),
                 full_text=excluded.full_text, content_type=excluded.content_type,
                 content_sha256=excluded.content_sha256, source_url=excluded.source_url,
                 fetch_method=excluded.fetch_method, fetched_at=excluded.fetched_at,
                 updated_at=excluded.updated_at""",
            (
                uid, record.get("title"), json.dumps(authors, ensure_ascii=False),
                authors[0] if authors else None, record.get("journal"),
                record.get("publication_date"), record.get("doi"), record.get("url"),
                record.get("abstract"), text, content_type, digest, source_url, method, stamp, stamp,
            ),
        )
        conn.execute(
            """INSERT INTO source_snapshots
                 (run_id, article_uid, source_url, content_type, content_sha256, fetched_at,
                  search_record_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id, article_uid) DO UPDATE SET
                 source_url=excluded.source_url, content_type=excluded.content_type,
                 content_sha256=excluded.content_sha256, fetched_at=excluded.fetched_at,
                 search_record_json=excluded.search_record_json""",
            (run_id, uid, source_url, content_type, digest, stamp, json.dumps(record, ensure_ascii=False)),
        )
    return text, content_type, source_url, method, digest


def _is_current(path: Path, item: dict[str, Any]) -> bool:
    source = path / item.get("text_file", "missing")
    return (
        item.get("fetch") == "full_text"
        and source.is_file()
        and hashlib.sha256(source.read_bytes()).hexdigest() == item.get("content_sha256")
    )


def fetch_batch(
    run_id: str,
    uids: list[str] | None = None,
    *,
    max_items: int = 5,
    retry_failed: bool = False,
    retry_abstract_only: bool = False,
    time_budget: float = TIME_BUDGET,
) -> dict[str, Any]:
    ncbi_email(required=True)
    project, path = locate(run_id)
    config = load_settings(project.root).fetch
    started = time.monotonic()
    with locked_run(path) as manifest:
        require_selection_complete(manifest, "fetch")
        # Highest-ranked first, so a fetch limit keeps the most relevant articles.
        chosen = sorted(
            selected_uids(manifest, uids),
            key=lambda uid: (manifest["articles"][uid].get("rank") is None, manifest["articles"][uid].get("rank") or 0),
        )
        explicit = bool(uids)
        retrying = retry_failed or retry_abstract_only
        # A retry pass tries each failed/abstract-only article once, so repeated calls finish.
        retry_key = manifest.setdefault("fetch_retry_pass", now()) if retrying else None

        def todo(uid: str) -> bool:
            item = manifest["articles"][uid]
            if explicit:
                return not _is_current(path, item)
            if retrying and item.get("fetch_retry") == retry_key:
                return False  # already retried in this pass; do not loop on permanent failures
            return (
                item["fetch"] == "pending"
                or (retry_failed and item["fetch"] == "failed")
                or (retry_abstract_only and item["fetch"] == "abstract_only")
            )

        queue = [uid for uid in chosen if todo(uid)]
        skipped_now: list[str] = []
        if config.limit is not None and not explicit:
            attempted = sum(1 for uid in chosen if manifest["articles"][uid]["fetch"] not in ("pending", "skipped"))
            room = max(0, config.limit - attempted)
            for uid in [u for u in queue if manifest["articles"][u]["fetch"] == "pending"][room:]:
                manifest["articles"][uid]["fetch"] = "skipped"
                manifest["articles"][uid]["error"] = f"over the project's fetch limit ({config.limit})"
                skipped_now.append(uid)
            if skipped_now:
                save_run(path, manifest)
            queue = [uid for uid in queue if uid not in skipped_now]
        processed: list[dict[str, Any]] = []
        stopped = "done"
        with database(project.db) as conn:
            for uid in queue:
                if len(processed) >= max_items:
                    stopped = "max_items"
                    break
                if processed and time.monotonic() - started > time_budget:
                    stopped = "time_budget"
                    break
                item = manifest["articles"][uid]
                if retrying and item["fetch"] != "pending":
                    item["fetch_retry"] = retry_key
                try:
                    fetched = fetch_content(item["record"], abstract_only=config.mode == "abstract_only")
                    text, content_type, source_url, method, digest = save_article(
                        conn, run_id, uid, item["record"], fetched
                    )
                    relative = f"sources/{hashlib.sha256(uid.encode()).hexdigest()}.txt"
                    atomic_text(path / relative, text)
                    item.update(
                        {
                            "fetch": content_type,
                            "wiki": item["wiki"] if item.get("content_sha256") == digest else "pending",
                            "text_file": relative,
                            "content_sha256": digest,
                            "source_url": source_url,
                            "fetch_method": method,
                            "fetched_at": now(),
                        }
                    )
                    item.pop("error", None)
                    if method == fetched[3]:  # otherwise earlier full text was kept, with its details
                        item["fetch_details"] = fetched[4] or None
                except (OSError, ValueError, sqlite3.Error) as exc:
                    item["fetch"] = "failed"
                    item["error"] = str(exc)[:300]
                save_run(path, manifest)
                processed.append(
                    {
                        "uid": uid,
                        "fetch": item["fetch"],
                        "fetch_method": item.get("fetch_method"),
                        "error": item.get("error"),
                    }
                )
        done = {entry["uid"] for entry in processed}
        remaining = sum(1 for uid in queue if uid not in done)
        if retrying and not remaining:
            manifest.pop("fetch_retry_pass", None)
            for item in manifest["articles"].values():
                item.pop("fetch_retry", None)
            save_run(path, manifest)
        return {
            "run_id": run_id,
            "processed": processed,
            "remaining": remaining,
            "stopped_reason": stopped if remaining else "done",
            "fetch": summary(manifest)["fetch"],
            "skipped_over_limit": skipped_now,
            "note": (
                "abstract_only articles have no retrievable full text; never describe them as full text"
            ),
        }
