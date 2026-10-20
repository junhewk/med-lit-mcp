# med-lit-mcp

[![M8ven Score](https://m8ven.ai/badge/mcp/junhewk/med-lit-mcp)](https://m8ven.ai/mcp/junhewk/med-lit-mcp?s=readme)
[![med-lit-mcp MCP server – quality and maintenance score on Glama](https://glama.ai/mcp/servers/junhewk/med-lit-mcp/badges/score.svg)](https://glama.ai/mcp/servers/junhewk/med-lit-mcp)
[![PyPI](https://img.shields.io/pypi/v/med-lit-mcp)](https://pypi.org/project/med-lit-mcp/)
[![Python](https://img.shields.io/badge/python-%3E%3D3.11-blue?logo=python)](https://pypi.org/project/med-lit-mcp/)
[![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial-blue)](https://github.com/junhewk/med-lit-mcp/blob/main/LICENSE)
[![Publish](https://github.com/junhewk/med-lit-mcp/actions/workflows/publish.yml/badge.svg)](https://github.com/junhewk/med-lit-mcp/actions/workflows/publish.yml)

med-lit-mcp is an MCP server for medical literature reviews. It takes a review through four deliberate stages, **search → screening → fetch → wiki**, and ends with an evidence-linked wiki you can open in Obsidian.

Your MCP client's model does the reading and writing (screening decisions, entity extraction, syntheses). med-lit-mcp searches the databases (PubMed, PMC, OpenAlex, Europe PMC, Semantic Scholar, Scopus), stores everything, checks the model's work against the source text, and exports the wiki. Every screening decision, extracted mention and synthesis citation must be a verbatim quote or a reference to the stored text.

- **Clients:** Hermes (terminal and Desktop), Claude Code, Claude Desktop, Codex, pi and ChatGPT desktop Work/Codex.
- **Platforms:** Linux, macOS and Windows.
- **Requirements:** a contact email (`NCBI_EMAIL`). No embedding endpoint, model API key or build step.
- **Bots:** with Hermes, a scheduled bot can keep a review up to date by screening new articles and adding them to the wiki.

## Install

| Client | How |
| --- | --- |
| Hermes, Claude Code, Codex, pi | `uvx med-lit-mcp setup` (needs [uv](https://docs.astral.sh/uv/getting-started/installation/)); for pi see [med-lit in pi](https://github.com/junhewk/med-lit-mcp/blob/main/docs/pi.md) |
| Claude Desktop | Install `med-lit-<version>.mcpb` from the [latest release](https://github.com/junhewk/med-lit-mcp/releases/latest) under **Settings → Extensions**, then switch it on |
| ChatGPT desktop (macOS, Windows x64) | Run `med-lit-macos-<version>.dmg` or `Install med-lit.bat` from the [latest release](https://github.com/junhewk/med-lit-mcp/releases/latest) and fill in the browser form |

Setup asks for your email, a folder for your reviews (default `~/med-lit`) and optional database API keys. Keys are stored in private local files, never in the chat. See [Setup](https://github.com/junhewk/med-lit-mcp/blob/main/docs/setup.md) for every client, manual registration and updating.

## Use

Talk to your agent. It starts each stage only when you ask and confirms the search question with you first:

> Start a new review on how large language models are used in shared decision making in healthcare.
>
> Looks good, run it.
>
> Screen it. Include studies of LLMs supporting patient–clinician decisions; exclude purely technical benchmarks.
>
> Fetch the included articles and build the wiki.

Each review is a self-contained project folder. Open it in Obsidian with *Open folder as vault*.

## What's new in 0.2.0

- **pi support.** `uvx med-lit-mcp setup --client pi` adds med-lit to pi's `mcp.json`. It writes only med-lit's own entry and leaves pi's model, sign-in and settings alone. An optional pi package (`pi install git:github.com/junhewk/med-lit-mcp@v0.2.0`) adds two things: the research policy in study folders, and fresh subagent workers that get only the tools their task names. See [med-lit in pi](https://github.com/junhewk/med-lit-mcp/blob/main/docs/pi.md).
- **Batches that fit the client.**
  - Screening, triage and wiki-task results are sized by the bytes the client actually receives (default 18 KiB, `MED_LIT_RESPONSE_BYTES`), so a client that cuts long tool results (pi at 20 KiB) gets every abstract whole.
  - A single record too large on its own is sent with its abstract cut at a word boundary and marked.
- **The page limit holds on every route.** `next_wiki_article`, `get_article_page` and `record_extraction` all keep to `wiki.max_pages`.
  - **Changed:** a per-call `max_pages` can only ask for fewer pages. To read more of an article, raise the setting with `project_settings`.
- **The rules travel with each task.** The entity types, roles and evidence rules, and the duplicate and synthesis rules, are defined once. They are included in every task `wiki_tasks` hands out, so a subagent follows them whether or not it reads a guide.
  - **Changed:** a plan names the tools its tasks may use (`kind`, `tools`).
  - **Changed:** a long step comes in portions (`total_tasks`, `remaining_tasks`); call `wiki_tasks` again for the rest.

These changes came from comparing hosts on a pilot review: Hermes left subagents free to read past the page limit and to start the duplicate review, and pi with a stripped-down host received the rules more reliably. With 0.2.0 the backend enforces the limit and every task carries its rules, and the two hosts reached the same results.

## Documentation

| Topic | |
| --- | --- |
| [Setup](https://github.com/junhewk/med-lit-mcp/blob/main/docs/setup.md) | Installing for each client, manual registration, updating |
| [Configuration](https://github.com/junhewk/med-lit-mcp/blob/main/docs/configuration.md) | Email, keys and environment variables; per-review settings and defaults |
| [Running a review](https://github.com/junhewk/med-lit-mcp/blob/main/docs/workflow.md) | Projects, how search, screening, fetch and wiki work, tools, the viewer |
| [med-lit in pi](https://github.com/junhewk/med-lit-mcp/blob/main/docs/pi.md) | Registration, the optional pi package (research policy, scoped workers), recommended settings |
| [Bots](https://github.com/junhewk/med-lit-mcp/blob/main/docs/bots.md) | Keeping a review up to date with Hermes scheduled jobs |
| [The wiki](https://github.com/junhewk/med-lit-mcp/blob/main/docs/wiki.md) | Obsidian, entity types, roles and relationships, Simple Graph Builder |
| [Ontology](https://github.com/junhewk/med-lit-mcp/blob/main/docs/ontology.md) | The full knowledge-graph contract and graph export format |
| [Development](https://github.com/junhewk/med-lit-mcp/blob/main/docs/development.md) | Running from a checkout, tests, compatibility |
| [Installation and release](https://github.com/junhewk/med-lit-mcp/blob/main/docs/installation.md) | Building and signing the installers |
| [Validation](https://github.com/junhewk/med-lit-mcp/blob/main/docs/validation.md) | Platform and installer test records |

## License

med-lit-mcp is licensed under the [PolyForm Noncommercial License 1.0.0](https://github.com/junhewk/med-lit-mcp/blob/main/LICENSE). You may use, modify and share it for any noncommercial purpose, including research, teaching, and use by educational institutions, public research organizations and public health organizations. Commercial use needs a separate license; open an issue to ask. [NOTICE](https://github.com/junhewk/med-lit-mcp/blob/main/NOTICE) records the MIT-licensed origin of the search engine.
