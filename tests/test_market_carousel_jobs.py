"""Real Marketing photo-carousel SQL queue and immutable A/B persistence contracts.

Only genuine JPEG bytes; provider and DB are replaced with deterministic fakes.
No gallery-dl network, no model charges, no fake video durations.
"""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from PIL import Image

from intelligence.market_intelligence.carousel_media import scan_images
from intelligence.market_intelligence import carousel_jobs
from intelligence.market_intelligence.contracts import MarketError

PROJECT="a"*32
SOURCE="11111111-1111-4111-8111-111111111111"
VERSION="22222222-2222-4222-8222-222222222222"
URL="https://www.tiktok.com/@faceyogi/photo/7701927285129055520"
NATIVE="7701927285129055520"
VIDEO={"video_id":71,"url":URL,"platform":"tiktok","native_id":NATIVE}


class R:
    def __init__(self,one=None):
        self.one=one
    def fetchone(self):return self.one


class DB:
    def __init__(self,allow=True):
        self.allow=allow
        self.writes=[]
        self.inserted=None
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def transaction(self):return nullcontext()
    def execute(self,sql,args):
        if "FROM project_sources ps" in sql:
            return R((SOURCE,VERSION,True,True,True,False)
                     if self.allow else (SOURCE,VERSION,False,True,True,False))
        if "SELECT v.id FROM videos v" in sql:
            return R((71,))
        if "INSERT INTO jobs(" in sql:
            self.writes.append("job")
            self.inserted=json.loads(args[2])
            return R((9,))
        if "SELECT state,lease_generation FROM jobs" in sql:
            return R(("running",1))
        if "INSERT INTO mi_carousel_analyses(" in sql:
            self.writes.append("carousel_evidence")
            self.inserted=json.loads(args[12])
            return R((44,))
        if "UPDATE jobs SET state='completed'" in sql:
            self.writes.append("completed")
            return R(None)
        raise AssertionError("unexpected SQL: "+sql[:90])


def write_slides(folder):
    folder.mkdir(parents=True,exist_ok=True)
    for i in range(1,4):
        Image.new("RGB",(64,64),(i*60,i*60,i*60)).save(
            folder/(f"{i:03d}.jpg"),format="JPEG")


class CarouselJobs(unittest.TestCase):
    def test_carousel_rights_source_types_match_marketing_batch(self):
        # A rights-approved research_link/other source must not pass the batch
        # and then be rejected by an inconsistent carousel-only SQL filter.
        class CapturingDB(DB):
            def execute(self,sql,args):
                if "FROM project_sources ps" in sql:
                    self.source_sql=sql
                    return R((SOURCE,VERSION,True,True,True,False))
                return super().execute(sql,args)
        db=CapturingDB()
        with patch("intelligence.market_intelligence.carousel_jobs.d1.connect",
                   return_value=db):
            result=carousel_jobs.queue_carousel(PROJECT,VIDEO,start=False)
        self.assertTrue(result["queued"])
        for source_type in ("user_video_reference","research_link","other"):
            self.assertIn(source_type,db.source_sql)
        self.assertIn("ps.link_kind='video'",db.source_sql)
        self.assertIn("ps.tombstoned_at IS NULL",db.source_sql)

    def test_no_implicit_rights_on_account_discovered_post(self):
        db=DB(allow=False)
        with patch("intelligence.market_intelligence.carousel_jobs.d1.connect",
                   return_value=db):
            with self.assertRaises(MarketError) as ex:
                carousel_jobs.queue_carousel(PROJECT,VIDEO,start=False)
        self.assertEqual(ex.exception.code,"carousel_source_rights_required")
        self.assertNotIn("job",db.writes)

    def test_authorized_post_enqueues_only_exact_id_not_paid_metrics(self):
        db=DB()
        with patch("intelligence.market_intelligence.carousel_jobs.d1.connect",
                   return_value=db),patch(
             "intelligence.market_intelligence.carousel_jobs.start_worker") as start:
            result=carousel_jobs.queue_carousel(PROJECT,VIDEO,start=True)
        self.assertTrue(result["queued"])
        start.assert_called_once()
        self.assertEqual(db.inserted["url"],URL)
        self.assertEqual(db.inserted["video_id"],71)
        self.assertEqual(db.inserted["source_id"],SOURCE)
        self.assertEqual(db.inserted["native_id"],NATIVE)

    def test_real_image_a_b_evidence_persists_with_verified_rights(self):
        with TemporaryDirectory() as d:
            db=DB()
            root=Path(d)
            def download(url,project_root):
                self.assertEqual(url,URL)
                folder=project_root/("carousel-tiktok-"+NATIVE)
                write_slides(folder)
                return scan_images(folder,expected_url=url)
            item=(9,{
                "project_id":PROJECT,"video_id":71,"url":URL,
                "platform":"tiktok","native_id":NATIVE,
                "source_id":SOURCE,"source_version_id":VERSION},1)
            with patch("intelligence.market_intelligence.carousel_jobs.d1.connect",
                       return_value=db),patch(
                 "intelligence.market_intelligence.carousel_jobs._root",
                 return_value=root),patch(
                 "intelligence.market_intelligence.carousel_media._ocr_backend",
                 return_value=None),patch(
                 "intelligence.market_intelligence.carousel_vision.classify_carousel",
                 side_effect=AssertionError("no provider permission")):
                report=carousel_jobs.process(item,downloader=download)
            self.assertEqual(report["slide_count"],3)
            self.assertEqual(db.writes,["carousel_evidence","completed"])
            manifest=db.inserted
            self.assertEqual(manifest["group_a"]["slide_count"],3)
            self.assertEqual(manifest["group_a"]["slide_observations"][0]["slide_index"],1)
            self.assertEqual(manifest["group_a"]["audio_asr"],"unavailable_no_verified_carousel_soundtrack")


if __name__=="__main__":
    unittest.main()
