"""The method contracts for wiki work, defined once.

guide("extraction"), guide("duplicates") and guide("synthesis"), the self-contained task texts that
wiki_tasks hands out, and the short reminder in each page header are all generated from here, so every
client and every subagent receives the same entity types, roles and evidence rules whether or not it
reads a guide. TASK_TOOLS names the server tools each kind of task needs, so a client that can scope a
subagent's tools gives it exactly those.
"""

from __future__ import annotations

from .ontology import ENTITY_TYPES, MENTION_ROLES

VERBATIM = "Each mention and each relationship's evidence must be copied verbatim from the page"
NO_METADATA = (
    "Never extract publication metadata: years, PMID/PMCID/DOI, licenses, copyright, journal or publisher "
    "names, dates, or generic words such as article, paper, study, source"
)
NO_STUDY_TOOLS = (
    "Never extract tools used only to run the study: statistics and survey software (SPSS, Stata, R, "
    "GraphPad Prism, NVivo, REDCap, Qualtrics). Extract a PLACE or ORGANIZATION only when the article says "
    "something about it, not when it is merely where the study was done or who funded it"
)
EMPTY_OK = "An empty list is valid for a page with nothing substantive"

EXTRACTION_RULES: tuple[str, ...] = (
    "Work only from the page text next_wiki_article returns; no outside knowledge.",
    (
        "Submit each page with record_extraction, passing the header's uid, content_sha256 and page. "
        "get_article_page(run_id, uid, page) re-reads a page within the article's allowance; find_entities "
        "looks up known entities."
    ),
    (
        "Extract 5-18 substantive entities per page. Broad concepts (artificial intelligence, large language "
        "models) only when the page substantively depicts them. " + EMPTY_OK + "."
    ),
    NO_METADATA + ".",
    NO_STUDY_TOOLS + ".",
    (
        'name: the canonical name. Expand acronyms except well-known ones (FDA, WHO); "expansion (ACRONYM)" '
        "records both. Reuse a known_entities name, or pass its id as entity_id."
    ),
    (
        "mention: a verbatim span from this page naming the entity. description: 1-2 sentences on how this "
        "page depicts it."
    ),
    (
        "role: the PICO/PCC element the entity plays in the study this article reports, one of the roles "
        "below; omit it for background or discussion mentions."
    ),
    (
        "Relationships: source and target must be entities in the same call; relationship is an active verb "
        "(evaluates, supports, limits, causes); evidence is a verbatim quote from this page; detail says how it "
        "shows up."
    ),
    "entity_type: exactly one of the types below.",
)


def entity_types_text() -> str:
    return "\n".join(f"- {name}: {definition} (Simple Graph Builder type {parent})" for name, (parent, definition) in ENTITY_TYPES.items())


def roles_text() -> str:
    return "\n".join(f"- {name}: {meaning}" for name, meaning in MENTION_ROLES.items())


def extraction_contract() -> str:
    rules = "\n".join(f"- {rule}" for rule in EXTRACTION_RULES)
    return f"{rules}\n\nEntity types (entity_type):\n{entity_types_text()}\n\nRoles (role):\n{roles_text()}"


# The page header repeats the core of the contract on every page the reader serves.
EXTRACTION_REMINDER = (
    "Extract 5-18 substantive entities from this page with record_extraction, each with an entity_type "
    "and, when it has one in this study, a role from the extraction contract (guide('extraction')). "
    f"{VERBATIM}. Reuse a known entity's name or pass its id as entity_id. {NO_METADATA}. "
    f"{NO_STUDY_TOOLS}. {EMPTY_OK}."
)

DUPLICATES_CONTRACT = """\
list_duplicate_candidates shows the pending pairs with an example mention of each.
Merge only names for the same thing at the same level of abstraction: acronym and expansion,
spelling or plural variants. Keep generic and specific terms distinct (machine learning vs deep
learning; decision aid vs patient decision aid). resolve_duplicates takes merge or distinct per pair;
merge_entities can also rename or retype an entity."""

SYNTHESIS_CONTRACT = """\
next_synthesis returns mode "new" or "update".

- new: write only from the context, in a neutral encyclopedic tone, describing how the gathered
  articles depict the entity rather than a generic definition. Sections, as '## ' headings: Overview,
  How Gathered Articles Depict It, Recurring Themes, Tensions and Limitations, Relationships. Use the
  PICO/PCC roles to say how studies used the entity. Send summary, synthesis, key_aspects and
  related_entities.
- update: the page exists (current_synthesis). The mentions are only the evidence added since it was
  written; removed_sources were withdrawn. Do not rewrite the page: change only the sections the new
  evidence affects, delete statements citing removed sources, and send just those sections as
  `sections` ({heading: full new text of the section}). Everything else is kept as it is. Send
  summary or key_aspects only if they change.

Cite sources inline as [uid], only uids from the context; link other entities as [[Name]]. Pass the
same entity_id and input_digest to record_synthesis."""

# Server tools each kind of wiki task needs (guide is always allowed). Settings, fetch, screening and
# other stages' tools are deliberately absent.
TASK_TOOLS: dict[str, tuple[str, ...]] = {
    "extract": ("next_wiki_article", "get_article_page", "record_extraction", "find_entities", "guide"),
    "duplicates": ("list_duplicate_candidates", "resolve_duplicates", "merge_entities", "find_entities", "guide"),
    "synthesize": ("next_synthesis", "record_synthesis", "find_entities", "guide"),
}
