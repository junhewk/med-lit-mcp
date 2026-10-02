# Installation and release

## Supported routes

| User | Entry point | Settings | Package source |
| --- | --- | --- | --- |
| Codex, Hermes, Claude Code | `uvx med-lit-mcp setup --client <client>` | Terminal prompts | PyPI |
| Claude Desktop | `med-lit-<version>.mcpb` | Claude extension form | PyPI |
| ChatGPT desktop Work/Codex on Windows x64 | `Install med-lit.bat` | Local browser form | Pinned PyPI version |
| ChatGPT desktop Work/Codex on macOS | Signed `med-lit-macos-<version>.dmg` | Local browser form | Bundled matching wheel |

See the [user installation steps](../README.md#install). Hosted Chat and mobile cannot run this local server. med-lit does not require a hosted service or plugin listing. The former setup plugin and Mac `.command` launcher have been removed: Apple rejected the script archive for notarization and Finder blocked downloaded launch.

## Shared implementation

- `cli_setup.py` owns command parsing and CLI prompts. Only ChatGPT uses `setup --ui`.
- `setup_ui.py` serves the browser form on loopback, opens the browser asynchronously, saves settings, registers the connection and verifies actual MCP initialization, tool listing and the workflow guide. Ready requires that verification to pass.
- `clients.py` writes the native med-lit entry in `$CODEX_HOME/config.toml` or `~/.codex/config.toml`, preserving unrelated TOML settings and comments. Registration uses an absolute launcher and 180-second startup / 600-second tool timeouts. Changing an existing connection requires a replacement choice.
- `installation.py` validates the matching portable wheel for both the Mac builder and browser setup.
- Existing configuration and key storage are shared by CLI and browser setup. Private files contain credentials; client configuration contains the launch command. Reviews remain local. Blank key fields keep saved keys; removal is explicit.
- `platforms.py` contains Windows/Unix file protection and process handling. Review locking uses `portalocker` on all three systems.

The form checks Host, Origin and an unpredictable session token, and expires after 15 minutes of inactivity. Closing before Save changes no settings. Setup may retain a downloaded runtime. After a connection failure, the user can retry without reentering saved settings.

The launchers reuse an existing uv or download uv **0.12.21** over HTTPS with a pinned SHA-256 check. uv installs managed Python 3.11 and dependencies without administrator access or system Python changes. Runtime folders are `~/Library/Application Support/med-lit-mcp/runtime` on Mac and `%LOCALAPPDATA%/med-lit-mcp/runtime` on Windows.

The Mac app supplies progress, Retry, Cancel and optional Details, and owns the setup process group. Its bootstrap verifies and copies the wheel into `runtime/packages/<version>/<digest>/`. Registration uses that persistent path, so ejecting the disk image or moving the installer does not break MCP startup. Windows runs inline PowerShell from the batch launcher.

## Build

Windows and Claude artifacts can be built from any development checkout:

```bash
uv run python scripts/build_windows_installer.py dist-installers
uv run python scripts/build_mcpb.py dist-extension
```

The Windows builder emits only `Install med-lit.bat` and `SHA256SUMS`. Publish the matching PyPI version before distributing Windows or Claude artifacts.

Run the Mac builder in GUI Terminal on the signing Mac, where the Developer ID key and existing notarization profile are accessible:

```bash
uv run python scripts/build_macos_installer.py dist-macos \
  --sign-identity "Developer ID Application: Junhewk Kim (5AVW8WCFJ2)" \
  --notary-profile med-lit-notarization
```

This builds the current portable wheel and universal arm64/x86_64 launcher, signs the app and DMG, submits to Apple, staples the accepted result and checks Gatekeeper assessment. Output is the DMG, `SHA256SUMS-macos`, and a notarization JSON report. Failed or pending candidates and logs stay under `.notarization/`; final release filenames are written only after acceptance and validation. Private keys stay in Keychain. An explicit `--unsigned` produces a separately named development artifact.

## Release

1. Review and test the same versioned source on Linux, native macOS and native Windows. Check the [remaining release gates](validation.md#release-gates).
2. Build and validate the signed Mac DMG from that source. The builder does not publish anything.
3. Publish the matching GitHub release. The release workflow runs the three-platform test matrix, checks the tag/version, builds and publishes PyPI, then attaches Claude and Windows assets.
4. Attach the validated DMG and `SHA256SUMS-macos` to the same release. This local signing process needs no private-key export or GitHub signing secret.
5. Test the downloaded public Windows batch against the published version before announcing it to users.

## Development

```bash
uv run med-lit-mcp setup --ui --client chatgpt --dev .
uv run pytest
```

The generated Windows batch also accepts `--dev "C:\path\to\checkout"`. Mac production-artifact tests use the launcher's explicit `--test-state-dir` option to isolate configuration, reviews, registration, caches and runtime. These test options do not change the ordinary user flow.
