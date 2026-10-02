> Historical test record before consolidation. See [current validation status](../validation.md) for the current source and release scope.

# Windows platform verification — 2026-10-02

Repository: `C:\Users\junhe\programming\med-lit-mcp` (accessed through WSL2 SSH).

## Environment

- Native Windows kernel 10.0.26300, x64; active desktop session for `junhe`.
- Windows Python 3.12.10, existing uv 0.12.17, Microsoft Edge 154.0.4258.48.
- Tests run with Windows `uv.exe`, using `.venv-windows`; these are not WSL Python results.

## Verified

- Full native Windows suite: **215 passed, 6 skipped, 18 subtests passed**. Skips cover macOS bootstrap cases and Unix-only behavior.
- Final suite was rerun after macOS/Windows source integration (25.09 seconds). Windows-only CLI packaging produced exactly `Install Med-lit.bat` and `SHA256SUMS`, with no macOS ZIP. The batch SHA-256 matched the checksum manifest: `35338c524759e0c5befef1e430927b76975064a24a44a432c0db5110174af9c2`. Artifacts are in `dist-installers-windows-final`.
- Generated batch executes its inline PowerShell normally; no separate `.ps1` execution, policy changes, protection bypass, or unblocking.
- Native bootstrap tests cover existing uv, fresh download/checksum/extraction, rejection of an invalid checksum before extraction, network failure cleanup, unsupported architecture, developer paths with spaces/Korean characters, and nonzero exit/error retention. Download/hash fixtures in these tests are intentionally simulated.
- Separate real desktop runs used Windows file association to open a developer wrapper that calls the generated batch with `--dev`. An isolated Edge profile displayed the actual local form. Native folder-dialog cancellation kept the field unchanged; selecting `reviews folder 한글` preserved the full path. Saving synthetic settings displayed **med-lit is ready** after real MCP initialization, listing 31 tools, and the workflow guide call.
- A fresh-runtime desktop run excluded the existing uv directories from PATH. The batch actually downloaded the pinned uv 0.12.21 archive over HTTPS, checked SHA-256, extracted it into an isolated runtime, and completed the same form/MCP flow. The installed runtime's version was checked separately.
- Both successful settings sessions closed their listening ports and printed the batch success message.
- Settings, review folders, state, and CODEX_HOME were isolated under `.platform-test`. Existing user credentials, reviews, client configuration, and ChatGPT chats were not read or changed.

## Fixes

1. Browser launch runs in a daemon thread while the main thread serves settings HTTP. Our first test command selected Python's generic browser handler, which waited for Edge to close before returning; this left the page spinning while HTTP was not served. A regression test reproduced the HTTP timeout before the change and now verifies HTML/settings requests and cancellation work while the browser handler remains blocked. The corrected native Edge test completed normally.
2. Windows folder selection explicitly writes and reads UTF-8, preventing corrupted Unicode paths.
3. Successful-save state is set before the response; the completion event remains after response delivery. Lifecycle tests wait for that event instead of depending on thread scheduling.
4. Windows bootstrap accepts `MED_LIT_RUNTIME_DIR` to isolate its runtime, matching the macOS development/test option.
5. UTF-8 test artifacts are decoded explicitly; the home-folder fixture sets Windows USERPROFILE as well as HOME.

## Release gates

- med-lit 0.1.6 is unpublished. Developer-checkout runs do **not** verify the public pinned PyPI installation flow. Publish first, then test the unmodified downloaded batch.
- Download reputation/SmartScreen behavior was not verified: these developer tests used local files, without browser-download provenance. No claim is made about a public installer passing that gate.
- This Windows run tested setup and the actual MCP connection. It did not repeat the literature-review workflow inside Windows ChatGPT or replace the prior macOS workflow evidence.
- macOS signing/notarization belongs to the separate macOS implementation.

## Local test artifacts

The ignored `.platform-test` directory contains the disposable desktop test drivers, synthetic settings, logs and screenshots of the test webpage only. In particular, `browser settings 3 한글` and `fresh bootstrap settings 한글` contain `settings-form.png` and `ready-form.png`. They are not included in the source handoff archive.
