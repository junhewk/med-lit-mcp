# Development

```bash
uv sync
uv run pytest
uvx ruff check src
npx @modelcontextprotocol/inspector uv run med-lit-mcp
```

To run a server from a checkout, use `uv run --directory /path/to/med-lit-mcp med-lit-mcp` as the MCP command. Unlike `uvx --from <path>`, it always runs the checkout's current code. `setup --dev /path/to/med-lit-mcp` and `setup bot --dev /path/to/med-lit-mcp` register that command for you (run them with `uv run med-lit-mcp …` from the checkout).

To test the new setup flows from this checkout:

```bash
uv run med-lit-mcp setup --client codex --dev .
uv run med-lit-mcp setup --ui --client chatgpt --dev .
```

For platform installer builds, signing and release steps, see [Installation and release](installation.md). The generated Windows batch supports `--dev "C:\path\to\checkout"`. Production Mac builds carry the matching wheel and require Developer ID signing and Apple notarization.

## Search engine

The database search engine in `src/med_lit_mcp/medsearch/` (query compilation, MeSH resolution, PubMed/PMC/OpenAlex/Semantic Scholar/Scopus clients, deduplication and ranking) was merged from [hermes-medical-search](https://github.com/junhewk/hermes-medical-search) 0.2.1 (MIT) and is maintained here. For debugging it can run on its own: `uv run python -m med_lit_mcp.medsearch --help`.

## Compatibility and validation

Review locks, private credential storage and process recovery support Linux, macOS and Windows. The 0.1.8 suites passed **231 tests locally on Linux and 232 on native macOS**. Release CI passed on all three operating systems, including **233 tests on Windows**. CI tests all three operating systems before publishing to PyPI.

The signed Mac DMG passed Apple notarization, a quarantined Chrome download, normal Finder launch, browser Save/connect and actual MCP startup with 31 tools after eject. Windows passed native batch execution, fresh verified runtime installation and browser Save/connect in Edge. ChatGPT Work on Mac also completed search, screening, full-text fetch, extraction, synthesis and a cited wiki export.

Guided setup targets macOS Apple Silicon/Intel and Windows x64. Intel execution and the public Windows download/PyPI flow remain release checks. The [validation record](validation.md) distinguishes these checks from completed tests. Hermes scheduled bots retain their existing platform scope.
