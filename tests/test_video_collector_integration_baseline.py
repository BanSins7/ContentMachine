"""VC-MOD-00 integrated-module baseline checks (no real DTK requests)."""
from __future__ import annotations
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CollectorIntegrationBaselineTests(unittest.TestCase):
    def test_main_and_collector_migrations_are_disjoint_and_ordered(self):
        d = ROOT / "intelligence/migrations"
        names = sorted(p.name for p in d.glob("[0-9]*.sql"))
        required = (
            "015_search_query_generation.sql", "016_query_validation.sql",
            "030_project_source_intelligence_foundation.sql",
            "041_tb_approved_project_context.sql", "042_ke_input_bundles.sql",
            "053_ke_c_qualified_execution.sql", "054_collector_planning.sql",
            "055_collector_worker.sql",
        )
        for name in required:
            with self.subTest(name=name):
                self.assertIn(name, names)
        self.assertEqual(list(required), sorted(required))
        self.assertNotIn("016_collector_planning.sql", names)
        self.assertNotIn("017_collector_worker.sql", names)
        sql = (d / "054_collector_planning.sql").read_text("utf-8")
        sql += (d / "055_collector_worker.sql").read_text("utf-8")
        for marker in (
            "CREATE TABLE collector_query_sets",
            "CREATE TABLE collector_task_queries",
            "CREATE TABLE collector_video_hypotheses",
            "CREATE TABLE collector_execution_grants",
        ):
            self.assertIn(marker, sql)
        self.assertNotIn("CREATE TABLE videos (", sql)

    def test_existing_main_routes_and_modules_not_clobbered(self):
        app = (ROOT / "app.py").read_text("utf-8")
        for name in ("context_http", "vision_http", "evidence_http",
                     "video_http", "file_http"):
            self.assertIn("intelligence.project_sources." + name, app)
        self.assertIn("intelligence.connectors.http_api", app)
        self.assertIn("intelligence.discovery.http_api", app)
        self.assertIn("'/dtk-tool.js': 'dtk-tool.js'", app)
        self.assertIn("intelligence.collector.http_api",
                      (ROOT / "intelligence/discovery/http_api.py").read_text("utf-8"))
        for path in (
            "intelligence/knowledge_extraction/knowledge_snapshot.py",
            "intelligence/project_sources/context_builder.py",
            "intelligence/search_planner/query_validation/service.py",
        ):
            self.assertTrue((ROOT / path).is_file(), path)

    def test_original_collector_and_single_dtk_transport_bundled(self):
        for path in (
            "intelligence/collector/contracts.py",
            "intelligence/collector/query_intake.py",
            "intelligence/collector/registry.py",
            "intelligence/collector/planner.py",
            "intelligence/collector/repository.py",
            "intelligence/collector/execution.py",
            "intelligence/collector/worker.py",
            "intelligence/collector/provider_inspector.py",
            "intelligence/collector/adapters/dtk_tiktok.py",
            "intelligence/collector/http_api.py",
            "intelligence/connectors/dtk.py",
            "intelligence/connectors/http_api.py",
            "tests/test_video_collector_stage2.py",
            "tests/test_video_collector_worker_stage8.py",
            "tests/test_video_collector_inspector_stage9.py",
            "tests/test_dtk_connector.py",
        ):
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file(), path)
        adapter = (ROOT / "intelligence/collector/adapters/dtk_tiktok.py").read_text("utf-8")
        self.assertIn("intelligence.connectors", adapter)
        self.assertEqual(len(list((ROOT / "intelligence/connectors").glob("dtk.py"))), 1)

    def test_owner_ui_contains_collector_and_new_main_elements(self):
        html = (ROOT / "site/index.html").read_text("utf-8")
        for marker in ('src="/dtk-tool.js"', 'id="dtkForm"',
                       'id="tbContextModeTitle"', 'src="/project-sources.js"',
                       'src="/context-quote.js"'):
            self.assertIn(marker, html)
        self.assertEqual(html.count('id="dtkForm"'), 1)
        self.assertTrue((ROOT / "site/dtk-tool.js").is_file())

    def test_obsolete_roadmap_not_part_of_module(self):
        self.assertFalse((ROOT / "docs/VIDEO_COLLECTOR_PIPELINE_V1.md").exists())
        self.assertTrue((ROOT / "docs/VIDEO_COLLECTOR_INTEGRATION.md").exists())

    def test_all_core_imports_do_not_contact_network_or_require_sre_pr(self):
        code = """
import importlib, socket, sys
def deny(*args, **kwargs):
    raise AssertionError("Network call occurred during VC import")
socket.create_connection = deny
socket.socket.connect = deny
for name in (
  'intelligence.collector', 'intelligence.collector.contracts',
  'intelligence.collector.query_intake', 'intelligence.collector.registry',
  'intelligence.collector.planner', 'intelligence.collector.repository',
  'intelligence.collector.execution', 'intelligence.collector.worker',
  'intelligence.collector.provider_inspector',
  'intelligence.collector.adapters.dtk_tiktok',
  'intelligence.collector.http_api', 'intelligence.connectors.dtk',
  'intelligence.connectors.http_api'):
    importlib.import_module(name)
print('VC_IMPORT_OK')
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                capture_output=True, text=True, timeout=30,
                                check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("VC_IMPORT_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
