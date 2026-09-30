"""Optional local viewer: runs, screening decisions, fetched text, and each project's wiki."""

from __future__ import annotations

import argparse
import json
import sqlite3
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .config import state_dir
from .projects import WORK, get_project, list_projects, locate
from .runs import summary
from .screening import review
from .store import RUN_FILE, RUN_ID, database, question_text, read_json


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        parts = [unquote(part) for part in urlsplit(self.path).path.strip("/").split("/")]
        try:
            if parts[:2] == ["api", "runs"]:
                self.runs(parts[2:])
            elif parts[:2] == ["api", "projects"] and len(parts) >= 4:
                self.wiki(parts[2], parts[3], "/".join(parts[4:]))
            else:
                path = self.translate_path(self.path)
                if not Path(path).exists() and "." not in Path(path).name:
                    self.path = "/index.html"
                super().do_GET()
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.json_response(404, {"error": str(exc)})

    def runs(self, rest: list[str]) -> None:
        if not rest:
            values = []
            for entry in list_projects():
                if not entry["available"]:
                    continue
                project = get_project(entry["project"])
                for path in project.runs.glob(f"*/{RUN_FILE}"):
                    try:
                        manifest = read_json(path)
                        values.append(
                            summary(manifest)
                            | {
                                "project": project.name,
                                "created_at": manifest["created_at"],
                                "question": question_text(path.parent),
                            }
                        )
                    except (OSError, ValueError, KeyError):
                        continue
            values.sort(key=lambda item: item["created_at"], reverse=True)
            self.json_response(200, {"runs": values[:200]})
            return
        if not RUN_ID.fullmatch(rest[0]):
            self.json_response(404, {"error": "Not found"})
            return
        project, path = locate(rest[0])
        if len(rest) == 3 and rest[1] == "articles":
            with database(project.db) as conn:
                row = conn.execute(
                    """SELECT uid, title, full_text, content_type, COALESCE(source_url, url) AS url, doi
                       FROM articles WHERE uid=?""",
                    (rest[2],),
                ).fetchone()
            self.json_response(200, dict(row)) if row else self.json_response(404, {"error": "Article not fetched"})
            return
        manifest = read_json(path / RUN_FILE)
        self.json_response(
            200,
            {
                "project": project.name,
                "summary": summary(manifest),
                "articles": [
                    {
                        "uid": uid,
                        "title": item["record"].get("title"),
                        "fetch": item["fetch"],
                        "wiki": item["wiki"],
                        "screening": item.get("screening"),
                        "error": item.get("error"),
                    }
                    for uid, item in manifest["articles"].items()
                ],
            },
        )

    def wiki(self, name: str, action: str, relative: str) -> None:
        root = get_project(name).root.resolve()
        if action == "index":
            pages = [
                {"title": path.stem, "path": str(path.relative_to(root))}
                for path in sorted(root.rglob("*.md"))[:500]
                if WORK not in path.relative_to(root).parts
            ]
            self.json_response(200, {"project": name, "pages": pages})
            return
        target = (root / relative).resolve()
        if (
            action != "page"
            or not target.is_relative_to(root)
            or WORK in target.relative_to(root).parts
            or target.suffix != ".md"
            or not target.is_file()
        ):
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path == "/api/review":
            self.review_article()
        else:
            self.send_error(404)

    def do_PUT(self) -> None:
        self.send_error(404)

    def do_DELETE(self) -> None:
        self.send_error(404)

    def review_article(self) -> None:
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self.send_error(415, "JSON content type required")
            return
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            self.send_error(403, "Cross-origin review is not allowed")
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 8192:
                self.send_error(413)
                return
            value = json.loads(self.rfile.read(size))
            result = review(
                str(value["run_id"]), str(value["uid"]),
                str(value["decision"]), str(value["reason"]), client="med-lit-viewer",
            )
            self.json_response(200, result)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self.json_response(400, {"error": str(exc)})

    def json_response(self, code: int, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> None:
    from .keys import load_into_environ

    load_into_environ()
    parser = argparse.ArgumentParser(prog="med-lit-viewer")
    parser.add_argument("--directory", type=Path, default=Path(str(files("med_lit_mcp") / "viewer")))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), partial(Handler, directory=str(args.directory)))
    print(f"med-lit viewer on http://{args.host}:{args.port} (projects: {state_dir() / 'projects.json'})")
    server.serve_forever()


if __name__ == "__main__":
    main()
