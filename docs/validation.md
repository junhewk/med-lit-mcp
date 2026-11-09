# Validation status

Since 0.2.1, installation runs through two install scripts (`scripts/install.sh`, `scripts/install-windows.ps1`) and the Claude Desktop extension, described in [Installation and release](installation.md). The signed macOS DMG and the Windows batch were removed; the desktop evidence below is their historical record (0.1.6 to 0.2.0). Linux, Mac and Windows development checkouts are synchronized. Version 0.1.6 is the first release with the ChatGPT installers.

## Desktop evidence (installers removed in 0.2.1)

| Scope | Verified | Remaining scope |
| --- | --- | --- |
| macOS Apple Silicon | Developer ID signing, Apple acceptance, stapling, quarantined Chrome download, normal Finder launch, fresh uv/Python install, browser Save/connect, native cancellation and actual 31-tool MCP startup after DMG eject | Intel execution and public GitHub download |
| Native Windows x64 | Batch execution, fresh verified uv download, Edge form, Unicode folder selection, Save/connect and actual 31-tool MCP initialization | Public pinned PyPI flow and download reputation |
| ChatGPT Work review on Mac | PubMed/PMC search, screening, full-text fetch, extraction, synthesis and cited wiki export | Full review inside Windows ChatGPT |

The full approved review produced eight entity pages and six relationships from one eligible open-access paper. All 14 extraction spans matched stored source text, local links resolved, and no pending duplicates or stale syntheses remained.

Historical evidence records preserve the exact tested source/artifact scope:

- [Research modes in Hermes and pi, basic and deep](validation/research-modes-20261026.md) (0.3.0)
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

## Last Mac build (0.1.8)

The released `med-lit-macos-0.1.8.dmg` was built on the signing Mac from release commit `1ebb68e`; the launcher and build scripts are unchanged since 0.1.6:

- SHA-256: `9e38cc449a754436ef11a5e866541fdf245f108279e19ee222ea581f92c0d459`.
- Apple submission: `c0e4dcaa-95b8-40e6-86ca-61cc0158cd49`, **Accepted**.
- Developer ID signature, stapling, ticket validation and Gatekeeper assessment passed. The bundled wheel is `med_lit_mcp-0.1.8` built from that commit; the native macOS source suite passed (232 tests, 7 platform skips and 34 subtests).
- The final DMG was mounted read-only and its app signature and universal arm64/x86_64 launcher were checked. The bundled wheel's hash matched its manifest. An isolated installation of that wheel reported version 0.1.8 and all 31 MCP tools with four explicit boolean annotations.
- Desktop launch evidence comes from the previous build of the same launcher (SHA-256 `7ab8ccc9…4b7617`, submission `4a7858db-bf58-42f8-83df-7c650b5d3485`): Chrome downloaded the exact DMG with quarantine intact. Normal Finder launch showed Apple's trusted Open prompt; isolated browser and native Ready passed. After closing and ejecting the DMG, the exact registered persistent-wheel command connected with **31 tools, exit 0**. Setup ports and the owned download server closed; existing settings remained unchanged. Its evidence directory on the Mac is `/tmp/med-lit-native-consolidated-final`.
- A copy of the DMG, checksum and notarization report is in the Linux checkout's ignored `dist-macos/v0.1.8/` directory.

## 0.2.0 release checks

**Local suite.** Release commit (this one) passed the Linux suite: 263 tests, 8 skips, 34 subtests.
The new tests cover:

- the encoded size of screening, triage and wiki-task results, including a Korean multibyte case, a
  single oversized record, the environment override, and the exact bytes received through an
  in-memory MCP client;
- the page allowance on all three routes, with legacy pages beyond a lowered limit;
- one contract shared by the guide, task texts and page headers, with every schema type and role;
- pi registration (other entries kept, the researcher's edits kept, conflict, invalid JSON, the agent
  directory variable).

**Live evaluation on Linux.** One local model (`qwen3.8-flash-next`) on 13 PubMed/PMC/OpenAlex/
Scopus records, then screening, fetch (one Unpaywall PDF and one PMC full text) and extraction:

- Hermes v0.21.5 against pi 1.3.0 with `setup --client pi` and the pi package from this source.
- Both arms produced the same screening, researcher-decision, fetch and extraction state.
- Hermes's subagent asked for 5 pages and was held to the 2-page allowance.
- pi workers were declared exactly the five extraction tools plus `read`.
- `pi mcp list` with the generated entry connected and listed 30 direct tools, with the bot tools
  hidden.

