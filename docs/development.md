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
scripts/install.sh --dev . --chatgpt
```

For the release steps, see [Installation and release](installation.md).

## Search engine

The database search engine in `src/med_lit_mcp/medsearch/` (query compilation, MeSH resolution, PubMed/PMC/OpenAlex/Semantic Scholar/Scopus clients, deduplication and ranking) was merged from [hermes-medical-search](https://github.com/junhewk/hermes-medical-search) 0.2.1 (MIT) and is maintained here. For debugging it can run on its own: `uv run python -m med_lit_mcp.medsearch --help`.

## Compatibility and validation

Review locks, private credential storage and process recovery support Linux, macOS and Windows. The 0.1.8 suites passed **231 tests locally on Linux and 232 on native macOS**. Release CI passed on all three operating systems, including **233 tests on Windows**. CI tests all three operating systems before publishing to PyPI.

Release CI also runs both install scripts from the checkout on Linux, macOS and Windows.

- **Earlier desktop installers (until 0.2.0).** The signed Mac DMG and the Windows batch passed their native checks; the [validation record](validation.md) keeps those results.
- **ChatGPT Work on Mac** completed search, screening, full-text fetch, extraction, synthesis and a cited wiki export.
- **Hermes scheduled bots** keep their existing platform scope.
