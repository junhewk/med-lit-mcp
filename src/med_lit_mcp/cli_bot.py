"""`med-lit-mcp setup bot`: create and edit scheduled bot projects run by Hermes cron jobs.

All bots share one Hermes profile (PROFILE). Its med-lit server runs with MED_LIT_SCOPE=bot, so it
sees only bot projects, and its scheduled runs get only the med-lit tools and subagents. Each bot
project has its own cron job.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import bot, projects, screening, settings
from .cli_setup import SERVER_NAME, Console, _run, server_command
from .config import ncbi_email
from .store import RUN_FILE, read_json

PROFILE = "medlitbot"
CRON_TOOLSETS = f"[delegation, {SERVER_NAME}]"
WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
JOB_ID = re.compile(r"\b[0-9a-f]{12}\b")


# Hermes


def hermes_path() -> str:
    path = shutil.which("hermes")
    if not path:
        raise SystemExit("Bots run as Hermes scheduled jobs, and hermes was not found on this machine.")
    return path


def _hermes(hermes: str, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return _run([hermes, "-p", PROFILE, *args], stdin)


def ensure_profile(hermes: str, command: list[str]) -> list[str]:
    """Create or repair the shared bot profile: only the med-lit server, scoped to bot projects."""
    notes = []
    if not re.search(rf"^\W*{PROFILE}\s", _run([hermes, "profile", "list"]).stdout, re.MULTILINE):
        created = _run([
            hermes, "profile", "create", PROFILE, "--clone", "--no-alias",
            "--description", "Scheduled med-lit literature bots",
        ])
        if created.returncode != 0:
            raise SystemExit(f"Could not create the Hermes profile {PROFILE}:\n{(created.stderr or created.stdout)[-500:]}")
        notes.append(f"created the Hermes profile '{PROFILE}' (a copy of your current model settings)")
    listed = _hermes(hermes, "mcp", "list").stdout
    for name in re.findall(r"^\s{2}(\S+)\s{2,}\S", listed, re.MULTILINE):
        if name not in ("Name",) and not name.startswith("─"):
            _hermes(hermes, "mcp", "remove", name, stdin="y\n")
    added = _hermes(
        hermes, "mcp", "add", SERVER_NAME, "--command", command[0], "--env", "MED_LIT_SCOPE=bot",
        "--connect-timeout", "180", "--args", *command[1:], stdin="y\n",
    )
    if added.returncode != 0 or "Saved" not in added.stdout:
        raise SystemExit(f"Could not register med-lit in the {PROFILE} profile:\n{(added.stderr or added.stdout)[-500:]}")
    toolsets = _hermes(hermes, "config", "set", "platform_toolsets.cron", CRON_TOOLSETS)
    if toolsets.returncode != 0:
        notes.append(f"could not limit scheduled runs to med-lit tools: {(toolsets.stderr or toolsets.stdout).strip()[-200:]}")
    return notes


def create_job(hermes: str, name: str, schedule: str) -> str:
    result = _hermes(
        hermes, "cron", "create", schedule, bot.cron_prompt(name), "--name", f"med-lit bot: {name}", "--deliver", "local",
    )
    found = JOB_ID.search(result.stdout)
    if result.returncode != 0 or not found:
        raise SystemExit(f"Could not create the scheduled job:\n{(result.stderr or result.stdout)[-500:]}")
    return found.group(0)


def job_command(hermes: str, action: str, job_id: str, *extra: str) -> str:
    result = _hermes(hermes, "cron", action, job_id, *extra, stdin="y\n")
    if result.returncode != 0:
        raise SystemExit(f"hermes cron {action} failed:\n{(result.stderr or result.stdout)[-500:]}")
    return result.stdout.strip()


# Questions


def schedule_text(schedule: str) -> str:
    minute, hour, _, _, weekday = schedule.split()
    day = "every day" if weekday == "*" else f"every {WEEKDAYS[int(weekday)].capitalize()}"
    return f"{day} at {int(hour):02d}:{int(minute):02d}"


def _taken_times() -> set[str]:
    taken = set()
    for row in projects.list_projects():
        schedule = (row.get("bot") or {}).get("schedule")
        if schedule and len(schedule.split()) == 5:
            minute, hour = schedule.split()[:2]
            taken.add(f"{int(hour):02d}:{int(minute):02d}")
    return taken


def suggest_time(taken: set[str]) -> str:
    """05:00, 05:30, 06:00, … : bots start apart so they do not share the model at once."""
    for slot in range(10, 48):
        text = f"{slot // 2:02d}:{30 * (slot % 2):02d}"
        if text not in taken:
            return text
    return "05:00"


def ask_schedule(console: Console, current: str | None = None) -> str:
    frequency = ""
    while frequency not in ("daily", "weekly"):
        frequency = console.text("  Run daily or weekly?", "daily" if not current or current.endswith("*") else "weekly").lower()
    weekday = "*"
    if frequency == "weekly":
        day = ""
        while day[:3] not in WEEKDAYS:
            day = console.text("  Which day (mon, tue, …)?", "mon").lower()
        weekday = str(WEEKDAYS.index(day[:3]))
    found = None
    while not found:
        found = TIME.match(console.text("  At what time (24-hour, this machine's clock)?", suggest_time(_taken_times())))
    return f"{int(found.group(2))} {int(found.group(1))} * * {weekday}"


BOT_QUESTIONS = (
    ("bot.lookback_days", "Look back how many days of publications each run", int),
    ("bot.max_new_articles", "Most new articles taken in per run (the rest wait for a later run)", int),
    ("bot.max_syntheses", "Most entity pages written per run", int),
    ("search.per_source", "Records requested from each source per run (1-200)", int),
)


def ask_bot_settings(console: Console, current: settings.ProjectSettings) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key, prompt, convert in BOT_QUESTIONS:
        section, name = key.split(".")
        default = str(getattr(getattr(current, section), name))
        while True:
            try:
                value = convert(console.text(f"  {prompt}", default))
                settings.apply_changes(current, {key: value})
                break
            except ValueError as exc:
                print(f"  {exc}")
        if str(value) != default:
            changes[key] = value
    return changes


def approved_runs() -> list[dict[str, Any]]:
    """Searches in interactive projects that have a question and screening criteria to copy."""
    found = []
    for row in projects.list_projects():
        if not row["available"] or row.get("mode") == "bot":
            continue
        project = projects.get_project(row["project"])
        for manifest_path in sorted(project.runs.glob(f"*/{RUN_FILE}")):
            manifest = read_json(manifest_path)
            question = manifest_path.parent / "question.json"
            if manifest.get("selection_criteria") and question.is_file():
                found.append({
                    "project": project.name,
                    "run_id": manifest["run_id"],
                    "created_at": manifest.get("created_at", "")[:10],
                    "question": read_json(question),
                    "sources": [s for s in manifest.get("sources") or [] if s != "europepmc"],
                    "criteria": manifest["selection_criteria"],
                })
    return found


def choose_run(console: Console, run_id: str | None) -> dict[str, Any]:
    runs = approved_runs()
    if run_id:
        match = [r for r in runs if r["run_id"] == run_id]
        if not match:
            raise SystemExit(f"Run {run_id} was not found among searches with screening criteria")
        return match[0]
    if not runs:
        raise SystemExit(
            "A bot starts from a search question and screening criteria you have already tried in a normal "
            "review. Run one first (\"Start a new review on …\" in chat), then run setup bot again."
        )
    print("\nWhich search should the bot keep running? It copies the question and screening criteria.")
    for number, run in enumerate(runs, start=1):
        print(f"  {number}. {run['project']} ({run['created_at']}): {run['question'].get('question', '')[:100]}")
    while True:
        answer = console.text("  Number", "1")
        if answer.isdigit() and 1 <= int(answer) <= len(runs):
            return runs[int(answer) - 1]


def edit_json(value: dict[str, Any], note: str) -> dict[str, Any]:
    """Open value in the user's editor and return the edited JSON."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or shutil.which("nano") or shutil.which("vi")
    if not editor:
        raise SystemExit("Set EDITOR to your text editor to edit this")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
        path = Path(handle.name)
    try:
        print(f"  {note}")
        subprocess.run([*editor.split(), str(path)], check=False)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"That is not valid JSON (line {exc.lineno}); nothing was changed") from exc
    finally:
        path.unlink(missing_ok=True)


