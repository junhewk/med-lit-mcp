# Validation status

The consolidated source uses four installation routes described in [Installation and release](installation.md). Linux, Mac and Windows development checkouts are synchronized. Version 0.1.6 is the first release with the ChatGPT installers.

## Desktop evidence

| Scope | Verified | Remaining scope |
| --- | --- | --- |
| macOS Apple Silicon | Developer ID signing, Apple acceptance, stapling, quarantined Chrome download, normal Finder launch, fresh uv/Python install, browser Save/connect, native cancellation and actual 31-tool MCP startup after DMG eject | Intel execution and public GitHub download |
| Native Windows x64 | Batch execution, fresh verified uv download, Edge form, Unicode folder selection, Save/connect and actual 31-tool MCP initialization | Public pinned PyPI flow and download reputation |
| ChatGPT Work review on Mac | PubMed/PMC search, screening, full-text fetch, extraction, synthesis and cited wiki export | Full review inside Windows ChatGPT |

The full approved review produced eight entity pages and six relationships from one eligible open-access paper. All 14 extraction spans matched stored source text, local links resolved, and no pending duplicates or stale syntheses remained.

Historical evidence records preserve the exact tested source/artifact scope:

- [ChatGPT review](validation/chatgpt-workflow-20261002.md)
- [Signed Mac installer](validation/macos-installer-20261002.md)
- [Windows installer and Edge fix](validation/windows-installer-20261002.md)

## Consolidation checks

All three development checkouts use the same consolidated implementation. Final full suites:

| System | Passed | Platform skips | Subtests | Time |
| --- | ---: | ---: | ---: | ---: |
| Linux | 220 | 8 | 34 | 12.60 s |
| Native macOS | 221 | 7 | 34 | 14.83 s |
| Native Windows | 223 | 5 | 34 | 25.36 s |

The Windows builder emits only the batch and checksum manifest; its CRLF format and SHA-256 were verified. The corrected batch SHA-256 is `368afc7d8ba29d6e3218f4b2f4d6960ed85e0a0946bb37365ac51db72f850027`. Product text and launcher names now consistently use `med-lit`. Historical records preserve names and checksums used by earlier builds. The wheel includes the shared validator and browser form. Diff whitespace checks passed.

Cleanup removed the setup-plugin artifact, Mac script launcher, mixed platform builder/tests and overlapping living support docs. Launcher behavior is preserved; product text uses `med-lit`, and wheel validation is shared between setup and the Mac builder. CI uses the full three-platform matrix once, then builds release assets.

## 0.1.8 release checks

Release commit `1ebb68e` passed the local Linux suite (231 tests, 8 skips) and the signing Mac's suite (232 tests, 7 skips), both with 34 subtests. [Release CI](https://github.com/junhewk/med-lit-mcp/actions/runs/37109879447) passed on Linux (230 tests, 9 skips), macOS (231 tests, 8 skips) and Windows (233 tests, 6 skips), each with 34 subtests, then published PyPI and attached the Windows and Claude Desktop assets.

An isolated installation of the published PyPI 0.1.8 package exposed all 31 tools with four explicit boolean annotations. The public Windows and Mac downloads matched their published SHA-256 manifests. The Windows launcher and Claude bundle both pin `med-lit-mcp==0.1.8`.

## Current Mac build

The released `med-lit-macos-0.1.8.dmg` was built on the signing Mac from release commit `1ebb68e`; the launcher and build scripts are unchanged since 0.1.6:

- SHA-256: `9e38cc449a754436ef11a5e866541fdf245f108279e19ee222ea581f92c0d459`.
- Apple submission: `c0e4dcaa-95b8-40e6-86ca-61cc0158cd49`, **Accepted**.
- Developer ID signature, stapling, ticket validation and Gatekeeper assessment passed. The bundled wheel is `med_lit_mcp-0.1.8` built from that commit; the native macOS source suite passed (232 tests, 7 platform skips and 34 subtests).
- The final DMG was mounted read-only and its app signature and universal arm64/x86_64 launcher were checked. The bundled wheel's hash matched its manifest. An isolated installation of that wheel reported version 0.1.8 and all 31 MCP tools with four explicit boolean annotations.
- Desktop launch evidence comes from the previous build of the same launcher (SHA-256 `7ab8ccc9…4b7617`, submission `4a7858db-bf58-42f8-83df-7c650b5d3485`): Chrome downloaded the exact DMG with quarantine intact. Normal Finder launch showed Apple's trusted Open prompt; isolated browser and native Ready passed. After closing and ejecting the DMG, the exact registered persistent-wheel command connected with **31 tools, exit 0**. Setup ports and the owned download server closed; existing settings remained unchanged. Its evidence directory on the Mac is `/tmp/med-lit-native-consolidated-final`.
- A copy of the DMG, checksum and notarization report is in the Linux checkout's ignored `dist-macos/v0.1.8/` directory.

## Release gates

- Test the unmodified Windows batch downloaded from the public release. Existing desktop tests used isolated developer settings and local files.
- Mac runtime execution has been tested on ARM64. The Intel slice is compiled and inspected but has not run on an Intel host.
- Actual desktop tests supplement automated tests; source-checkout tests do not establish public download behavior.
