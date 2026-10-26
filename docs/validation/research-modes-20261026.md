# Research modes test: Hermes and pi, basic, full and deep — 2026-10-26

> The first four runs used the mode then called *deep*, which fetched full text and built the wiki once. It is
> now called **full**; *deep* now runs full mode in rounds (see the last section). The run names
> `hermes-deep` and `pi-deep` are kept as recorded.

## Environment and scope

jkworkstation (Linux). Hermes Agent v0.21.5 and pi 1.3.0, both on `qwen3.8-flash-next` (262k context,
medium reasoning) served by the workstation's own gateway. med-lit 0.2.1 plus the uncommitted research-mode
change, run from a synced checkout (`uv run --directory`). Each run had its own med-lit state, config
(contact email only) and projects folder, and an empty study folder.

- **Hermes:** a fresh `HERMES_HOME` with the comparison harness's research profile: no tool search,
  compression or memory; web, browser and other unneeded toolsets off; bot tools excluded; one subagent at a time.
- **pi:** a fresh `PI_CODING_AGENT_DIR` with the documented research settings (compaction off). It was
  registered by `setup --client pi --dev` and given the med-lit pi package (`pi install <checkout>`).

The tester played the researcher, one non-interactive turn per message (Hermes `chat -Q --resume`, pi
`-p --session-id`), and read every tool call and the stored state after each turn.

The question was the same in all four runs: *how are large language models being used to support
shared decision making between patients and clinicians?* The researcher chose these settings at the
first checkpoint:

- 15 records per source, publications from 2023 onward.
- Deep runs only: screen at most 20 articles (`research.max_screened`) and fetch at most 4 (`fetch.limit`).

The researcher changed one thing in every run: the criterion for non-primary studies also excludes reviews.

This is a test of the research flow, not a completed review.

## Runs

| | Hermes basic | pi basic | Hermes deep | pi deep |
|---|---|---|---|---|
| Turns / total time | 6 / 392 s | 5 / 404 s | 5 / 1,186 s | 5 / 1,130 s |
| Candidates / screened | 37 / 20, then 37 | 37 / 37 | 37 / 20 (cap) | 37 / 20 (cap) |
| Included / excluded / uncertain | 20 / 17 / 0 | 8 / 29 / 0 | 8 / 12 / 0 | 12 / 8 / 0 |
| Full text / abstract only / beyond fetch limit | — | — | 4 / 0 / 4 | 2 / 2 / 8 |
| Entity pages (synthesized) | — | — | 90 (14) | 85 (10) |
| Key findings with checked quotes | 16, then 20 | 20 | 18 | 25 |
| Report | `reports/… 44fff5fc.md`, `…-2.md` | `reports/… 1b69a7fc.md` | `reports/… 09b31d76.md` | `reports/… bcc536bd.md` |

Inclusion counts differ because each run drafted its own criteria and the model screened them; the
flow, not the screening judgment, is what was tested.

## Step by step

