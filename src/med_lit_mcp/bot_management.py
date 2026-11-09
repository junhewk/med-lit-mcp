"""Researcher-requested bot management, shared with the existing Hermes scheduling adapter."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any

from . import bot, cli_bot, projects, screening, search, settings
from .cli_setup import server_command
from .config import ncbi_email
from .onboarding import require_interactive
from .store import file_lock


def validate_schedule(schedule: str) -> str:
    parts = schedule.split()
    if (len(parts) != 5 or not parts[0].isdigit() or not parts[1].isdigit()
            or not 0 <= int(parts[0]) <= 59 or not 0 <= int(parts[1]) <= 23
            or parts[2:4] != ["*", "*"] or parts[4] not in ("*", "0", "1", "2", "3", "4", "5", "6")):
        raise ValueError("Use a daily 'M H * * *' or weekly 'M H * * D' schedule; D=0 is Sunday. Times use this machine's local clock.")
    return " ".join(parts)


def _call(function, *args, **kwargs):
    """CLI adapters signal errors with SystemExit; never let one terminate the MCP server."""
    try:
        return function(*args, **kwargs)
    except SystemExit as exc:
        raise ValueError(str(exc)) from exc


def launcher() -> list[str]:
    """Keep the installer's launcher, so bots follow updates and development checkouts alike."""
    raw = os.environ.get("MED_LIT_LAUNCH_COMMAND", "")
    if raw:
        try:
            command = json.loads(raw)
        except ValueError as exc:
            raise ValueError("Invalid med-lit launch configuration. Reconnect med-lit using setup --client for your agent.") from exc
        if not isinstance(command, list) or not command or not all(isinstance(p, str) and p for p in command):
            raise ValueError("Invalid med-lit launch configuration. Reconnect med-lit using setup --client for your agent.")
        return command
    try:
        return server_command(None)
    except SystemExit:
        return [sys.executable, "-m", "med_lit_mcp"]


def sources() -> list[dict[str, Any]]:
    require_interactive()
    return cli_bot.approved_runs()


def show(project: projects.Project) -> dict[str, Any]:
    state = bot.read_state(project)
    return project.summary() | {
        "status": state["status"],
        "schedule": state["schedule"],
        "schedule_text": cli_bot.schedule_text(state["schedule"]),
        "timezone": str(datetime.now().astimezone().tzinfo),
        "question": state["questions"][-1],
        "criteria": state["criteria"][-1],
        "settings": settings.load_settings(project.root).model_dump(),
        "job_id": state.get("hermes", {}).get("job_id"),
        "running": bool(state.get("active")),
    }


def _settings(current: settings.ProjectSettings, changes: dict[str, Any] | None) -> settings.ProjectSettings:
    return settings.apply_changes(current, changes) if changes else current


def create(name: str, from_run: str, schedule: str, changes: dict[str, Any] | None = None,
           confirmed: bool = False) -> dict[str, Any]:
    require_interactive()
    ncbi_email(required=True)
    name = " ".join(name.split())
    if not name or len(name) > 80:
        raise ValueError("Choose a bot name of 1-80 characters.")
    schedule = validate_schedule(schedule)
    candidates = [r for r in sources() if r["run_id"] == from_run]
    if not candidates:
        raise ValueError("Choose a run from bot_sources: a normal review with a saved question and screening criteria.")
    source = candidates[0]
    if not source["sources"]:
        raise ValueError("This search has no bot-compatible sources.")
    proposed = _settings(settings.new_settings("bot"), changes)
    preview = {
        "project": name, "from_run": from_run, "source_project": source["project"],
        "question": source["question"], "criteria": source["criteria"], "sources": source["sources"],
        "schedule": schedule, "schedule_text": cli_bot.schedule_text(schedule),
        "timezone": str(datetime.now().astimezone().tzinfo), "settings": proposed.model_dump(),
    }
    if not confirmed:
        return preview | {"ask_researcher": True, "message": "Show these settings and ask before creating the scheduled bot."}
    with file_lock(projects.state_dir() / ".bot-management.lock"):
        existing = next((r for r in projects.list_projects() if r["project"] == name), None)
        if existing:
            owner = projects.get_project(name)
            state = bot.read_state(owner)
            if state["status"] == "archived":
                raise ValueError("This bot is archived. Choose a new bot name.")
            if state.get("hermes", {}).get("source_run_id") != from_run:
                raise ValueError("A project with this name already exists; choose another name or use manage_bot.")
            if state.get("hermes", {}).get("job_id"):
                return show(owner) | {"created": False, "message": "This bot is already scheduled; use manage_bot to change it."}
            if state["schedule"] != schedule or settings.load_settings(owner.root) != proposed:
                raise ValueError("An unfinished bot with different settings exists; use manage_bot to repair it.")
        else:
            owner = bot.create_bot(name, source["question"], source["sources"], source["criteria"],
                                   schedule=schedule, changes=changes)
            bot.set_status(owner, "paused")
            bot.set_hermes(owner, source_run_id=from_run)
        hermes = _call(cli_bot.hermes_path)
        notes = _call(cli_bot.ensure_profile, hermes, launcher())
        job_id = _call(cli_bot.find_job, hermes, name)
        if job_id:
            _call(cli_bot.job_command, hermes, "edit", job_id, "--schedule", schedule,
                  "--prompt", bot.cron_prompt(name), "--name", cli_bot.job_name(name), "--deliver", cli_bot.DELIVER)
        else:
            job_id = _call(cli_bot.create_job, hermes, name, schedule)
        bot.set_hermes(owner, profile=cli_bot.PROFILE, job_id=job_id, schedule=schedule)
        bot.set_status(owner, "active")
        return show(owner) | {"created": True, "notes": notes,
                              "message": "Scheduled. Reports appear in the med-lit bot's chat and project updates folder. Hermes Desktop or its gateway must be running."}


