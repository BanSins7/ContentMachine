"""UA-MOD-01: documentation/ownership drift guards. No DB or paid services."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "UA_LEGACY_PORT_MATRIX.md"
OLD_SHA = "12921968ef3d5c6f5e706fd8e625c54733405f3b"
LEGACY_RUNTIME = (
    "contracts.py", "metrics.py", "service.py", "aggregate.py",
    "queue.py", "worker.py", "http_api.py", "__init__.py",
)
OLD_MIGRATIONS = (
    "016_analytics_unified.sql",
    "017_analytics_jobs_and_read_gates.sql",
    "018_analytics_audit_hardening.sql",
)
OLD_TESTS = (
    "test_analytics_engine.py",
    "test_analytics_aggregate.py",
    "test_analytics_pipeline_postgres.py",
    "test_analytics_complete_pipeline_pg.py",
    "test_analytics_http_local.py",
    "test_analytics_worker_watch.py",
    "test_analytics_ui.cjs",
)


class LegacyAuditBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = DOC.read_text(encoding="utf-8")

    def test_old_sources_are_pinned_and_all_owned(self):
        self.assertIn(OLD_SHA, self.doc)
        for name in LEGACY_RUNTIME:
            with self.subTest(source=name):
                self.assertIn("intelligence/analytics/" + name, self.doc)
        for name in OLD_TESTS:
            with self.subTest(test=name):
                self.assertIn("tests/" + name, self.doc)
        for name in OLD_MIGRATIONS:
            with self.subTest(migration=name):
                self.assertIn("intelligence/migrations/" + name, self.doc)

    def test_no_legacy_runtime_or_conflicting_ddl_is_installed(self):
        self.assertFalse((ROOT / "intelligence" / "analytics").exists())
        self.assertTrue((ROOT / "intelligence" / "migrations"
                         / "016_query_validation.sql").exists())
        for name in OLD_MIGRATIONS:
            with self.subTest(migration=name):
                self.assertFalse((ROOT / "intelligence" / "migrations" / name).exists())

    def test_explicit_group_c_only_ownership_and_stage_gate(self):
        self.assertIn("**Group C**; **drop from UAE**", self.doc)
        self.assertIn("**Next stage UA-MOD-02:**", self.doc)
        self.assertIn("QDP", self.doc)
        self.assertIn("current live", self.doc.lower())
        import intelligence.unified_analytics as module
        self.assertFalse(hasattr(module, "compute_c_metrics"))
        self.assertFalse(hasattr(module, "run_qualification"))


if __name__ == "__main__":
    unittest.main()
