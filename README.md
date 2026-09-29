# med-lit-mcp

med-lit-mcp is an MCP server for medical literature reviews. It takes a review through four deliberate stages, **search → screening → fetch → wiki**, and ends with an evidence-linked wiki you can open in Obsidian.

Your MCP client's model does the reading and writing (screening decisions, entity extraction, syntheses). med-lit-mcp searches the databases, stores everything, checks the model's work against the source text, and exports the wiki. Every screening decision, extracted mention and synthesis citation must be a verbatim quote or a reference to the stored text.

It works with Hermes, Claude Code and Claude Desktop on Linux and macOS. It needs no embedding endpoint, no model API key and no build step. The only required setting is `NCBI_EMAIL`.

## Install

med-lit-mcp runs through [`uv`](https://docs.astral.sh/uv/). `uvx` fetches the server and its dependencies on first start. The first start takes a while, so pre-warm the cache and check your settings once:

```bash
NCBI_EMAIL=you@example.org uvx --from git+https://github.com/junhewk/med-lit-mcp med-lit-mcp --check
```

### Hermes

```bash
hermes mcp add med-lit --command uvx \
  --env NCBI_EMAIL=you@example.org \
  --connect-timeout 180 \
  --args --from git+https://github.com/junhewk/med-lit-mcp med-lit-mcp
hermes mcp test med-lit
```

`--env` must come before `--args`, because `--args` takes everything after it. Hermes passes an MCP server only the variables given with `--env` (plus basics such as `PATH` and `HOME`), so add optional keys there too, for example `--env NCBI_EMAIL=… NCBI_API_KEY=…`.

### Claude Code

```bash
claude mcp add med-lit --scope user -e NCBI_EMAIL=you@example.org -- \
  uvx --from git+https://github.com/junhewk/med-lit-mcp med-lit-mcp
```

### Claude Desktop

Add the server to `claude_desktop_config.json`: `~/Library/Application Support/Claude/` on macOS, `~/.config/Claude/` on Linux. Desktop starts servers with a minimal `PATH`, so use the absolute path printed by `which uvx`.

```json
{
  "mcpServers": {
    "med-lit": {
      "command": "/Users/you/.local/bin/uvx",
      "args": ["--from", "git+https://github.com/junhewk/med-lit-mcp", "med-lit-mcp"],
      "env": {"NCBI_EMAIL": "you@example.org"}
    }
  }
}
```

### Updating

`uvx` caches the installed version. To pick up a new version, run `uvx --refresh --from git+https://github.com/junhewk/med-lit-mcp med-lit-mcp --check` once, then restart the client.

## Settings

All settings are environment variables passed in the MCP client configuration.

| Variable | Purpose |
|---|---|
| `NCBI_EMAIL` | **Required** for PubMed/PMC search and for fetching. Also sent to Unpaywall, which requires a contact email. Use a real address. |
| `NCBI_API_KEY` | Optional; raises NCBI rate limits. |
| `SEMANTIC_SCHOLAR_API_KEY` (or `S2_API_KEY`) | Enables Semantic Scholar by default. Without a key it shares a public rate limit that usually answers HTTP 429, so it is skipped unless requested. [Request a free key](https://www.semanticscholar.org/product/api#api-key-form). |
| `OPENALEX_API_KEY` | Optional OpenAlex key. |
| `MED_LIT_PROJECTS_DIR` | Where new projects are created when no path is given. Default: `~/med-lit`. |
| `MED_LIT_STATE_DIR` | Where the list of known projects is kept. Default: `$XDG_DATA_HOME/med-lit-mcp` or `~/.local/share/med-lit-mcp`. |
| `MED_LIT_STAGES` | Optional, for advanced users. Exposes only the tools up to a stage: `search`, `screening`, `fetch` or `wiki` (default: all). For example, `fetch` hides the 11 wiki tools. Clients that load tools on demand rarely need this; it helps with clients that load every tool up front. Project, status and guide tools are always available. |

## Projects

Each review is a **project**: one self-contained folder holding the review's wiki and, hidden inside it, everything else.

```
~/med-lit/LLMs in shared decision making/      ← the project, and the wiki
├── index.md
├── log.md
├── entities/Shared decision making.md
├── sources/Guirgus 2026 - Assessing Artificial Intelligence in Patient Education.md
└── .med-lit/                                  ← hidden working data
    ├── project.json                           ← name, id, format version
    ├── med-lit.sqlite3                        ← this review's articles and knowledge graph
    └── runs/<run-id>/                         ← searches, screening decisions, fetched text
```

- **One topic, one project.** Unrelated reviews never share entities or syntheses. One project can hold several searches, for example an update search a year later.
- **The folder is the unit.** Move, copy, archive or back it up as a whole. If you move it, ask the agent to `open_project` the new location.
- **Where it lives** is up to you: the default is `~/med-lit/<name>`, or give a path when creating the project.

## Run a study

Talk to your agent. The server's instructions tell it to start each stage only when you ask, and to confirm the search question with you first.

> **You:** Start a new review on how large language models are used in shared decision making in healthcare.
>
> **Agent:** `create_project`, reads `guide("question")`, drafts a PCC question, calls `validate_question`, and shows it to you.
>
> **You:** Looks good, run it.
>
> **Agent:** `start_search` with the approved `question_id`, which returns a run ID and candidate counts.
>
> **You:** Screen it. Include studies of LLMs supporting patient–clinician decisions; exclude purely technical benchmarks.
>
> **Agent:** `set_screening_criteria`, then repeats `next_screening_batch` and `record_screening_decisions`.
>
> **You:** Include the uncertain one about decision aids: it describes an LLM-drafted aid.
>
> **Agent:** `review_article`.
>
> **You:** Fetch the included articles and build the wiki.
>
> **Agent:** `fetch_articles`, then `wiki_tasks`, which hands each article and each batch of entity pages to a fresh subagent, then `export_wiki`.

Claude Code also exposes the guided prompts `/mcp__med-lit__plan_search`, `screen_run` and `build_wiki`.

How the stages work:
- **Question structure.** Groups within a component are ANDed, and a group's synonyms are ORed. Give each separately required facet (for example technology and task) its own group; put only true synonyms in a group; leave out ambiguous bare acronyms such as `LLM` unless you approve them. PCC context is for the setting only. Groups can suggest candidate MeSH headings, which are checked against NCBI before use.
- **Date range.** Without a `from_date` filter, a search covers only the last three years. `validate_question` states the effective start date, so ask for an earlier one if you need it.
- **Screening** uses titles and abstracts only. Include and exclude decisions need a verbatim quote from the record. A decision whose quote cannot be verified is stored as `uncertain`, and uncertain articles wait for the researcher's own decision.
- **Changing criteria** requires `replace=true`, starts a new revision, and re-screens every article. Earlier decisions stay in the history.
- **Fetch** tries PMC full text (NCBI, then Europe PMC). For articles without a PMCID it asks [Unpaywall](https://unpaywall.org) for legal open-access copies of the DOI, preferring a PMC copy and otherwise extracting text from an open-access PDF. The abstract is the last resort, and abstract-only articles are always labelled as such. The source, license and version (published, accepted or submitted) of each full text are recorded. To retry abstract-only articles later, ask for a fetch with `retry_abstract_only`.
- **The wiki** is built from paged article text of about 12,000 characters per page, without truncation.
  - Each entity mention and relationship must be quoted verbatim from its page.
  - Entities are matched lexically: case, plurals, spelling variants and acronyms defined in the text, such as "large language models (LLMs)". Only exact key or acronym matches merge automatically. Near matches are queued as possible duplicates for the agent or researcher to merge or keep distinct.
  - Syntheses must cite their sources as `[uid]`.
  - Entities, roles and relationships follow the ontology described [below](#entity-types-roles-and-relationships).
  - Article pages are large, so `wiki_tasks` splits the work into self-contained tasks: one per article, then duplicate review, then batches of five entity pages. Clients that can delegate (Hermes subagents, Claude Code agents) give each task to a fresh subagent, which keeps the main conversation short. Other clients work through the same tasks in order.

Long steps are split into bounded calls. If a call reports `running` or `remaining > 0`, call it again with the same run ID. A failed stage is retried with the same tool and run ID.

If some sources fail while others succeed (for example PMC answering HTTP 500 while NCBI is degraded), the run completes with the working sources and lists the rest in `source_failures`. Later, `resume_search` with `retry_failed_sources=true` searches only the failed sources again and adds their new records to the same run. Those records then appear as pending in screening.

## Entity types, roles and relationships

The wiki is a small knowledge graph. Its model follows the lightweight ontology of the [Simple Graph Builder](https://github.com/junhewk/simple-graph-builder) Obsidian plugin: a few fixed entity types, free-form relationship verbs, and a detail note on each relationship. That plugin's types are general-purpose, so med-lit-mcp uses medical types, each refining exactly one of the plugin's types. The full contract is in [docs/ontology.md](docs/ontology.md).

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

## Tools

| Stage | Tools |
|---|---|
| Projects | `create_project`, `list_projects`, `open_project` |
| Search | `validate_question`, `start_search`, `resume_search` |
| Guidance | `guide` (rules for each stage, on demand) |
| Screening | `set_screening_criteria`, `next_screening_batch`, `record_screening_decisions`, `review_article` |
| Fetch | `fetch_articles` |
| Wiki | `wiki_tasks`, `next_wiki_article`, `get_article_page`, `record_extraction`, `find_entities`, `list_duplicate_candidates`, `resolve_duplicates`, `merge_entities`, `next_synthesis`, `record_synthesis`, `export_wiki` |
| Status | `list_runs`, `get_run_status`, `list_articles` |

Tools that work on a whole project take a `project` name, which can be left out while only one project exists. Tools that work on one search take its run ID.

`validate_question` stores the checked question and returns a `question_id`; `start_search` takes that id, so a search always runs exactly the question and sources the researcher approved.

Tool descriptions are kept to a line each, and the detailed rules for each stage come from `guide(topic)` when the agent needs them. Hermes, Claude Code and Claude Desktop all load MCP tools on demand (Tool Search), finding them by name; the server instructions list every tool by stage for that reason.

## The wiki in Obsidian

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

**Simple Graph Builder.** The ontology is designed so that a future version of the [Simple Graph Builder](https://github.com/junhewk/simple-graph-builder) plugin can import a project's graph into your own vault, with typed relationships and their evidence. Until then, if you open a project inside a vault where that plugin runs, add the project folder to its *Analysis Exclusions*. Otherwise it would re-extract entities from these pages with its own LLM, creating a second, unsourced copy of the graph.

## Viewer (optional)

```bash
uvx --from git+https://github.com/junhewk/med-lit-mcp med-lit-viewer --port 3000
```

The viewer lists the runs of every known project, with screening reasons and evidence, fetched text and each project's wiki pages, and lets researchers record manual screening decisions. It binds to `127.0.0.1`. For remote access, put it behind an authenticated reverse proxy or a private network such as Tailscale.

## Development

```bash
uv sync
uv run pytest
uvx ruff check src
npx @modelcontextprotocol/inspector uv run med-lit-mcp
```

To run a server from a checkout, use `uv run --directory /path/to/med-lit-mcp med-lit-mcp` as the MCP command. Unlike `uvx --from <path>`, it always runs the checkout's current code.

Windows is not supported (run locks use `fcntl`).

The database search engine in `src/med_lit_mcp/medsearch/` (query compilation, MeSH resolution, PubMed/PMC/OpenAlex/Semantic Scholar/Scopus clients, deduplication and ranking) was merged from [hermes-medical-search](https://github.com/junhewk/hermes-medical-search) 0.2.1 (MIT) and is maintained here. For debugging it can run on its own: `uv run python -m med_lit_mcp.medsearch --help`.

## License

med-lit-mcp is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE). You may use, modify and share it for any noncommercial purpose, including research, teaching, and use by educational institutions, public research organizations and public health organizations. Commercial use needs a separate license; open an issue to ask. [NOTICE](NOTICE) records the MIT-licensed origin of the search engine.

