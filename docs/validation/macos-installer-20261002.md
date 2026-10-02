> Historical test record before consolidation. See [current validation status](../validation.md) for the current source and release scope.

# Signed native macOS installer validation — 2026-10-02

## Result

The new universal **Med-lit Installer.app** inside a signed, notarized and stapled DMG passes normal downloaded Finder launch on this Apple Silicon Mac. The same downloaded production artifact completes a fresh isolated runtime installation, browser Save/connect and actual MCP verification, and its registered server still starts after the installer closes and the disk image is ejected.

The earlier signed `.command` archive was rejected by Apple notarization and blocked by Finder; that route has been removed. The app is a small native launcher for the existing local browser form. It does not host articles or credentials, alter article access or licensing, require a Terminal window, or change OS protections.

## Source and reproducible build

Development checkout: `/Users/jk/programming/med-lit-mcp`.

New files:

- `installers/macos/Launcher.swift`: AppKit progress, retry, cancellation and process-group lifecycle.
- `installers/macos/bootstrap.sh`: verified uv acquisition and persistent bundled-package installation.
- `scripts/build_macos_installer.py`: universal compilation, Developer ID signing, notarization, stapling, assessment and checksum generation.
- `tests/test_macos_installer.py`: release gates, package persistence, damaged bundle and real native descendant-process shutdown.

Portable setup accepts the installer's hidden `--package` argument, validates the wheel metadata against the running med-lit version, and registers the persistent wheel rather than an unpublished PyPI version or mounted-volume path. CLI setup for other clients is preserved.

```bash
uv run python scripts/build_macos_installer.py dist-macos \
  --sign-identity "Developer ID Application: Junhewk Kim (5AVW8WCFJ2)" \
  --notary-profile med-lit-notarization
```

Run from the Mac GUI Terminal security session where the signing key and notarization profile are accessible. The builder does not publish assets. It emits final release filenames only after Apple acceptance, stapling and Gatekeeper assessment. Failed or unconfirmed candidates and responses are retained under `.notarization/`, outside the release-asset glob. Unsigned builds require an explicit developer option and have a separate filename.

## Artifact and Apple validation

- Artifact: `/tmp/med-lit-native-release-final/med-lit-macos-0.1.6.dmg`.
- Size: **307,892 bytes**.
- SHA-256: `175b6b25aec3e5e760f14842f57bca391fdfae5c83bf4d857c500e27b3be11b1`.
- Signing identity: Developer ID Application, Junhewk Kim, team `5AVW8WCFJ2`.
- Native binary architectures: `arm64` and `x86_64`.
- Apple submission ID: `9e9c78bf-8202-4576-845b-4b9bfb782615`.
- Apple status: **Accepted**.
- DMG stapling and ticket validation: **passed**.
- Mounted application strict code-signature verification: **passed**.
- Mounted application Gatekeeper assessment: **accepted, Notarized Developer ID**.

The first build exposed a duplicate signing attempt; the builder now signs the enclosing bundle once with `--force`, a secure timestamp and hardened runtime. No Keychain access controls were changed or private key exported.

## Actual desktop test

Host: macOS **15.7.9**, build **24G830**, Apple Silicon. Chrome **154.0.8037.93**.

1. Chrome downloaded the exact final production DMG from a dedicated loopback HTTP endpoint to `/Users/jk/Downloads/med-lit-native-finder-test-20261002-115138.dmg`. Download checksum matched the signed artifact. Chrome quarantine metadata remained present. This tests browser download/quarantine, not a published GitHub HTTPS asset or download reputation.
2. Finder mounted the disk image, and the application opened through the normal Finder action. macOS showed its standard trusted Internet-download confirmation, including the message that Apple had checked the application. Selecting the normal **Open** button launched it. No Open Anyway or other protection override was used.
3. The production launch used its bundled package and reached the actual browser settings form. **Native Cancel** terminated the uv/Python children and closed settings port `55498`. No Save was performed against existing user settings.
4. The **same downloaded, signed production app** was relaunched through LaunchServices with its explicit developer `--test-state-dir` option to isolate synthetic configuration, state, registration and review files. This also isolated uv caches, tool environments and Python installations, and forced fresh uv acquisition rather than reusing Homebrew. No source-development option or global environment change was used.
5. Fresh runtime installation succeeded: **uv 0.12.21**, verified against the pinned SHA-256, and managed **Python 3.11.16**, with dependencies installed into the isolated runtime/cache.
6. The actual browser form accepted the synthetic email and reviews folder. **Save and connect** reached **med-lit is ready** in both browser and native window. The native Ready state requires bootstrap exit **0**. Actual MCP initialization, tool listing and the workflow guide passed. Settings port `55582` closed after completion.
7. The native app closed and disk `10` was ejected normally. With that volume absent, the exact registered command using the persistent wheel started successfully: **connected=true, 31 tools, exit 0**.
8. The owned download server stopped. User credentials, existing MCP registration, reviews and ChatGPT conversations remained unchanged.

The test-state option is solely an explicit isolation seam for development. Ordinary users follow the normal Finder launch and browser form without terminal typing. It does not change the production payload, signature, quarantine state or package source.

## Automated verification

- Final native macOS suite with Homebrew PATH enabled: **222 passed, 7 Windows-only skips, 28 subtests passed** in 18.23 seconds.
- Native Windows portable setup suite: **218 passed, 6 skips, 22 subtests passed**.
- Newly added Mac-builder validation on Windows: **2 passed, 3 Mac-only skips, 6 subtests passed**.
- `git diff --check`: **passed**.

The lifecycle test compiles and executes the actual Swift ChildGroup implementation, starts a child with an active descendant and verifies shutdown stops that descendant. The real native Cancel test additionally verifies closure of the settings service.

## Evidence and remaining publication scope

- `/tmp/med-lit-native-release-final/gui-test-evidence.json`
- `/tmp/med-lit-native-release-final/med-lit-macos-0.1.6-notarization.json`
- `/tmp/med-lit-native-release-final/SHA256SUMS-macos`
- `/tmp/med-lit-native-release-final/full-pytest.log`
- `/tmp/med-lit-native-first-open.png`
- `/tmp/med-lit-native-fresh-progress.png`
- `/tmp/med-lit-native-app-ready.png`

Actual runtime execution was tested on **ARM64**; the Intel binary slice was built and verified but was not executed on an Intel Mac. The full literature-review behavior remains covered by [the earlier ChatGPT workflow record](chatgpt-workflow-20261002.md); this run specifically validates the new installer and its persistent MCP launcher.

Changes are uncommitted and unpublished. The Linux development checkout was left unchanged. The matching source is present in the approved Mac and Windows development repositories. Package version 0.1.6 remains unpublished on PyPI; this no longer blocks the self-contained Mac installer, while Windows and Claude's thin launchers still require matching package publication.
