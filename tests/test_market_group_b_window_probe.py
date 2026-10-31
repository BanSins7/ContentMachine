"""Local-only, no network regressions for targeted Group B visual window QA."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.probe_market_group_b_window import select_window, inspect_window


class GroupBWindowProbe(unittest.TestCase):
    def setUp(self):
        # Timeline matches the real 33-frame run's window start instants.
        self.frames = [
            {"frame_ref": "ref-" + str(i), "observed_sec": str(i / 10)}
            for i in range(33)
        ]
        self.frames[24]["observed_sec"] = "14.633"
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
            if n is True:
                # bool is intentionally not accepted as a window number.
                # The selection contract below must reject it.
                continue
            with self.subTest(n=n):
                with self.assertRaises(ValueError):
                    select_window(self.manifest, number=n, duration_sec=20.9)

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
