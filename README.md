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
| Hermes, Claude Code, Codex, pi | Run the install script: [`scripts/install.sh`](https://github.com/junhewk/med-lit-mcp/blob/main/scripts/install.sh) on macOS and Linux, [`scripts/install-windows.ps1`](https://github.com/junhewk/med-lit-mcp/blob/main/scripts/install-windows.ps1) on Windows. Or run `uvx med-lit-mcp setup`. Both need [uv](https://docs.astral.sh/uv/getting-started/installation/). For pi see [med-lit in pi](https://github.com/junhewk/med-lit-mcp/blob/main/docs/pi.md) |
| ChatGPT desktop Work/Codex (macOS, Windows) | The same install script with `--chatgpt` (Windows: `-ChatGPT`), then fill in the browser form |
| Claude Desktop | Install `med-lit-<version>.mcpb` from the [latest release](https://github.com/junhewk/med-lit-mcp/releases/latest) under **Settings → Extensions**, then switch it on |

The same script installs and updates. Setup asks for your email, a folder for your reviews (default `~/med-lit`) and optional database API keys. Keys are stored in private local files, never in the chat. See [Setup](https://github.com/junhewk/med-lit-mcp/blob/main/docs/setup.md) for every client, manual registration and updating.

## Use

Ask your agent a research question. med-lit leads it through the review one step at a time, and stops for every decision that is yours:

> How are large language models used in shared decision making in healthcare? Do a basic review.
>
> *(the agent shows the mode and search settings, then the drafted search question)* Looks good, run it.
>
> *(it reports the results and proposes inclusion and exclusion criteria, each with its reason)* Approved.
>
> *(it screens, asks about the articles it could not decide, and replies with the answer and the full report)*

There are three modes:

- **basic** screens titles and abstracts, then writes the report from the abstracts.
- **full** also fetches the included articles' full text and builds the evidence-linked wiki, then writes a report that can quote the full text.
- **deep** runs full mode in rounds, like a bot's runs in one session. After each round the agent judges whether the included studies answer the question; if not, a revised search aimed at the gaps adds the best new articles, which are screened, fetched and added to the wiki, until the evidence is enough or the round limit is reached.

The report is written for a reader who did not see the review: a short answer first, then the evidence by theme, how much to trust it, and how the review was done, with numbered references. Each cited study and quoted finding is checked against the stored articles, and the description of the search and screening comes from the review's own record. See [Running a review](https://github.com/junhewk/med-lit-mcp/blob/main/docs/workflow.md#research-modes).

You can also run each stage yourself ("Screen it. Include … exclude …", "Fetch the included articles and build the wiki"). Each review is a self-contained project folder. Open it in Obsidian with *Open folder as vault*.

## What's new in 0.3.0

- **Research modes: from a question to a report.** Ask a research question and `research_next` leads the review step by step, stopping at every decision that is yours: the mode and search settings, the question, the criteria, whether to screen on, and the articles it could not decide.
  - **basic**: titles and abstracts; **full**: also full text and the evidence wiki; **deep**: full mode in rounds. After each round the agent judges whether the evidence answers the question (`record_evaluation`) and, if not, a revised search adds the best new articles, until it is enough or `research.deep_rounds` is reached.
  - **Inclusion and exclusion criteria come with reasons**, each with a code (I1 … E1 …), and every exclusion names the criterion it rests on, so the report counts the reasons for exclusion.
- **A report that answers the question** (`next_report`, `record_report`), written for a reader who did not see the review: a short answer, the evidence by theme, how much to trust it, how the review was done, numbered references and an appendix with the quotations and criteria. Citations and quotations are checked against the stored articles; the description of the search and screening comes from the review's record. Each report gets its own file in `reports/`.
- **Fits Hermes as it works.** Tool schemas are published without `$ref`, so every argument states its type: Hermes passes `$ref` to OpenAI-compatible models unresolved, and the model then sent the search question as a broken string (6 of 20 replayed calls before, 0 of 20 after). The call that ends a screening session returns the next step itself, for an agent that asks the researcher at once.
- **Fixed:** an article stayed in `wiki_tasks` when a worker lowered its page allowance after recording its last page; deep-mode report portions now fit pi's 20 KiB tool results.

Tested on Hermes and pi, interactive and one-shot, in every mode: [research modes test](https://github.com/junhewk/med-lit-mcp/blob/main/docs/validation/research-modes-20261026.md).

## What's new in 0.2.1

- **Install scripts replace the desktop installers.** `scripts/install.sh` (macOS, Linux) and `scripts/install-windows.ps1` (Windows) install and update med-lit for every terminal client, and with `--chatgpt` for ChatGPT desktop. They need [uv](https://docs.astral.sh/uv/getting-started/installation/). The signed macOS `.dmg` and the Windows `Install med-lit.bat` are no longer built. The Claude Desktop `.mcpb` stays.
- `setup --dev` stops with a clear message when uv is not on `PATH`, instead of registering a command that cannot start.

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
| [Installation and release](https://github.com/junhewk/med-lit-mcp/blob/main/docs/installation.md) | The install scripts, the Claude Desktop extension, releasing |
| [Validation](https://github.com/junhewk/med-lit-mcp/blob/main/docs/validation.md) | Platform test records, including the earlier desktop installers |

## License

med-lit-mcp is licensed under the [PolyForm Noncommercial License 1.0.0](https://github.com/junhewk/med-lit-mcp/blob/main/LICENSE). You may use, modify and share it for any noncommercial purpose, including research, teaching, and use by educational institutions, public research organizations and public health organizations. Commercial use needs a separate license; open an issue to ask. [NOTICE](https://github.com/junhewk/med-lit-mcp/blob/main/NOTICE) records the MIT-licensed origin of the search engine.