| Step | Expected | Hermes basic | pi basic | Hermes deep | pi deep |
|---|---|---|---|---|---|
| 1. First request (no mode named) | Project in the given folder; `parameters` checkpoint; modes and every setting shown; stop | ✓ | ✓ | ✓ | ✓ |
| 2. Mode and settings | One `project_settings` call; `question` checkpoint; validated draft with settings and warnings; stop | ✓ (first `validate_question` passed the question as a string, retried) | ✓ (same slip, retried) | ✓ (`max_screened` 20 and `fetch.limit` 4 saved) | ✓ |
| 2b. Redraft ("make shared decision making required") | Validate again, show the new draft, stop | — | — | ✓ | ✓ (also said what the change costs in recall) |
| 3. Approve question | `start_search`; `criteria` checkpoint; results and failures reported; I/E criteria each with a reason; stop | ✓ | ✓ | ✓ | ✓ |
| 4. Approve criteria with one change | `set_screening_criteria` with reasons; change applied with an updated reason | ✓ | ✓ | ✓ | ✓ |
| Triage (37 > 20) | `triage` step, every title scored | ✓ | ✓ | ✓ | ✓ |
| Screening | Excludes carry criterion codes; non-verbatim quotes downgraded and fixed once | ✓ (2 quotes corrected) | ✓ (one exclusion on a failed inclusion criterion, I2) | ✓ | ✓ |
| Session boundary, no cap | `screening` checkpoint; stop | ✓ asked, but without the final `research_next` call | ✓ via `research_next` | — | — |
| Session boundary, `max_screened` 20 | Stop automatically, no question | — | — | ✓ | ✓ |
| Stop screening | `confirm="stop_screening"` → `report` | ✓ | — (screened on) | — | — |
| Screen on after the checkpoint | `new_round=true`; all screened | ✓ (after a stop: the stop was lifted) | ✓ | — | — |
| Uncertain | `uncertain` checkpoint | none arose | none arose | none arose | none arose |
| Fetch (deep) | Only the 4 best-ranked included articles; the rest marked beyond the limit | — | — | ✓ | ✓ |
| Wiki (deep) | `wiki_tasks` followed to export; one fresh worker per task | ✗ extraction looped on one article (wiki bug, below); finished after the parent read the page itself | — | — | ✓ every task through `run_subtask` with scoped tools |
| Report | Every portion read; citations and quotes checked; methods and counts from the record; reply with answer, counts and path | ✓ first try | ✓ second try (limitations sent as objects, rejected by pi's schema check, resent) | ✓ first try | ✓ first try, but pi cut 2 `next_report` portions (size bug, below) and the agent read the overflow file |
| Report after more screening | Out-of-date report rewritten as a new file; the first kept | ✓ `…-2.md` | — | — | — |

## Problems found and fixed during the test

Each fix was synced before the next run; `.commit` in the synced checkout records the order.

1. **Limitations written twice** (Hermes basic). The model added "abstracts only" and "screening stopped",
   and the server added them again. Now `next_report` returns `limitations_added` (the server's list) and the
   rules say not to repeat them. pi basic and both deep runs show no repeats.
2. **Exclusion reasons line repeated each criterion's full text.** Now: codes and counts, defined by the
   criteria table above it.
3. **Fetch limit not named in the report** (Hermes deep; the model mentioned it itself). Now the
   limitations say how many included articles were beyond the fetch limit, had no open full text, or were
   not fetched, and the methods list the text read per article ("beyond the fetch limit 8, full text 2,
   abstract only 2"). Seen in pi deep.
4. **Wiki: an article stuck in extraction** (Hermes deep; present in 0.2.0). A worker recorded page 0 of 2,
   then called `next_wiki_article(uid, max_pages=1)`. The lower allowance was stored, the article was reported
   done, but it was never marked extracted, so `wiki_tasks` kept handing it out. Now an article whose
   allowance is fully recorded is marked extracted (`wiki._complete_extracted`; regression test in
   `test_settings.py`, which fails without the fix). Not triggered again in pi deep, so this fix is verified
   by the test only.
5. **`next_report` portions over pi's 20 KiB** (pi deep: 29 KB and 22 KB). Every portion repeated the
   rules and methods, a deep article carried long mention descriptions, and `remaining`/`next` were added
   after measuring. Now the rules, methods and entity summaries come with the first portion only, mentions
   carry no descriptions, an oversized article loses wiki rows before abstract text, and the size is
   reserved before measuring. On the pi deep data the five portions are 18.2, 10.2, 18.3, 18.3 and 11.1 KB,
   none oversized; a unit test covers it. Measured offline on the run's stored data, not in a new pi session.

## Not exercised

- The `uncertain` checkpoint: no run produced an uncertain decision. Unit and MCP tests cover it.
- A researcher's `review_article` on an included article after the report (staleness): unit tests only.
- Hermes delegated duplicates review and one extraction back to the parent rather than a subagent; med-lit
  allows this, and the wiki was complete.

## Follow-up: Hermes tool-argument fix

**Problem.** In Hermes basic, the first `validate_question` call carried `question` as a string, and the
string was not valid JSON (one `}` too many before `"filters"`), so the MCP layer rejected it. The SDK
already parses arguments sent as JSON strings; this one could not be parsed. It was the only model-side
argument error in Hermes's 112 tool calls.

**Cause.** For an OpenAI-compatible endpoint, Hermes passes MCP schemas to the model with `$ref`/`$defs`
as they are (it inlines them only for Gemini). med-lit's schema gave `question` as
`{"$ref": "#/$defs/ResearchQuestion"}`, with no type at that level. Seven tools had such references.

**Fix.** The server publishes every tool's argument schema with same-document `$ref`s inlined
(`server.inline_tool_schemas`), so each argument states its own type; validation still uses the Python
models. `validate_question`'s schema grows from 3.3 KB to 7.2 KB. A server test checks that no listed
tool has a `$ref`.

**Controlled replay.** The conversation Hermes had before the failed call (messages 1–19 and the system
prompt, from its `state.db`) was sent to the same model 20 times per arm. The tool list was med-lit's,
put through Hermes's own schema normalization, and only the schema form differed; arms alternated.

| Schema | `validate_question` accepted | `question` sent as a string (invalid JSON, rejected) | Median response |
|---|---|---|---|
| `$ref` (before) | 14 / 20 | 6 / 20 | 4.6 s |
| Inlined (after) | 20 / 20 | 0 / 20 | 4.8 s |

**Live run (`hermes-basic-2`, fresh profile, same researcher messages).** 7 turns, all exit 0.

| Step | Result |
|---|---|
| Parameters, question | ✓; `validate_question` accepted on the first call, `question` sent as an object |
| Criteria with reasons, the E3 change | ✓ |
| Triage, screening of the first 20 | ✓; 19 excluded (all with codes), 1 uncertain |
| Session boundary | Hermes asked "continue or stop?" directly. The last `record_screening_decisions` result now names `research_next` (`next`), but Hermes did not call it. Its question matched the checkpoint, and after "stop" it called `research_next(confirm="stop_screening")` |
| Uncertain (first time seen live) | ✓ `uncertain` checkpoint; the researcher's exclusion recorded with `review_article(..., criterion="E2")` |
| Report with no included article | ✓ the first attempt cited excluded articles and was refused with nothing saved; the second stated that no screened study answers the question |
| Screen the rest, update the report | ✓ stop lifted, 37 screened, `…-2.md` written, first report kept |

**Found and fixed.** After screening the rest, `research_next` reported *done*: a report counted as current
while the included set was unchanged, though its counts ("17 not screened") were out of date. Now the
screening counts are part of the check (`report.selection`), with a test that fails without it.

## Follow-up: adapting to Hermes at the end of a screening session

Hermes ends its turn at a natural question ("continue or stop?") instead of first calling
`research_next`; that is how Hermes works, so the server adapts to it rather than asking for the extra call.

**Change.** In a research review, the call that ends a screening session (`record_screening_decisions`
with no unsettled downgrades, or `next_screening_batch` once the session is screened) returns `next_step`:
the review's next step computed by the server, the same as `research_next` would return (the
`screening` checkpoint with its counts and both routes, or the automatic `max_screened` stop and what
follows). The agent asks from the server's counts whether or not it calls `research_next`. If an agent
goes to the report after the researcher says stop, the error now names
`research_next(run_id, confirm='stop_screening')` as well as screening on.

**Live run (`hermes-basic-3`, fresh profile, same researcher messages).** 6 turns, all exit 0.

| Step | Result |
|---|---|
| Parameters, question, criteria | ✓; `validate_question` accepted on the first call |
| End of the first session | Hermes again asked at once without calling `research_next`. The result it read carried `next_step` (`screening`, ask_researcher, include 0 / exclude 19 / uncertain 1 / pending 17), and its question used exactly those counts |
| "Stop" | ✓ it called `research_next(confirm="stop_screening")`, the route `next_step` named, and got the `uncertain` checkpoint |
| "Leave it undecided" (first time seen live) | ✓ `confirm="leave_uncertain"` recorded; report written; the limitations state 17 unscreened (researcher) and 1 undecided |

Hermes added outside knowledge about the undecided article ("a well-known … review") as a suggestion to
the researcher; it did not decide it.

## Follow-up: the readable report

The report was reorganised for a reader who did not see the review, in every mode: the answer first, the
agent's sections, "How much to trust this" (the agent's limitations, then the record's in plain words),
"How this review was done" as a few paragraphs from the record, numbered references ([1], [2] by first
citation, with links and uids), and an appendix with the key findings and their quotations, the criteria
with reasons, the exclusion counts and the search terms. The report rules now ask for plain language,
spelled-out abbreviations, and no criterion codes, tool names or uids in the text. The agent's inputs are
stored with the report, so `render_report` can write it again in a later format.

The six reports from the runs above were re-rendered in the new format from the `record_report` arguments in
their logs, without a model call (`runs/<run>/readable/report.md` on jkworkstation). Their text was written
under the earlier rules, so abbreviations in it are not yet spelled out.

## Follow-up: deep mode in rounds (`hermes-deep-2`)

Deep mode now runs full mode in rounds, like a bot's runs in one session: after each round the agent reads
the included evidence (`next_report`) and records `record_evaluation` (sufficient, assessment, gaps, the
studies that answer the question directly, and, when not sufficient, a revised search). The server validates
the search, refuses one that repeats an earlier round, and searches it into the same review with the
question's dates. The new articles wait as candidates, are triaged, and the best `research.round_articles`
are kept; they are screened (without the continue-or-stop checkpoint), fetched and added to the wiki, and
the round is judged again, up to `research.deep_rounds`. Deep mode ignores `max_screened`.

Fresh Hermes profile; researcher's settings: deep, 2 rounds, 10 new articles per extra round, 10 records per
source, 2023 onward, 1 wiki page per article. To give round 1 a real gap, the researcher asked for the
technology to be searched only as "ChatGPT". 6 turns, all exit 0, 25 minutes in all.

| Step | Result |
|---|---|
| Parameters | ✓ three modes shown; all six settings saved in one call |
| Question, narrowed | ✓ redrafted to "ChatGPT" only; the agent noted round 2 could broaden it |
| Criteria, "reviews out" | ✓ the agent also revised the inclusion criterion that admitted systematic reviews |
| Round 1 screening | ✓ 27 triaged and screened in two sessions without asking (`next_step` = `screen`); 4 included, 3 uncertain |
| Uncertain | ✓ checkpoint from the last screening result's `next_step`; the researcher's three decisions recorded with codes |
| Round 1 fetch, wiki | ✓; one `wiki_tasks` call carried `delegate_task`'s arguments (model slip, resent correctly) |
| Round 1 evaluation | ✓ not sufficient; gaps "LLM tools not named ChatGPT" and other settings; revised search with the full technology terms plus preference elicitation, decision aids, risk communication |
| Round 2 | ✓ 12 new articles, triaged, best 10 kept, screened (1 included), fetched, extracted and synthesized |
| Round 2 evaluation | ✓ sufficient; remaining gaps named (effectiveness evidence, other specialties) |
| Report | ✓ saved first try in the readable format: answer, sections by use, "Is the evidence enough?", limitations, rounds in "How this review was done", both searches in the appendix |

Hermes's own limit on subagents per one-shot run (`delegation.oneshot_max_children` 8 in this test profile)
was reached in round 2; the parent did the remaining synthesis itself, and the wiki was complete. A deep
review needs about one subagent per included article and per five entity pages in each round.

**Found and fixed.** The report said the first search "found 37 articles", counting both rounds (it found
27); it now gives the first search's count and, per round, how many new articles were kept of how many found.
Two limitation lines had "1 included study … were"; counts and verbs now agree.

## Follow-up: deep mode in an interactive Hermes session (`hermes-deep-3`)

The earlier runs sent each researcher message as a one-shot `hermes chat -Q` call, and Hermes caps subagents
only in one-shot sessions (`delegation.oneshot_max_children`; "interactive and gateway sessions are never
charged"). This run used a real interactive session: `hermes chat --cli` in a tmux session over SSH, the
researcher's messages typed into it, and the results read from the profile's `state.db`. Same fresh profile
and the same deep settings and messages as `hermes-deep-2`. 6 turns, about 42 minutes.

| Step | Result |
|---|---|
| Parameters, question (narrowed to "ChatGPT"), criteria with the "reviews out" change | ✓ |
| Round 1 | ✓ 18 screened without asking, 6 included; the `uncertain` checkpoint for 1, included by the researcher |
| Wiki work | ✓ Hermes runs each subagent as a background task ("I'll resume when it finishes"), posts a progress line, and carries on by itself: **19 subagents**, no budget error (the one-shot run stopped at 8) |
| Round 1 evaluation | ✓ not sufficient ("no study measured decisional outcomes"); revised search for LLM decision aids and decision-outcome studies |
| Round 2 | ✓ 21 new articles, the best 10 kept, screened, 6 included, fetched and extracted |
| Round 2 evaluation | ✓ sufficient, on the second call (below) |
| Report | ✓ 13 of 28 included; both rounds in "How this review was done"; all 13 articles complete in the wiki (137 entity pages, 22 synthesized) |

**Found and fixed.** With the evidence sufficient, Hermes sent `question: {}` to `record_evaluation`:
Hermes collapses the optional `anyOf: [question, null]` to its object branch, so the model sends an empty
object. The server refused it as a malformed question, and Hermes resent the call without it. Now an empty
`question` counts as none (MCP test in `test_server.py`, which fails without the change).

## Final test: deep mode in an interactive pi session (`pi-deep-2`)

pi 1.3.0 run interactively in a tmux session over SSH (`pi --session-dir …` in the study folder), set up as a
researcher would: a fresh agent folder with the model and the documented research settings, `setup --client pi
--dev`, and the med-lit pi package (`pi install`), so `run_subtask` workers and the research policy were
active. The researcher's messages were typed into the session and read from pi's session file. Same deep
settings and narrowed first search as the Hermes runs. 6 turns, about 40 minutes.

| Step | Result |
|---|---|
| Parameters, question (narrowed to "ChatGPT") | ✓; one `validate_question` call left out the `question` wrapper and was caught by pi's schema check, then resent |
| Criteria | The agent drafted them from the narrowed search (I1 "ChatGPT", E3 "not named ChatGPT"), which would have excluded round 2's articles; the researcher corrected them, and the stored criteria follow the question |
| Round 1 | ✓ 27 triaged and screened without asking, 9 included; the `uncertain` checkpoint for 1, excluded by the researcher with a code |
| Wiki work | ✓ every task through `run_subtask` (16 workers, each with only its task's tools), no limit |
| Round 1 evaluation | ✓ not sufficient (no clinician-side use in a decision dialogue; the ChatGPT-only wording); the agent validated its revised search before recording it |
| Round 2 | ✓ 15 new articles, the best 10 kept, screened, 1 included, fetched and extracted |
| Round 2 evaluation, report | ✓ sufficient; report saved first try: 10 of 37 included, both rounds described, 10 numbered references, no uids in the text |

No tool errors in the turns that ran the rounds.

**Found and fixed.** In deep mode every round is screened against the first round's criteria, so criteria
drafted from a narrowed first search exclude what later rounds are meant to add. The `criteria` step now
says so in deep mode ("draft them from the research question itself, not from this round's search terms"),
and so does `guide("research")`; a unit test checks it. The test session kept the code it started with, so
this guidance was not seen live.
