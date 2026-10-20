#!/usr/bin/env bash
# Install or update med-lit-mcp on macOS and Linux.
#
# Saves your contact email, reviews folder and optional database keys (`med-lit-mcp setup`) and
# registers the server with the MCP clients on this computer (Hermes, Claude Code, Codex, pi). Use
# the same command to install and to update: it runs the newest release from PyPI, so the
# `uvx med-lit-mcp` that clients start is that release from then on.
#
# Usage: scripts/install.sh [options]
#   --client NAME       Register with this client: hermes, claude-code, codex or pi (repeatable).
#                       Default: every one found.
#   --chatgpt           For ChatGPT desktop Work/Codex: fill in the settings in a browser form
#                       instead of terminal prompts, then register and test the connection.
#   --email ADDRESS     Contact email for PubMed and Unpaywall.
#   --projects-dir DIR  Folder for your reviews (default ~/med-lit).
#   --yes               Accept defaults without asking (needs --email; keys are skipped).
#   --no-register       Only save the settings and keys.
#   --dev CHECKOUT      Run the server from a source checkout instead of PyPI (development).
#   -h, --help          Show this help.
#
# Needs uv (https://docs.astral.sh/uv/). Written for the bash 3.2 that macOS ships.

set -euo pipefail

clients=()
chatgpt=0
email=""
projects_dir=""
assume_yes=0
no_register=0
dev=""

usage() { sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --client) clients+=("${2:?--client needs a value}"); shift 2 ;;
        --chatgpt) chatgpt=1; shift ;;
        --email) email="${2:?--email needs a value}"; shift 2 ;;
        --projects-dir) projects_dir="${2:?--projects-dir needs a value}"; shift 2 ;;
        --yes) assume_yes=1; shift ;;
        --no-register) no_register=1; shift ;;
        --dev) dev="${2:?--dev needs a checkout folder}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
done

if [ "$chatgpt" -eq 1 ] && { [ ${#clients[@]} -gt 0 ] || [ "$assume_yes" -eq 1 ] || [ "$no_register" -eq 1 ]; }; then
    echo "--chatgpt uses the browser form; it cannot be combined with --client, --yes or --no-register." >&2
    exit 2
fi

# 1. uv, which runs med-lit and its Python without touching the system's.
if ! command -v uv >/dev/null 2>&1 || ! command -v uvx >/dev/null 2>&1; then
    echo "uv is not installed. Install it, open a new terminal, and rerun this script:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# 2. The server: the newest release from PyPI, or a source checkout.
if [ -n "$dev" ]; then
    if [ ! -f "$dev/pyproject.toml" ]; then
        echo "--dev must name a med-lit-mcp checkout (no pyproject.toml in $dev)." >&2
        exit 2
    fi
    dev="$(cd "$dev" && pwd)"
    runner=(uv run --quiet --directory "$dev" med-lit-mcp)
else
    runner=(uvx med-lit-mcp@latest)
fi
echo "==> $("${runner[@]}" --version)"

# 3. Settings, keys and client registration.
setup_args=(setup)
if [ "$chatgpt" -eq 1 ]; then
    setup_args+=(--ui --client chatgpt)
else
    for name in ${clients[@]+"${clients[@]}"}; do
        setup_args+=(--client "$name")
    done
    if [ -n "$email" ]; then setup_args+=(--email "$email"); fi
    if [ -n "$projects_dir" ]; then setup_args+=(--projects-dir "$projects_dir"); fi
    if [ "$assume_yes" -eq 1 ]; then setup_args+=(--yes); fi
    if [ "$no_register" -eq 1 ]; then setup_args+=(--no-register); fi
fi
if [ -n "$dev" ]; then setup_args+=(--dev "$dev"); fi
echo "==> Setting up med-lit"
# Prompts read the terminal even when this script itself arrived through a pipe.
if [ ! -t 0 ] && [ "$assume_yes" -eq 0 ] && [ "$chatgpt" -eq 0 ] && (: < /dev/tty) 2>/dev/null; then
    "${runner[@]}" "${setup_args[@]}" < /dev/tty
else
    "${runner[@]}" "${setup_args[@]}"
fi

# 4. Check: the saved configuration as the server reads it.
echo "==> Checking the configuration"
"${runner[@]}" --check
echo ""
echo "Done. Restart your MCP client (in Hermes /reload-mcp, in pi a new session or /reload), then ask:"
echo "  \"Start a new review on ...\""
echo "To update later, run this script again."
