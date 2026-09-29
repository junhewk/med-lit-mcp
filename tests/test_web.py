from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

from helpers import Case

from med_lit_mcp.web import Handler


class WebTests(Case):
    def test_viewer_serves_runs_articles_review_and_confines_wiki_files(self) -> None:
        run_id = self.make_run()
        self.add_text(run_id, "pubmed:123", "Fetched text.", "abstract_only")
        web_root = self.root / "web"
        web_root.mkdir()
        (web_root / "index.html").write_text("<h1>App</h1>")
        (self.project.root / "log.md").write_text("# Updates")
        (self.project.work / "secret.md").write_text("private")
        (self.root / "outside.md").write_text("private")
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(web_root)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"

            def get(path: str) -> dict:
                with urllib.request.urlopen(base + path) as response:
                    return json.load(response)

            runs = get("/api/runs")["runs"]
            self.assertEqual((runs[0]["run_id"], runs[0]["project"]), (run_id, "Test review"))
            article = get(f"/api/runs/{run_id}/articles/pubmed:123")
            self.assertEqual((article["full_text"], article["content_type"]), ("Fetched text.", "abstract_only"))
            request = urllib.request.Request(
                base + "/api/review",
                data=json.dumps({"run_id": run_id, "uid": "pubmed:123", "decision": "exclude", "reason": "Wrong population"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.load(response)["selection"]["exclude"], 1)
            screening = get(f"/api/runs/{run_id}")["articles"][0]["screening"]
            self.assertEqual((screening["reason"], screening["method"]), ("Wrong population", "manual"))
            pages = get("/api/projects/Test%20review/index")["pages"]
            self.assertEqual([page["path"] for page in pages], ["log.md"])  # nothing from .med-lit
            with urllib.request.urlopen(base + "/api/projects/Test%20review/page/log.md") as response:
                self.assertEqual(response.read(), b"# Updates")
            for blocked in ("/api/projects/Test%20review/page/.med-lit/secret.md", "/api/projects/Test%20review/page/%2e%2e/%2e%2e/outside.md"):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base + blocked)
                self.assertEqual(error.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
