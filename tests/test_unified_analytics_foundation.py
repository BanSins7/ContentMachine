"""UA-MOD-00 pure, no-provider/no-database foundation tests."""
import unittest
from dataclasses import replace

from intelligence.unified_analytics import (
    AnalyticsBoundaryError, UnifiedAnalyticsInputPointer,
    canonical_hash, content_type_for_kind,
)

POINTER = UnifiedAnalyticsInputPointer(
    project_id="a"*32,
    video_id=123,
    source_id="fixture-source",
    source_policy_version_id=7,
    content_kind="video",
    qdp_run_id="00112233-4455-6677-8899-aabbccddeeff",
    preparation_sha256="b"*64,
    group_a_analysis_id=100,
    group_b_analysis_id=200,
    calculated_metric_id=300,
    sre_decision_sha256="c"*64,
)

class TestUnifiedAnalyticsFoundation(unittest.TestCase):
    def test_supported_content_kinds_only(self):
        for source, expected in [
            ("video","video"), ("slideshow_video","video"),
            ("photo_carousel","carousel"), ("mixed_carousel","carousel"),
        ]:
            with self.subTest(kind=source):
                self.assertEqual(content_type_for_kind(source), expected)
        for wrong in ("image_post", "unknown", "carousel", "video ", None, True):
            with self.subTest(value=wrong), self.assertRaises(AnalyticsBoundaryError):
                content_type_for_kind(wrong)

    def test_exact_pointer_checks(self):
        self.assertEqual(POINTER.video_id, 123)
        for kw in [
            {"project_id":"A"*32}, {"project_id":"a"*31},
            {"video_id":0}, {"video_id":True},
            {"source_id":" "}, {"source_policy_version_id":0},
            {"content_kind":"image_post"}, {"group_a_analysis_id":False},
            {"group_b_analysis_id":-1}, {"calculated_metric_id":0},
            {"qdp_run_id":"not-uuid"}, {"qdp_run_id":"00112233445566778899aabbccddeeff"},
            {"preparation_sha256":"B"*64}, {"preparation_sha256":"b"*63},
            {"sre_decision_sha256":"c"*65},
            {"schema_version":"unified-analytics-input/v99"},
        ]:
            with self.subTest(change=kw), self.assertRaises(AnalyticsBoundaryError):
                replace(POINTER, **kw)

    def test_stable_canonical_hash(self):
        self.assertEqual(canonical_hash({"b":2,"a":1}), canonical_hash({"a":1,"b":2}))
        self.assertNotEqual(canonical_hash({"a":1}), canonical_hash({"a":2}))
        for value in (None, [], {"x":float("nan")}, {"x":object()}):
            with self.subTest(type=type(value).__name__), self.assertRaises(AnalyticsBoundaryError):
                canonical_hash(value)

    def test_no_fake_qualification_or_c_scorer(self):
        import intelligence.unified_analytics as module
        self.assertFalse(hasattr(module, "compute_c_metrics"))
        self.assertFalse(hasattr(module, "run_qualification"))
        self.assertFalse(hasattr(module, "aggregate_project"))

if __name__ == "__main__":
    unittest.main()
