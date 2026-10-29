"""Safety tests for the local Windows QA/CI launcher (no PostgreSQL writes)."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.local_qa_ci import Runner, _safe_child_env


class LocalQARunnerSafetyTests(unittest.TestCase):
    def test_env_redirects_both_database_consumers_and_project_directory(self):
        dsn = "postgresql://qa_user:qa_test_pass@127.0.0.1:54329/cmqa_abc"
        with patch.dict(os.environ, {
            "CM_DB_DSN": "postgresql://production",
            "CM_TEST_PG_DSN": "postgresql://production",
            "OPENROUTER_API_KEY": "do-not-leak",
            "CM_DTK_API_KEY": "do-not-leak",
            "CM_COLLECTOR_LIVE_ENABLED": "1",
        }):
            env = _safe_child_env(dsn, workspace=Path("isolated_test_projects"))
        self.assertEqual(env["CM_DB_DSN"], dsn)
        self.assertEqual(env["CM_TEST_PG_DSN"], dsn)
        self.assertEqual(env["CM_DB_ENABLED"], "1")
        self.assertEqual(env["CM_PROJECTS_DIR"], "isolated_test_projects")
        self.assertEqual(env["CM_COLLECTOR_LIVE_ENABLED"], "0")
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertNotIn("CM_DTK_API_KEY", env)

    def test_offline_does_not_inherit_production_dsn(self):
        with patch.dict(os.environ, {
            "CM_DB_ENABLED": "1",
            "CM_DB_DSN": "postgresql://production",
            "CM_TEST_PG_DSN": "postgresql://production",
        }):
            env = _safe_child_env()
        self.assertEqual(env["CM_DB_ENABLED"], "0")
        self.assertEqual(env["CM_DB_DSN"], "")
        self.assertNotIn("CM_TEST_PG_DSN", env)

    def test_postgres_fixture_names_must_be_explicitly_allowlisted(self):
        with TemporaryDirectory() as temp:
            runner = Runner(Path(temp), offline=False)
            for name in ("contentmachine", "postgres", "cm_sp16_upgrade",
                         "cmqa_foo", "cmqa_a;b", "cmqa_123"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    runner.create_database(name)

    def test_offline_report_cannot_be_technical_pass(self):
        with TemporaryDirectory() as temp:
            runner = Runner(Path(temp), offline=True)
            self.assertEqual(runner.finish(), 2)
            import json
            info = json.loads((Path(temp) / "report.json").read_text(
                encoding="utf-8"))
            self.assertEqual(info["status"], "INCOMPLETE_OFFLINE")
            self.assertEqual(info["full_business_pipeline_e2e"], "NOT_VERIFIED")

    def test_failed_gate_cannot_be_marked_ready(self):
        with TemporaryDirectory() as temp:
            runner = Runner(Path(temp), offline=False)
            runner.record("missing_node", "FAIL")
            self.assertEqual(runner.finish(), 2)


if __name__ == "__main__":
    unittest.main()
