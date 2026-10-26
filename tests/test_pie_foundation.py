"""PIE-MOD-00: pure input-boundary tests; no DB, UAE calls, or LLM."""
from dataclasses import replace
import unittest

from intelligence.patterns_insights import (
    PIEBoundaryError, PIEDatasetPointer, canonical_hash,
)

FIXTURE = PIEDatasetPointer(
    project_id="a"*32,
    source_id="source-fixture",
    source_policy_version_id=123,
    uae_engine_version="uae-test/v1",
    uae_dataset_sha256="b"*64,
    uae_manifest_sha256="c"*64,
    calculation_config_id=34,
    source_metric_definition="views-v1",
    source_metric_version="provider-v1",
    as_of="2026-10-26T11:00:00+01:00",
)


class PIEFoundationTests(unittest.TestCase):
    def test_strict_valid_pointer(self):
        self.assertEqual(FIXTURE.project_id, "a"*32)
        self.assertEqual(FIXTURE.source_metric_definition, "views-v1")
        self.assertEqual(replace(FIXTURE, as_of="2026-10-26T10:00:00Z").as_of[-1], "Z")

    def test_invalid_metadata_rejected(self):
        invalid = (
            {"project_id":"A"*32}, {"project_id":"a"*31},
            {"source_id":" "}, {"source_policy_version_id":False},
            {"source_policy_version_id":0}, {"calculation_config_id":0},
            {"calculation_config_id":True}, {"uae_engine_version":" "},
            {"source_metric_definition":""}, {"source_metric_version":"x"*121},
            {"uae_dataset_sha256":"B"*64}, {"uae_manifest_sha256":"c"*63},
            {"as_of":"2026-10-26T11:00:00"}, {"as_of":"not-a-time"},
            {"schema_version":"pie-dataset-pointer/v2"},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(PIEBoundaryError):
                replace(FIXTURE, **changes)

    def test_hash_deterministic_and_no_nan(self):
        self.assertEqual(canonical_hash({"b":2,"a":1}),canonical_hash({"a":1,"b":2}))
        self.assertNotEqual(canonical_hash({"a":1}),canonical_hash({"a":2}))
        for item in ([],None,{"x":float("nan")},{"x":object()}):
            with self.subTest(type=type(item).__name__), self.assertRaises(PIEBoundaryError):
                canonical_hash(item)

    def test_no_fake_statistics_or_live_insight(self):
        import intelligence.patterns_insights as pie
        for name in ("discover_patterns","generate_insights","run_analysis","call_llm"):
            self.assertFalse(hasattr(pie,name))


if __name__ == "__main__":
    unittest.main()
