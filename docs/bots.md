# Bots: keep a review up to date (Hermes)

A **bot project** re-runs a search you have already tried, on a schedule, and adds what is new: it screens new articles, fetches the included ones, extracts them into the wiki and updates the entity pages their evidence changes. Bots run as Hermes scheduled jobs; Claude Code and Claude Desktop have no scheduler for them.

## Creating a bot

**Before you start,** run a normal review in Hermes at least up to screening criteria (search, then "Screen it. Include … Exclude …"). A bot copies that search's question and criteria, so you begin from something you have seen work. Then:

```bash
uvx med-lit-mcp setup bot
```

Setup lists the searches that have screening criteria and asks which one to keep running, a name, daily or weekly and at what time, and the per-run limits. It then:
- creates the bot project, which starts empty and holds only what the bot finds from now on;
- creates the Hermes profile `medlitbot` once, shared by all bots. It starts as a copy of your current Hermes profile (model included), but its scheduled runs and its chats get only the med-lit tools and subagents, and its med-lit server sees only bot projects;
- adds one scheduled job per bot. Several bots are fine; setup suggests start times 30 minutes apart so they do not share your model at once.

To run a bot now instead of waiting for its time, answer yes to setup's last question, or run `hermes -p medlitbot cron run <job id>` (the job id is printed by setup and listed by `hermes -p medlitbot cron list`). Either way the run happens in that terminal and takes as long as a scheduled run, so keep the terminal open until it prints its result.

## Bots in Hermes Desktop

The `medlitbot` profile is a Bot in Bot Mode, titled *med-lit bot* (rename or restyle it there; setup leaves your changes alone). All your bots are its **Routines**, named `[bot:medlitbot] <bot name>`, and each keeps its own question, criteria and schedule. Each run's report is posted to the med-lit bot's chat, so you click the Bot to read the latest updates. A silent run posts nothing, and each posted report costs the Bot one model turn.

You can ask the Bot about its reviews ("how did SDM watch's last run go?"). Like the scheduled runs, its chat has only the med-lit tools: it cannot change a bot project or decide its uncertain articles, and it cannot message other Bots to do so; do that in a normal Hermes chat. Scheduled runs fire while Hermes Desktop is open or a Hermes gateway runs. Bots created before this support move into Bot Mode the next time you open them with `setup bot --edit`.

## Each run

- searches articles published in the look-back window (90 days by default, open-ended because journals date issues ahead) and takes in only articles new to the project. The agent scores their titles 0–3, and the run keeps the best-scored `max_new_articles` (5 by default, at most 20: one screening session), search rank breaking ties. Articles over the cap are not marked as seen, so later runs pick them up while they are still in the window;
- screens them against the frozen criteria, fetches and extracts the included ones, and writes every entity page these articles make due (entities with at least `wiki.min_sources` source articles): new pages first, then updates with the new evidence. A run's size is therefore set by `max_new_articles`;
- finishes all the work its articles bring, however long it takes. Two runs of the same bot never overlap. If a run stops before finishing (the agent ends early, or the machine sleeps), the next run continues its unfinished work and its report includes the stopped run's articles, decisions and pages;
- excludes articles with no DOI, PMID or PMCID and lists them in the report; ask for one in a normal chat to add it;
- writes a report to `updates/<date>.md` in the project folder and replies with the same text, which Hermes posts to the med-lit bot's chat and saves under `~/.hermes/profiles/medlitbot/cron/output/<job id>/`. A run that found nothing stays silent and writes no report.

**Choosing `max_new_articles`.** A run's length follows from its cap, not from the window: the articles it takes in decide how many pages are extracted and how many entity pages fall due. With a local model, a test bot's run of 5 new articles (3 included) took 33 minutes, and a run of 20 (11 included, starting from an empty wiki) took about 2 hours: roughly 3 minutes per extracted article page and 1–2 minutes per entity page written. A new bot's first window usually holds a backlog: 125–129 matching articles in the tests' 90 days. The cap works through it a run at a time, best-triaged first, and the report lists how many are waiting. A small cap keeps each run short; change it with `setup bot --edit`. The few lowest-scored articles may leave the 90-day window before a run reaches them.

**Uncertain articles wait for you.** The bot never decides them; the report lists them with the bot's reasons. Open the bot project in a normal chat ("show the uncertain articles in SDM watch") and decide them with `review_article`; the next run fetches and adds the ones you include.

## Changing a bot

`uvx med-lit-mcp setup bot --edit "SDM watch"` opens a menu:

| Change | What happens |
|---|---|
| Schedule; caps, look-back, records per source | Applies from the next run. |
| Screening criteria (opens your editor) | A new criteria revision. The next runs re-screen everything collected, 20 per run; articles that become excludes are withdrawn from the wiki, and the entity pages that cited them are updated to drop them. |
| Search question (copied from another search, or edited) | A new question version. The next run searches again from the bot's start date with it; known articles are skipped. |
| Pause or resume | Pauses or resumes the Hermes job. |
| Archive | Removes the schedule. The folder, wiki, reports and history stay; delete the folder yourself if you no longer want it. |

Every question and criteria version is kept in `.med-lit/bot.json`. A normal chat can read a bot project but not change its question, criteria or settings.

## Bot settings

| Bot setting | Default | Meaning |
|---|---|---|
| `bot.lookback_days` | 90 | Publication-date window of each run. |
| `bot.max_new_articles` | 5 | Articles screened per run, 1–20 (one screening session); the best-triaged are kept and the rest wait for later runs. |
| `bot.max_syntheses` | 100 | Safety limit on entity pages written or updated in one run, for exceptional events such as a criteria change. Normally every due page is written; pages over the limit wait for the next run. |
| `search.per_source` | 100 | Records requested per source. A bot sees new articles only among these, so the report warns when a source had more matches. |

Set your own defaults for new bots under `"bot"` in `defaults.json` (see [Defaults for new projects](configuration.md#defaults-for-new-projects)).

## Notes

- The agent is handed one step at a time: a triage batch, a screening batch, a fetch, or a single wiki task to give to a subagent. A wiki task that comes back three times without progress ends the run instead of looping. The job's instructions tell the agent to mark a run as failed in Hermes when it cannot go on.
- Scheduled jobs need a Hermes build from 28 September 2026 or later. On older builds every scheduled job fails at start (`No module named 'ruamel'`, visible in `hermes -p medlitbot cron runs`); run `hermes update` and `hermes gateway restart`.
- Report names and the look-back window use the machine's local date.
- The bot uses the `medlitbot` profile's model; change it in Hermes Desktop (right-click the med-lit bot → Edit Profile) or with `hermes -p medlitbot model`. Jobs fire while Hermes Desktop is open or the Hermes gateway runs (`hermes -p medlitbot cron status`); with only Desktop running, `hermes cron list` may still warn that the scheduler is not ready, but the jobs fire; list past runs with `hermes -p medlitbot cron runs <job id>`.
- An [OpenAlex key](https://openalex.org/settings/api) is worth adding for bots: without one, OpenAlex may refuse searches when it is busy.
- Europe PMC cannot be searched by a bot.
- After updating med-lit, open each bot once with `setup bot --edit` (see [Updating](setup.md#updating)).