**Release CI and published package.**
[Release CI](https://github.com/junhewk/med-lit-mcp/actions/runs/39382148536) passed, each with 34
subtests:

| Platform | Passed | Skips |
| --- | ---: | ---: |
| Linux | 262 | 9 |
| macOS | 263 | 8 |
| Windows | 261 | 10 |

It then published PyPI 0.2.0 and attached `med-lit-0.2.0.mcpb`, `Install med-lit.bat` and `SHA256SUMS`.

On a clean pi agent directory on Linux, from the published artifacts:
- `uvx med-lit-mcp@0.2.0 setup --client pi` registered the server;
- `pi install git:github.com/junhewk/med-lit-mcp@v0.2.0` installed the package;
- `pi mcp list` connected with 30 direct tools;
- a pi session in a study folder carried the `med_lit_research` section and declared `run_subtask`.

**Not repeated for 0.2.0.** The desktop installers and their launchers are unchanged since 0.1.8;
their native checks were not repeated for this release.

## 0.3.0 release checks

**Release CI.** [Release CI](https://github.com/junhewk/med-lit-mcp/actions/runs/40321722494) ran
the test suite on each platform. Each platform then ran its install script from the checkout
(`--dev`, without registering): the script started med-lit-mcp 0.3.0, saved the settings and
completed `--check`. CI then published PyPI 0.3.0 and attached `med-lit-0.3.0.mcpb`.

| Platform | Passed | Skips | Subtests |
| --- | ---: | ---: | ---: |
| Linux | 285 | 2 | 16 |
| macOS | 285 | 2 | 16 |
| Windows | 274 | 13 (bash-only tests) | 16 |

**Published package.** `uvx med-lit-mcp@0.3.0` reported 0.3.0 and listed 37 tools.

**Downloaded script on Linux.** `scripts/install.sh`, downloaded from the `v0.3.0` tag (identical to
the tag's source), was run with `--client pi --yes` against the published PyPI release, using fresh pi and
med-lit configuration folders and pi 1.3.0:

- it reported med-lit-mcp 0.3.0;
- it wrote pi's `med-lit` entry (`uvx med-lit-mcp`, absolute uvx, bot tools hidden), and `pi mcp list`
  showed it connected with 37 tools, `research_next` among them;
- it printed the matching `pi install …@v0.3.0` hint, and a second run reported it already registered;
- its check passed.

`pi install git:github.com/junhewk/med-lit-mcp@v0.3.0` installed the package from the public tag; its
research policy names `research_next`.

**Under pwsh on Linux.** `install-windows.ps1`, downloaded from the `v0.3.0` tag, ran with `-Dev`, without
registering, through version, setup and check (0.3.0, 37 tools), and refused `-ChatGPT -Yes` with exit
code 2.

**Not checked by hand for 0.3.0:**

- the `--chatgpt` browser flow end to end (it needs a person to save the form; its server side is
  covered by the existing form tests);
- the Windows script downloaded and run on native Windows against PyPI;
- the research modes on Claude Code, Claude Desktop, Codex and ChatGPT (tested on Hermes and pi:
  [research modes test](validation/research-modes-20261026.md)).

## 0.2.1 release checks

**Release CI.** [Release CI](https://github.com/junhewk/med-lit-mcp/actions/runs/39385312285) ran
the test suite on each platform. Each platform then ran its install script from the checkout
(`--dev`, without registering): the script started med-lit-mcp 0.2.1, saved the settings and
completed `--check`. CI then published PyPI 0.2.1 and attached `med-lit-0.2.1.mcpb`.

| Platform | Passed | Skips | Subtests |
| --- | ---: | ---: | ---: |
| Linux | 260 | 2 | 16 |
| macOS | 260 | 2 | 16 |
| Windows | 249 | 13 (bash-only tests) | 16 |

**Downloaded script on Linux.** `scripts/install.sh`, downloaded from the `v0.2.1` tag, was run with
`--client pi --yes` against the published PyPI release, using fresh pi and med-lit configuration
folders:

- it reported med-lit-mcp 0.2.1;
- it wrote pi's `med-lit` entry (`uvx med-lit-mcp`, absolute uvx);
- it printed the matching `pi install …@v0.2.1` hint;
- its check passed.

**Under pwsh on Linux.** `install-windows.ps1` ran with `-Dev`, without registering, through
version, setup and check, and refused invalid option combinations with exit code 2.

**Not checked by hand for 0.2.1:**

- the `--chatgpt` browser flow end to end (it needs a person to save the form; its server side is
  covered by the existing form tests);
- the Windows script downloaded and run on native Windows against PyPI.

## 0.4.0 release checks

**Source and built packages.** The versioned source passed **312 tests, 1 skipped, 26 subtests**
on Linux, with source lint and whitespace checks passing. Wheel and sdist metadata passed
`twine check`. An isolated installation of the built wheel reported 0.4.0, initialized the MCP
connection with all **42 tools**, and served the private setup guide and configuration status.
The wheel includes the browser form. The Claude Desktop bundle passed manifest validation,
declares 42 tools, and pins `med-lit-mcp==0.4.0`.

**Install scripts from the checkout.** Both bash and PowerShell (`pwsh` on Linux) ran version,
setup and configuration checks against 0.4.0, using isolated settings and `--no-register`.
These checks do not establish native Windows downloaded-script behavior; release CI runs that
platform's checkout script separately.

The live client checks below cover setup and bot management before release. Research-mode
evaluations and earlier desktop checks retain their original version and platform scope above.

## Private setup and conversational Hermes bots — 2026-11-09 (0.4.0)

Tested the development checkout over SSH on jkworkstation with its installed Hermes Agent
v0.21.5+7498.gebadb54, using the configured `qwen3.8-flash-next` model. The successful run used
separate Hermes profiles, med-lit configuration, project registry and review folders under `/tmp`.

**Twelve real conversation turns passed:** register without credentials; open the private settings
form; save synthetic email/key values outside chat; check configuration and change new-review
defaults; preview and approve a real scheduled literature bot; preview and approve schedule/cap
changes; then preview and approve pause, resume and archive. Existing review defaults and archived
review data were retained. No literature search or scheduled review run was started.

**Additional native CLI/MCP checks passed:** the installer's launch command reaches the bot profile;
an interrupted creation with a lost job ID recovers the existing routine rather than duplicating it;
the restricted bot server lists its execution tools and hides all five setup/management tools.
Direct management calls also reject bot-scoped execution in the unit tests.

The synthetic email, key and form token were absent from captured tool/chat output and Hermes
session databases, including SQLite WAL files. A browser-driven form check passed with no page
errors and no requests outside loopback during save. Provider key checks, when selected by a user,
intentionally contact the provider. This verifies the local browser fallback; it does not establish
an embedded private card in Hermes Desktop or OS isolation from unrestricted agent file access.

An initial live attempt exposed Hermes's `--env` argument behaviour: repeated flags retain only
the last group, so isolated store paths were lost. Registration now passes every assignment after
one flag; regression tests check it. The corrected run additionally checked the form's test folder
before submission and verified hashes of real user configuration before and after the run.

Local Linux checks: **305 passed, 1 skipped, 16 subtests**, plus lint, whitespace and browser checks.
The same final suite on jkworkstation passed **303 tests, 3 skipped, 16 subtests**, with uv on PATH.
These are source-checkout checks performed before the 0.4.0 release.

## Private setup in Claude Code and Codex — 2026-11-09 (0.4.0)

The same private setup is now available after registration with `--client claude-code` or
`--client codex`. Neither registration asks for contact details or database credentials. The
browser form names the selected agent, and both clients retain the isolated configuration paths
and launch command in their native MCP registration. `--terminal` retains terminal setup;
`--ui --client <name>` registers the selected client before opening the form.

**Live clients and models:**

| Host | Installed client | Model | Checks |
| --- | --- | --- | --- |
| Local Linux | Claude Code 2.1.319 | `claude-opus-5-5` | Two conversation turns; real Chromium form submission; status refresh; defaults |
| Local Linux | Codex CLI 0.167.0 | `gpt-6.1-sol` | Two conversation turns through its app server; real Chromium form submission; status refresh; defaults |
| jkworkstation | Claude Code 2.1.312 | `claude-opus-5-5` | Twelve conversation turns; private setup, defaults and full bot-management lifecycle |
| jkworkstation | Codex CLI 0.166.0 | `gpt-6.1-sol` | Twelve conversation turns through its app server; private setup, defaults and full bot-management lifecycle |

Each session began with unconfigured settings. A test harness, separate from the agent, entered a
synthetic contact address and Scopus key through the private form. The local checks drove Chromium
and verified the correct client instructions, with no page errors or requests outside loopback.
The headless workstation checks submitted to the same form endpoints directly. Both agents saw
the saved configuration through the same open MCP connection and changed defaults for new reviews.

For each workstation agent, bot creation and schedule/cap updates required preview followed by a
separate approval turn. Pause, resume and archive used the same sequence. The tests verified the
saved state after each preview and approval, the real Hermes routine after creation, and removal
of the routine after archive. Review data was retained. No literature search or scheduled review
execution was started. Hermes remains the scheduling backend for every agent.

All four runs used temporary native client settings, med-lit stores and review folders. The
workstation runs also used isolated Hermes homes. Hashes of the existing user settings were
unchanged. The synthetic contact address, key, private form URL and access token were absent from
captured transcripts and native client state, including SQLite databases and WAL files. Temporary
login copies, client homes and the test checkout were removed after testing. An initial workstation Codex attempt could not refresh its
expired login; the successful retry used a temporary copy of the working local login, leaving the
workstation's saved login untouched.

The repository suite passed **312 tests, 1 skipped, 26 subtests**. The final registration and bot
regression checks passed **39 tests, 6 subtests**, and lint and whitespace checks passed. These are
source-checkout checks performed before the 0.4.0 release. The form opens in a local browser;
these checks do not establish an embedded chat card or prevent an unrestricted agent from reading
local files outside the intended setup flow.

## Release gates

- **Script runs.** Release CI runs both install scripts from the checkout, without registering, on Linux, macOS and Windows. A run that registers with real clients (Hermes, Codex, pi) and the ChatGPT browser form is checked by hand on the platforms available before a release, and recorded here.
- **Source versus download.** Tests run from the source checkout do not establish the behaviour of a downloaded script. Before announcing a release, run the script downloaded from the public repository against the published PyPI version.
