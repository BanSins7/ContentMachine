"""Local-only, no network regressions for targeted Group B visual window QA."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.probe_market_group_b_window import select_window, inspect_window, read_failed_baseline


class GroupBWindowProbe(unittest.TestCase):
    def setUp(self):
        # Timeline matches the real 33-frame run's window start instants.
        self.frames = [
            {"frame_ref": "ref-" + str(i), "observed_sec": str(round(i * 0.6135, 3))}
            for i in range(33)
        ]
        for i in range(24, 32):
            self.frames[i]["observed_sec"] = str(round(14.633 + (i - 24) * 0.625, 3))
        self.frames[32]["observed_sec"] = "19.633"
        self.manifest = {"frames": self.frames}

    def test_window_four_selects_only_eight_frames(self):
        frames, metadata = select_window(
            self.manifest, number=4, duration_sec=20.9)
        self.assertEqual(len(frames), 8)
        self.assertEqual(metadata["window"], 4)
        self.assertEqual(metadata["start_sec"], 14.633)
        self.assertEqual(metadata["end_sec"], 19.633)
        self.assertEqual(metadata["end_boundary_kind"], "next_sampled_frame")
        self.assertEqual(frames[0]["frame_ref"], "ref-24")
        self.assertEqual(frames[-1]["frame_ref"], "ref-31")

    def test_final_single_frame_window_has_valid_end(self):
        frames, metadata = select_window(
            self.manifest, number=5, duration_sec=20.9)
        self.assertEqual(len(frames), 1)
        self.assertEqual(metadata["start_sec"], 19.633)
        self.assertEqual(metadata["end_sec"], 20.9)
        self.assertEqual(metadata["end_boundary_kind"], "video_end_unobserved")

    def test_window_zero_or_beyond_retained_frames_denied(self):
        for n in (0, 6, -1, True):
            with self.subTest(n=n):
                with self.assertRaises(ValueError):
                    select_window(self.manifest, number=n, duration_sec=20.9)

    def test_baseline_requires_same_sha_and_old_failed_window(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "baseline.json"
            previous = {
                "schema_version": "marketing-real-mp4-live-b-check/v1",
                "video_sha256": "a" * 64,
                "vision_windows": [
                    {"state": "model_observed_partial"},
                    {"state": "unavailable", "reason": "model_or_validation_error",
                     "start_sec": 14.633, "end_sec": 19.633,
                     "sampled_frame_count": 8},
                ],
            }
            file.write_text(json.dumps(previous), encoding="utf-8")
            accepted = read_failed_baseline(file, sha="a" * 64, number=2)
            self.assertEqual(accepted["state"], "unavailable")
            with self.assertRaisesRegex(ValueError, "baseline_video_or_window_mismatch"):
                read_failed_baseline(file, sha="b" * 64, number=2)
            with self.assertRaisesRegex(ValueError, "baseline_window_was_not_failed"):
                read_failed_baseline(file, sha="a" * 64, number=1)
            with self.assertRaisesRegex(ValueError, "baseline_video_or_window_mismatch"):
                read_failed_baseline(file, sha="a" * 64, number=3)

    def test_empty_manifest_denied(self):
        with self.assertRaisesRegex(ValueError, "no_verified_frames"):
            select_window({"frames": []}, number=1, duration_sec=20.9)

    def test_no_consent_blocks_before_file_hash_or_vision(self):
        with patch("scripts.probe_market_group_b_window.preflight",
                   side_effect=ValueError("Explicit --allow-media-upload required")) as gate, patch(
                   "scripts.probe_market_group_b_window.classify_grounded") as vision:
            with self.assertRaisesRegex(ValueError, "--allow-media-upload"):
                inspect_window("does-not-exist.mp4", number=4, authorized=False)
            gate.assert_called_once()
            vision.assert_not_called()


if __name__ == "__main__":
    unittest.main()
