"""GC-MOD-00 dependency-free Group C boundary unit tests."""
import unittest
from dataclasses import replace
from decimal import Decimal

from intelligence.group_c import (
    ContractError, GroupCInputPointer, BREAKOUT_WEIGHTS,
    FRESHNESS_HALF_LIFE_H, canonical_hash, content_type_for_kind,
)

P = GroupCInputPointer(
    project_id="a"*32,
    qdp_run_id="00112233-4455-6677-8899-aabbccddeeff",
    video_id=123,
    source_id="fixture-source",
    content_kind="video",
    metric_snapshot_id=789,
    preparation_sha256="b"*64,
    metric_source_definition="views-v1",
    metric_source_version="provider-v1",
)


class TestGroupCFoundation(unittest.TestCase):
    def test_kind_mapping(self):
        for kind, expected in (
            ("video", "video"), ("slideshow_video", "video"),
            ("photo_carousel", "carousel"), ("mixed_carousel", "carousel")
        ):
            with self.subTest(kind=kind):
                self.assertEqual(content_type_for_kind(kind), expected)
        for invalid in ("", "unknown", "image_post", "carousel", "video ", None, 2):
            with self.subTest(value=invalid), self.assertRaises(ContractError):
                content_type_for_kind(invalid)

    def test_strict_input_pointer(self):
        self.assertEqual(P.metric_snapshot_id, 789)
        for change in (
            {"project_id": "A"*32}, {"project_id": "a"*31},
            {"qdp_run_id": "bad"}, {"qdp_run_id": "00112233445566778899aabbccddeeff"},
            {"video_id": 0}, {"video_id": True},
            {"metric_snapshot_id": -1}, {"metric_snapshot_id": False},
            {"source_id": " "}, {"content_kind": "image_post"},
            {"preparation_sha256": "B"*64}, {"preparation_sha256": "b"*63},
            {"metric_source_definition": ""}, {"metric_source_version": " "*2},
            {"schema_version": "group-c-input/v2"},
        ):
            with self.subTest(change=change), self.assertRaises(ContractError):
                replace(P, **change)

    def test_fixed_formula_constants(self):
        self.assertEqual(BREAKOUT_WEIGHTS,
            (Decimal("0.45"), Decimal("0.30"), Decimal("0.15"), Decimal("0.10")))
        self.assertEqual(sum(BREAKOUT_WEIGHTS), Decimal("1.00"))
        self.assertEqual(FRESHNESS_HALF_LIFE_H, Decimal("48"))

    def test_canonical_metadata_hash(self):
        self.assertEqual(canonical_hash({"b": 2, "a": 1}),
                         canonical_hash({"a": 1, "b": 2}))
        self.assertNotEqual(canonical_hash({"a": 1}), canonical_hash({"a": 2}))
        for invalid in ([], None, {"x": float("nan")}, {"x": object()}):
            with self.subTest(type=type(invalid).__name__), self.assertRaises(ContractError):
                canonical_hash(invalid)

    def test_no_fake_completed_scorer(self):
        import intelligence.group_c as module
        self.assertFalse(hasattr(module, "compute_c_metrics"))
        self.assertFalse(hasattr(module, "score_video"))


if __name__ == "__main__":
    unittest.main()
