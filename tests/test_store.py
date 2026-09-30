from __future__ import annotations

import os
import shutil
import sqlite3
from unittest.mock import patch

from helpers import Case

from med_lit_mcp import config, projects
from med_lit_mcp.store import MIGRATIONS, database


class StoreTests(Case):
    def test_schema_enforces_ontology_and_cascades(self) -> None:
        with database(self.project.db) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(MIGRATIONS))
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            conn.execute(
                "INSERT INTO articles (uid, full_text, content_type, content_sha256, updated_at) VALUES ('a', 't', 'full_text', 'x', 'n')"
            )
            conn.execute("INSERT INTO kg_entities VALUES (1, 'sepsis', 'sepsis', 'CONDITION', 'd', NULL, 'n', 'n')")
            conn.execute(
                "INSERT INTO kg_mentions (article_uid, entity_id, page_index, mention_text, context, role) VALUES ('a', 1, 0, 'x', 'x', 'outcome')"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO kg_entities VALUES (2, 'x', 'x', 'MEDICAL_CONDITION', 'd', NULL, 'n', 'n')")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO kg_mentions (article_uid, entity_id, page_index, mention_text, context, role) VALUES ('a', 1, 1, 'x', 'x', 'bogus')"
                )
            conn.execute("DELETE FROM kg_entities WHERE id=1")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM kg_mentions").fetchone()[0], 0)

    def test_projects_are_self_contained_folders(self) -> None:
        project = self.project
        self.assertEqual(project.root, self.root / "projects" / "Test review")
        self.assertTrue((project.root / ".med-lit" / "project.json").is_file())
        self.assertEqual(projects.get_project().name, "Test review")  # the only project needs no name
        second = projects.create_project("Sepsis: prediction?", str(self.root / "vault" / "Sepsis"))
        self.assertEqual(second.root, self.root / "vault" / "Sepsis")
        with self.assertRaisesRegex(ValueError, "Several projects"):
            projects.get_project()
        with self.assertRaisesRegex(ValueError, "already exists"):
            projects.create_project("Test review", str(self.root / "another"))
        with self.assertRaisesRegex(ValueError, "already a project"):
            projects.create_project("Other", str(second.root))
        self.assertEqual(projects.create_project("a/b:c").root.name, "a b c")

    def test_folder_settings_expand_variables_and_never_depend_on_the_working_directory(self) -> None:
        home = self.root / "home"
        with patch.dict(os.environ, {"HOME": str(home), "MED_LIT_PROJECTS_DIR": "${HOME}/reviews"}):
            self.assertEqual(config.projects_dir(), home / "reviews")
            created = projects.create_project("Desktop test")
            self.assertEqual(created.root, home / "reviews" / "Desktop test")
            nested = projects.create_project("Nested", "topics/nested")
            self.assertEqual(nested.root, home / "reviews" / "topics" / "nested")
        with patch.dict(os.environ, {"HOME": str(home), "MED_LIT_PROJECTS_DIR": "relative/reviews"}):
            self.assertEqual(config.projects_dir(), home / "relative" / "reviews")
        with patch.dict(os.environ, {"HOME": str(home), "MED_LIT_PROJECTS_DIR": "  "}):
            self.assertEqual(config.projects_dir(), home / "med-lit")

    def test_moved_project_is_found_again_with_its_runs(self) -> None:
        run_id = self.make_run()
        moved = self.root / "elsewhere" / "review"
        shutil.move(str(self.project.root), moved)
        self.assertFalse(projects.list_projects()[0]["available"])
        with self.assertRaisesRegex(ValueError, "open_project"):
            projects.get_project("Test review")
        with self.assertRaisesRegex(ValueError, "not found"):
            projects.locate(run_id)
        reopened = projects.open_project(str(moved))
        self.assertEqual((reopened.id, reopened.root), (self.project.id, moved))
        self.assertEqual(projects.locate(run_id)[1], moved / ".med-lit" / "runs" / run_id)
        self.assertTrue(projects.list_projects()[0]["available"])