def manage(project: str, action: str = "show", schedule: str | None = None,
           changes: dict[str, Any] | None = None, question: dict[str, Any] | None = None,
           sources: list[str] | None = None, include: list[str] | None = None,
           exclude: list[str] | None = None, confirmed: bool = False) -> dict[str, Any]:
    require_interactive()
    owner = projects.get_project(project)
    state = bot.read_state(owner)
    if action == "show":
        if any(v is not None for v in (schedule, changes, question, sources, include, exclude)):
            raise ValueError("Use action='update' to change bot settings.")
        return show(owner)
    if action not in ("update", "pause", "resume", "archive"):
        raise ValueError("Choose show, update, pause, resume or archive.")
    if action != "update" and any(v is not None for v in (schedule, changes, question, sources, include, exclude)):
        raise ValueError("Settings belong to action='update'; pause, resume and archive take no changes.")
    if schedule is not None:
        schedule = validate_schedule(schedule)
    current = settings.load_settings(owner.root)
    proposed = _settings(current, changes)
    criteria = None
    if include is not None or exclude is not None:
        latest = state["criteria"][-1]
        criteria = screening.normalize_criteria(include if include is not None else latest["include"],
                                                exclude if exclude is not None else latest["exclude"])
    latest_question = state["questions"][-1]
    if sources is not None and not sources:
        raise ValueError("Choose at least one database source for the bot.")
    if sources is not None and question is None:
        question = latest_question["question"]
    if question is not None:
        checked = search.validate_question(question, sources or latest_question["sources"], owner, repair_sentences=True)
        if "europepmc" in checked["sources"]:
            raise ValueError("Bots cannot search europepmc.")
        question, sources = checked["normalized_question"], checked["sources"]
    preview = show(owner) | {"action": action, "proposed_schedule": schedule or state["schedule"],
                             "proposed_settings": proposed.model_dump()}
    if question is not None:
        preview.update(proposed_question=question, proposed_sources=sources,
                       consequence="The changed question searches back through the bot's collection window.")
    if criteria is not None:
        preview.update(proposed_criteria=criteria,
                       consequence="The next runs re-screen collected articles and update affected wiki pages.")
    if not confirmed:
        return preview | {"ask_researcher": True, "message": "Show the proposed change and ask before applying it."}
    with file_lock(owner.work / ".management.lock"):
        state = bot.read_state(owner)
        if action == "update" and state.get("active") and bot._run_alive(owner, state):
            raise ValueError("The bot is running. Wait for it to finish before changing its question, criteria or settings.")
        if state["status"] == "archived":
            raise ValueError("This bot is archived. Create a new bot from an approved review.")
        job_id = state.get("hermes", {}).get("job_id")
        if not job_id:
            raise ValueError("This bot has no scheduled job. Retry its creation with the original settings or repair it with setup bot.")
        hermes = _call(cli_bot.hermes_path)
        if action == "update":
            if schedule is not None:
                _call(cli_bot.job_command, hermes, "edit", job_id, "--schedule", schedule)
                bot.set_hermes(owner, schedule=schedule)
            latest = state["questions"][-1]
            if question is not None and (question != latest["question"] or sources != latest["sources"]):
                bot.set_question(owner, question, sources)
            if criteria is not None:
                bot.set_criteria(owner, criteria["include"], criteria["exclude"])
            if changes:
                settings.write_settings(owner.root, proposed)
        elif action == "archive":
            _call(cli_bot.job_command, hermes, "remove", job_id)
            bot.set_status(owner, "archived")
            bot.set_hermes(owner, job_id=None)
        else:
            _call(cli_bot.job_command, hermes, action, job_id)
            bot.set_status(owner, "paused" if action == "pause" else "active")
    return show(owner) | {"changed": True}
