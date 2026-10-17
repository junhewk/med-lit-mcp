# Setup

med-lit-mcp is on [PyPI](https://pypi.org/project/med-lit-mcp/) and runs through [`uv`](https://docs.astral.sh/uv/) ([install uv](https://docs.astral.sh/uv/getting-started/installation/) first if you don't have it). Claude Desktop and ChatGPT desktop users can skip the terminal entirely.

| Client | Installation | Email, folder and keys |
| --- | --- | --- |
| Hermes (terminal or Desktop), Claude Code, Codex | [CLI `setup`](#hermes-claude-code-and-codex-cli-setup) | Terminal prompts |
| Claude Desktop | [`.mcpb` extension](#claude-desktop-no-command-line) | Claude's settings form |
| ChatGPT desktop Work/Codex on Windows x64 | [`Install med-lit.bat`](#chatgpt-desktop-download-and-open-the-installer) | Local browser form |
| ChatGPT desktop Work/Codex on macOS | [Signed, notarized `.dmg`](#chatgpt-desktop-download-and-open-the-installer) | Local browser form |

To try the latest unreleased code, replace `med-lit-mcp` with `--from git+https://github.com/junhewk/med-lit-mcp med-lit-mcp` in any command.

## Hermes, Claude Code and Codex: CLI setup

```bash
uvx med-lit-mcp setup
```

Setup asks for your contact email (needed for PubMed and Unpaywall), a folder for your reviews (default `~/med-lit`), optionally API keys, and optionally your default [review settings](configuration.md#review-settings), including how many articles a Hermes bot screens per run. Each key is typed with hidden input, tested against its provider right away, and saved in `~/.config/med-lit-mcp/keys.json`, a file only you can read. Keys never go into the chat or into the clients' configuration files.

Setup then registers med-lit with every Hermes, Claude Code and Codex CLI it finds on the machine. Hermes Desktop uses the same Hermes configuration, so this registers it too, even when the Desktop's `hermes` command is not on your `PATH`; start a new chat (or `/reload-mcp`) to load the tools. Hermes Desktop ships its own uv, so without uv of your own you can run `~/.hermes/bin/uvx med-lit-mcp setup`. Either way med-lit runs in its own uv environment, separate from Hermes's Python.

Manage keys later without rerunning setup:

```bash
uvx med-lit-mcp keys              # which keys are set, and from where
uvx med-lit-mcp keys set scopus   # add or replace one (ncbi, openalex, semantic-scholar, scopus, scopus-insttoken)
uvx med-lit-mcp keys test         # check every key against its provider
uvx med-lit-mcp keys remove openalex
```

For unattended use: `uvx med-lit-mcp setup --email you@example.org --yes` accepts the defaults, skips the key questions and registers with every client found; `--client hermes`, `--client claude-code` or `--client codex` limits it to one. `--yes` also accepts replacing an existing registration.

### Codex

```bash
uvx med-lit-mcp setup --client codex
```

Setup writes only the `med-lit` entry in `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`), preserving other settings and comments. It uses an absolute launcher path, a 180-second startup timeout and a 600-second tool timeout. An identical connection is kept; a different connection requires a replacement choice. Restart the client or start a new session, then use `/mcp` to check the connection. The ChatGPT desktop app and Codex CLI share this native configuration on the same host. [Official MCP documentation](https://learn.chatgpt.com/docs/extend/mcp)

## ChatGPT desktop: download and open the installer

This flow targets **desktop Work/Codex with local computer access** on macOS (Apple Silicon or Intel) and Windows x64. Hosted Chat and mobile do not run the local server.

1. Download the installer from the [release page](https://github.com/junhewk/med-lit-mcp/releases/latest): **Windows:** `Install med-lit.bat`; **macOS:** `med-lit-macos-<version>.dmg`, then open the disk image and double-click **med-lit Installer**. Accept the normal macOS downloaded-app confirmation if shown.
2. The installer finds or installs uv and Python, showing progress in a native window on Mac or a console window on Windows. Complete the browser form: your contact email, reviews folder (use **Choose folder…** or keep the suggested path), and optional database keys. No terminal commands are needed.
3. Select **Save and connect**. Wait for **med-lit is ready**, which means the actual server connection passed verification. If it fails, the form offers **Retry connection**.
4. Restart ChatGPT. Open **Work** with the reviews folder as a local project using **Choose project** or **File → Open Folder…**, and accept its normal folder trust prompt.
5. Copy the message from the Ready page into ChatGPT, or ask: **“Start a med-lit review. Help me define the question, then wait for my approval before searching.”**

Reopen the downloaded installer to change settings or repair the connection. Blank key fields keep saved keys; **Remove saved key** removes one. Keys stay in private local files and are entered in the browser form, never in a conversation. The form expires after 15 minutes of inactivity. Cancellation before Save changes no settings; a downloaded runtime may remain.

A failed setup shows retry guidance. The Mac installer is a small native launcher signed with Developer ID and notarized by Apple. It includes the matching med-lit Python package, copies it into a persistent user runtime folder, and opens the same local browser form. No terminal typing, system Python, administrator access or copying into Applications is required. After setup you can eject the disk image; the registered server runs from the persistent runtime folder. Rerun a newer installer to update, preserving settings and reviews.

The installer registers the same native MCP connection used by Codex. It needs neither a setup plugin nor the standalone Codex CLI. See [Installation and release](installation.md) for build instructions and [validation](validation.md).

## Claude Desktop (no command line)

1. Download `med-lit-<version>.mcpb` from the [latest release](https://github.com/junhewk/med-lit-mcp/releases/latest).
2. In Claude Desktop open **Settings → Extensions → Advanced settings → Install Extension…** and choose the file.
3. Fill in the form: your email address (needed for PubMed and Unpaywall), optionally a folder for your reviews (default `~/med-lit`) and any API keys. Claude Desktop stores the keys encrypted.
4. **Switch the extension on.** Its switch shows "Disabled" until you turn it on.
5. In a chat, ask Claude to start a new review.

Claude Desktop installs Python and the server's dependencies itself. To change a setting or add a key later, open the extension in **Settings → Extensions**.

## Registering by hand

### Hermes, Claude Code or Codex

`setup` runs these for you. To register by hand, first save your settings without registering (`uvx med-lit-mcp setup --no-register`), then:

```bash
hermes mcp add med-lit --command uvx --connect-timeout 180 --args med-lit-mcp
claude mcp add med-lit --scope user -- uvx med-lit-mcp
codex mcp add med-lit -- uvx med-lit-mcp
```

For manual Codex registration, use the absolute path to `uvx` if the desktop app has a minimal `PATH`, and set `startup_timeout_sec = 180` and `tool_timeout_sec = 600` in `[mcp_servers.med-lit]`. Native `setup --client codex` does this for you.

In Hermes, `--env` must come before `--args`, because `--args` takes everything after it. Hermes passes an MCP server only basics such as `PATH` and `HOME` plus the variables given with `--env`; med-lit reads its settings and keys from `~/.config/med-lit-mcp/`, so none need to be passed.

### Claude Desktop

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

## Updating

ChatGPT desktop users reopen the installer to change settings or repair a connection, and download a newer installer to update med-lit. The Mac installer carries its matching package; the Windows launcher downloads its pinned package version from PyPI. Both preserve saved settings and reviews. A changed native registration asks for explicit replacement in the browser form.

`uvx` caches the installed version. To pick up a new release, run `uvx med-lit-mcp@latest --check` once, then restart the client (in Hermes, `/reload-mcp`). Bots use the same cached version from their next run. After an update, open each bot once with `uvx med-lit-mcp setup bot --edit "<name>"` (then choose done): this refreshes its scheduled job's instructions to the new version.