# Commands


def create(args: argparse.Namespace, console: Console) -> int:
    ncbi_email(required=True)
    hermes = hermes_path()
    source = choose_run(console, args.from_run)
    name = args.name or console.text("\nName for the bot project", f"{source['project']} (bot)")
    print(f"\nQuestion: {source['question'].get('question')}")
    print(f"Criteria: include {source['criteria']['include']}; exclude {source['criteria']['exclude']}")
    print(f"Sources: {', '.join(source['sources'])}")
    schedule = args.schedule or ask_schedule(console)
    print("\nBot settings (Enter keeps the default):")
    changes = ask_bot_settings(console, settings.new_settings("bot"))
    if not console.confirm(f"\nCreate '{name}', running {schedule_text(schedule)}?"):
        return 1
    project = bot.create_bot(name, source["question"], source["sources"], source["criteria"], schedule=schedule, changes=changes)
    for note in ensure_profile(hermes, server_command(args.dev)):
        print(f"- {note}")
    job_id = create_job(hermes, project.name, schedule)
    bot.set_hermes(project, profile=PROFILE, job_id=job_id, schedule=schedule)
    print(f"\nCreated the bot project at {project.root}")
    print(f"Scheduled {schedule_text(schedule)} as Hermes job {job_id} (profile {PROFILE}).")
    print(f"Reports go to {project.root / bot.UPDATES}; open the folder in Obsidian to read the wiki.")
    print(f"Change it later with: uvx med-lit-mcp setup bot --edit \"{project.name}\"")
    if console.confirm("Run it once now?", default=False):
        print(job_command(hermes, "run", job_id))
        print(f"It starts on the scheduler's next tick; follow it with: hermes -p {PROFILE} cron runs {job_id}")
    return 0


