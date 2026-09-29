"""med-lit-mcp's medical-literature ontology (spec: docs/ontology.md).

Each entity type refines exactly one Simple Graph Builder type, so a med-lit graph can always
be read as a Simple Graph Builder graph by replacing each type with its parent.
"""

from __future__ import annotations

ONTOLOGY_VERSION = "1"

# type: (Simple Graph Builder parent type, definition)
ENTITY_TYPES: dict[str, tuple[str, str]] = {
    "CONDITION": ("CONCEPT", "diseases, disorders, symptoms, diagnoses and other health states"),
    "INTERVENTION": ("METHOD", "treatments, drugs, procedures, surgeries, therapies and programs delivered to people"),
    "TECHNOLOGY": ("TOOL", "AI systems and models, software, devices and platforms"),
    "METHOD": ("METHOD", "study designs, research and analytic methods, assessment instruments and scores"),
    "GUIDELINE": ("DOCUMENT", "laws, regulations, clinical guidelines, reporting standards and policies"),
    "DATASET": ("TOOL", "datasets, databases, registries and benchmarks"),
    "CONCEPT": ("CONCEPT", "ideas, principles, and ethical, social or professional notions"),
    "PERSON": ("PERSON", "named individuals such as researchers, clinicians or public figures"),
    "ORGANIZATION": ("ORGANIZATION", "hospitals, universities, companies, agencies and professional bodies"),
    "PLACE": ("PLACE", "countries, regions, and care or education settings as places"),
}

# The PICO/PCC element an entity plays in the study one article reports. A property of a
# mention, not of the entity: a condition can be the population in one study and an outcome
# in another.
MENTION_ROLES: dict[str, str] = {
    "population": "who was studied",
    "intervention": "what was done, given or evaluated (PICO)",
    "comparator": "what it was compared against (PICO)",
    "outcome": "what was measured as a result (PICO)",
    "concept": "the phenomenon examined (PCC)",
    "context": "the setting the study took place in",
}

TYPE_GUIDE = "; ".join(f"{name} {definition}" for name, (_, definition) in ENTITY_TYPES.items())
ROLE_GUIDE = "; ".join(f"{name}: {definition}" for name, definition in MENTION_ROLES.items())


def graph_type(entity_type: str) -> str:
    """The Simple Graph Builder type a med-lit type refines."""
    return ENTITY_TYPES[entity_type][0]


def related_types(entity_type: str) -> tuple[str, ...]:
    """Types sharing a parent, where inconsistent typing can hide a duplicate."""
    parent = graph_type(entity_type)
    return tuple(name for name, (other, _) in ENTITY_TYPES.items() if other == parent)
