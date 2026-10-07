"""On-demand guidance for the agent, served by the guide tool.

Tool and field descriptions stay short so clients that load tools on demand (Tool Search in
Hermes, Claude Code and Claude Desktop) can list them cheaply; the detail lives here.
"""

from __future__ import annotations

from .ontology import ENTITY_TYPES, MENTION_ROLES

_TYPES = "\n".join(
    f"- {name}: {definition} (Simple Graph Builder type {parent})" for name, (parent, definition) in ENTITY_TYPES.items()
)
_ROLES = "\n".join(f"- {name}: {meaning}" for name, meaning in MENTION_ROLES.items())

GUIDES: dict[str, str] = {
    "workflow": """\
# med-lit workflow

Stages: project → search → screening → fetch → wiki. Start each stage only when the researcher asks;
within a stage, repeat its batch tool while remaining > 0, then report and stop.

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

Status at any time: get_run_status(run_id), list_runs, list_articles. On failure keep the run ID and
rerun the same tool; never start a new search to retry a stage. Report every failed source and any
non-zero records_filtered_by_source. A retrieved and screened set is not a completed systematic review.""",
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
self-contained task texts. Article pages are large: if you can delegate, give each task verbatim to a
fresh subagent, one after another; otherwise do the tasks yourself in order. Call wiki_tasks again
for the next step. See guide "extraction", "duplicates" and "synthesis" for the rules each task follows.""",
    "extraction": f"""\
# Extracting entities and relationships from a page

- Work only from the page text next_wiki_article returns; no outside knowledge.
  Submit each page with record_extraction, passing the header's uid, content_sha256 and page.
  get_article_page(run_id, uid, page) re-reads a page; find_entities looks up known entities.
- Extract 5-18 substantive entities per page. Broad concepts (artificial intelligence, large
  language models) only when the page substantively depicts them. An empty list is valid.
- Never extract publication metadata: years, PMID/PMCID/DOI, licenses, copyright, journal or
  publisher names, dates, or generic words such as article, paper, study, source.
- Never extract tools used only to run the study: statistics and survey software (SPSS, Stata, R,
  GraphPad Prism, NVivo, REDCap, Qualtrics). Extract a PLACE or ORGANIZATION only when the article
  says something about it, not when it is merely where the study was done or who funded it.
- name: the canonical name. Expand acronyms except well-known ones (FDA, WHO);
  "expansion (ACRONYM)" records both. Reuse a known_entities name, or pass its id as entity_id.
- mention: a verbatim span from this page naming the entity. description: 1-2 sentences on how
  this page depicts it.
- role: the PICO/PCC element the entity plays in the study this article reports; omit it for
  background or discussion mentions.
{_ROLES}
- Relationships: source and target must be entities in the same call; relationship is an active
  verb (evaluates, supports, limits, causes); evidence is a verbatim quote from this page; detail
  says how it shows up.
- entity_type:
{_TYPES}""",
    "duplicates": """\
# Resolving possible duplicates

list_duplicate_candidates shows the pending pairs with an example mention of each.
Merge only names for the same thing at the same level of abstraction: acronym and expansion,
spelling or plural variants. Keep generic and specific terms distinct (machine learning vs deep
learning; decision aid vs patient decision aid). resolve_duplicates takes merge or distinct per pair;
merge_entities can also rename or retype an entity.""",
    "synthesis": """\
# Writing and updating an entity page

next_synthesis returns mode "new" or "update".

- new: write only from the context, in a neutral encyclopedic tone, describing how the gathered
  articles depict the entity rather than a generic definition. Sections, as '## ' headings: Overview,
  How Gathered Articles Depict It, Recurring Themes, Tensions and Limitations, Relationships. Use the
  PICO/PCC roles to say how studies used the entity. Send summary, synthesis, key_aspects and
  related_entities.
- update: the page exists (current_synthesis). The mentions are only the evidence added since it was
  written; removed_sources were withdrawn. Do not rewrite the page: change only the sections the new
  evidence affects, delete statements citing removed sources, and send just those sections as
  `sections` ({heading: full new text of the section}). Everything else is kept as it is. Send
  summary or key_aspects only if they change.

Cite sources inline as [uid], only uids from the context; link other entities as [[Name]]. Pass the
same entity_id and input_digest to record_synthesis.""",
    "ontology": f"""\
# Entity types and roles (ontology med-lit/1)

Each type refines one Simple Graph Builder type.
{_TYPES}

Mention roles:
{_ROLES}""",
}
