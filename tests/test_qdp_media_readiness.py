"""FIX-09 strict, read-only local media readiness and independent A/B gates."""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from intelligence.qualified_data.media_readiness import (
    MAX_LOCAL_BYTES, read_verified_media, verify_media_candidates,
)

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
# A tiny container HEADER fixture, not a decoded/real Group A video.
# FFprobe/codec checks belong to Group A and are never inferred by QDP.
HEADER = b"\0\0\0\x18ftypisom\0\0\x02\0" + b"file-content-example"


def meta(**kw):
    out = {"video_id": 44, "source_id": "source-one", "content_kind": "video",
           "can_analyze_media": True, "permissions": {"can_analyze_media": True}}
    out.update(kw)
    return out


def candidate(path, **kw):
    v = {"id": 9, "video_id": 44, "source_id": "source-one", "media_kind": "video",
         "local_path": str(path), "sha256": hashlib.sha256(HEADER).hexdigest(),
         "permission": "analysis", "expires_at": NOW+timedelta(days=1)}
    v.update(kw)
    return v


class MediaReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "sample.mp4"
        self.path.write_bytes(HEADER)

    def check(self, rows, metadata=None, **kw):
        return verify_media_candidates(rows, metadata if metadata is not None else meta(),
                                       roots=[str(self.root)], now=NOW, **kw)

    def test_real_local_candidate_has_pointer_no_local_path(self):
        found = self.check([candidate(self.path)])
        self.assertEqual(found["A"]["state"], "ready")
        self.assertEqual(found["B"]["state"], "ready")
        self.assertEqual(found["asset"]["id"], 9)
        self.assertEqual(found["asset"]["size_bytes"], len(HEADER))
        self.assertNotIn("local_path", found["asset"])
        self.assertNotIn("path", found["asset"])

    def test_absent_media_does_not_imply_access(self):
        found = self.check([])
        self.assertEqual(found["A"]["reason_code"], "media_asset_missing")
        self.assertEqual(found["B"]["reason_code"], "media_asset_missing")
        self.assertIsNone(found["asset"])

    def test_foreign_video_or_source_not_authorized(self):
        for kw in ({"video_id": 45}, {"source_id": "foreign"}):
            with self.subTest(kw=kw):
                found = self.check([candidate(self.path, **kw)])
                self.assertEqual(found["A"]["reason_code"], "media_source_mismatch")

    def test_no_rights_no_path_io(self):
        class WatchRows(list):
            def __getitem__(self, _):
                raise AssertionError("Media must never be inspected without consent")
        for x in (meta(can_analyze_media=False),
                  meta(permissions={"can_analyze_media": False})):
            found = self.check(WatchRows([candidate(self.path)]), x)
            self.assertEqual(found["A"]["reason_code"], "media_rights_missing")

    def test_expiry_metadata_permission_and_kind(self):
        cases = (
            ({"expires_at": NOW-timedelta(seconds=1)}, "media_asset_expired"),
            ({"permission": "metadata_only"}, "media_permission_missing"),
            ({"permission": ""}, "media_permission_missing"),
            ({"media_kind": "image"}, "media_kind_incompatible"),
            ({"sha256": None}, "media_sha256_unverified"),
            ({"sha256": "0"*64}, "media_hash_mismatch"),
        )
        for change, reason in cases:
            with self.subTest(change=change):
                r = self.check([candidate(self.path, **change)])
                self.assertEqual(r["A"]["reason_code"], reason)
                self.assertEqual(r["B"]["state"], "blocked")

    def test_relative_path_and_missing_real_file(self):
        rel = self.check([candidate("file.mp4")])
        self.assertEqual(rel["A"]["reason_code"], "media_path_invalid")
        missing = self.check([candidate(self.root/"absent.mp4")])
        self.assertEqual(missing["A"]["reason_code"], "media_file_unavailable")

    def test_empty_overlarge_and_wrong_extension(self):
        self.path.write_bytes(b"")
        self.assertEqual(self.check([candidate(self.path)])["A"]["reason_code"],
                         "media_file_size_invalid")
        self.path.write_bytes(HEADER)
        text=self.root/"fake.txt"
        text.write_bytes(HEADER)
        self.assertEqual(self.check([candidate(text)])["A"]["reason_code"],
                         "media_type_unsupported")

    def test_text_renamed_mp4_rejected_before_checksum(self):
        self.path.write_text("this is not an MP4 container")
        row = candidate(self.path, sha256=hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(self.check([row])["A"]["reason_code"], "media_container_invalid")

    def test_path_escape_and_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as foreign:
            real = Path(foreign)/"video.mp4"
            real.write_bytes(HEADER)
            outside = self.check([candidate(real)])
            self.assertEqual(outside["A"]["reason_code"], "media_path_not_allowed")
            link = self.root/"escape.mp4"
            try:
                link.symlink_to(real)
            except (OSError, NotImplementedError):
                # Windows may deny symlink creation without Developer Mode.
                # Still exercise the exact root-containment check with a
                # canonical-path escape. Mock path resolution, NOT the QDP
                # authorization decision, and never report a skipped case.
                link.write_bytes(HEADER)
                original_resolve = Path.resolve

                def resolve_escape(path, *args, **kwargs):
                    if path == link:
                        return original_resolve(real, *args, **kwargs)
                    return original_resolve(path, *args, **kwargs)

                with patch.object(Path, "resolve", resolve_escape):
                    escaped = self.check([candidate(link)])
            else:
                # Real filesystem symlink on systems that support it.
                escaped = self.check([candidate(link)])
            self.assertEqual(escaped["A"]["reason_code"],
                             "media_path_not_allowed")
            self.assertEqual(escaped["B"]["reason_code"],
                             "media_path_not_allowed")
            self.assertIsNone(escaped["asset"])

    def test_symlink_permission_denied_uses_same_root_containment_gate(self):
        # Exercise the Windows privilege-denied path on Linux/CI too.
        # The underlying test must actually verify the canonical escape;
        # it must not skip or substitute a successful authorization result.
        with patch.object(Path, "symlink_to",
                          side_effect=PermissionError("symlink privilege denied")):
            self.test_path_escape_and_symlink_fail_closed()

    def test_unconfigured_root_fails_closed(self):
        with patch.dict(os.environ, {"CM_QDP_MEDIA_ROOTS": "",
                                     "CM_GROUP_A_MEDIA_ROOTS": ""}):
            r = verify_media_candidates([candidate(self.path)], meta(), now=NOW)
        self.assertEqual(r["A"]["reason_code"], "media_roots_unconfigured")

    def test_carousel_never_becomes_video_or_slide_manifest(self):
        for kind in ("photo_carousel", "mixed_carousel"):
            with self.subTest(kind=kind):
                result = self.check([candidate(self.path)], meta(content_kind=kind))
                self.assertEqual(result["A"]["reason_code"],
                                 "video_a_carousel_unsupported")
                self.assertEqual(result["B"]["reason_code"], "media_manifest_required")
                self.assertIsNone(result["asset"])

    def test_image_post_and_unknown_kind_block(self):
        self.assertEqual(
            self.check([candidate(self.path)], meta(content_kind="image_post"))
            ["B"]["reason_code"], "group_b_content_kind_unsupported")
        self.assertEqual(
            self.check([candidate(self.path)], meta(content_kind=None))
            ["A"]["reason_code"], "content_kind_unverified")

    def test_newest_invalid_same_source_not_silently_replaced(self):
        older=candidate(self.path, id=8)
        newer=candidate(self.path, id=9, permission="metadata_only")
        r=self.check([newer, older])
        self.assertEqual(r["A"]["reason_code"], "media_permission_missing")

    def test_read_only_db_scope_and_permission_gating(self):
        class DB:
            def __init__(self):
                self.calls = []
            def execute(self, q, values):
                self.calls.append((q, values))
                class Result:
                    def fetchall(self):
                        return []
                return Result()
        db=DB()
        r=read_verified_media(db, meta(permissions={}), roots=[str(self.root)])
        self.assertFalse(db.calls)
        self.assertEqual(r["A"]["reason_code"], "media_rights_missing")
        read_verified_media(db, meta(), roots=[str(self.root)])
        self.assertEqual(db.calls[0][1], (44,"source-one"))
        self.assertIn("LIMIT 16",db.calls[0][0])

    def test_limits_and_non_network_safety(self):
        with self.assertRaises(ValueError):
            self.check([candidate(self.path)]*17)
        r=self.check([candidate("https://cdn.example/fake.mp4")])
        self.assertEqual(r["A"]["reason_code"], "media_path_invalid")


if __name__ == "__main__":
    unittest.main()
