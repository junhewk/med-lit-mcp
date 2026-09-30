"""Per-project settings: the visible <project>/med-lit.settings.json.

Layers, lowest first: built-in defaults, the user's defaults (~/.config/med-lit-mcp/defaults.json,
one set per mode), the project's file, and a one-off value passed to a single tool call. A new
project gets a copy of the defaults at creation, so later changes to the defaults never alter
existing reviews.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .store import atomic_text

SETTINGS_FILE = "med-lit.settings.json"
SETTINGS_VERSION = 1
YEARS = re.compile(r"^(?:all|(\d{4})-(\d{4})?)$")
PREPRINT_SERVERS = (
    "medrxiv", "biorxiv", "arxiv", "research square", "ssrn", "preprints.org", "chemrxiv",
    "psyarxiv", "osf preprints", "jmir preprints", "authorea", "techrxiv", "sciety",
)
PREPRINT_DOI_PREFIXES = ("10.1101/", "10.48550/", "10.21203/", "10.2139/", "10.20944/", "10.31219/", "10.36227/")


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SearchSettings(Section):
    sources: list[Literal["pubmed", "pmc", "openalex", "semantic-scholar", "europepmc", "scopus"]] | None = Field(
        default=None, description="None = pubmed, pmc, openalex (+ semantic-scholar/scopus when their keys are set)"
    )
    per_source: int = Field(default=20, ge=1, le=200, description="Records requested from each source")
    years: str | None = Field(
        default=None, description="Publication years: '2020-', '2010-2020' or 'all'; None = the last three years"
    )
    preprint_allow: bool = Field(default=False, description="Keep preprints (medRxiv, arXiv, …) in results")
    languages: list[str] = Field(default_factory=list, description="e.g. ['english']; empty = any")
    publication_types: list[str] = Field(default_factory=list, description="e.g. ['review']; empty = any")

    @field_validator("years")
    @classmethod
    def _years(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        found = YEARS.fullmatch(value)
        if not found:
            raise ValueError("years must look like '2020-', '2010-2020' or 'all'")
        if found.group(2) and int(found.group(2)) < int(found.group(1)):
            raise ValueError("years: the end year is before the start year")
        return value


class FetchSettings(Section):
    limit: int | None = Field(default=None, ge=1, le=5000, description="Most included articles to fetch per run, in ranked order")
    mode: Literal["full_text", "abstract_only"] = Field(default="full_text", description="abstract_only skips PMC and Unpaywall")


class WikiSettings(Section):
    max_pages: int | None = Field(default=3, ge=1, le=50, description="Pages (about 12,000 characters each) read per article")
    min_sources: int = Field(default=2, ge=1, le=10, description="Source articles an entity needs before it gets a synthesis page")
    tasks_per_conversation: int = Field(
        default=5, ge=1, le=100, description="For clients without subagents: wiki tasks per conversation"
    )


class BotSettings(Section):
    lookback_days: int = Field(default=90, ge=1, le=3650, description="Publication-date look-back of each run (open-ended to the future)")
    max_new_articles: int = Field(default=20, ge=1, le=1000, description="New articles screened per run; the rest are dropped and reported")
    max_syntheses: int = Field(default=15, ge=0, le=1000, description="Entity pages written per run")
    time_budget_minutes: int = Field(default=50, ge=5, le=1440, description="No new work starts after this")


class ProjectSettings(Section):
    version: int = SETTINGS_VERSION
    mode: Literal["interactive", "bot"] = "interactive"
    search: SearchSettings = Field(default_factory=SearchSettings)
    fetch: FetchSettings = Field(default_factory=FetchSettings)
    wiki: WikiSettings = Field(default_factory=WikiSettings)
    bot: BotSettings | None = None


def _merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _defaults_file() -> Path:
    from .keys import config_dir

    return config_dir() / "defaults.json"


def user_defaults() -> dict[str, dict[str, Any]]:
    path = _defaults_file()
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def save_user_defaults(mode: str, values: dict[str, Any]) -> Path:
    """Validate and store the defaults new projects of this mode start from."""
    data = user_defaults()
    candidate = _merge(data.get(mode, {}), values)
    _build(mode, candidate)  # raises on invalid values
    data[mode] = candidate
    path = _defaults_file()
    atomic_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return path


def _build(mode: str, overrides: dict[str, Any]) -> ProjectSettings:
    base = ProjectSettings(mode=mode, bot=BotSettings() if mode == "bot" else None).model_dump()
    try:
        return ProjectSettings.model_validate(_merge(base, overrides) | {"mode": mode})
    except ValidationError as exc:
        raise ValueError(_explain(exc)) from exc


def _explain(exc: ValidationError) -> str:
    problems = [f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}" for error in exc.errors()]
    return "Invalid settings: " + "; ".join(problems)


def new_settings(mode: str = "interactive") -> ProjectSettings:
    return _build(mode, user_defaults().get(mode, {}))


def settings_path(root: Path) -> Path:
    return root / SETTINGS_FILE


def write_settings(root: Path, settings: ProjectSettings) -> Path:
    path = settings_path(root)
    atomic_text(path, settings.model_dump_json(indent=2, exclude_none=False) + "\n")
    return path


def load_settings(root: Path) -> ProjectSettings:
    """The project's settings; a project without the file gets defaults written to it."""
    path = settings_path(root)
    if not path.is_file():
        settings = new_settings()
        write_settings(root, settings)
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON (line {exc.lineno}): fix or delete it") from exc
    try:
        return ProjectSettings.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"{path}: {_explain(exc)}") from exc


def apply_changes(settings: ProjectSettings, changes: dict[str, Any]) -> ProjectSettings:
    """Apply dotted-key changes such as {"search.years": "2015-", "fetch.limit": 30}."""
    data = settings.model_dump()
    for dotted, value in changes.items():
        parts = dotted.split(".")
        if parts[0] in ("mode", "version"):
            raise ValueError(f"'{dotted}' cannot be changed")
        if parts[0] == "bot" and settings.mode != "bot":
            raise ValueError("bot settings exist only in bot projects")
        target = data
        for part in parts[:-1]:
            if not isinstance(target.get(part), dict):
                raise ValueError(f"Unknown setting '{dotted}'")  # noqa: TRY004 - reported to the user as a tool error
            target = target[part]
        if parts[-1] not in target:
            raise ValueError(f"Unknown setting '{dotted}'")
        target[parts[-1]] = value
    try:
        return ProjectSettings.model_validate(data)
    except ValidationError as exc:
        raise ValueError(_explain(exc)) from exc


def year_filters(years: str | None) -> dict[str, str]:
    """Question filters for a years setting; empty for None (engine default) or 'all'."""
    if not years or years == "all":
        return {"from_date": "1800-01-01"} if years == "all" else {}
    start, end = YEARS.fullmatch(years).groups()  # type: ignore[union-attr]
    filters = {"from_date": f"{start}-01-01"}
    if end:
        filters["to_date"] = f"{end}-12-31"
    return filters


def is_preprint(record: dict[str, Any]) -> bool:
    """Best-effort preprint detection from publication type, venue, DOI and source id."""
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
