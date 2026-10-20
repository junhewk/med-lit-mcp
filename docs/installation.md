# Installation and release

## Supported routes

| User | Entry point | Settings | Package source |
| --- | --- | --- | --- |
| Hermes, Claude Code, Codex, pi on macOS or Linux | `scripts/install.sh` (or `uvx med-lit-mcp setup`) | Terminal prompts | PyPI, newest release |
| Hermes, Claude Code, Codex, pi on Windows | `scripts\install-windows.ps1` (or `uvx med-lit-mcp setup`) | Terminal prompts | PyPI, newest release |
| ChatGPT desktop Work/Codex | The same scripts with `--chatgpt` / `-ChatGPT` | Local browser form | PyPI release matching the form |
| Claude Desktop | `med-lit-<version>.mcpb` | Claude extension form | PyPI |

See the [user installation steps](setup.md). Hosted Chat and mobile cannot run this local server.

Until 0.2.0, ChatGPT desktop users had a signed macOS `.dmg` and a Windows `Install med-lit.bat`.
These installers downloaded a pinned uv and opened the browser form. From 0.2.1 the two install
scripts replace them, and uv is a prerequisite. The earlier installers' test records stay in
[validation](validation.md).

## The install scripts

`scripts/install.sh` (macOS and Linux) and `scripts/install-windows.ps1` (Windows) do the same steps,
and the same command installs and updates:

1. **Check uv.** If uv is missing, the script prints the official install command for the system and
   stops.
2. **Pick the server.** It runs the newest med-lit-mcp from PyPI (`uvx med-lit-mcp@latest`), which
   also makes it what clients start. With `--dev CHECKOUT` / `-Dev`, it runs a source checkout
   instead (`uv run --directory CHECKOUT med-lit-mcp`).
3. **Run setup.** `med-lit-mcp setup` saves the email, reviews folder and optional keys and registers
   the server with the clients found, or with the ones named by `--client` / `-Client`. With
   `--chatgpt` / `-ChatGPT` it opens the browser form instead (`setup --ui --client chatgpt`). Other
   options pass through: `--email`, `--projects-dir`, `--yes`, `--no-register`.
4. **Check.** It runs `med-lit-mcp --check` and prints the saved configuration as the server reads it.

Details:

- **No checkout needed.** The scripts do not depend on a checkout: a downloaded single file works.
- **Piped scripts.** `install.sh` is written for the bash 3.2 that macOS ships. When the script
  itself arrives through a pipe, setup reads its prompts from the terminal.
- **Windows PowerShell.** `install-windows.ps1` runs in Windows PowerShell 5.1 and PowerShell 7.

## Shared implementation

- **`cli_setup.py`** owns command parsing and the terminal prompts. Only ChatGPT uses `setup --ui`.
- **`setup_ui.py`** serves the browser form on loopback and opens the browser asynchronously. It then
  saves settings, registers the connection and verifies the actual MCP initialization, tool listing
  and workflow guide. Ready requires that verification to pass. The registered command runs the
  PyPI release matching the form (`uv tool run --python 3.11 --from med-lit-mcp==<version>`) with an
  absolute uv. After an update the registration differs, so the form asks before replacing it.
- **`clients.py`** writes the native med-lit entry:
  - **Codex:** `$CODEX_HOME/config.toml` or `~/.codex/config.toml`, keeping unrelated TOML and
    comments. It uses an absolute launcher, a 180-second startup timeout and a 600-second tool
    timeout.
  - **pi:** `mcp.json` (see [med-lit in pi](pi.md)).

  Changing an existing connection requires a replacement choice.
- **Keys and settings.** The CLI and the browser form share the existing configuration and key
  storage. Credentials sit in private files; client configuration holds only the launch command.
  Reviews remain local. Blank key fields keep saved keys; removing one is explicit.
- **`platforms.py`** holds Windows/Unix file protection and process handling. Review locking uses
  `portalocker` on all three systems.

The form checks Host, Origin and an unpredictable session token, and expires after 15 minutes of
inactivity. Closing it before Save changes no settings. After a connection failure, the user can
retry without re-entering saved settings.

## Build

The Claude Desktop extension can be built from any development checkout:

```bash
uv run python scripts/build_mcpb.py dist-extension
```

Publish the matching PyPI version before distributing it.

## Release

1. **Test.** Review and test the versioned source; see the [release gates](validation.md#release-gates).
2. **Publish the matching GitHub release.** The release workflow then:
   1. runs the test suite on Linux, macOS and Windows;
   2. runs each install script there from the checkout (`--dev`, without registering);
   3. checks that the tag matches the version;
   4. builds and publishes PyPI;
   5. attaches the Claude Desktop extension.

## Development

```bash
scripts/install.sh --dev . --client codex          # or: scripts\install-windows.ps1 -Dev . -Client codex
scripts/install.sh --dev . --chatgpt               # the browser form, from this checkout
uv run pytest
```
