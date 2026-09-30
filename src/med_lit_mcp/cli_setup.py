"""`med-lit-mcp setup` and `med-lit-mcp keys`: terminal setup for Hermes and Claude Code users."""

from __future__ import annotations

import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from . import keys, settings
from .config import projects_dir, user_path

SERVER_NAME = "med-lit"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Console:
    """Prompts that --yes answers with defaults; keys are only ever read without echo."""

    def __init__(self, assume_yes: bool, ask: Callable[[str], str] = input, secret: Callable[[str], str] = getpass.getpass):
        self.assume_yes = assume_yes
        self._ask = ask
        self._secret = secret

    def text(self, prompt: str, default: str = "") -> str:
        if self.assume_yes:
            return default
        answer = self._ask(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
        return answer or default

    def confirm(self, prompt: str, default: bool = True) -> bool:
        if self.assume_yes:
            return default
        answer = self._ask(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not answer else answer.startswith("y")

    def secret(self, prompt: str) -> str:
        return self._secret(f"{prompt}: ").strip()


def server_command(dev: str | None) -> list[str]:
    """How clients start the server: uvx from PyPI, or `uv run` in a checkout for development."""
    if dev:
        uv = shutil.which("uv") or "uv"
        return [uv, "run", "--quiet", "--directory", str(Path(dev).expanduser().resolve()), "med-lit-mcp"]
    uvx = shutil.which("uvx")
    if not uvx:
        raise SystemExit("uvx was not found. Install uv first: https://docs.astral.sh/uv/getting-started/installation/")
    return [uvx, "med-lit-mcp"]


def _run(command: list[str], stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=300, check=False)


def register_hermes(hermes: str, command: list[str], console: Console) -> str:
    listed = _run([hermes, "mcp", "list"]).stdout
    if re.search(rf"^\s*{re.escape(SERVER_NAME)}\s", listed, re.MULTILINE):
        if not console.confirm(f"Hermes already has a '{SERVER_NAME}' server. Replace it?"):
            return "kept the existing Hermes registration"
        _run([hermes, "mcp", "remove", SERVER_NAME], "y\n")
    result = _run(
        [hermes, "mcp", "add", SERVER_NAME, "--command", command[0], "--connect-timeout", "180", "--args", *command[1:]],
        "y\n",
    )
    if result.returncode != 0 or "Saved" not in result.stdout:
        return f"Hermes registration failed:\n{(result.stderr or result.stdout).strip()[-500:]}"
    return "registered with Hermes; run /reload-mcp in open sessions"


def register_claude_code(claude: str, command: list[str], console: Console) -> str:
    if _run([claude, "mcp", "get", SERVER_NAME]).returncode == 0:
        if not console.confirm(f"Claude Code already has a '{SERVER_NAME}' server. Replace it?"):
            return "kept the existing Claude Code registration"
        _run([claude, "mcp", "remove", SERVER_NAME, "--scope", "user"])
    result = _run([claude, "mcp", "add", SERVER_NAME, "--scope", "user", "--", *command])
    if result.returncode != 0:
        return f"Claude Code registration failed:\n{(result.stderr or result.stdout).strip()[-500:]}"
    return "registered with Claude Code (all projects); restart Claude Code sessions"


def ask_key(provider: keys.Provider, console: Console) -> None:
    print(f"  {provider.title}: {provider.purpose}. Get one at {provider.signup}")
    value = console.secret("  Paste the key (input is hidden)")
    if not value:
        print("  skipped")
        return
    insttoken = os.environ.get("SCOPUS_INSTTOKEN") or keys.read_keys().get("SCOPUS_INSTTOKEN")
    ok, message = keys.test_key(provider, value, insttoken=insttoken)
    print(f"  test: {message}")
    if ok is False and not console.confirm("  Save it anyway?", default=False):
        return
    keys.set_key(provider, value)
    os.environ[provider.env] = value
    print(f"  saved to {keys.config_dir() / keys.KEYS_FILE}")


def ask_defaults(console: Console) -> None:
    """Defaults copied into each new interactive project; each review can still change its own."""
    current = settings.new_settings("interactive")
    questions = [
        ("search.years", "Publication years ('2020-', '2010-2020', 'all'; empty = last three years)",
         current.search.years or "", lambda v: v or None),
        ("search.per_source", "Records per source (1-200)", str(current.search.per_source), int),
        ("search.preprint_allow", "Keep preprints? (y/n)", "y" if current.search.preprint_allow else "n",
         lambda v: v.lower().startswith("y")),
        ("fetch.limit", "Most articles to fetch per search (empty = no limit)",
         str(current.fetch.limit or ""), lambda v: int(v) if v else None),
        ("wiki.max_pages", "Pages read per article for the wiki (1-50)", str(current.wiki.max_pages or ""),
         lambda v: int(v) if v else None),
    ]
    values: dict = {}
    for key, prompt, default, convert in questions:
        while True:
            try:
                value = convert(console.text(f"  {prompt}", default))
                settings.apply_changes(current, {key: value})
                break
            except ValueError as exc:
                print(f"  {exc}")
        section, name = key.split(".")
        values.setdefault(section, {})[name] = value
    path = settings.save_user_defaults("interactive", values)
    print(f"  saved to {path}; each review keeps its own copy in med-lit.settings.json")


def setup(args: argparse.Namespace, console: Console) -> int:
    keys.load_into_environ()
    print("med-lit-mcp setup. Settings go to", keys.config_dir())
    email = args.email or console.text("Contact email for PubMed and Unpaywall", os.environ.get("NCBI_EMAIL", ""))
    while not EMAIL.match(email or ""):
        if console.assume_yes:
            raise SystemExit("A valid --email is required with --yes")
        email = console.text("Please enter a valid email address")
    folder = args.projects_dir or console.text("Folder for your reviews", str(projects_dir()))
    keys.write_config({"NCBI_EMAIL": email, "MED_LIT_PROJECTS_DIR": str(user_path(folder))})

    if not console.assume_yes:
        print("\nAPI keys are optional; everything works with just the email.")
        stored = {row["key"]: row["source"] for row in keys.status()}
        for provider in keys.PROVIDERS:
            state = f" (currently from the {stored[provider.name]})" if stored[provider.name] else ""
            if console.confirm(f"Add a {provider.title} key{state}?", default=False):
                ask_key(provider, console)

    if not console.assume_yes and console.confirm("\nSet your default settings for new reviews now?", default=False):
        ask_defaults(console)

    if args.no_register:
        print("\nSaved. Skipped client registration (--no-register).")
        return 0
    clients = {"hermes": shutil.which("hermes"), "claude-code": shutil.which("claude")}
    wanted = args.client or [name for name, path in clients.items() if path]
    command = server_command(args.dev)
    print()
    if not wanted:
        print("No Hermes or Claude Code found. Register the server yourself with:", " ".join(command))
    for name in wanted:
        path = clients.get(name)
        if not path:
            print(f"- {name}: not found on this machine")
            continue
        if not console.confirm(f"Register med-lit with {name}?"):
            continue
        register = register_hermes if name == "hermes" else register_claude_code
        print(f"- {name}: {register(path, command, console)}")
    print("\nDone. Ask your agent: \"Start a new review on …\". Manage keys later with: uvx med-lit-mcp keys")
    return 0


def keys_command(args: argparse.Namespace, console: Console) -> int:
    keys.load_into_environ()
    if args.action in (None, "list"):
        for row in keys.status():
            print(f"{row['key']:18} {row['title']:28} {row['source'] or 'not set':12} {row['value'] or ''}")
        print(f"\nKey store: {keys.config_dir() / keys.KEYS_FILE}")
        return 0
    if not args.name:
        if args.action != "test":
            raise SystemExit(f"Name the key: {', '.join(keys.BY_NAME)}")
        providers = [keys.BY_NAME[row["key"]] for row in keys.status() if row["source"]]
    else:
        if args.name not in keys.BY_NAME:
            raise SystemExit(f"Unknown key '{args.name}'; use one of {', '.join(keys.BY_NAME)}")
        providers = [keys.BY_NAME[args.name]]
    if args.action == "set":
        ask_key(providers[0], console)
    elif args.action == "remove":
        print("removed" if keys.remove_key(providers[0]) else "that key was not in the key store")
    elif args.action == "test":
        if not providers:
            print("No keys are set.")
        for provider in providers:
            value = os.environ.get(provider.env, "")
            if not value:
                print(f"{provider.name}: not set")
                continue
            _, message = keys.test_key(provider, value, insttoken=os.environ.get("SCOPUS_INSTTOKEN"))
            print(f"{provider.name}: {message}")
    return 0


def bot_main(argv: list[str]) -> int:
    from . import cli_bot

    parser = argparse.ArgumentParser(
        prog="med-lit-mcp setup bot",
        description="Create a scheduled bot project (a Hermes cron job), or change one with --edit.",
    )
    parser.add_argument("--edit", metavar="PROJECT", help="Change an existing bot: schedule, caps, criteria, question, pause, archive")
    parser.add_argument("--from-run", metavar="RUN_ID", help="Copy the question and criteria from this search")
    parser.add_argument("--name", help="Name of the new bot project")
    parser.add_argument("--schedule", metavar="CRON", help="Cron schedule such as '0 5 * * *' (daily at 05:00)")
    parser.add_argument("--dev", metavar="CHECKOUT", help="Run the bot's server from a source checkout (development)")
    parser.add_argument("--yes", action="store_true", help="Accept defaults without asking")
    args = parser.parse_args(argv)
    if args.schedule and not re.fullmatch(r"\d{1,2} \d{1,2} \* \* [0-6*]", args.schedule.strip()):
        parser.error("--schedule must look like 'M H * * *' (daily) or 'M H * * D' (weekly, D = 0-6, 0 = Sunday)")
    try:
        return cli_bot.main(args, Console(args.yes))
    except (KeyboardInterrupt, EOFError):
        print("\ncancelled", file=sys.stderr)
        return 130
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str]) -> int:
    if argv[:2] == ["setup", "bot"]:
        return bot_main(argv[2:])
    parser = argparse.ArgumentParser(prog="med-lit-mcp")
    commands = parser.add_subparsers(dest="command", required=True)
    setup_parser = commands.add_parser("setup", help="Set your email, reviews folder and API keys, and register with Hermes/Claude Code")
    setup_parser.add_argument("--email")
    setup_parser.add_argument("--projects-dir")
    setup_parser.add_argument("--client", action="append", choices=("hermes", "claude-code"),
                              help="Register with this client (repeatable); default: every one found")
    setup_parser.add_argument("--no-register", action="store_true", help="Only save settings and keys")
    setup_parser.add_argument("--dev", metavar="CHECKOUT", help="Run the server from a source checkout (development)")
    setup_parser.add_argument("--yes", action="store_true", help="Accept defaults without asking (keys are skipped)")
    keys_parser = commands.add_parser("keys", help="Show, set, remove or test API keys")
    keys_parser.add_argument("action", nargs="?", choices=("list", "set", "remove", "test"))
    keys_parser.add_argument("name", nargs="?", help=", ".join(keys.BY_NAME))
    args = parser.parse_args(argv)
    console = Console(getattr(args, "yes", False))
    try:
        return setup(args, console) if args.command == "setup" else keys_command(args, console)
    except (KeyboardInterrupt, EOFError):
        print("\ncancelled", file=sys.stderr)
        return 130
