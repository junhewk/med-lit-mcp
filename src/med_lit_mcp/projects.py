"""Projects: one self-contained folder per literature review.

A project folder holds the wiki at its top level and everything else under a hidden work
folder, so the folder can be moved, copied or backed up as a whole:

    <project>/index.md, log.md, entities/, sources/    the wiki
    <project>/.med-lit/project.json                    identity
    <project>/.med-lit/med-lit.sqlite3                 articles and knowledge graph
    <project>/.med-lit/runs/<run-id>/                  searches, screening, fetched text

The registry of known projects is only a convenience; open_project re-registers a moved folder.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import projects_dir, state_dir
from .ontology import ONTOLOGY_VERSION
from .store import RUN_FILE, RUN_ID, atomic_json, file_lock, now, read_json

WORK = ".med-lit"
LAYOUT_VERSION = 1


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    root: Path

    @property
    def work(self) -> Path:
        return self.root / WORK

    @property
    def db(self) -> Path:
        return self.work / "med-lit.sqlite3"

    @property
    def runs(self) -> Path:
        return self.work / "runs"

    def summary(self) -> dict[str, Any]:
        return {"project": self.name, "path": str(self.root)}


def _registry_file() -> Path:
    return state_dir() / "projects.json"


@contextmanager
def _registry() -> Iterator[dict[str, dict[str, str]]]:
    """Locked read-modify-write of {name: {"id", "path"}}."""
    path = _registry_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.with_suffix(".lock")):
        entries = read_json(path).get("projects", {}) if path.exists() else {}
        before = dict(entries)
        yield entries
        if entries != before:
            atomic_json(path, {"projects": entries})


def _read_identity(root: Path) -> dict[str, Any]:
    marker = root / WORK / "project.json"
    if not marker.is_file():
        raise ValueError(f"{root} is not a med-lit project (no {WORK}/project.json)")
    return read_json(marker)


def _folder_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", name)
    cleaned = " ".join(cleaned.split()).strip(" .")
    if not cleaned:
        raise ValueError("Project name has no usable characters")
    return cleaned[:80]


def create_project(name: str, path: str | None = None) -> Project:
    name = " ".join(name.split())
    if not name:
        raise ValueError("Give the project a name, e.g. the review topic")
    root = Path(path).expanduser() if path else projects_dir() / _folder_name(name)
    root = root.resolve()
    if (root / WORK / "project.json").exists():
        raise ValueError(f"{root} is already a project; use open_project")
    with _registry() as entries:
        if name in entries:
            raise ValueError(f"A project named '{name}' already exists at {entries[name]['path']}")
        project = Project(id=uuid.uuid4().hex, name=name, root=root)
        project.runs.mkdir(parents=True, exist_ok=True)
        atomic_json(
            project.work / "project.json",
            {
                "id": project.id,
                "name": name,
                "created_at": now(),
                "layout_version": LAYOUT_VERSION,
                "ontology": f"med-lit/{ONTOLOGY_VERSION}",
            },
        )
        entries[name] = {"id": project.id, "path": str(root)}
    return project


def open_project(path: str) -> Project:
    root = Path(path).expanduser().resolve()
    identity = _read_identity(root)
    project = Project(id=identity["id"], name=identity["name"], root=root)
    with _registry() as entries:
        existing = entries.get(project.name)
        if existing and existing["id"] != project.id and Path(existing["path"]).exists():
            raise ValueError(
                f"Another project is already registered as '{project.name}' at {existing['path']}"
            )
        entries[project.name] = {"id": project.id, "path": str(root)}
    return project


def _registered() -> dict[str, dict[str, str]]:
    path = _registry_file()
    return read_json(path).get("projects", {}) if path.exists() else {}


def _load(name: str, entry: dict[str, str]) -> Project | None:
    root = Path(entry["path"])
    try:
        identity = _read_identity(root)
    except (OSError, ValueError):
        return None
    return Project(id=identity["id"], name=name, root=root) if identity["id"] == entry["id"] else None


def list_projects() -> list[dict[str, Any]]:
    rows = []
    for name, entry in sorted(_registered().items()):
        project = _load(name, entry)
        rows.append(
            {
                "project": name,
                "path": entry["path"],
                "available": project is not None,
                "runs": len(list(project.runs.glob(f"*/{RUN_FILE}"))) if project else 0,
            }
        )
    return rows


def get_project(name: str | None = None) -> Project:
    """A registered project by name; with no name, the only project there is."""
    entries = _registered()
    if name is None:
        if len(entries) == 1:
            name = next(iter(entries))
        elif not entries:
            raise ValueError("No project yet; call create_project with a name for this review")
        else:
            raise ValueError(f"Several projects exist ({', '.join(sorted(entries))}); pass project")
    entry = entries.get(name)
    if entry is None:
        raise ValueError(f"Unknown project '{name}'; known: {', '.join(sorted(entries)) or 'none'}")
    project = _load(name, entry)
    if project is None:
        raise ValueError(
            f"Project '{name}' is no longer at {entry['path']}; if it moved, call open_project with its new path"
        )
    return project


def locate(run_id: str) -> tuple[Project, Path]:
    """The project holding a run, and the run's folder."""
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid run ID")
    for name, entry in _registered().items():
        project = _load(name, entry)
        if project and (project.runs / run_id / RUN_FILE).exists():
            return project, project.runs / run_id
    raise ValueError(f"Run {run_id} was not found in any available project (list_projects shows them)")


def run_dir(run_id: str) -> Path:
    return locate(run_id)[1]


def new_run_dir(project: Project) -> tuple[str, Path]:
    run_id = uuid.uuid4().hex
    path = project.runs / run_id
    path.mkdir(parents=True)
    return run_id, path
