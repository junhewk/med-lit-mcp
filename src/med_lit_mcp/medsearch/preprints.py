"""Best-effort preprint detection for records from any source."""

from __future__ import annotations

from typing import Any

PREPRINT_SERVERS = (
    "medrxiv", "biorxiv", "arxiv", "research square", "ssrn", "preprints.org", "chemrxiv",
    "psyarxiv", "osf preprints", "jmir preprints", "authorea", "techrxiv", "sciety",
)
# 10.64898/ is the prefix bioRxiv and medRxiv moved to in 2026.
PREPRINT_DOI_PREFIXES = (
    "10.1101/", "10.64898/", "10.48550/", "10.21203/", "10.2139/", "10.20944/", "10.31219/", "10.36227/",
)


def is_preprint(record: dict[str, Any]) -> bool:
    """From publication type, venue, DOI prefix and Europe PMC's PPR ids."""
    types = " ".join(str(t).lower() for t in record.get("publication_types") or [])
    if "preprint" in types or "posted-content" in types:
        return True
    venue = str(record.get("journal") or "").lower()
    if any(server in venue for server in PREPRINT_SERVERS):
        return True
    doi = str(record.get("doi") or "").lower().removeprefix("https://doi.org/")
    if doi.startswith(PREPRINT_DOI_PREFIXES):
        return True
    return str(record.get("source_id") or "").upper().startswith("PPR")
