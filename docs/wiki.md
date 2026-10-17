# The wiki

## Opening it in Obsidian

A project folder is plain Markdown with YAML front matter and relative links, so Obsidian can open it with no plugin: choose *Open folder as vault* and pick the project folder. Obsidian ignores the hidden `.med-lit` folder.

| File | Contents |
|---|---|
| `index.md` | Synthesized entities grouped by type, with a one-line summary each |
| `entities/<Name>.md` | One page per entity: summary, key aspects, synthesis, relationships with evidence, and source articles with the entity's role in each. Entities without a synthesis yet get a short page listing their mentions. |
| `sources/<Author Year - Title>.md` | One page per fetched article: bibliographic details, abstract, the entities it mentions and their roles, and a notice when only the abstract was available |
| `log.md` | Articles added to the wiki, newest first |

Page names are the names people read, so they appear as page titles in Obsidian. Each name is assigned once and stays stable; the entity or article id is kept in the front matter. Front matter becomes Obsidian properties (`entity_type`, `sgb_type`, `aliases`, `sources`, `version`, `stale` and so on), which you can search, sort and filter. `aliases` lets Obsidian resolve an acronym or spelling variant to the right page. Every relationship and citation is a link, so Obsidian's graph view connects entities to each other and to their source articles. Obsidian's graph draws links without labels, so relationship verbs appear on the pages rather than on the graph edges.

Things to know:

- **Don't edit generated pages.** Every export rewrites the pages it generated, so keep your own notes in separate files and link to the generated pages. Export only writes or removes files carrying `generator: med-lit-mcp` in their front matter. It never overwrites any other file and lists any it skipped under `not_overwritten`.
- **Exports rewrite only changed files,** so Git or a sync service sees real changes only.
- **Back up the whole folder, including `.med-lit/`.** Avoid opening the same project from two machines through a file-sync service while it is in use: the database inside is a live SQLite file.

## Entity types, roles and relationships

The wiki is a small knowledge graph. Its model follows the lightweight ontology of the [Simple Graph Builder](https://github.com/junhewk/simple-graph-builder) Obsidian plugin: a few fixed entity types, free-form relationship verbs, and a detail note on each relationship. That plugin's types are general-purpose, so med-lit-mcp uses medical types, each refining exactly one of the plugin's types. The full contract is in [ontology.md](ontology.md).

| Type | Covers | Plugin type |
|---|---|---|
| `CONDITION` | Diseases, disorders, symptoms, diagnoses | `CONCEPT` |
| `INTERVENTION` | Treatments, drugs, procedures, surgeries, therapies, programs | `METHOD` |
| `TECHNOLOGY` | AI systems and models, software, devices, platforms | `TOOL` |
| `METHOD` | Study designs, research and analytic methods, instruments, scores | `METHOD` |
| `GUIDELINE` | Laws, regulations, clinical guidelines, reporting standards, policies | `DOCUMENT` |
| `DATASET` | Datasets, databases, registries, benchmarks | `TOOL` |
| `CONCEPT` | Ideas, principles, ethical, social or professional notions | `CONCEPT` |
| `PERSON`, `ORGANIZATION`, `PLACE` | People; institutions; countries, regions, care settings | same name |

**Roles.** Each mention of an entity can record the PICO/PCC role it plays in that article's own study: `population`, `intervention`, `comparator`, `outcome`, `concept` or `context`. Roles belong to mentions rather than entities, because type 2 diabetes can be the population of one study and an outcome of another.

**Relationships** are directed, such as `ChatGPT —evaluates→ patient education materials`. Each has a free-form verb, an optional detail note, and a verbatim evidence quote from every article that asserts it. A relationship's strength is the number of articles supporting it.

## Simple Graph Builder

The ontology is designed so that the [Simple Graph Builder](https://github.com/junhewk/simple-graph-builder) plugin can import a project's graph into your own vault, with typed relationships and their evidence. Every export also writes the graph to `.med-lit/sgb-export.json` for that importer, in step with the pages ([format](ontology.md#graph-export)).

Until your version of the plugin imports med-lit projects, if you open a project inside a vault where that plugin runs, add the project folder to its *Analysis Exclusions*. Otherwise it would re-extract entities from these pages with its own LLM, creating a second, unsourced copy of the graph.
