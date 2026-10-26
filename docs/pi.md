# med-lit in pi

[pi](https://pi.dev) is installed and configured by you: its model, sign-in and settings are yours,
and med-lit never changes them. med-lit adds two things.

## 1. The server: `setup --client pi`

```bash
uvx med-lit-mcp setup --client pi
```

Setup writes one entry, `med-lit`, into pi's `mcp.json` in its agent folder (`$PI_CODING_AGENT_DIR`,
default `~/.pi/agent`) and leaves every other server and setting in the file as it was.

- **Tool exposure.** The entry declares med-lit's tools to the model directly (`"exposure": "direct"`)
  and hides the three scheduled-bot tools.
- **Your changes are kept.** If you later change the entry (for example its exposure in `/mcp`), a
  repeated setup keeps your change.
- **Checking it.** Run `pi mcp list`. Start a new pi session, or `/reload`, to load the tools.

## 2. The package (optional): research policy and fresh workers

```bash
pi install git:github.com/junhewk/med-lit-mcp@v0.3.0   # the tag matching your med-lit-mcp version
```

The package is one extension. It is not part of the Python package.

- **Research policy, in study folders only.** In a folder that holds a med-lit project
  (`.med-lit/project.json`), it adds a short research policy to the system prompt as its own
  section. Elsewhere it adds nothing.
- **`run_subtask`.** This tool gives one task from `wiki_tasks` to a fresh `pi` process.
  - The worker starts with an empty context and gets the task text verbatim; the task already carries
    its rules.
  - It gets only the med-lit tools the task names ("Use only these med-lit tools: …"), plus `read`. An
    extraction worker therefore cannot resolve duplicates or change settings.
  - Workers run one after another. Their pi sessions are kept in the study's `.pi-med-lit/sessions`.
- **Optional whole-task budget.** Set `MED_LIT_PI_MAX_REQUESTS` and `MED_LIT_PI_MAX_SECONDS` before
  starting pi. Model requests of the main session and of its workers are counted together, and pi
  stops before the next request once a limit is reached; the research state is saved in the study.

Without the package, med-lit still works in pi: the agent can do the wiki tasks itself, in order,
following each task's text.

## Recommended settings for research sessions

These are recommendations, not something setup applies. They describe how the evaluation sessions
ran, and how a study keeps its sessions predictable:

- **Run pi in the study folder**, so the package can recognise the study.
- **Keep coding-project instructions out of study folders.** pi loads `AGENTS.md`/`CLAUDE.md` from
  the working folder. Leave them out of study folders, or start pi with `--no-context-files`.
- **Turn off automatic compaction** for bounded, auditable stages, in `~/.pi/agent/settings.json`:

  ```json
  { "compaction": { "enabled": false } }
  ```

  A stage that runs out of context then stops instead of summarising earlier source text, and the
  next session continues from med-lit's stored state.
- **Prompt-cache warming** matters only for providers that charge for it. `"cacheWarming": "off"`
  turns it off.
- **One stage per session.** For example, screen in one session and fetch and extract in the next.
  med-lit keeps the state, so a new session picks up where the last one stopped.

## Result size

pi gives the model at most 20 KiB of one MCP result and saves the rest to a file. med-lit sizes its
batch results (screening, triage, wiki tasks) to stay under 18 KiB as pi receives them, so
abstracts arrive whole. Article pages are about 12,000 characters. A page mostly in a script that
needs three bytes per character in UTF-8 (Korean, Chinese, Japanese) can still exceed 20 KiB; pi
then says so in the result and names the file holding the full page.
