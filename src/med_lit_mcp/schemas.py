"""Typed MCP tool inputs, so every client validates arguments before calling."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .ontology import ENTITY_TYPES, MENTION_ROLES

Source = Literal["pubmed", "pmc", "openalex", "semantic-scholar", "scopus", "europepmc"]
EntityType = Literal[tuple(ENTITY_TYPES)]  # type: ignore[valid-type]
MentionRole = Literal[tuple(MENTION_ROLES)]  # type: ignore[valid-type]

ReviewDecision = Literal["include", "exclude"]
DecisionFilter = Literal["include", "exclude", "uncertain", "pending"]
FetchStatus = Literal["pending", "full_text", "abstract_only", "failed", "skipped"]
WikiStatus = Literal["pending", "kg_complete", "complete", "no_entities", "failed"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TermGroup(Strict):
    text: str = Field(min_length=1, description="The researcher's term")
    synonyms: list[str] = Field(default_factory=list, max_length=20, description="True synonyms only (ORed)")
    label: str | None = None
    candidate_mesh: list[str] = Field(default_factory=list, max_length=10, description="Suggested MeSH headings")


class Component(Strict):
    groups: list[TermGroup] = Field(min_length=1, max_length=8, description="ANDed; see guide('question')")


class Components(Strict):
    population: Component
    intervention: Component | None = Field(default=None, description="PICO, required")
    comparison: Component | None = Field(default=None, description="PICO, optional")
    outcome: Component | None = Field(default=None, description="PICO, optional")
    concept: Component | None = Field(default=None, description="PCC, required")
    context: Component | None = Field(default=None, description="PCC, optional: setting only")


class Filters(Strict):
    from_date: date | None = Field(default=None, description="Default: three years ago")
    to_date: date | None = None
    languages: list[str] = Field(default_factory=list)
    publication_types: list[str] = Field(default_factory=list)


class ResearchQuestion(Strict):
    framework: Literal["PICO", "PCC"]
    question: str = Field(min_length=10, description="In the researcher's words")
    components: Components
    filters: Filters = Field(default_factory=Filters, description="Only what the researcher asked for")


class ScreeningDecision(Strict):
    uid: str
    decision: Literal["include", "exclude", "uncertain"]
    reason: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(default="", max_length=500, description="Verbatim quote; required for include/exclude")


class ExtractedEntity(Strict):
    name: str = Field(min_length=1, max_length=150, description="Canonical name")
    entity_type: EntityType = Field(description="See guide('ontology')")
    description: str = Field(min_length=1, max_length=600, description="How this page depicts it")
    mention: str = Field(min_length=1, max_length=300, description="Verbatim span from this page")
    entity_id: int | None = Field(default=None, description="Id of a known entity this refers to")
    role: MentionRole | None = Field(default=None, description="PICO/PCC role in this article's study, if any")


class ExtractedRelationship(Strict):
    source: str = Field(min_length=1, description="Entity name from this call")
    target: str = Field(min_length=1, description="Entity name from this call")
    relationship: str = Field(min_length=1, max_length=60, description="Active verb")
    detail: str = Field(default="", max_length=400)
    evidence: str = Field(min_length=1, max_length=400, description="Verbatim quote from this page")


class DuplicateDecision(Strict):
    entity_id: int
    candidate_id: int
    action: Literal["merge", "distinct"]
    keep: Literal["entity", "candidate"] = "candidate"
    reason: str = Field(default="", max_length=400)


class RelatedEntity(Strict):
    name: str
    relationship_type: str = ""
    entity_type: EntityType | None = None
