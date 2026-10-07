"""Repair concept groups that would search something other than what they name.

A group ORs its text, synonyms and MeSH headings, so one misplaced term changes the whole group:
an age heading beside a condition ("type 2 diabetes" OR "Adult") matches every adult study, and a
sentence-like text ("large language model or chatbot that conducts the interview") can never
match as a phrase. Generic words ("patients") are only reported: beside "clinicians" they may be a
real alternative, so removing them could change the question.

Questions are repaired when validated, so the researcher approves the repaired version, and again
when a search is planned, so questions saved earlier (bot questions) get the same repairs.
"""

from __future__ import annotations

import re

from .models import ConceptGroup, Question


def _norm(term: str) -> str:
    return " ".join(term.casefold().replace("-", " ").replace("_", " ").split())


# MeSH age groups and their plain forms. An age is a population facet of its own.
AGE_TERMS = frozenset(
    _norm(term)
    for term in (
        "Infant, Newborn", "newborn", "newborns", "Infant", "infants", "Child, Preschool", "preschool children",
        "Child", "children", "Adolescent", "adolescents", "teenagers", "Young Adult", "young adults", "Adult",
        "adults", "Middle Aged", "Aged", "Aged, 80 and over", "older adult", "older adults", "older people",
        "elderly",
    )
)
# Plain search words for an age group built from MeSH headings alone.
AGE_WORDS = {
    "adult": ["adults", "adult"],
    "aged": ["older adults", "aged", "elderly"],
    "child": ["children", "child"],
    "adolescent": ["adolescents", "adolescent"],
    "infant": ["infants", "infant"],
    "young adult": ["young adults", "young adult"],
    "middle aged": ["middle aged"],
}
# Words that name no particular population; validation reports them, repairs leave them.
GENERIC_TERMS = frozenset(
    _norm(term)
    for term in (
        "patient", "patients", "people", "person", "persons", "individual", "individuals", "participant",
        "participants", "subject", "subjects", "human", "humans", "population", "populations",
    )
)
MAX_TERM_WORDS = 6
_SENTENCE = re.compile(r"[();]|\bor\b", re.IGNORECASE)


def unsearchable(term: str) -> bool:
    """A sentence or list, not a term a title or abstract would contain as a phrase."""
    return len(term.split()) > MAX_TERM_WORDS or bool(_SENTENCE.search(term))


def is_age(term: str) -> bool:
    return _norm(term) in AGE_TERMS


_AGE_WORDS_IN_TERM = re.compile(
    r"\b(" + "|".join(re.escape(term) for term in sorted(AGE_TERMS, key=len, reverse=True) if "," not in term) + r")\b"
)


def mentions_age(term: str) -> bool:
    """An age term, or a phrase qualified by one ("adults with diabetes")."""
    return is_age(term) or bool(_AGE_WORDS_IN_TERM.search(_norm(term)))


def is_generic(term: str) -> bool:
    return _norm(term) in GENERIC_TERMS


def repair_question(question: Question) -> tuple[list[str], list[str]]:
    """Repair every group in place; returns (notes on what changed, groups left unrepairable)."""
    notes: list[str] = []
    unrepairable: list[str] = []
    for name, block in question.components.items():
        moved: list[ConceptGroup] = []
        for group in block.groups:
            where = f"{name}/{group.label}"
            _drop_sentences(group, where, notes)
            ages = _take_ages(group)
            if ages:
                moved.append(ages)
                notes.append(f"{where}: moved {', '.join(ages.all_terms())} to an age group of its own")
            if not [term for term in group.free_terms() if not unsearchable(term)]:
                unrepairable.append(where)
        for ages in moved:
            _merge_age_group(block.groups, ages)
    return notes, unrepairable


def _remove(group: ConceptGroup, norms: set[str]) -> None:
    group.synonyms = [term for term in group.synonyms if _norm(term) not in norms]
    group.candidate_mesh = [term for term in group.candidate_mesh if _norm(term) not in norms]
    group.resolved_mesh = [term for term in group.resolved_mesh if _norm(term) not in norms]
    if _norm(group.text) in norms and group.synonyms:
        group.text = group.synonyms.pop(0)


def _drop_sentences(group: ConceptGroup, where: str, notes: list[str]) -> None:
    long = [term for term in group.free_terms() if unsearchable(term)]
    if not long or len(long) == len(group.free_terms()):
        return  # nothing to drop, or nothing would be left: reported as unrepairable
    _remove(group, {_norm(term) for term in long})
    notes.append(f"{where}: left out {'; '.join(repr(term) for term in long)}, too long to match as a phrase")


def _take_ages(group: ConceptGroup) -> ConceptGroup | None:
    """Take age terms out of a group that names something else as well."""
    terms = [*group.free_terms(), *group.candidate_mesh, *group.resolved_mesh]
    if not any(is_age(term) for term in terms) or all(mentions_age(term) for term in terms):
        return None  # no age term, or an age group as drafted
    words = [term for term in group.free_terms() if is_age(term)]
    candidate = [term for term in group.candidate_mesh if is_age(term)]
    resolved = [term for term in group.resolved_mesh if is_age(term)]
    _remove(group, {_norm(term) for term in [*words, *candidate, *resolved]})
    if not words:
        words = [w for heading in [*candidate, *resolved] for w in AGE_WORDS.get(_norm(heading), [_norm(heading)])]
    words = list(dict.fromkeys(words))
    return ConceptGroup(label="age", text=words[0], synonyms=words[1:], candidate_mesh=candidate, resolved_mesh=resolved)


def _merge_age_group(groups: list[ConceptGroup], ages: ConceptGroup) -> None:
    existing = next(
        (group for group in groups if all(mentions_age(term) for term in [*group.all_terms(), *group.candidate_mesh])),
        None,
    )
    if existing is None:
        labels = {group.label.casefold() for group in groups}
        label, number = "age", 2
        while label in labels:
            label, number = f"age {number}", number + 1
        ages.label = label
        groups.append(ages)
        return
    seen = {_norm(term) for term in existing.free_terms()}
    existing.synonyms += [term for term in ages.free_terms() if _norm(term) not in seen]
    existing.candidate_mesh = list(dict.fromkeys([*existing.candidate_mesh, *ages.candidate_mesh]))
    existing.resolved_mesh = list(dict.fromkeys([*existing.resolved_mesh, *ages.resolved_mesh]))
