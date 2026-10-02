> Historical test record before consolidation. See [current validation status](../validation.md) for the current source and release scope.

# ChatGPT Work workflow test — 2026-10-02

## Environment and scope

macOS 15.7.9 (24G830), Apple Silicon; ChatGPT 26.928.31416 (12553),
Codex CLI 0.159.2. Native stdio med-lit 0.1.6 from an unpublished source checkout.
The user completed the browser settings form. The installed onboarding plugin and
native tools were exercised in a trusted local ChatGPT Work project, using normal
client approvals. The tester controlled the app through macOS UI automation;
searching, screening, extraction and synthesis were performed by ChatGPT through
the native MCP tools. SSH filesystem reads independently verified the outputs.

The researcher approved this question:

> Among adults with hypertension, does home blood-pressure telemonitoring compared
> with usual care improve systolic blood pressure?

PubMed and PMC, 2020–2025; fetch at most three included papers; extract at most one
page per paper; synthesize entities with at least one source. Screening required
adult hypertension, telemonitoring versus usual care, and systolic pressure outcomes.
Uncertain records stayed uncertain. This is a bounded integration test, not a
completed systematic review or a treatment recommendation.

## Recorded runs

Project: `chatgpt-e2e-20261002`.

| Result | Initial run | Expanded run |
|---|---|---|
| Run ID | `79949908b8ff4d2693ff7470d36e7085` | `dac7cd922d424aedadda3d6e547fad38` |
| Limit per source | 3 | 20, explicitly approved by the researcher |
| PubMed / PMC records | 3 / 3 | 13 / 12 |
| Unique records retrieved | 4 | 13 |
| New candidates / already known | 4 / 0 | 9 / 4 |
| Included / excluded / uncertain new candidates | 0 / 2 / 2 | 1 / 3 / 5 |
| Unscreened new candidates | 0 | 0 |
| Full-text / abstract-only fetched | 0 / 0 | 1 / 0 |
| Extracted pages | 0 | 1 |
| Entities / relationships | 0 / 0 | 8 / 6 |
| Synthesized entity pages | 0 | 8 |
| Exported source pages | 0 | 1 |

There were zero search runs before question approval. Both searches completed
without source failures. The expanded run preserved all four earlier records and
their screening decisions. Seven uncertain decisions remain across both runs.
The first run exercised the empty-fetch/export path; it did not exercise extraction
or synthesis. Expanding the approved search made those stages testable.

## Evidence and exported wiki

Fetched `pmc:PMC8678721` through `pmc_efetch_xml`: 24,072 characters of full text,
one configured extraction page. The paper uses a nonrandomized controlled design.
The extracted page covers methods and baseline material; synthesized pages retain
that limitation rather than drawing conclusions from unread results.

Wiki root on the test Mac:

```text
/Users/jk/Library/Application Support/med-lit-mcp/desktop-test-0.1.6-20261001/e2e-reviews/chatgpt-e2e-20261002
```

Outputs include `index.md`, `log.md`, eight files under `entities/`, and one under
`sources/` (`Yue 2021 - Home blood pressure telemonitoring for improving blood pressure.md`).

Independent checks found:

- All eight entity mentions and six relationship evidence spans appeared verbatim
  in the served extraction page.
- All entity pages contained source citations; all exported local Markdown links
  resolved to existing files.
- Eight stored syntheses, none stale; zero pending duplicate candidates. Duplicate
  checking ran, but this dataset did not exercise a merge or distinct-pair decision.
- One completed extraction page and no rejected extraction evidence.
- ChatGPT completed synthesis sequentially and reported successful wiki export.
- A final native `wiki_tasks` call returned `step: export` with an empty task list;
  final run-status and export calls confirmed completion. The exported index opened
  successfully in ChatGPT's rendered file preview through its response link. Clicking
  the telemonitoring entry opened its synthesized page with rendered citations and
  entity links.

## Bug found and corrected

Before an article was opened, `wiki_tasks` reported the full three-page count even
though `wiki.max_pages` was one. The reader enforced the cap correctly, and ChatGPT
explicitly selected one page during the test. Task planning now previews the same
cap before the reader initializes its count, without overriding a stored explicit
reader choice. A regression test covers both the configured cap and reader override.
The relevant 27 tests passed on Linux and macOS; both complete suites also passed.

## Remaining release gates

This run reused uv and an unpublished source checkout. It does not verify first-time
runtime download, installation from the public plugin directory/PyPI, macOS Intel,
Windows desktop, real duplicate resolution, abstract-only fallback, or a complete
Codex CLI review. Those gates remain in [current validation status](../validation.md).
