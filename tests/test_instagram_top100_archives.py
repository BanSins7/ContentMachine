"""Offline ZIP export regression tests; no network, provider billing or AI calls."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from intelligence.instagram_top100.archives import (
    append_download, archive_path, inventory_from_run, package_available,
    package_batch, BATCH_SIZE
)


class ZipBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.source = self.work / "InstagramArchive"
        self.base = self.work / "ContentMachineInstagramTop100"
        self.run_id = "a" * 32

    def video(self, n: int, *, account="my.account", code=None):
        shortcode = code or f"CLIP{n:06d}"
        url = f"https://www.instagram.com/reel/{shortcode}/"
        folder = self.source / account
        folder.mkdir(parents=True, exist_ok=True)
        original = folder / (shortcode + ".mp4")
        original.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes([n % 256]) * 128)
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        (folder / (shortcode + ".json")).write_text(json.dumps({
            "verified_shortcode": shortcode, "source_url": url,
            "video_sha256": digest
        }), encoding="utf-8")
        return {
            "username": account, "shortcode": shortcode, "url": url,
            "views": n * 1000, "published_at": "2026-11-10T10:00:00+00:00",
        }

    def make_run(self, clips):
        return {
            "id": self.run_id,
            "accounts": [],
            "archive_inventory": clips,
            "archives": []
        }

    def zip_names(self, batch):
        with zipfile.ZipFile(archive_path(self.base, self.run_id, batch)) as zf:
            self.assertIsNone(zf.testzip())
            names = zf.namelist()
            manifest = json.loads(zf.read("manifest.json"))
        return names, manifest

    def test_exactly_100_in_one_zip_and_originals_untouched(self):
        clips = [self.video(i) for i in range(100)]
        run = self.make_run(clips)
        result = package_available(run, self.base, include_partial=False, source_root=self.source)
        self.assertEqual([x["count"] for x in result], [100])
        names, manifest = self.zip_names(1)
        self.assertEqual(len([x for x in names if x.endswith(".mp4")]), 100)
        self.assertEqual(manifest["video_count"], 100)
        self.assertEqual(manifest["run_id"], self.run_id)
        self.assertTrue(all((self.source / v["file"]).is_file() for v in manifest["videos"]))
        self.assertEqual(names[-1], "manifest.json")

    def test_101_creates_full_plus_partial_final(self):
        clips = [self.video(i) for i in range(101)]
        run = self.make_run(clips)
        package_available(run, self.base, include_partial=False, source_root=self.source)
        self.assertEqual([x["count"] for x in run["archives"]], [100])
        package_available(run, self.base, include_partial=True, source_root=self.source)
        self.assertEqual([x["count"] for x in run["archives"]], [100, 1])
        names, manifest = self.zip_names(2)
        self.assertEqual(len(names), 2)
        self.assertEqual(manifest["video_count"], 1)

    def test_205_is_three_zips_100_100_5(self):
        clips = [self.video(i) for i in range(205)]
        run = self.make_run(clips)
        package_available(run, self.base, include_partial=True, source_root=self.source)
        self.assertEqual([x["count"] for x in run["archives"]], [100, 100, 5])
        self.assertEqual(sum(x["count"] for x in run["archives"]), 205)
        names, manifest = self.zip_names(3)
        self.assertEqual(len(names), 6)
        self.assertEqual(manifest["video_count"], 5)
        self.assertEqual(manifest["videos"][0]["views"], 200000)

    def test_existing_zip_reusable_after_source_deleted(self):
        clips = [self.video(i) for i in range(3)]
        run = self.make_run(clips)
        first = package_available(run, self.base, include_partial=True, source_root=self.source)
        before = archive_path(self.base, self.run_id, 1).stat().st_mtime_ns
        for file in self.source.glob("*/*"):
            file.unlink()
        again = package_available(run, self.base, include_partial=True, source_root=self.source)
        self.assertEqual(first, again)
        self.assertEqual(archive_path(self.base, self.run_id, 1).stat().st_mtime_ns, before)
        names, _ = self.zip_names(1)
        self.assertEqual(len(names), 4)

    def test_partial_replaced_when_more_successful_clips_arrive(self):
        clips = [self.video(i) for i in range(20)]
        run = self.make_run(clips)
        package_available(run, self.base, include_partial=True, source_root=self.source)
        self.assertEqual(run["archives"][0]["count"], 20)
        run["archive_inventory"].extend(self.video(i) for i in range(20, 101))
        package_available(run, self.base, include_partial=False, source_root=self.source)
        self.assertEqual(run["archives"][0]["count"], 100)
        package_available(run, self.base, include_partial=True, source_root=self.source)
        self.assertEqual([x["count"] for x in run["archives"]], [100, 1])
        self.assertEqual(self.zip_names(1)[1]["video_count"], 100)

    def test_corrupt_source_never_archived(self):
        clip = self.video(3)
        source = self.source / clip["username"] / (clip["shortcode"] + ".mp4")
        source.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            package_batch(self.base, self.run_id, 1, [clip], source_root=self.source)
        self.assertFalse(archive_path(self.base, self.run_id, 1).exists())
        self.assertEqual(list((self.base / "archives" / self.run_id).glob("*.partial")), [])

    def test_duplicate_shortcodes_different_accounts_do_not_collide(self):
        clips = [self.video(1, account="first", code="ABCDE123"),
                 self.video(2, account="second", code="ABCDE123")]
        package_batch(self.base, self.run_id, 1, clips, source_root=self.source)
        names, manifest = self.zip_names(1)
        self.assertEqual(names[:2], ["first/ABCDE123.mp4", "second/ABCDE123.mp4"])
        self.assertEqual(manifest["video_count"], 2)

    def test_repeated_registration_does_not_duplicate_mp4(self):
        clip = self.video(1)
        post = {**clip, "status": "downloaded"}
        run = {"accounts": [{"username": "my.account", "posts": [post]}],
               "archive_inventory": [], "archives": []}
        append_download(run, "my.account", post)
        append_download(run, "my.account", post)
        inventory_from_run(run)
        self.assertEqual(len(run["archive_inventory"]), 1)
        self.assertTrue(post["archive_registered"])

    def test_no_tail_zip_during_active_run(self):
        run = self.make_run([self.video(i) for i in range(99)])
        result = package_available(run, self.base, include_partial=False, source_root=self.source)
        self.assertEqual(result, [])
        self.assertFalse((self.base / "archives" / self.run_id).exists())

    def test_duplicate_member_is_rejected(self):
        clip = self.video(1)
        with self.assertRaisesRegex(ValueError, "Повтор MP4"):
            package_batch(self.base, self.run_id, 1, [clip, clip], source_root=self.source)


if __name__ == "__main__":
    unittest.main()
