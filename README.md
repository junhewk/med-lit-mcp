# med-lit-mcp

med-lit-mcp is an MCP server for medical literature reviews. It takes a review through four deliberate stages, **search → screening → fetch → wiki**, and ends with an evidence-linked wiki you can open in Obsidian.

Your MCP client's model does the reading and writing (screening decisions, entity extraction, syntheses). med-lit-mcp searches the databases, stores everything, checks the model's work against the source text, and exports the wiki. Every screening decision, extracted mention and synthesis citation must be a verbatim quote or a reference to the stored text.

It works with Hermes, Claude Code and Claude Desktop on Linux and macOS. It needs no embedding endpoint, no model API key and no build step. The only required setting is `NCBI_EMAIL`.

With Hermes, a review can also be kept up to date by a **bot**: a scheduled job that re-runs a search you have tried, screens what is new and adds it to the wiki. See [Bots](#bots-keep-a-review-up-to-date-hermes).

## Install

med-lit-mcp is on [PyPI](https://pypi.org/project/med-lit-mcp/) and runs through [`uv`](https://docs.astral.sh/uv/) ([install uv](https://docs.astral.sh/uv/getting-started/installation/) first if you don't have it). Claude Desktop users can skip the terminal entirely with the extension below.

### Hermes and Claude Code: `setup`

```bash
uvx med-lit-mcp setup
```

Setup asks for your contact email (needed for PubMed and Unpaywall), a folder for your reviews (default `~/med-lit`), optionally API keys, and optionally your default [review settings](#review-settings). Each key is typed with hidden input, tested against its provider right away, and saved in `~/.config/med-lit-mcp/keys.json`, a file only you can read. Setup then registers med-lit with every Hermes and Claude Code it finds on the machine. Keys never go into the chat or into the clients' configuration files.

Manage keys later without rerunning setup:

```bash
uvx med-lit-mcp keys              # which keys are set, and from where
uvx med-lit-mcp keys set scopus   # add or replace one (ncbi, openalex, semantic-scholar, scopus, scopus-insttoken)
uvx med-lit-mcp keys test         # check every key against its provider
uvx med-lit-mcp keys remove openalex
```

For unattended use: `uvx med-lit-mcp setup --email you@example.org --yes` accepts the defaults, skips the key questions and registers with every client found; `--client hermes` or `--client claude-code` limits it to one.

To try the latest unreleased code, replace `med-lit-mcp` with `--from git+https://github.com/junhewk/med-lit-mcp med-lit-mcp` in any command.

### Claude Desktop (no command line)

1. Download `med-lit-<version>.mcpb` from the [latest release](https://github.com/junhewk/med-lit-mcp/releases/latest).
2. In Claude Desktop open **Settings → Extensions → Advanced settings → Install Extension…** and choose the file.
3. Fill in the form: your email address (needed for PubMed and Unpaywall), optionally a folder for your reviews (default `~/med-lit`) and any API keys. Claude Desktop stores the keys encrypted.
4. **Switch the extension on.** Its switch shows "Disabled" until you turn it on.
5. In a chat, ask Claude to start a new review.

Claude Desktop installs Python and the server's dependencies itself. To change a setting or add a key later, open the extension in **Settings → Extensions**.

### Hermes or Claude Code, registered by hand

`setup` runs these for you. To register by hand, first save your settings without registering (`uvx med-lit-mcp setup --no-register`), then:

```bash
hermes mcp add med-lit --command uvx --connect-timeout 180 --args med-lit-mcp
claude mcp add med-lit --scope user -- uvx med-lit-mcp
```

In Hermes, `--env` must come before `--args`, because `--args` takes everything after it. Hermes passes an MCP server only basics such as `PATH` and `HOME` plus the variables given with `--env`; med-lit reads its settings and keys from `~/.config/med-lit-mcp/`, so none need to be passed.

### Claude Desktop (manual configuration)

Instead of the extension, you can add the server to `claude_desktop_config.json`: `~/Library/Application Support/Claude/` on macOS, `~/.config/Claude/` on Linux. Desktop starts servers with a minimal `PATH`, so use the absolute path printed by `which uvx`.

```json
{
  "mcpServers": {
    "med-lit": {
      "command": "/Users/you/.local/bin/uvx",
      "args": ["med-lit-mcp"],
      "env": {"NCBI_EMAIL": "you@example.org"}
    }
  }
}
```

### Updating

`uvx` caches the installed version. To pick up a new release, run `uvx med-lit-mcp@latest --check` once, then restart the client (in Hermes, `/reload-mcp`). Bots use the same cached version from their next run.

## Settings

### Email, folders and keys

`setup` stores your email and reviews folder in `~/.config/med-lit-mcp/config.json` and your keys in `keys.json` next to it. Environment variables set in an MCP client's configuration take precedence over both.

| Variable | Purpose |
|---|---|
| `NCBI_EMAIL` | **Required** for PubMed/PMC search and for fetching. Also sent to Unpaywall, which requires a contact email. Use a real address. |
| `NCBI_API_KEY` | Optional; raises NCBI rate limits. |
| `SEMANTIC_SCHOLAR_API_KEY` (or `S2_API_KEY`) | Adds Semantic Scholar to the default sources. Without a key it shares a public rate limit that usually answers HTTP 429, so it is skipped unless requested. [Request a free key](https://www.semanticscholar.org/product/api#api-key-form). |
| `OPENALEX_API_KEY` | Optional but recommended free [OpenAlex key](https://openalex.org/settings/api). OpenAlex meters keyless use and throttles keyless searches when it is busy. |
| `SCOPUS_API_KEY`, `SCOPUS_INSTTOKEN` | Adds Scopus to the default sources. Elsevier keys usually work only from an institution's network; the institutional token lifts that. `keys test scopus` tells you which applies. |
| `MED_LIT_PROJECTS_DIR` | Where new projects are created when no path is given. Default: `~/med-lit`. |
| `MED_LIT_CONFIG_DIR` | Where `setup` keeps `config.json`, `keys.json` and `defaults.json`. Default: `$XDG_CONFIG_HOME/med-lit-mcp` or `~/.config/med-lit-mcp`. |
| `MED_LIT_STATE_DIR` | Where the list of known projects is kept. Default: `$XDG_DATA_HOME/med-lit-mcp` or `~/.local/share/med-lit-mcp`. |
| `MED_LIT_SCOPE` | Set to `bot` by `setup bot` for the `medlitbot` Hermes profile: that server sees only bot projects and cannot create projects or decide uncertain articles. |
| `MED_LIT_STAGES` | Optional, for advanced users. Exposes only the tools up to a stage: `search`, `screening`, `fetch` or `wiki` (default: all). For example, `fetch` hides the 11 wiki tools and the 3 bot tools. Clients that load tools on demand rarely need this; it helps with clients that load every tool up front. Project, status and guide tools are always available. |

### Review settings

Each review keeps its own settings in `med-lit.settings.json`, a visible file at the top of the project folder. The agent shows them when it presents the search question, and changes them with `project_settings` when you ask ("fetch only the top 30", "include preprints"). You can also edit the file yourself.

| Setting | Default | Meaning |
|---|---|---|
| `search.sources` | `null` | Sources to search; `null` means PubMed, PMC and OpenAlex, plus Semantic Scholar and Scopus when their keys are set. Europe PMC is searched on its own. |
| `search.per_source` | 20 | Records requested from each source (1–200). |
| `search.years` | `null` | Publication years: `"2020-"`, `"2010-2020"` or `"all"`. `null` means the last three years. Dates written into the question itself take precedence. |
| `search.preprint_allow` | `false` | Keep preprints (medRxiv, bioRxiv, arXiv, Research Square, SSRN, …). When `false` each source's query excludes them where it can (PubMed, PMC, OpenAlex, Europe PMC); elsewhere they are recognised by venue, DOI and type, dropped, and counted in the run's `skipped_preprints`. |
| `search.languages`, `search.publication_types` | `[]` | Filters applied when the question has none, for example `["english"]` or `["review"]`. |
| `fetch.limit` | `null` | Most included articles to fetch per search. Articles are fetched in search-rank order; those beyond the limit are marked `skipped`. |
| `fetch.mode` | `"full_text"` | `"abstract_only"` skips PMC and Unpaywall. |
| `wiki.max_pages` | 3 | Pages (about 12,000 characters each) read from each article for the wiki. |
| `wiki.min_sources` | 2 | Source articles an entity needs before it gets a synthesis page; others stay as stubs. |
| `wiki.tasks_per_conversation` | 5 | For clients without subagents (Claude Desktop): wiki tasks done in one conversation before the agent asks you to continue in a new one. |

A new project starts from a copy of your defaults, so changing the defaults later never alters existing reviews. Set the defaults during `setup`, or edit `~/.config/med-lit-mcp/defaults.json`, which holds one set for normal reviews and one for [bots](#bots-keep-a-review-up-to-date-hermes):

```json
{
  "interactive": {"search": {"years": "2015-", "per_source": 40}, "fetch": {"limit": 30}},
  "bot": {"bot": {"max_new_articles": 5}}
}
```

Settings apply in this order, later ones winning: built-in defaults, your defaults, the project's file, and a value given for a single call (for example `limit_per_source` in `start_search`).

## Projects

Each review is a **project**: one self-contained folder holding the review's wiki and, hidden inside it, everything else.

```
~/med-lit/LLMs in shared decision making/      ← the project, and the wiki
├── index.md
├── log.md
├── med-lit.settings.json                      ← this review's settings
├── entities/Shared decision making.md
├── sources/Guirgus 2026 - Assessing Artificial Intelligence in Patient Education.md
├── updates/2026-09-30.md                      ← bot projects only: one report per run
└── .med-lit/                                  ← hidden working data
    ├── project.json                           ← name, id, format version
    ├── med-lit.sqlite3                        ← this review's articles and knowledge graph
    ├── bot.json                               ← bot projects only: schedule, versions, run history
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
- **Sources.** Unless you name sources, a search uses the defaults: PubMed, PMC and OpenAlex, plus Semantic Scholar and Scopus when their keys are set. Europe PMC is searched on its own, when you ask for it.
- **Date range.** Without a `from_date` filter or a `search.years` setting, a search covers only the last three years. `validate_question` states the effective start date, so ask for an earlier one if you need it. Dates are applied to the day in every source except Scopus, which searches whole years; its results are then trimmed to the exact dates by cover date.
- **Repeat searches.** A later search in the same project imports only articles new to the project, matched by DOI, PMID, PMCID or record id across sources. Articles an earlier search already found are counted as `already_known` and keep that search's screening decisions and wiki work.
- **Scopus** returns abstracts and full author lists when your institution subscribes (the COMPLETE view); otherwise it falls back to titles and first authors only.
- **Screening** uses titles and abstracts only. Include and exclude decisions need a verbatim quote from the record. A decision whose quote cannot be verified is stored as `uncertain`, and uncertain articles wait for the researcher's own decision.
- **Changing criteria** requires `replace=true`, starts a new revision, and re-screens every article. Earlier decisions stay in the history.
- **Fetch** tries PMC full text (NCBI, then Europe PMC). For articles without a PMCID it asks [Unpaywall](https://unpaywall.org) for legal open-access copies of the DOI, preferring a PMC copy and otherwise extracting text from an open-access PDF. The abstract is the last resort, and abstract-only articles are always labelled as such. Articles are fetched in search-rank order, so a `fetch.limit` keeps the best-ranked ones. The source, license and version (published, accepted or submitted) of each full text are recorded. To retry abstract-only articles later, ask for a fetch with `retry_abstract_only`.
- **The wiki** is built from paged article text of about 12,000 characters per page, without truncation.
  - Each entity mention and relationship must be quoted verbatim from its page.
  - Entities are matched lexically: case, plurals, spelling variants and acronyms defined in the text, such as "large language models (LLMs)". Only exact key or acronym matches merge automatically. Near matches are queued as possible duplicates for the agent or researcher to merge or keep distinct.
  - Syntheses must cite their sources as `[uid]`.
  - Entities, roles and relationships follow the ontology described [below](#entity-types-roles-and-relationships).
  - Article pages are large, so `wiki_tasks` splits the work into self-contained tasks: one per article, then duplicate review, then batches of five entity pages. Clients that can delegate (Hermes subagents, Claude Code agents) give each task to a fresh subagent, which keeps the main conversation short. Other clients work through the same tasks in order.

Long steps are split into bounded calls. If a call reports `running` or `remaining > 0`, call it again with the same run ID. A failed stage is retried with the same tool and run ID.

If some sources fail while others succeed (for example PMC answering HTTP 500 while NCBI is degraded), the run completes with the working sources and lists the rest in `source_failures`. Later, `resume_search` with `retry_failed_sources=true` searches only the failed sources again and adds their new records to the same run. Those records then appear as pending in screening.

## Bots: keep a review up to date (Hermes)

A **bot project** re-runs a search you have already tried, on a schedule, and adds what is new: it screens new articles, fetches the included ones, extracts them into the wiki and rewrites the entity pages their evidence changes. Bots run as Hermes scheduled jobs; Claude Code and Claude Desktop have no scheduler for them.

**Before you start,** run a normal review in Hermes at least up to screening criteria (search, then "Screen it. Include … Exclude …"). A bot copies that search's question and criteria, so you begin from something you have seen work. Then:

```bash
uvx med-lit-mcp setup bot
```

Setup lists the searches that have screening criteria and asks which one to keep running, a name, daily or weekly and at what time, and the per-run limits. It then:
- creates the bot project, which starts empty and holds only what the bot finds from now on;
- creates the Hermes profile `medlitbot` once, shared by all bots. It starts as a copy of your current Hermes profile (model included), but its scheduled runs get only the med-lit tools and subagents, and its med-lit server sees only bot projects;
- adds one scheduled job per bot. Several bots are fine; setup suggests start times 30 minutes apart so they do not share your model at once.

To run a bot now instead of waiting for its time, answer yes to setup's last question, or run `hermes -p medlitbot cron run <job id>` (the job id is printed by setup and listed by `hermes -p medlitbot cron list`). Either way the run happens in that terminal and takes as long as a scheduled run, so keep the terminal open until it prints its result.

**Each run:**
- searches articles published in the look-back window (90 days by default, open-ended because journals date issues ahead) and takes in only articles new to the project: at most `max_new_articles`, best-ranked first. Articles over the cap are not marked as seen, so later runs pick them up while they are still in the window;
- screens them against the frozen criteria, fetches and extracts the included ones, and writes or rewrites at most `max_syntheses` entity pages (those with at least `wiki.min_sources` source articles);
- finishes all the work its caps allow, however long it takes. Two runs of the same bot never overlap; if a run is interrupted, the next one continues its unfinished work;
- writes a report to `updates/<date>.md` in the project folder and replies with the same text, which Hermes saves under `~/.hermes/profiles/medlitbot/cron/output/<job id>/`. A run that found nothing stays silent and writes no report.

**Choosing `max_new_articles`.** A run's length follows from its cap, not from the window. With a local model, a test bot took about 45 minutes for 5 new articles (3 included, 9 pages extracted, 9 entity pages written) and about 1 hour 50 minutes for 20 (15 included, 35 pages). A new bot's first window usually holds a backlog: 129 matching articles in the test's 90 days. The cap works through it a run at a time, best-ranked first, and the report lists how many are waiting. A small cap keeps each run short; the few lowest-ranked articles may leave the 90-day window before a run reaches them.

**Uncertain articles wait for you.** The bot never decides them; the report lists them with the bot's reasons. Open the bot project in a normal chat ("show the uncertain articles in SDM watch") and decide them with `review_article`; the next run fetches and adds the ones you include.

**Changing a bot:** `uvx med-lit-mcp setup bot --edit "SDM watch"` opens a menu:

| Change | What happens |
|---|---|
| Schedule; caps, look-back, records per source | Applies from the next run. |
| Screening criteria (opens your editor) | A new criteria revision. The next runs re-screen everything collected; articles that become excludes are withdrawn from the wiki and the entity pages that cited them are rewritten. |
| Search question (copied from another search, or edited) | A new question version. The next run searches again from the bot's start date with it; known articles are skipped. |
| Pause or resume | Pauses or resumes the Hermes job. |
| Archive | Removes the schedule. The folder, wiki, reports and history stay; delete the folder yourself if you no longer want it. |

Every question and criteria version is kept in `.med-lit/bot.json`. A normal chat can read a bot project but not change its question, criteria or settings.

| Bot setting | Default | Meaning |
|---|---|---|
| `bot.lookback_days` | 90 | Publication-date window of each run. |
| `bot.max_new_articles` | 20 | New articles taken in per run. |
| `bot.max_syntheses` | 15 | Entity pages written or rewritten per run. |
| `search.per_source` | 100 | Records requested per source. A bot sees new articles only among these, so the report warns when a source had more matches. |

Set your own defaults for new bots under `"bot"` in `defaults.json` (see [Review settings](#review-settings)).

Notes:
- The bot uses the `medlitbot` profile's model; change it with `hermes -p medlitbot model`. Jobs fire while the Hermes gateway runs (`hermes -p medlitbot cron status`); list past runs with `hermes -p medlitbot cron runs <job id>`.
- An [OpenAlex key](https://openalex.org/settings/api) is worth adding for bots: without one, OpenAlex may refuse searches when it is busy.
- Europe PMC cannot be searched by a bot.

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
| Projects | `create_project`, `list_projects`, `open_project`, `project_settings` |
| Search | `validate_question`, `start_search`, `resume_search` |
| Guidance | `guide` (rules for each stage, on demand) |
| Screening | `set_screening_criteria`, `next_screening_batch`, `record_screening_decisions`, `review_article` |
| Fetch | `fetch_articles` |
| Wiki | `wiki_tasks`, `next_wiki_article`, `get_article_page`, `record_extraction`, `find_entities`, `list_duplicate_candidates`, `resolve_duplicates`, `merge_entities`, `next_synthesis`, `record_synthesis`, `export_wiki` |
| Status | `list_runs`, `get_run_status`, `list_articles` |
| Bot runs | `bot_start`, `bot_next`, `bot_finish` (used by the scheduled jobs) |

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
uvx --from med-lit-mcp med-lit-viewer --port 3000
```

The viewer lists the runs of every known project, with screening reasons and evidence, fetched text and each project's wiki pages, and lets researchers record manual screening decisions. It binds to `127.0.0.1`. For remote access, put it behind an authenticated reverse proxy or a private network such as Tailscale.

## Development

```bash
uv sync
uv run pytest
uvx ruff check src
npx @modelcontextprotocol/inspector uv run med-lit-mcp
```

To run a server from a checkout, use `uv run --directory /path/to/med-lit-mcp med-lit-mcp` as the MCP command. Unlike `uvx --from <path>`, it always runs the checkout's current code. `setup --dev /path/to/med-lit-mcp` and `setup bot --dev /path/to/med-lit-mcp` register that command for you (run them with `uv run med-lit-mcp …` from the checkout).

Windows is not supported (run locks use `fcntl`).

The database search engine in `src/med_lit_mcp/medsearch/` (query compilation, MeSH resolution, PubMed/PMC/OpenAlex/Semantic Scholar/Scopus clients, deduplication and ranking) was merged from [hermes-medical-search](https://github.com/junhewk/hermes-medical-search) 0.2.1 (MIT) and is maintained here. For debugging it can run on its own: `uv run python -m med_lit_mcp.medsearch --help`.

## License

med-lit-mcp is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE). You may use, modify and share it for any noncommercial purpose, including research, teaching, and use by educational institutions, public research organizations and public health organizations. Commercial use needs a separate license; open an issue to ask. [NOTICE](NOTICE) records the MIT-licensed origin of the search engine.

