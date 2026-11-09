"""Marketing-owned GA13 regression for a real Windows OCR tracking crash.

No paid model, media, database, network or legacy group_a dependency.
"""
from __future__ import annotations

from decimal import Decimal
import unittest

from intelligence.market_intelligence._copied.group_a.contracts_v1 import canonical_sha256
from intelligence.market_intelligence._copied.group_a.extractors.text_tracking import (
    _spatial, track_screen_text,
)

SOURCE = "b" * 64


def _word(text="HELLO", *, x=20, line="l000001"):
    return {"text": text, "line_ref": line, "confidence_pct": "90.00",
            "bbox": {"x": x, "y": 65, "width": 100, "height": 25}}


def _fixture(rows):
    source_frames = []
    ocr_frames = []
    for idx, (stamp, words) in enumerate(rows, 1):
        sha = f"{idx:064x}"
        ref = f"ga-frame:f{idx:03d}-{sha}"
        shared = {"sample_index": idx, "observed_sec": stamp,
                  "width": 600, "height": 200, "frame_ref": ref}
        source_frames.append({**shared, "png_sha256": sha})
        ocr_frames.append({
            **shared,
            "frame_sha256": sha,
            "state": "partial_language",
            "text_status": "text_observed" if words else "unknown",
            "words": words,
            "word_count": len(words),
            "languages_used": ["eng"],
            "engine_rotation": 0,
        })
    manifest = {
        "schema_version": "group-a-frame-manifest/v1",
        "source_media_sha256": SOURCE,
        "source_duration_sec": "10.000",
        "coverage": {"type": "sparse_observations",
                     "unsampled_intervals_present": True},
        "frames": source_frames,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    ocr = {
        "schema_version": "ga-frame-ocr/v1",
        "method_version": "group-a-local-frame-ocr-v1",
        "source_media_sha256": SOURCE,
        "frame_manifest_sha256": manifest["manifest_sha256"],
        "project_id": "b" * 32,
        "run_id": "11111111-2222-4333-8444-555555555555",
        "temporal_coverage": "unknown",
        "screen_text_absence": "unknown",
        "unsampled_intervals_present": True,
        "counts": {"observed": len(rows), "attempted": len(rows)},
        "frames": ocr_frames,
    }
    ocr["observation_sha256"] = canonical_sha256(ocr)
    return ocr, manifest


class MarketingTextTrackingRegression(unittest.TestCase):
    def test_cross_frame_iou_preserves_second_observation(self):
        first = {"frame_width": 600, "frame_height": 200,
                 "bbox": {"x": 20, "y": 65, "width": 100, "height": 25}}
        second = {"frame_width": 600, "frame_height": 200,
                  "bbox": {"x": 22, "y": 65, "width": 100, "height": 25}}
        center_distance, overlap = _spatial(first, second)
        self.assertIsInstance(center_distance, Decimal)
        self.assertGreater(overlap, Decimal("0.9"))
        self.assertEqual(second["frame_width"], 600)

    def test_ocr_recurrence_matches_nearby_words_on_two_frames(self):
        ocr, manifest = _fixture([
            ("0.100", [_word(x=20)]),
            ("0.400", [_word(x=22)]),
        ])
        result = track_screen_text(
            ocr, manifest, verify_current=lambda _: True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["tracks"]), 1)
        self.assertEqual(result["tracks"][0]["observation_count"], 2)
        self.assertFalse(result["continuous_intervals_verified"])
        self.assertIsNone(result["video_wide_screen_word_count"])

    def test_same_frame_duplicate_word_uses_spatial_iou(self):
        ocr, manifest = _fixture([
            ("0.100", [_word(x=20), _word(x=21, line="l000002")]),
        ])
        result = track_screen_text(
            ocr, manifest, verify_current=lambda _: True)
        self.assertEqual(result["counts"]["duplicate_lines_suppressed"], 1)
        self.assertEqual(len(result["tracks"]), 1)


if __name__ == "__main__":
    unittest.main()