def edit(args: argparse.Namespace, console: Console) -> int:
    hermes = hermes_path()
    project = projects.get_project(args.edit)
    state = bot.read_state(project)
    job_id = (state.get("hermes") or {}).get("job_id")
    while True:
        state = bot.read_state(project)
        print(f"\n{project.name}: {state['status']}, {schedule_text(state['schedule'])}")
        print("  1. schedule   2. caps and look-back   3. screening criteria")
        print("  4. search question   5. pause or resume   6. archive   7. done")
        choice = console.text("Change", "7")
        if choice == "1":
            schedule = ask_schedule(console, state["schedule"])
            if job_id:
                job_command(hermes, "edit", job_id, "--schedule", schedule)
            bot.set_hermes(project, schedule=schedule)
        elif choice == "2":
            current = settings.load_settings(project.root)
            changes = ask_bot_settings(console, current)
            if changes:
                settings.write_settings(project.root, settings.apply_changes(current, changes))
                print("  saved; applies from the next run")
        elif choice == "3":
            latest = state["criteria"][-1]
            edited = edit_json(
                {"include": latest["include"], "exclude": latest["exclude"]},
                "Edit the criteria, save and close the editor.",
            )
            criteria = screening.normalize_criteria(edited.get("include", []), edited.get("exclude", []))
            if criteria == {"include": latest["include"], "exclude": latest["exclude"]}:
                print("  unchanged")
                continue
            collected = len(read_json(project.runs / state["run_id"] / RUN_FILE)["articles"])
            if console.confirm(f"  Save? The next runs re-screen all {collected} collected articles; new excludes leave the wiki."):
                bot.set_criteria(project, criteria["include"], criteria["exclude"])
                print("  saved")
        elif choice == "4":
            latest = state["questions"][-1]
            source = None
            if console.confirm("  Copy the question from another search? (No opens it in your editor)", default=False):
                source = choose_run(console, None)
            question = source["question"] if source else edit_json(latest["question"], "Edit the question, save and close.")
            sources = source["sources"] if source else latest["sources"]
            if console.confirm(f"  Save? The next run searches again from {state['start_date']} with it (known articles are skipped)."):
                result = bot.set_question(project, question, sources)
                for warning in result["warnings"]:
                    print(f"  warning: {warning}")
                print(f"  saved as question version {result['version']}")
        elif choice == "5":
            paused = state["status"] == "paused"
            if job_id:
                job_command(hermes, "resume" if paused else "pause", job_id)
            bot.set_status(project, "active" if paused else "paused")
            print("  resumed" if paused else "  paused")
        elif choice == "6":
            if console.confirm("  Archive? The schedule is removed; the folder, wiki and history stay.", default=False):
                if job_id:
                    job_command(hermes, "remove", job_id)
                bot.set_status(project, "archived")
                bot.set_hermes(project, job_id=None)
                print("  archived")
                return 0
        else:
            return 0


def main(args: argparse.Namespace, console: Console) -> int:
    from . import keys

    keys.load_into_environ()
    return edit(args, console) if args.edit else create(args, console)
