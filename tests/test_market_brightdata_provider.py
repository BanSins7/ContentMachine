"""Provider contract tests: no real Bright Data calls, tokens or billing."""
import os
import unittest
from unittest.mock import patch

from intelligence.market_intelligence import brightdata_provider as bd
from intelligence.market_intelligence import brightdata_jobs as jobs
from intelligence.market_intelligence.contracts import MarketError


class BrightDataContractTests(unittest.TestCase):
    def test_public_dataset_identifiers(self):
        self.assertEqual(bd.dataset_id("tiktok"),"gd_lu702nij2f790tmv9h")
        self.assertEqual(bd.dataset_id("instagram"),"gd_lyclm20il4r5helnj")
        self.assertEqual(bd.dataset_id("youtube"),"gd_lk56epmy2i5g7lzu0k")

    def test_youtube_comments_dataset_override_is_blocked(self):
        with patch.dict(os.environ,{
            "CM_BRIGHTDATA_YOUTUBE_VIDEOS_DATASET":"gd_lk9q0ew71spt1mxywf"
        }):
            with self.assertRaises(MarketError):
                bd.dataset_id("youtube")

    def test_youtube_video_dataset_trigger_is_expected(self):
        called=[]
        def fake(method,path,token,*,query=None,payload=None):
            called.append((method,path,query,payload))
            return {"snapshot_id":"s_youtube987654321"}
        with patch.dict(os.environ,{
            "CM_BRIGHTDATA_API_TOKEN":"test-token",
            "CM_BRIGHTDATA_METRICS_ENABLED":"1",
            "CM_BRIGHTDATA_METRICS_RIGHTS_VERIFIED":"1",
            "CM_BRIGHTDATA_YOUTUBE_VIDEOS_DATASET":""
        }):
            # Empty override isn't the default; remove it explicitly below.
            os.environ.pop("CM_BRIGHTDATA_YOUTUBE_VIDEOS_DATASET",None)
            value=bd.trigger("youtube",["https://www.youtube.com/shorts/AbCdEfGhI12"],request=fake)
        self.assertEqual(value,"s_youtube987654321")
        self.assertEqual(called[0][2]["dataset_id"],"gd_lk56epmy2i5g7lzu0k")
        self.assertEqual(called[0][3],[{"url":"https://www.youtube.com/watch?v=AbCdEfGhI12"}])

    def test_token_only_server_environment_never_returned(self):
        fake="dev-token-for-testing"
        with patch.dict(os.environ,{"CM_BRIGHTDATA_API_TOKEN":fake,
                  "CM_BRIGHTDATA_METRICS_ENABLED":"1",
                  "CM_BRIGHTDATA_METRICS_RIGHTS_VERIFIED":"1"}):
            data=bd.readiness()
            self.assertTrue(data["ready"])
            self.assertNotIn(fake,str(data))

    def test_no_credentials_means_no_dispatch(self):
        with patch.dict(os.environ,{"CM_BRIGHTDATA_API_TOKEN":"",
                "CM_BRIGHTDATA_METRICS_ENABLED":"1",
                "CM_BRIGHTDATA_METRICS_RIGHTS_VERIFIED":"1"}):
            with self.assertRaises(MarketError):
                bd.trigger("tiktok",["https://www.tiktok.com/@creator/video/1234567890123456789"],
                           request=lambda *a,**k: self.fail("network called"))

    def test_trigger_uses_static_dataset_and_validated_urls(self):
        captured=[]
        def call(method,path,token,*,query=None,payload=None):
            captured.append((method,path,query,payload,token))
            return {"snapshot_id":"s_abcd123456789"}
        with patch.dict(os.environ,{"CM_BRIGHTDATA_API_TOKEN":"dummy",
            "CM_BRIGHTDATA_METRICS_ENABLED":"1",
            "CM_BRIGHTDATA_METRICS_RIGHTS_VERIFIED":"1"}):
            sid=bd.trigger("tiktok",["https://www.tiktok.com/@creator/video/1234567890123456789"],request=call)
        self.assertEqual(sid,"s_abcd123456789")
        self.assertEqual(captured[0][0:2],("POST","/datasets/v3/trigger"))
        self.assertEqual(captured[0][2]["dataset_id"],bd.DATASETS["tiktok"])
        self.assertEqual(len(captured[0][3]),1)

    def test_trigger_wrong_platform_rejected_before_network(self):
        with patch.dict(os.environ,{"CM_BRIGHTDATA_API_TOKEN":"dummy",
              "CM_BRIGHTDATA_METRICS_ENABLED":"1",
              "CM_BRIGHTDATA_METRICS_RIGHTS_VERIFIED":"1"}):
            with self.assertRaises(MarketError):
                bd.trigger("tiktok",["https://www.youtube.com/shorts/AbCdEfGhI12"],
                           request=lambda *a,**k:self.fail("network"))
        self.assertRaises(MarketError,bd.progress,"../passwd")

    def test_tiktok_fields_and_missing_not_zero(self):
        row=bd.normalize_record("tiktok",{
            "url":"https://www.tiktok.com/@creator/video/1234567890123456789",
            "play_count":34000,"digg_count":600,"comment_count":10,
            "share_count":21,"collect_count":12,
            "create_time":"2026-10-25T10:00:00.000Z"})
        self.assertEqual(row["views"],34000)
        self.assertEqual(row["likes"],600)
        self.assertEqual(row["saves"],12)
        self.assertTrue(row["published_at"].startswith("2026-10-25"))
        row2=bd.normalize_record("tiktok",{
            "url":"https://www.tiktok.com/@creator/video/1234567890123456789",
            "digg_count":0})
        self.assertIsNone(row2["views"])
        self.assertEqual(row2["likes"],0)

    def test_reels_fields_and_mismatched_platform_blocked(self):
        url="https://www.instagram.com/reel/AbCdEfGhIjK/"
        row=bd.normalize_record("instagram",{"url":url,"views":900,"likes":15,
                   "num_comments":2,"date_posted":"2026-10-21T12:00:00.000Z"})
        self.assertEqual(row["comments"],2)
        self.assertEqual(row["views"],900)
        self.assertIsNone(row["shares"])
        self.assertIsNone(bd.normalize_record("tiktok",{"url":url,"views":900}))
        self.assertIsNone(bd.normalize_record("instagram",{"url":url,"views":"2.5K"}))

    def test_provider_keeps_numeric_fields_without_raw_comments(self):
        row=bd.normalize_record("tiktok",{
            "url":"https://www.tiktok.com/@creator/video/1234567890123456789",
            "play_count":345, "statistics":{
                "average_watch_time":12.25, "retention_percent":68.0
            },
            "comment_text":"Private comment must never be retained",
            "comments":[{"text":"not stored", "likes":100}],
        })
        self.assertEqual(row["numeric_fields"]["statistics.average_watch_time"],12.25)
        self.assertEqual(row["numeric_fields"]["statistics.retention_percent"],68.0)
        self.assertNotIn("comments",str(row))
        self.assertNotIn("Private comment",str(row))

    def test_provider_does_not_fabricate_watch_time(self):
        row=bd.normalize_record("youtube",{
            "url":"https://www.youtube.com/shorts/AbCdEfGhI12",
            "views":123,
        })
        self.assertNotIn("retention",str(row["numeric_fields"]))
        self.assertNotIn("watch_time",str(row["numeric_fields"]))

    def test_no_writes_to_group_a_b_c(self):
        for name in ("group_a","group_b","group_c","calculated_metrics","content_b_"):
            self.assertNotIn(name,jobs.SQL_TARGETS)
            self.assertNotIn(name,jobs._ingest.__code__.co_names)

    def test_author_age_refresh_intervals(self):
        from datetime import datetime,timedelta,timezone
        now=datetime.now(timezone.utc)
        self.assertEqual(jobs._interval(now-timedelta(hours=20),now),6*3600)
        self.assertEqual(jobs._interval(now-timedelta(days=5),now),86400)
        self.assertEqual(jobs._interval(now-timedelta(days=25),now),3*86400)


if __name__=="__main__":
    unittest.main()
