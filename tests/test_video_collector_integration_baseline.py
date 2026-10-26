"""VC-FIX-00 independent-module integration tree regressions."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CollectorIntegrationBaselineTests(unittest.TestCase):
    def test_migrations_preserve_query_validation(self):
        m = ROOT / "intelligence" / "migrations"
        self.assertTrue((m / "016_query_validation.sql").is_file())
        self.assertTrue((m / "054_collector_planning.sql").is_file())
        self.assertTrue((m / "055_collector_worker.sql").is_file())
        self.assertFalse((m / "016_collector_planning.sql").exists())
        self.assertFalse((m / "017_collector_worker.sql").exists())

    def test_dtk_and_main_api_routes_both_present(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("intelligence.project_sources.context_http", app)
        self.assertIn("intelligence.project_sources.vision_http", app)
        self.assertIn("intelligence.connectors.http_api", app)
        self.assertIn("intelligence.discovery.http_api", app)
        self.assertIn("'/dtk-tool.js': 'dtk-tool.js'", app)

    def test_provider_and_frontend_bundled(self):
        self.assertTrue((ROOT / "intelligence/collector/provider_inspector.py").is_file())
        self.assertTrue((ROOT / "intelligence/collector/worker.py").is_file())
        self.assertTrue((ROOT / "intelligence/connectors/dtk.py").is_file())
        html = (ROOT / "site/index.html").read_text(encoding="utf-8")
        self.assertIn('src="/dtk-tool.js"', html)
        self.assertIn('id="dtkForm"', html)
        self.assertIn('id="tbContextModeTitle"', html)


if __name__ == "__main__":
    unittest.main()
