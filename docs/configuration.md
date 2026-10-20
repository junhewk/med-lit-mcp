# Configuration

## Email, folders and keys

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
| `MED_LIT_RESPONSE_BYTES` | Optional. Most bytes in one batch-type tool result (screening, triage, wiki tasks), measured as the client receives it: JSON, UTF-8. Default 18,432, under pi's 20 KiB limit; Hermes allows 50,000 characters. A single screening record larger than this is sent with its abstract cut at a word boundary and marked. |
| `MED_LIT_STAGES` | Optional, for advanced users. Exposes only the tools up to a stage: `search`, `screening`, `fetch` or `wiki` (default: all). For example, `fetch` hides the 11 wiki tools and the 3 bot tools. Clients that load tools on demand rarely need this; it helps with clients that load every tool up front. Project, status and guide tools are always available. |

## Review settings

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
| `wiki.max_pages` | 3 | Pages (about 12,000 characters each) read from each article for the wiki. The reader, `get_article_page` and `record_extraction` all keep to it; a per-call `max_pages` can only ask for fewer. |
| `wiki.min_sources` | 2 | Source articles an entity needs before it gets a synthesis page; others stay as stubs. |
| `wiki.tasks_per_conversation` | 5 | For clients without subagents (Claude Desktop): wiki tasks done in one conversation before the agent asks you to continue in a new one. |

Bot projects have a few more settings; see [Bots](bots.md#bot-settings).

## Defaults for new projects

A new project starts from a copy of your defaults, so changing the defaults later never alters existing reviews. Set the defaults during `setup`, or edit `~/.config/med-lit-mcp/defaults.json`, which holds one set for normal reviews and one for [bots](bots.md):

```json
{
  "interactive": {"search": {"years": "2015-", "per_source": 40}, "fetch": {"limit": 30}},
  "bot": {"bot": {"max_new_articles": 5}}
}
```

Settings apply in this order, later ones winning: built-in defaults, your defaults, the project's file, and a value given for a single call (for example `limit_per_source` in `start_search`).
