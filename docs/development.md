# Development

```bash
uv sync
uv run pytest
uvx ruff check src
npx @modelcontextprotocol/inspector uv run med-lit-mcp
```

To run a server from a checkout, use `uv run --directory /path/to/med-lit-mcp med-lit-mcp` as the MCP command. Unlike `uvx --from <path>`, it always runs the checkout's current code. `setup --dev /path/to/med-lit-mcp` and `setup bot --dev /path/to/med-lit-mcp` register that command for you (run them with `uv run med-lit-mcp …` from the checkout).

To test the setup flows from this checkout, run the install scripts with `--dev` (Windows: `-Dev`):

```bash
scripts/install.sh --dev . --client codex
scripts/install.sh --dev . --client claude-code
scripts/install.sh --dev . --client hermes
scripts/install.sh --dev . --chatgpt
```

For the release steps, see [Installation and release](installation.md).

## Search engine

The database search engine in `src/med_lit_mcp/medsearch/` (query compilation, MeSH resolution, PubMed/PMC/OpenAlex/Semantic Scholar/Scopus clients, deduplication and ranking) was merged from [hermes-medical-search](https://github.com/junhewk/hermes-medical-search) 0.2.1 (MIT) and is maintained here. For debugging it can run on its own: `uv run python -m med_lit_mcp.medsearch --help`.

## Compatibility and validation

Review locks, private credential storage and process recovery support Linux, macOS and Windows.
The 0.4.0 source suite passed **312 tests, 1 skipped, 26 subtests** on Linux. Private setup and
conversational settings were tested with installed Hermes, Claude Code and Codex; the latter two
also passed the full bot-management lifecycle using Hermes's scheduler on jkworkstation. These
tests used separate native client homes and med-lit stores, and verified that synthetic credentials
stayed out of transcripts and saved client state. CI tests all three operating systems before
publishing to PyPI. See [Validation](validation.md) for the exact scope and earlier platform checks.

When testing setup or bot registration, use temporary `MED_LIT_CONFIG_DIR`, `MED_LIT_STATE_DIR`,
`MED_LIT_PROJECTS_DIR`, and the client's own configuration directory (`HERMES_HOME`,
`CLAUDE_CONFIG_DIR` or `CODEX_HOME`). Check the generated registration and the form's reviews folder
before submitting synthetic values. Preserve those paths in the server environment. Never run
these tests against a researcher's saved settings or put real credentials in fixtures or transcripts.

Release CI also runs both install scripts from the checkout on Linux, macOS and Windows.

- **Earlier desktop installers (until 0.2.0).** The signed Mac DMG and the Windows batch passed their native checks; the [validation record](validation.md) keeps those results.
- **ChatGPT Work on Mac** completed search, screening, full-text fetch, extraction, synthesis and a cited wiki export.
- **Hermes scheduled bots** keep their existing platform scope.
