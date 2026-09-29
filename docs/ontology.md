# med-lit ontology, version 1

A med-lit-mcp wiki is a small knowledge graph built from medical literature: entities, relationships between them, and mentions that tie both to the articles they came from. This document is the contract for that graph. The source of truth in code is `src/med_lit_mcp/ontology.py`.

The model follows the lightweight ontology of [Simple Graph Builder](https://github.com/junhewk/simple-graph-builder): a small fixed set of entity types, free-form relationship verbs, and a detail note on each relationship. Its ten types are general-purpose, so med-lit defines its own medical types and declares each one a **refinement of exactly one Simple Graph Builder type**. Replacing every med-lit type with its parent always yields a valid Simple Graph Builder graph.

## Entity types

| Type | Covers | Simple Graph Builder parent |
|---|---|---|
| `CONDITION` | Diseases, disorders, symptoms, diagnoses and other health states | `CONCEPT` |
| `INTERVENTION` | Treatments, drugs, procedures, surgeries, therapies and programs delivered to people | `METHOD` |
| `TECHNOLOGY` | AI systems and models, software, devices and platforms | `TOOL` |
| `METHOD` | Study designs, research and analytic methods, assessment instruments and scores | `METHOD` |
| `GUIDELINE` | Laws, regulations, clinical guidelines, reporting standards and policies | `DOCUMENT` |
| `DATASET` | Datasets, databases, registries and benchmarks | `TOOL` |
| `CONCEPT` | Ideas, principles, and ethical, social or professional notions | `CONCEPT` |
| `PERSON` | Named individuals such as researchers, clinicians or public figures | `PERSON` |
| `ORGANIZATION` | Hospitals, universities, companies, agencies and professional bodies | `ORGANIZATION` |
| `PLACE` | Countries, regions, and care or education settings as places | `PLACE` |

Simple Graph Builder's `PROJECT`, `EVENT` and `TOPIC` have no med-lit refinement. Publication metadata (years, identifiers, licenses, journal and publisher names, generic words such as "article" or "study") is never an entity.

An entity has a canonical name, a type, a description, and aliases. Each alias is unique across the graph.

## Mentions and roles

A mention records that an article names an entity: the article, the page of its text, the verbatim span, the surrounding sentence, and a short description of how the article depicts the entity.

A mention may carry a **role**: the PICO/PCC element the entity plays in the study that article reports.

| Role | Meaning |
|---|---|
| `population` | Who was studied |
| `intervention` | What was done, given or evaluated (PICO) |
| `comparator` | What it was compared against (PICO) |
| `outcome` | What was measured as a result (PICO) |
| `concept` | The phenomenon examined (PCC) |
| `context` | The setting the study took place in |

Roles belong to mentions, not entities: type 2 diabetes can be the population of one study and an outcome of another, and ChatGPT the intervention in one trial and the comparator in the next. Mentions in background or discussion text carry no role.

## Relationships

A relationship is a directed edge `source —verb→ target` between two entities:

- **verb**: a free-form active verb in lower case, such as `evaluates`, `supports` or `limits`. Verbs are not standardized.
- **detail**: an optional note on how the relationship shows up, matching Simple Graph Builder's `detail`.
- **evidence**: one record per article page that asserts it, with a verbatim quote from that page. A relationship's strength is the number of distinct articles giving evidence for it.

## Name matching

Entities are matched by a name key, not by embeddings:

1. Unicode NFKC normalization, case folding, and unified dashes and quotes.
2. Hyphens, slashes and underscores become spaces, and punctuation other than `+` and `#` is dropped.
3. Plurals are folded per word: an all-caps acronym loses a trailing `s` (`LLMs` → `llm`), `-ies` becomes `-y`, and a trailing `s` is dropped from words longer than four letters unless they end in `ss`, `us`, `is` or `ys` (`sepsis` and `AIDS` are kept).

An entity merges automatically only on an equal name key, an alias, an acronym defined in the article ("large language models (LLMs)"), or an explicit link to an existing entity. Similar names are proposed as possible duplicates, within types that share a Simple Graph Builder parent, and a person or agent decides.

## Markdown export

Each entity page's front matter carries `entity_type` (the med-lit type), `sgb_type` (its Simple Graph Builder parent), `aliases`, and `ontology: med-lit/1`. Page files are named after the entity (`entities/<Name>.md`); the stable identifier is `entity_id` in the front matter. Relationships are listed as `- verb [Entity](link)` lines with their evidence, the same shape Simple Graph Builder writes into its entity notes.
