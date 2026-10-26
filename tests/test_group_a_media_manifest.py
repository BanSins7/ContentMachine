"""GA-MOD-05: local byte verification, path hardening and full A media kinds."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from intelligence.group_a.media_manifest import (
    MediaVerificationError, build_manifest, open_verified, verify_file,
)
from intelligence.group_a.contracts_v1 import media_manifest_v1, ensure_same_lineage


MP4=b"\x00\x00\x00\x18ftypisom"+b"\x00"*64
PNG=b"\x89PNG\r\n\x1a\n"+b"\x00"*64
JPG=b"\xff\xd8\xff\xe0"+b"\x00"*64
WEBM=b"\x1a\x45\xdf\xa3"+b"\x81"*64


def ptr():
    return {
        "schema_version":"qdp-handoff/v1","event_id":str(uuid4()),
        "project_id":"a"*32,"qdp_run_id":str(uuid4()),
        "video_id":33,"analysis_group":"A","input_sha256":"b"*64,
    }


class GA05Manifest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/"authorized"
        self.root.mkdir()
        self.now=datetime(2026,10,26,9,45,0,tzinfo=timezone.utc)
        self.p=ptr()

    def asset(self,index,kind="video",body=None,ext=None,**patch):
        if body is None:
            body=PNG if kind=="image" else MP4
        suffix=ext or (".png" if kind=="image" else ".mp4")
        file=self.root/f"asset-{index}{suffix}"
        file.write_bytes(body)
        row={
            "media_asset_id":100+index,"video_id":self.p["video_id"],
            "source_id":"source:fixture","media_kind":kind,
            "local_path":str(file),"sha256":hashlib.sha256(body).hexdigest(),
            "byte_size":len(body),
        }
        row.update(patch)
        return row

    def manifest(self,kind,assets,**kwargs):
        return build_manifest(
            self.p,source_id="source:fixture",
            source_policy_version_id=10,
            content_kind=kind,ordered_assets=assets,
            roots=[str(self.root)],verified_at=self.now,**kwargs)

    def reject(self,code,call):
        with self.assertRaises(MediaVerificationError) as ctx:
            call()
        self.assertEqual(ctx.exception.code,code)

    def test_single_video_and_verified_fd_reopen(self):
        item=self.asset(1)
        manifest,refs=self.manifest("video",[item])
        self.assertEqual(media_manifest_v1(manifest),manifest)
        self.assertTrue(ensure_same_lineage(self.p,manifest))
        self.assertEqual(manifest["items"][0]["byte_size"],len(MP4))
        ref=manifest["items"][0]["verified_local_path_ref"]
        self.assertEqual(set(refs),{ref})
        self.assertNotIn(str(self.root),str(manifest))
        self.assertEqual(ref,"ga-local:a101-"+item["sha256"][:16])
        with open_verified(refs[ref],roots=[str(self.root)]) as fd:
            self.assertEqual(fd.read(),MP4)
        next_manifest,_=self.manifest("video",[item])
        self.assertEqual(manifest["manifest_sha256"],next_manifest["manifest_sha256"])

    def test_single_image_and_carousels_preserve_exact_slide_order(self):
        first=self.asset(1,"image")
        second=self.asset(2,"image",body=JPG,ext=".jpg")
        img,refs=self.manifest("image_post",[first])
        self.assertEqual(img["content_kind"],"image_post")
        self.assertNotIn("duration_sec",img["items"][0])
        photos,_=self.manifest("photo_carousel",[second,first])
        self.assertEqual([i["media_asset_id"] for i in photos["items"]],[102,101])
        self.assertEqual([i["index"] for i in photos["items"]],[1,2])
        clip=self.asset(3,"video",body=WEBM,ext=".webm",
                        duration_sec="3.500")
        mixed,_=self.manifest("mixed_carousel",[first,clip,second])
        self.assertEqual([i["kind"] for i in mixed["items"]],
                         ["image","video","image"])
        self.assertEqual(mixed["items"][1]["duration_sec"],"3.500")
        self.assertNotIn("duration_sec",mixed["items"][0])
        self.assertEqual(len(refs),1)

    def test_duplicate_slide_foreign_identity_and_wrong_kind(self):
        image=self.asset(1,"image")
        self.reject("duplicate_media_asset",
                    lambda:self.manifest("photo_carousel",[image,image]))
        foreign=dict(image,video_id=999)
        self.reject("foreign_video_asset",
                    lambda:self.manifest("image_post",[foreign]))
        external=dict(image,source_id="other")
        self.reject("foreign_source_asset",
                    lambda:self.manifest("image_post",[external]))
        self.reject("invalid_asset_id",
                    lambda:self.manifest("image_post",[dict(image,media_asset_id=True)]))
        self.reject("invalid_source_policy",
                    lambda:build_manifest(self.p,source_id="source:fixture",
                        source_policy_version_id=0,content_kind="image_post",
                        ordered_assets=[image],roots=[str(self.root)]))
        with self.assertRaises(Exception):
            self.manifest("video",[image])  # v1 video requires video media

    def test_mismatched_sha_bytes_and_deleted_media(self):
        asset=self.asset(1)
        self.reject("sha256_mismatch",lambda:self.manifest(
            "video",[dict(asset,sha256="a"*64)]))
        self.reject("size_mismatch",lambda:self.manifest(
            "video",[dict(asset,byte_size=2)]))
        file=Path(asset["local_path"])
        file.unlink()
        self.reject("media_missing",lambda:self.manifest("video",[asset]))

    def test_replaced_file_fails_when_processing_rechecks(self):
        asset=self.asset(1)
        manifest,refs=self.manifest("video",[asset])
        ref=manifest["items"][0]["verified_local_path_ref"]
        original=Path(asset["local_path"])
        swap=self.root/"swap.mp4"
        swap.write_bytes(MP4)
        os.replace(swap,original)
        self.reject("media_changed_since_verification",
                    lambda:next(open_verified(refs[ref],roots=[str(self.root)]).__enter__() for _ in [0]))
        # A new manifest may be verified, but the old descriptor proof is invalid.
        fresh,_=self.manifest("video",[asset])
        self.assertEqual(fresh["manifest_sha256"],manifest["manifest_sha256"])

    def test_changed_bytes_same_path_and_digest_recheck(self):
        a=self.asset(1)
        m,refs=self.manifest("video",[a])
        ref=m["items"][0]["verified_local_path_ref"]
        p=Path(a["local_path"])
        p.write_bytes(MP4+b"garbage")
        self.reject("size_mismatch",lambda:self.manifest("video",[a]))
        with self.assertRaises(MediaVerificationError):
            with open_verified(refs[ref],roots=[str(self.root)]):
                pass

    def test_nonmatching_extension_magic_and_invalid_content(self):
        wrong=self.asset(1,kind="video",body=PNG,ext=".mp4")
        self.reject("magic_kind_mismatch",lambda:self.manifest("video",[wrong]))
        txt=self.asset(2,kind="image",body=PNG,ext=".txt")
        self.reject("extension_kind_mismatch",lambda:self.manifest("image_post",[txt]))
        mp4asimage=self.asset(3,kind="image",body=MP4,ext=".png")
        self.reject("magic_kind_mismatch",
                    lambda:self.manifest("image_post",[mp4asimage]))
        empty=self.asset(4,body=b"")
        self.reject("media_size_limit",lambda:self.manifest("video",[empty]))

    def test_path_allowlist_symlinks_and_traversal(self):
        good=self.asset(1)
        unapproved=Path(self.temp.name)/"elsewhere.mp4"
        unapproved.write_bytes(MP4)
        self.reject("media_path_not_allowed",lambda:self.manifest(
            "video",[dict(good,local_path=str(unapproved))]))
        self.reject("invalid_path",lambda:self.manifest(
            "video",[dict(good,local_path="../asset.mp4")]))
        traversal=str(self.root)+"/../elsewhere.mp4"
        self.reject("invalid_path",lambda:self.manifest(
            "video",[dict(good,local_path=traversal)]))
        link=self.root/"link.mp4"
        try:
            link.symlink_to(Path(good["local_path"]))
        except (NotImplementedError, OSError):
            pass
        else:
            self.reject("symlink_not_allowed",lambda:self.manifest(
                "video",[dict(good,local_path=str(link))]))
        nested=self.root/"nested"
        nested.mkdir()
        deep=nested/"deep.mp4"
        deep.write_bytes(MP4)
        ld=self.root/"alias"
        try:
            ld.symlink_to(nested,target_is_directory=True)
        except (NotImplementedError,OSError):
            pass
        else:
            self.reject("symlink_not_allowed",lambda:self.manifest(
                "video",[dict(good,local_path=str(ld/"deep.mp4"))]))
        self.reject("roots_unconfigured",
                    lambda:verify_file(good["local_path"],media_kind="video",
                        sha256=good["sha256"],roots=[]))

    @unittest.skipUnless(hasattr(os,"mkfifo"),"POSIX FIFO not available")
    def test_fifo_rejected_before_open_without_blocking(self):
        pipe=self.root/"pipe.mp4"
        os.mkfifo(pipe)
        asset=dict(self.asset(1),local_path=str(pipe))
        # A FIFO is untrusted; avoid opening it in blocking mode.
        self.reject("not_regular_file",lambda:self.manifest("video",[asset]))


if __name__ == "__main__":
    unittest.main()
