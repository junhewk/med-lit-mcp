"""On-demand guidance for the agent, served by the guide tool.

Tool and field descriptions stay short so clients that load tools on demand (Tool Search in
Hermes, Claude Code and Claude Desktop) can list them cheaply; the detail lives here.
"""

from __future__ import annotations

from .contracts import DUPLICATES_CONTRACT, SYNTHESIS_CONTRACT, extraction_contract
from .ontology import ENTITY_TYPES, MENTION_ROLES
from .research import EVALUATION_RULES, REPORT_RULES

_TYPES = "\n".join(
    f"- {name}: {definition} (Simple Graph Builder type {parent})" for name, (parent, definition) in ENTITY_TYPES.items()
)
_ROLES = "\n".join(f"- {name}: {meaning}" for name, meaning in MENTION_ROLES.items())

GUIDES: dict[str, str] = {
    "workflow": """\
# med-lit workflow

Stages: project → search → screening → (fetch → wiki) → report. For a researcher's question, let
research_next lead (guide "research"): it returns each step and stops at every decision that is the
researcher's. Otherwise start each stage only when the researcher asks; within a stage, repeat its
batch tool while remaining > 0, then report and stop.

1. Project: list_projects; create_project(name, path?) for a new topic; open_project(path) for a moved folder.
   project_settings shows the review's settings (years, records per source, preprints, fetch limit,
   pages per article); change them only when the researcher asks.
2. Search: draft the question (guide "question"), validate_question, show the researcher the
   normalized question, sources, date range and warnings, wait for approval, then
   start_search(question_id). If it reports running, resume_search(run_id).
   resume_search(run_id, retry_failed_sources=true) later retries sources that failed.
3. Screening (guide "screening"): set_screening_criteria; triage the titles when asked
   (next_triage_batch, record_triage_scores); then next_screening_batch and record_screening_decisions,
   20 articles per session. review_article only for the researcher's own decision.
4. Fetch (guide "fetch"): fetch_articles until remaining is 0.
5. Wiki (guide "wiki"): call wiki_tasks and follow it; export_wiki at the end.
6. Report (guide "report"): next_report, then record_report; reply with its message.

Status at any time: get_run_status(run_id), list_runs, list_articles. On failure keep the run ID and
rerun the same tool; never start a new search to retry a stage. Report every failed source and any
non-zero records_filtered_by_source. A retrieved and screened set is not a completed systematic review.""",
    "research": """\
# A review from question to report: research_next

Call research_next(project) and do what its `do` says, then call it again (pass the run_id once there
is one). A step with ask_researcher=true is the researcher's decision: show them what the step lists
in plain words (not raw JSON), ask, and stop until they answer. Between steps, keep them posted in a
line or two (what was done, what comes next); do not paste tool output.

Modes, chosen at the first step and saved as research.mode with project_settings:
- basic: search, triage titles, screen titles and abstracts, then a report from the abstracts. Quick.
- full: the same, then fetch the included articles' full text, build the evidence wiki, and a report
  that can quote the full text. Takes longer.
- deep: full mode in rounds, like a bot's runs in one session. After each round you judge whether the
  included evidence answers the question (record_evaluation). If not, your revised search adds the
  best new articles (research.round_articles), which are screened, fetched and added to the wiki, until
  the evidence is enough or research.deep_rounds is reached. Choosing deep with its round limit is the
  researcher's approval of those searches; each is validated and named in the report. Longest.

Checkpoints, in order:
1. parameters: the mode and the search settings (years, sources, records per source, preprints,
   languages, publication types, research.max_screened, fetch.limit). Change only what they ask.
2. question: the drafted question as validate_question normalized it, with sources, dates and warnings.
3. criteria: inclusion and exclusion criteria drafted from the approved question, each with the reason
   it follows from the question. Saved with set_screening_criteria(..., reasons={"I1": ..., "E1": ...}).
   In deep mode later rounds keep these criteria, so they follow the research question, not the terms
   of the first search.
4. screening (basic and full): after each session of 20, screen on or stop (research_next
   confirm="stop_screening"). With research.max_screened set, screening goes on to that number without
   asking. Deep mode ignores max_screened: each round keeps research.round_articles and screens
   them all without asking.
5. uncertain: the researcher decides each (review_article), or leaves the rest undecided
   (confirm="leave_uncertain"); undecided articles are not part of the answer.
Then (full, deep) fetch and wiki, (deep) the evaluation of each round, then the report. Pass confirm only
when the researcher said so.

In a research mode every exclude names its criterion code (E1 for an exclusion criterion met, I2 for
an inclusion criterion failed), so the report can count the reasons for exclusion.
When the report is saved, reply with its message: the answer first, then the counts and the file.""",
    "report": "# Writing the report\n\nnext_report gives the question, the methods facts and the included "
    "articles, in portions: call it again with the offset it gives while remaining > 0. Then call "
    "record_report(run_id, answer, findings, body, limitations). If it returns saved=false, fix each problem "
    "and call it again. Reply to the researcher with its message.\n\n" + REPORT_RULES,
    "evaluation": "# Judging a deep round: record_evaluation\n\n" + EVALUATION_RULES,
    "question": """\
# Drafting the search question

- Framework: PICO when an intervention or exposure is central (population and intervention
  required; comparison and outcome optional). PCC for scoping questions (population and concept
  required; context optional).
- Keep the researcher's own words in `question`. A group's `text` and synonyms are search terms:
  each is matched as an exact phrase in titles and abstracts, so write each as papers write it,
  in a few words ("history-taking", not "student interviews (history-taking, OSCE ...)").
  validate_question rejects sentence-like terms (more than 6 words, "or", brackets).
- Groups inside a component are ANDed; a group's text and synonyms are ORed.
  - Give each separately required facet its own group: "LLMs that conduct student interviews" is
    two groups, technology AND interview task. A facet merged into another group's text is lost.
  - Put only alternatives for the same facet in one group (patients OR clinicians).
- Population: name it as precisely as the question allows, one group per facet: the condition
  (type 2 diabetes), the role (medical students), and an age only when the question is about that
  age. Never put an age beside a condition in one group; validate_question moves it to an age group
  of its own. An age group misses studies that do not state the age, so when "adults" only rules
  out children, leave it to the screening criteria. Words like "patients" or "people" barely
  narrow a search; use them only as true alternatives.
- Synonyms: only terms that name the same thing, and spelling variants. A broader or neighbouring
  concept is not a synonym ("decision support" for shared decision-making, "resident" for medical
  students). Leave out ambiguous bare acronyms (LLM, SDM, GPT) unless the researcher supplied or
  approved them.
- Required groups decide what is retrieved. Study design and outcome-type words (experience,
  perceptions, effectiveness, qualitative) miss papers that use other words: put them in an
  optional component (outcome or context) or in the screening criteria, not in a required group.
- PCC context is only for the setting (care setting, geography, education setting), never a spare
  topical facet. Optional components (comparison, outcome, context) narrow only the precision variant.
- candidate_mesh: suggest MeSH headings for every group, such as "Decision Making, Shared".
  validate_question checks them against NCBI: a near miss is corrected to the real heading, an
  unknown one is removed. A group without one is matched in PubMed by its words only.
- Show the researcher the repairs and warnings validate_question returns with the question.
- Filters: add only restrictions the researcher asked for. Without filters.from_date the search
  covers only the last three years; validate_question states the effective start date.
- Sources: leave sources out unless the researcher names some. The default is pubmed, pmc and
  openalex, plus semantic-scholar and scopus when their keys are set. europepmc must be searched on
  its own.
- Pass project to validate_question: the project's settings fill in the year window, languages,
  publication types and sources when the question does not set them, and the result lists them.
- validate_question returns a question_id. After the researcher approves exactly what was shown,
  including the settings, pass that id to start_search; to change anything, validate again.
- Preprints are excluded in each source's query unless the project's search.preprint_allow is true;
  any that still come back are dropped and counted as skipped_preprints.
- Articles without a DOI, PMID or PMCID are skipped (skipped_no_identifier in the run status). Add
  them with add_skipped_articles only when the researcher asks for them.
- A later search in the same project imports only articles new to the project (matched by DOI,
  PMID, PMCID, record id or an identical abstract). Articles an earlier search found are counted
  as already_known and keep that search's screening and wiki work; say so when reporting the results.""",
    "screening": """\
# Screening titles and abstracts

- Screening goes 20 articles per session, best first. When more than 20 wait, next_screening_batch
  asks for triage: repeat next_triage_batch and record_triage_scores until remaining is 0, scoring
  each title alone 0-3 (3 clearly meets the criteria, 2 probably, 1 related but probably not,
  0 unrelated). Judge what the study is about, not shared words: a study that only uses the
  intervention as a measuring tool is unrelated to a question about the intervention.
- A session holds the 20 best-triaged articles (search rank breaks ties). Repeat next_screening_batch
  and record_screening_decisions until it reports the session complete, then report to the
  researcher and stop. Start the next session with next_screening_batch(new_round=true) only when
  the researcher asks; fetch waits until every article is screened.
- Judge each item against the saved criteria using only its title and abstract.
- include: clearly meets the inclusion criteria. exclude: clearly meets an exclusion criterion.
  uncertain: missing or ambiguous information. Never infer facts that are not in the record.
- include and exclude need `evidence`: an exact short quote from the title or abstract (copy it,
  do not retype it). A decision whose quote cannot be found is stored as uncertain and listed as
  downgraded; you may resubmit it once with a real quote.
- Criteria have codes, I1, I2 … and E1, E2 … in order (criterion_codes). An exclude names the one it
  rests on as `criterion`: E1 when it meets exclusion criterion 1, I2 when it fails inclusion
  criterion 2. In a research mode an exclude without one is downgraded.
- Uncertain articles wait for the researcher: use review_article only with their decision and reason.
- Changing criteria needs replace=true, starts a new revision and re-screens every article; tell
  the researcher first.""",
    "fetch": """\
# Fetching full text

fetch_articles tries PMC (NCBI, then Europe PMC); for articles without a PMCID it asks Unpaywall for
legal open-access copies (a PMC copy first, then an open-access PDF); the abstract is the last resort.
Repeat while remaining > 0 with the same options. Never describe abstract_only material as full text.
retry_abstract_only=true and retry_failed=true try those articles again (each once per pass).
Articles are fetched in the search's relevance ranking. With a project fetch.limit, articles beyond it
are marked skipped and listed in skipped_over_limit; report them. fetch.mode abstract_only skips full text.""",
    "wiki": """\
# Building the wiki

Call wiki_tasks(run_id). It returns the current step (extract, duplicates, synthesize, export) as
self-contained task texts; each carries the full rules of its kind and names the tools it may use
(`tools`). Article pages are large: if you can delegate, give each task verbatim to a fresh subagent,
one after another, with only those tools; otherwise do the tasks yourself in order. A long step comes
in portions (remaining_tasks): call wiki_tasks again when the tasks are done, for the rest and then
the next step. The rules are also in guide "extraction", "duplicates" and "synthesis".
An article's pages are read up to the project's wiki.max_pages; a per-call max_pages cannot exceed it.""",
    "extraction": "# Extracting entities and relationships from a page\n\n" + extraction_contract(),
    "duplicates": "# Resolving possible duplicates\n\n" + DUPLICATES_CONTRACT,
    "synthesis": "# Writing and updating an entity page\n\n" + SYNTHESIS_CONTRACT,
    "ontology": f"""\
# Entity types and roles (ontology med-lit/1)

Each type refines one Simple Graph Builder type.
{_TYPES}

Mention roles:
{_ROLES}""",
}
