"""GA-MOD-05 D1 read-only catalog adapter: ordered media, rights, revalidation."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import tempfile
from pathlib import Path
import unittest
from uuid import uuid4

from intelligence.group_a.adapters.media_assets import VerifiedMediaCatalog
from intelligence.group_a.media_manifest import MediaVerificationError
from intelligence.group_a.contracts_v1 import ensure_same_lineage

MP4=b"\x00\x00\x00\x18ftypmp42"+b"\x00"*64
PNG=b"\x89PNG\r\n\x1a\n"+b"\x00"*64


def pointer():
    return dict(schema_version="qdp-handoff/v1", event_id=str(uuid4()),
                project_id="a"*32,qdp_run_id=str(uuid4()),
                video_id=41,analysis_group="A",input_sha256="b"*64)


class Cursor:
    def __init__(self,one):
        self.one=one
    def fetchone(self):
        return self.one


class StubD1:
    def __init__(self,p,assets,kind="video"):
        self.p=p
        self.assets={r["media_asset_id"]:r for r in assets}
        self.kind=kind
        self.source="source:fixture"
        self.policy=29
        self.selected=assets[0]["media_asset_id"]
        self.source_status="enabled"
        self.permission="analysis_allowed"
        self.unexpired=True
        self.prep_state="ready"
        self.calls=[]

    def execute(self,sql,args):
        self.calls.append((sql,args))
        if "SELECT to_regclass" in sql:
            return Cursor((args[0],))
        if "FROM qdp_preparations p" in sql:
            return Cursor((self.kind,self.source,self.policy,self.selected,None,
                           self.source_status,True,True,
                           {"can_analyze_media":True},self.prep_state,
                           self.p["input_sha256"],"partial",self.unexpired))
        if "FROM media_assets WHERE id" in sql:
            a=self.assets.get(args[0])
            return Cursor(None if a is None else (
                a["media_asset_id"],a["video_id"],a["source_id"],
                a["media_kind"],a["local_path"],a["sha256"],
                self.permission,self.unexpired))
        raise AssertionError("Unknown SQL: "+sql[:80])


class GA05MediaCatalog(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.p=pointer()
        self.clock=lambda:datetime(2026,10,26,11,30,tzinfo=timezone.utc)

    def row(self,id,kind="video",data=None,video=None):
        bytesdata = (MP4 if kind=="video" else PNG) if data is None else data
        path=self.root/(f"{id}."+("mp4" if kind=="video" else "png"))
        path.write_bytes(bytesdata)
        return dict(media_asset_id=id,video_id=self.p["video_id"] if video is None else video,
                    source_id="source:fixture",media_kind=kind,
                    local_path=str(path),sha256=hashlib.sha256(bytesdata).hexdigest())

    def catalog(self,db,selector=None,version="ga-media-manifest/v1"):
        return VerifiedMediaCatalog(db,roots=[str(self.root)],
            observed_version=version,ordered_selector=selector,clock=self.clock)

    def error(self,expected,action):
        with self.assertRaises(MediaVerificationError) as ctx:
            action()
        self.assertEqual(ctx.exception.code,expected)

    def test_real_bytes_verified_and_reopened_via_opaque_ref(self):
        a=self.row(5)
        db=StubD1(self.p,[a])
        cat=self.catalog(db)
        self.assertEqual(cat.capability().state,"available")
        m=cat.read_verified_manifest(self.p)
        self.assertEqual(m["items"][0]["sha256"],a["sha256"])
        self.assertEqual(m["content_kind"],"video")
        self.assertTrue(ensure_same_lineage(self.p,m))
        ref=m["items"][0]["verified_local_path_ref"]
        self.assertNotIn(str(self.root),str(m))
        with cat.open_verified_ref(ref) as f:
            self.assertEqual(f.read(),MP4)
        self.assertEqual(cat.read_verified_manifest(self.p)["manifest_sha256"],
                         m["manifest_sha256"])

    def test_carousel_requires_authoritative_order_and_selection(self):
        a=self.row(8,"image")
        b=self.row(9,"image")
        db=StubD1(self.p,[a,b],kind="photo_carousel")
        self.error("carousel_order_unavailable",
                   lambda:self.catalog(db).read_verified_manifest(self.p))
        self.error("invalid_ordered_carousel",
                   lambda:self.catalog(db,selector=lambda *a:[9,9]).read_verified_manifest(self.p))
        # Selected media ID 8 remains pinned even if ordered at position 2.
        cat=self.catalog(db,selector=lambda *args:[9,8])
        m=cat.read_verified_manifest(self.p)
        self.assertEqual([x["media_asset_id"] for x in m["items"]],[9,8])
        self.assertEqual([x["index"] for x in m["items"]],[1,2])
        self.assertEqual([x["kind"] for x in m["items"]],["image","image"])
        self.error("invalid_ordered_carousel",lambda:self.catalog(
            db,selector=lambda *args:[9,7]).read_verified_manifest(self.p))

    def test_mixed_order_preserved_without_fake_durations(self):
        first=self.row(1,"image")
        video=self.row(2,"video")
        db=StubD1(self.p,[first,video],kind="mixed_carousel")
        m=self.catalog(db,selector=lambda *args:[1,2]).read_verified_manifest(self.p)
        self.assertEqual([i["kind"] for i in m["items"]],["image","video"])
        self.assertTrue(all("duration_sec" not in i for i in m["items"]))

    def test_bad_source_foreign_video_and_bad_bytes_rejected(self):
        a=self.row(1)
        db=StubD1(self.p,[a])
        db.assets[1]=dict(a,video_id=99)
        self.error("foreign_media_asset",
                   lambda:self.catalog(db).read_verified_manifest(self.p))
        db.assets[1]=dict(a,video_id=self.p["video_id"],sha256="c"*64)
        self.error("sha256_mismatch",
                   lambda:self.catalog(db).read_verified_manifest(self.p))
        db.assets[1]=a
        db.source_status="disabled"
        self.error("media_rights_or_preparation_revoked",
                   lambda:self.catalog(db).read_verified_manifest(self.p))
        db.source_status="enabled"
        db.unexpired=False
        self.error("media_rights_or_preparation_revoked",
                   lambda:self.catalog(db).read_verified_manifest(self.p))

    def test_revocation_invalidates_opaque_handles(self):
        a=self.row(1)
        db=StubD1(self.p,[a])
        cat=self.catalog(db)
        manifest=cat.read_verified_manifest(self.p)
        ref=manifest["items"][0]["verified_local_path_ref"]
        db.source_status="disabled"
        self.error("media_rights_or_preparation_revoked",
                   lambda:cat.read_verified_manifest(self.p))
        self.error("unknown_verified_ref",
                   lambda:cat.open_verified_ref(ref).__enter__())

    def test_incompatible_unnegotiated_or_bad_root_cannot_claim_capability(self):
        a=self.row(1)
        db=StubD1(self.p,[a])
        self.assertEqual(self.catalog(db,version=None).capability().state,"unavailable")
        self.assertEqual(self.catalog(db,version="ga-media-manifest/v9").capability().state,
                         "incompatible")
        bad=VerifiedMediaCatalog(db,roots=[],observed_version="ga-media-manifest/v1")
        self.assertEqual(bad.capability().state,"unavailable")
        self.error("media_catalog_unavailable",lambda:bad.read_verified_manifest(self.p))


if __name__=="__main__":
    unittest.main()
