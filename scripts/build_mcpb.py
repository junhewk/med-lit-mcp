"""Build the Claude Desktop extension (med-lit-<version>.mcpb) for the current package version.

The bundle is a thin wrapper: its pyproject pins med-lit-mcp from PyPI and Claude Desktop's uv
runtime installs it, so release to PyPI first. Usage: uv run python scripts/build_mcpb.py [out_dir]
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from med_lit_mcp import __version__, server

MCPB_CLI = "@anthropic-ai/mcpb@2.1.2"
ENTRY = '''"""Claude Desktop entry point for the med-lit extension."""

import os
import sys

# Optional settings left blank may arrive empty or as an unexpanded "${user_config...}" placeholder;
# drop both so med-lit-mcp treats them as unset.
for name, value in list(os.environ.items()):
    if not value.strip() or value.startswith("${user_config"):
        os.environ.pop(name)

from med_lit_mcp.server import main  # noqa: E402

sys.argv = [sys.argv[0]]
main()
'''


def secret(title: str, description: str) -> dict:
    return {"type": "string", "title": title, "description": description, "sensitive": True, "required": False}


def manifest() -> dict:
    tools = [
        {"name": tool.name, "description": (tool.description or "").split("\n")[0].strip()}
        for tool in server.mcp._tool_manager.list_tools()
    ]
    return {
        "manifest_version": "0.4",
        "name": "med-lit",
        "display_name": "med-lit: Medical Literature Review",
        "version": __version__,
        "description": "Staged, auditable medical literature reviews that build an evidence-linked Obsidian wiki.",
        "long_description": (
            "Search PubMed, PMC, OpenAlex, Semantic Scholar and Europe PMC with a structured PICO/PCC "
            "question, screen titles and abstracts with verbatim evidence, fetch open-access full text, and "
            "build a wiki of entities, relationships and cited syntheses in a project folder you can open in "
            "Obsidian. After installing, switch the extension on, then ask Claude to start a new review."
        ),
        "author": {"name": "Junhewk", "url": "https://github.com/junhewk"},
        "homepage": "https://github.com/junhewk/med-lit-mcp",
        "repository": {"type": "git", "url": "https://github.com/junhewk/med-lit-mcp"},
        "documentation": "https://github.com/junhewk/med-lit-mcp#readme",
        "license": "PolyForm-Noncommercial-1.0.0",
        "keywords": ["medical", "literature review", "pubmed", "systematic review", "obsidian", "wiki"],
        "server": {
            "type": "uv",
            "entry_point": "src/server.py",
            "mcp_config": {
                "command": "uv",
                "args": ["run", "--directory", "${__dirname}", "src/server.py"],
                "env": {
                    "NCBI_EMAIL": "${user_config.ncbi_email}",
                    "MED_LIT_PROJECTS_DIR": "${user_config.projects_dir}",
                    "NCBI_API_KEY": "${user_config.ncbi_api_key}",
                    "OPENALEX_API_KEY": "${user_config.openalex_api_key}",
                    "SEMANTIC_SCHOLAR_API_KEY": "${user_config.semantic_scholar_api_key}",
                    "SCOPUS_API_KEY": "${user_config.scopus_api_key}",
                    "SCOPUS_INSTTOKEN": "${user_config.scopus_insttoken}",
                },
            },
        },
        "tools": tools,
        "user_config": {
            # Not required: Claude Desktop leaves an extension with a missing required field switched
            # off after install. The server asks for the email when a search or fetch needs it.
            "ncbi_email": {
                "type": "string",
                "title": "Your email address",
                "required": False,
                "description": "Needed for PubMed/PMC (NCBI) and Unpaywall, which require a contact address.",
            },
            "projects_dir": {
                "type": "directory",
                "title": "Folder for your reviews",
                "required": False,
                "description": "Each review becomes a folder here that you can open in Obsidian. Leave empty for ~/med-lit.",
            },
            "ncbi_api_key": secret("NCBI API key (optional)", "Faster PubMed/PMC access. Free at https://account.ncbi.nlm.nih.gov/settings/"),
            "openalex_api_key": secret("OpenAlex API key (optional)", "Avoids OpenAlex rate limits. Free at https://openalex.org/settings/api"),
            "semantic_scholar_api_key": secret(
                "Semantic Scholar API key (optional)",
                "Needed to search Semantic Scholar reliably. Request free at https://www.semanticscholar.org/product/api#api-key-form",
            ),
            "scopus_api_key": secret("Scopus API key (optional)", "Requires institutional Scopus access. https://dev.elsevier.com/"),
            "scopus_insttoken": secret(
                "Scopus institutional token (optional)", "Only if Elsevier issued one to your institution; lets Scopus work off campus."
            ),
        },
        "compatibility": {"platforms": ["darwin", "win32"], "runtimes": {"python": ">=3.11"}},
    }


def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "dist").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        bundle = Path(temporary) / "med-lit"
        (bundle / "src").mkdir(parents=True)
        (bundle / "manifest.json").write_text(json.dumps(manifest(), indent=2, ensure_ascii=False) + "\n")
        (bundle / "pyproject.toml").write_text(
            f'[project]\nname = "med-lit-desktop-extension"\nversion = "{__version__}"\n'
            f'requires-python = ">=3.11"\ndependencies = ["med-lit-mcp=={__version__}"]\n'
        )
        (bundle / "src" / "server.py").write_text(ENTRY)
        npx = shutil.which("npx") or "npx"
        subprocess.run([npx, "-y", MCPB_CLI, "validate", str(bundle / "manifest.json")], check=True)
        target = out_dir / f"med-lit-{__version__}.mcpb"
        subprocess.run([npx, "-y", MCPB_CLI, "pack", str(bundle), str(target)], check=True)
    print(target)


if __name__ == "__main__":
    main()
