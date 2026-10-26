"""VC-MOD-06 operator UI: static safety and versioned route smoke."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CollectorOwnerUITests(unittest.TestCase):
    def test_dedicated_operator_ui_is_served_and_ids_are_unique(self):
        html = (ROOT / "site/index.html").read_text(encoding="utf-8")
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertEqual(html.count('src="/collector.js"'), 1)
        self.assertIn("'/collector.js': 'collector.js'", app)
        for name in (
            "collectorV1", "collectorV1Project", "collectorV1Load",
            "collectorV1Manifest", "collectorV1Shard",
            "collectorV1Preview", "collectorV1Confirm",
            "collectorV1Commit", "collectorV1RunId",
            "collectorV1ReleaseConfirm", "collectorV1Release",
            "collectorV1Status", "collectorV1Result",
        ):
            with self.subTest(name=name):
                self.assertEqual(len(re.findall(r'id="'+name+r'"', html)), 1)
        self.assertIn('id="collectorV1Commit" type="button" class="button primary" disabled',html)
        self.assertIn('id="collectorV1Release" type="button" class="button settings-secondary" disabled',html)

    def test_operator_js_has_no_automatic_provider_submission(self):
        source=(ROOT / "site/collector.js").read_text(encoding="utf-8")
        self.assertIn("manifest-preview", source)
        self.assertIn("manifest-runs", source)
        self.assertIn("manifests?project_id=", source)
        self.assertIn("/release", source)
        self.assertIn("X-ContentMachine", source)
        self.assertIn("crypto.randomUUID()", source)
        self.assertNotIn("setInterval(", source)
        self.assertNotIn("setTimeout(", source)
        self.assertNotIn("/api/tools/dtk/run", source)
        self.assertNotIn("search_posts", source)
        self.assertIn("collectorV1ReleaseConfirm", source)
        self.assertIn("collectorV1Confirm", source)
        self.assertIn("preview_only", (ROOT / "intelligence/collector/manifest_access.py").read_text(encoding="utf-8"))


if __name__=="__main__":
    unittest.main()
