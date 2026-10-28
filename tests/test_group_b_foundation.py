"""GB-MOD-00: pure boundary tests, no DB, no network and no paid models."""
import unittest
from dataclasses import replace

from intelligence.group_b import (
    ContractError, GroupBInputPointer, canonical_hash, content_type_for_kind,
)

P = GroupBInputPointer(
    project_id="a"*32,
    qdp_run_id="00112233-4455-6677-8899-aabbccddeeff",
    video_id=101,
    source_id="source-fixture",
    content_kind="video",
    group_a_analysis_id=500,
    qdp_input_sha256="b"*64,
    group_a_snapshot_sha256="c"*64,
    media_manifest_sha256="d"*64,
)

class TestGroupBFoundation(unittest.TestCase):
    def test_kind_contract(self):
        self.assertEqual(content_type_for_kind("video"), "video")
        self.assertEqual(content_type_for_kind("slideshow_video"), "video")
        self.assertEqual(content_type_for_kind("photo_carousel"), "carousel")
        self.assertEqual(content_type_for_kind("mixed_carousel"), "carousel")
        for unsupported in ("image_post", "unknown", None, "", "video "):
            with self.subTest(kind=unsupported), self.assertRaises(ContractError):
                content_type_for_kind(unsupported)

    def test_strict_pointer(self):
        self.assertEqual(P.content_kind, "video")
        for kwargs in (
            {"project_id": "A"*32}, {"project_id": "a"*31},
            {"qdp_run_id": "not-uuid"}, {"qdp_run_id": "00112233445566778899aabbccddeeff"},
            {"video_id": 0}, {"video_id": True}, {"group_a_analysis_id": False},
            {"source_id": " "}, {"content_kind": "image_post"},
            {"qdp_input_sha256": "A"*64},
            {"group_a_snapshot_sha256": "c"*63},
            {"media_manifest_sha256": "d"*65},
            {"schema_version": "group-b-input/v2"},
        ):
            with self.subTest(case=kwargs), self.assertRaises(ContractError):
                replace(P, **kwargs)

    def test_canonical_hash_and_invalid_data(self):
        self.assertEqual(canonical_hash({"b": 2, "a": 1}),
                         canonical_hash({"a": 1, "b": 2}))
        self.assertNotEqual(canonical_hash({"a": 1}), canonical_hash({"a": 2}))
        for v in ([], None, {"x": float("nan")}, {"x": object()}):
            with self.subTest(value=type(v).__name__), self.assertRaises(ContractError):
                canonical_hash(v)

    def test_only_local_boundary(self):
        import intelligence.group_b as module
        self.assertFalse(hasattr(module, "run_analysis"))
        self.assertFalse(hasattr(module, "call_llm"))

if __name__ == "__main__":
    unittest.main()
