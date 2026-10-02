"""Per-user settings and API keys stored outside any client configuration.

`~/.config/med-lit-mcp/config.json` holds non-secret machine settings (contact email, reviews
folder) and `keys.json` the API keys, readable only by the user. Both are loaded into the
environment at startup; variables already set by the MCP client always win.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .config import user_path
from .platforms import protect

CONFIG_FILE = "config.json"
KEYS_FILE = "keys.json"
CONFIG_VARS = ("NCBI_EMAIL", "MED_LIT_PROJECTS_DIR")


@dataclass(frozen=True)
class Provider:
    name: str
    title: str
    env: str
    signup: str
    purpose: str


PROVIDERS = (
    Provider("ncbi", "NCBI (PubMed/PMC)", "NCBI_API_KEY", "https://account.ncbi.nlm.nih.gov/settings/",
             "raises PubMed/PMC rate limits"),
    Provider("openalex", "OpenAlex", "OPENALEX_API_KEY", "https://openalex.org/settings/api",
             "avoids OpenAlex's anonymous rate limits"),
    Provider("semantic-scholar", "Semantic Scholar", "SEMANTIC_SCHOLAR_API_KEY",
             "https://www.semanticscholar.org/product/api#api-key-form",
             "makes Semantic Scholar usable (keyless requests are mostly refused)"),
    Provider("scopus", "Scopus", "SCOPUS_API_KEY", "https://dev.elsevier.com/",
             "adds Scopus; needs your institution's Scopus access"),
    Provider("scopus-insttoken", "Scopus institutional token", "SCOPUS_INSTTOKEN", "https://dev.elsevier.com/",
             "lets Scopus work off your institution's network, if Elsevier issued one"),
)
BY_NAME = {provider.name: provider for provider in PROVIDERS}


def config_dir() -> Path:
    configured = os.environ.get("MED_LIT_CONFIG_DIR", "").strip()
    if configured:
        return user_path(configured)
    return user_path(os.environ.get("XDG_CONFIG_HOME") or "~/.config") / "med-lit-mcp"


def _read(name: str) -> dict[str, str]:
    path = config_dir() / name
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v.strip()}


def _write_private(name: str, data: dict[str, str]) -> Path:
    """Write a file only the user can read (directory 0700, file 0600), atomically."""
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    protect(directory, directory=True)
    path = directory / name
    fd, temporary_name = tempfile.mkstemp(dir=directory, prefix=f".{name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            protect(temporary)
            json.dump(dict(sorted(data.items())), handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        protect(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def read_config() -> dict[str, str]:
    return _read(CONFIG_FILE)


def write_config(values: dict[str, str]) -> Path:
    current = read_config()
    current.update({k: v for k, v in values.items() if k in CONFIG_VARS and v})
    return _write_private(CONFIG_FILE, current)


def read_keys() -> dict[str, str]:
    return _read(KEYS_FILE)


def set_key(provider: Provider, value: str) -> Path:
    stored = read_keys()
    stored[provider.env] = value.strip()
    return _write_private(KEYS_FILE, stored)


def remove_key(provider: Provider) -> bool:
    stored = read_keys()
    if stored.pop(provider.env, None) is None:
        return False
    _write_private(KEYS_FILE, stored)
    return True


def load_into_environ() -> None:
    """Fill unset variables from config.json and keys.json; the client's environment wins."""
    for values in (read_config(), read_keys()):
        for name, value in values.items():
            if not os.environ.get(name, "").strip():
                os.environ[name] = value


def mask(value: str) -> str:
    return "…" + value[-4:] if len(value) > 8 else "set"


def status() -> list[dict[str, Any]]:
    """Where each key comes from, without revealing it."""
    stored = read_keys()
    rows = []
    for provider in PROVIDERS:
        env_value = os.environ.get(provider.env, "").strip()
        if provider.env in stored and env_value == stored[provider.env]:
            source = "key store"
        elif env_value:
            source = "environment"
        else:
            source = None
        rows.append({"key": provider.name, "title": provider.title, "source": source,
                     "value": mask(env_value) if env_value else None})
    return rows


def _request(url: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": f"med-lit-mcp/{__version__}", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read(2000).decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(2000).decode(errors="replace")


def test_key(provider: Provider, value: str, *, insttoken: str | None = None) -> tuple[bool | None, str]:
    """Check a key against its provider: (True, ok) / (False, why) / (None, inconclusive)."""
    try:
        if provider.name == "ncbi":
            query = urllib.parse.urlencode({"db": "pubmed", "term": "sepsis", "retmax": 1, "retmode": "json", "api_key": value})
            code, body = _request("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + query)
        elif provider.name == "openalex":
            code, body = _request("https://api.openalex.org/works?" + urllib.parse.urlencode({"per-page": 1, "api_key": value}))
        elif provider.name == "semantic-scholar":
            code, body = _request("https://api.semanticscholar.org/graph/v1/paper/search?query=sepsis&limit=1", {"x-api-key": value})
        elif provider.name in ("scopus", "scopus-insttoken"):
            key = value if provider.name == "scopus" else os.environ.get("SCOPUS_API_KEY", "") or read_keys().get("SCOPUS_API_KEY", "")
            token = insttoken if provider.name == "scopus" else value
            if not key:
                return None, "set the Scopus API key first; the token is tested together with it"
            headers = {"X-ELS-APIKey": key, "Accept": "application/json"}
            if token:
                headers["X-ELS-Insttoken"] = token
            code, body = _request("https://api.elsevier.com/content/search/scopus?query=TITLE(sepsis)&count=1", headers)
            if code == 200:
                return True, "Scopus search works from this machine"
            if code == 401 and "Invalid API Key" in body:
                return False, "Elsevier rejected the key as invalid"
            if code in (401, 403):
                return False, (
                    "the key is recognised but not authorized here: Scopus needs your institution's network "
                    "(campus or VPN) or an institutional token"
                )
            return None, f"Scopus answered HTTP {code}; try again later"
        else:  # pragma: no cover - PROVIDERS is closed
            raise ValueError(provider.name)
    except (OSError, urllib.error.URLError) as exc:
        return None, f"could not reach {provider.title}: {exc}"
    if code == 200:
        return True, f"{provider.title} accepted the key"
    if code in (400, 401, 403):
        return False, f"{provider.title} rejected the key (HTTP {code})"
    if code == 429:
        return None, f"{provider.title} is rate-limiting right now; the key may still be fine"
    return None, f"{provider.title} answered HTTP {code}"
