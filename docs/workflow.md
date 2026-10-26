# Running a review

## Projects

Each review is a **project**: one self-contained folder holding the review's wiki and, hidden inside it, everything else.

```
~/med-lit/LLMs in shared decision making/      ← the project, and the wiki
├── index.md
├── log.md
├── med-lit.settings.json                      ← this review's settings
├── entities/Shared decision making.md
├── sources/Guirgus 2026 - Assessing Artificial Intelligence in Patient Education.md
├── reports/2026-10-26 3f9c1a2b.md             ← the report answering a search's question
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

## Research modes

Ask a research question and `research_next` leads the review. It reads the project's settings and the search's stored state and returns the next step. Its `do` names the tools to call. A step marked `ask_researcher` is your decision: the agent shows you what the step lists and waits for your answer. Between steps it keeps you posted in a line or two.

| Mode | What it does | The report is written from |
|---|---|---|
| `basic` | Search, title triage, screening of titles and abstracts | The included articles' abstracts |
| `full` | The same, then full-text fetch and the evidence wiki | The full text: entity roles, relationships and pages the agent reads |
| `deep` | Full mode in rounds: after each round the evidence is judged, and a revised search adds new articles until it is enough | The full text of every round's included articles |

The checkpoints, in order:

1. **Mode and search settings.** You choose a mode, and change any setting: years, sources, records per source, preprints, languages, publication types, how many articles to screen (`research.max_screened`), the fetch limit, and for deep mode the rounds (`research.deep_rounds`) and new articles per round (`research.round_articles`).
2. **Search question.** You see the drafted PICO/PCC question as `validate_question` normalized it, with its sources, dates, warnings and repairs.
3. **Criteria and their reasons.** The agent drafts inclusion and exclusion criteria from the approved question, each with the reason it follows from the question. They get codes, I1, I2 … and E1, E2 …. You approve or change them.
4. **Screening on or stop** (basic and full). After each session of 20 you choose to screen the next 20 or stop. With `research.max_screened` set, screening goes on to that number without asking. If you stop, the report states how many articles were not screened. Deep mode screens each round's articles without asking.
5. **Uncertain articles.** You decide each one (`review_article`), or leave the rest undecided. Undecided articles are not part of the answer.

In a research mode every exclusion names the criterion it rests on: E1 when it meets exclusion criterion 1, I2 when it fails inclusion criterion 2. An exclusion without one is stored as uncertain. The report counts the reasons for exclusion.

The call that ends a screening session returns the review's next step itself (`next_step`, the same as `research_next`), so an agent that asks the researcher at once, as Hermes does, asks from the server's counts.

**Deep rounds.** Deep mode works like a bot's runs, one after another in one session. Round 1 is the approved search, screened, fetched and added to the wiki. Then the agent reads the included evidence (`next_report`) and records its judgment with `record_evaluation` (rules: `guide("evaluation")`):

- whether the evidence is **sufficient**: each main part of the question is answered, or more searching is unlikely to change the answer;
- the **gaps**, and which included studies are **adequate** (answer the question directly);
- when it is not sufficient and rounds are left, a **revised search** aimed at the gaps. The server validates it, refuses one that repeats an earlier round, and searches it into the same review with the question's own dates.

The new articles wait as candidates, their titles are triaged, and the best `research.round_articles` (at most 20) are kept, as a bot run keeps its cap. They are screened against the same criteria, fetched and added to the wiki, and the next round is judged. The review ends when the evidence is judged sufficient or at `research.deep_rounds`, which the report then says. Choosing deep mode with its round limit is your approval of these searches; each is named in the report's appendix.

**The report.** `next_report` gives the agent the question, the facts of the search and screening, and the included articles, in portions that fit one tool result. The agent writes the answer, sections on the evidence by theme, key findings each with a verbatim quote from the study's stored text, and the limitations the evidence shows. `record_report` refuses the report, saving nothing, when it cites an article that is not included or when a quote is not in the article's text. Otherwise it saves `reports/<date> <run>.md`, written for a reader who did not see the review:

- **In short**: the answer, then the agent's sections. Citations are numbered [1], [2] by first appearance.
- **Is the evidence enough?** (deep): the last round's judgment and what is still open.
- **How much to trust this**: the agent's limitations, then the limits from the review's record (abstracts only, studies beyond the fetch limit, unscreened or undecided articles, failed sources, the sample size of the search, the round limit, no risk-of-bias assessment).
- **How this review was done**: a few plain paragraphs from the record: sources, dates, how many articles were found, ranked, screened and included, the rounds, and what was read in full.
- **References**, with links and each study's uid.
- **Appendix**: the key findings with their quotations, the criteria with their reasons, the counts of exclusion reasons, and the search terms of every round.

The agent replies with the answer, the counts and the report's path. If the screening, the included articles or the mode change later, `research_next` asks for the report to be written again. Each report gets its own file, and earlier ones are kept (`reports/<date> <run>-2.md`, …), so a file in `reports/` is never overwritten. After stopping, screening goes on when you ask for the next session; after an automatic stop, when you raise `research.max_screened`.

A screened set and its report are not a completed systematic review: no risk-of-bias or certainty assessment is made, and the report says so.

## A study, step by step

You can also run each stage yourself. The server's instructions tell the agent to start each stage only when you ask, and to confirm the search question with you first.

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
> **Agent:** `set_screening_criteria`; when more than 20 articles wait, scores every title 0–3 (`next_triage_batch`, `record_triage_scores`); then screens the 20 best with `next_screening_batch` and `record_screening_decisions`, and stops to report. The next 20 follow in a later session, when you ask.
>
> **You:** Include the uncertain one about decision aids: it describes an LLM-drafted aid.
>
> **Agent:** `review_article`.
>
> **You:** Fetch the included articles and build the wiki.
>
> **Agent:** `fetch_articles`, then `wiki_tasks`, which hands each article and each batch of entity pages to a fresh subagent, then `export_wiki`.

Claude Code also exposes the guided prompts `/mcp__med-lit__research_question` (the whole review), `plan_search`, `screen_run` and `build_wiki`.

## Search

- **Question structure.** Groups within a component are ANDed, and a group's synonyms are ORed. Give each separately required facet (for example technology and task) its own group; put only true synonyms in a group; leave out ambiguous bare acronyms such as `LLM` unless you approve them. PCC context is for the setting only. Groups can suggest candidate MeSH headings, which are checked against NCBI before use.
- **Sources.** Unless you name sources, a search uses the defaults: PubMed, PMC and OpenAlex, plus Semantic Scholar and Scopus when their keys are set. Europe PMC is searched on its own, when you ask for it.
- **Date range.** Without a `from_date` filter or a `search.years` setting, a search covers only the last three years. `validate_question` states the effective start date, so ask for an earlier one if you need it. Dates are applied to the day in every source except Scopus, which searches whole years; its results are then trimmed to the exact dates by cover date.
- **Identifiers.** Every imported article has a DOI, PMID or PMCID: that is how one article is recognised across sources and searches, and how its full text is found. A record that arrives without one (some OpenAlex records, for example) is looked up in PubMed and then Crossref by its title; only the same title, or the same title plus a subtitle, published within a year, counts. Records still without an identifier are excluded and listed in the run's `skipped_no_identifier`; they are looked up again whenever a later search finds them. To include one anyway, ask for it by name ("add the skipped thesis on physiotherapy education"): the agent adds it with `add_skipped_articles`, unless its title matches an article already in the run, and it is then screened like any other.
- **Repeat searches.** A later search in the same project imports only articles new to the project, matched by DOI, PMID, PMCID or record id across sources. An identical abstract (at least 300 characters, ignoring case, spacing and punctuation) also marks one article: repository versions such as Zenodo or Figshare deposits each carry their own DOI, and an index copy (DOAJ) can sit beside the journal's record under a shorter title. The same matching keeps two copies found by one search from both being imported. Articles an earlier search already found are counted as `already_known` and keep that search's screening decisions and wiki work.
- **Scopus** returns abstracts and full author lists when your institution subscribes (the COMPLETE view); otherwise it falls back to titles and first authors only.
- **Failed sources.** If some sources fail while others succeed (for example PMC answering HTTP 500 while NCBI is degraded), the run completes with the working sources and lists the rest in `source_failures`. Later, `resume_search` with `retry_failed_sources=true` searches only the failed sources again and adds their new records to the same run. Those records then appear as pending in screening.

## Screening

- **Batches fit the client.** A batch of titles or abstracts stops when the whole tool result, as the client receives it, would pass about 18 KB (`MED_LIT_RESPONSE_BYTES`), so clients that cut long results (pi at 20 KiB) receive every abstract in full.
- **Triage, then 20 per session.** Screening goes 20 articles per session, best first. When more articles wait, the agent first scores each title alone from 0 (unrelated) to 3 (clearly meets the criteria), judging what the study is about rather than shared words; the search's relevance rank breaks ties. In a test on four reviews, where the same model then screened the abstracts, 17–20 of the top 20 by triage were included, against 6–20 by search rank alone; the largest gain was a qualitative question. Fetch waits until every article is screened.
- **Decisions** use titles and abstracts only. Include and exclude decisions need a verbatim quote from the record, and an uncertain decision needs a reason naming the criterion that cannot be judged and what the record leaves open. A decision that fails these checks is stored as `uncertain` and can be corrected once; uncertain articles wait for the researcher's own decision. Uncertain decisions recorded before 0.1.4 that give no specific reason are sent back to screening once.
- **Changing criteria** requires `replace=true`, starts a new revision, and re-screens every article. Earlier decisions stay in the history.

## Fetch

Fetch tries PMC full text (NCBI, then Europe PMC). For articles without a PMCID it asks [Unpaywall](https://unpaywall.org) for legal open-access copies of the DOI, preferring a PMC copy and otherwise extracting text from an open-access PDF. The abstract is the last resort, and abstract-only articles are always labelled as such. Articles are fetched in search-rank order, so a `fetch.limit` keeps the best-ranked ones. The source, license and version (published, accepted or submitted) of each full text are recorded. To retry abstract-only articles later, ask for a fetch with `retry_abstract_only`.

## Wiki

The wiki is built from paged article text of about 12,000 characters per page, without truncation.

- Each entity mention and relationship must be quoted verbatim from its page.
- Entities are matched lexically: case, plurals, spelling variants and acronyms defined in the text, such as "large language models (LLMs)". Only exact key or acronym matches merge automatically. Near matches are queued as possible duplicates for the agent or researcher to merge or keep distinct.
- Entity pages must cite their sources as `[uid]`.
- **Pages are updated, not rewritten.** Each page records the mentions it was written from. When later articles add evidence, the page is updated: the model gets the current page and only the new evidence, and returns just the sections that change; everything else stays as it was. New pages are written before updates. Evidence that only names an entity as a study's setting (for example *medical education* in an education study) does not reopen its page, and statements citing a withdrawn article must be removed.
- Statistics and survey software (SPSS, Stata, GraphPad Prism, REDCap…) is never an entity, and places or institutions become entities only when an article says something about them.
- Entities, roles and relationships follow the [ontology](wiki.md#entity-types-roles-and-relationships).
- Article pages are large, so `wiki_tasks` splits the work into self-contained tasks: one per article, then duplicate review, then batches of five entity pages. Each task carries the full rules of its kind (the same text as `guide`), and the plan names the tools it may use (`tools`), so a subagent gets the ontology and evidence rules whether or not it reads a guide. Clients that can delegate (Hermes subagents, Claude Code agents, pi) give each task to a fresh subagent, which keeps the main conversation short; clients that can scope a subagent's tools give it only those. Other clients work through the same tasks in order. A long step comes in portions: call `wiki_tasks` again for the rest.
- Each article is read up to `wiki.max_pages` pages. That limit holds for every tool that reads or records pages; a per-call `max_pages` can only ask for fewer. To read more of an article, change the setting.

To open and use the result, see [The wiki in Obsidian](wiki.md).

## Long and failed steps

Long steps are split into bounded calls. If a call reports `running` or `remaining > 0`, call it again with the same run ID. A failed stage is retried with the same tool and run ID.

## Tools

| Stage | Tools |
|---|---|
| Research | `research_next` (the next step from question to report), `next_report`, `record_report`, `record_evaluation` (deep mode) |
| Projects | `create_project`, `list_projects`, `open_project`, `project_settings` |
| Search | `validate_question`, `start_search`, `resume_search`, `add_skipped_articles` |
| Guidance | `guide` (rules for each stage, on demand) |
| Screening | `set_screening_criteria`, `next_triage_batch`, `record_triage_scores`, `next_screening_batch`, `record_screening_decisions`, `review_article` |
| Fetch | `fetch_articles` |
| Wiki | `wiki_tasks`, `next_wiki_article`, `get_article_page`, `record_extraction`, `find_entities`, `list_duplicate_candidates`, `resolve_duplicates`, `merge_entities`, `next_synthesis`, `record_synthesis`, `export_wiki` |
| Status | `list_runs`, `get_run_status`, `list_articles` |
| Bot runs | `bot_start`, `bot_next`, `bot_finish` (used by the scheduled jobs) |

Tools that work on a whole project take a `project` name, which can be left out while only one project exists. Tools that work on one search take its run ID.

`validate_question` stores the checked question and returns a `question_id`; `start_search` takes that id, so a search always runs exactly the question and sources the researcher approved.

Tool descriptions are kept to a line each, and the detailed rules for each stage come from `guide(topic)` when the agent needs them. Hermes, Claude Code and Claude Desktop all load MCP tools on demand (Tool Search), finding them by name; the server instructions list every tool by stage for that reason.

## Viewer (optional)

```bash
uvx --from med-lit-mcp med-lit-viewer --port 3000
```

The viewer lists the runs of every known project, with screening reasons and evidence, fetched text and each project's wiki pages, and lets researchers record manual screening decisions. It binds to `127.0.0.1`. For remote access, put it behind an authenticated reverse proxy or a private network such as Tailscale.
