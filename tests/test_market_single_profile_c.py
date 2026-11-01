"""Offline exact-one-TikTok-profile C1/C4 tests; NEVER call providers."""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from intelligence.market_intelligence.single_profile_c import analyze_single_profile
from intelligence.market_intelligence.tiktok_top100 import rank_profile_posts
from scripts import prepare_market_tiktok_top100 as cli

NOW=datetime(2026,11,1,12,0,tzinfo=timezone.utc)
PROFILE="@faceyogilab"
PROJECT="a"*32


def fixture():
    rows=[]
    for i in range(1,101):
        kind="video" if i<=50 else "photo"
        # Mix labels while maintaining deterministic top-100 ordering.
        native=str(7600000000000000000+i)
        rows.append({
            "url":f"https://www.tiktok.com/{PROFILE}/{kind}/{native}",
            "author_username":"faceyogilab",
            "play_count":i*1000,
            "digg_count":i*7,
            "comment_count":i*2,
            "share_count":i*3,
            "collect_count":i,
            "create_time":(NOW-timedelta(days=10+i%3)).isoformat(),
            "type":kind,
            "average_watch_time":float(i)/2,
        })
    selection=rank_profile_posts(PROFILE,rows,limit=100)
    selection["profile_snapshot_id"]="s_reused_abc456"
    selection["profile_dataset_id"]="gd_m7n5v2gq296pex2f5m"
    videos=[{**r,"video_id":k+1,"state":"analysis_partial"}
            for k,r in enumerate(selection["posts"])]
    evidence=[{"video_id":v["video_id"],"group_a":{"stages":{}},
               "group_b":{"blocks":{f"B{x}":{} for x in range(1,9)}},
               "format_family":"tutorial"} for v in videos]
    return rows,selection,videos,evidence


class SingleProfileC(unittest.TestCase):
    def test_100_real_creator_ratios_and_engagements_source_clock_unverified(self):
        rows,sel,videos,evidence=fixture()
        report=analyze_single_profile(PROFILE,sel,videos,rows,
                                     frozen_evidence=evidence,now=NOW)
        self.assertEqual(report["selected"],100)
        self.assertEqual(report["coverage"]["profile_views"],100)
        self.assertEqual(report["coverage"]["A_reports"],100)
        self.assertEqual(report["coverage"]["B_reports"],100)
        self.assertEqual(report["coverage"]["C1_diagnostic"],100)
        self.assertEqual(report["coverage"]["C4_diagnostic"],100)
        self.assertEqual(report["coverage"]["publication_age_descriptive"],100)
        self.assertEqual(report["format_comparison"]["video"]["posts"],50)
        self.assertEqual(report["format_comparison"]["photo_carousel"]["posts"],50)
        self.assertEqual(len(report["top_10_by_within_creator_ratio"]),10)
        self.assertEqual(len(report["top_10_by_raw_engagement"]),10)
        self.assertEqual(len(report["top_10_by_views"]),10)
        self.assertFalse(report["official_c0_to_c7_certified"])
        self.assertEqual(len(report["counter_evidence_sha256"]),64)
        for r in report["posts"]:
            self.assertIsNotNone(r["C1"])
            self.assertIsNotNone(r["C4"])
            self.assertEqual(r["C4"]["engagement_rate_fraction"],
                             "0.012000000000")
            self.assertEqual(r["C4"]["weighted_engagement_fraction"],
                             "0.019000000000")
            for c in ("C2","C3","C5","C6","C7"):
                self.assertIsNone(r[c])
            self.assertFalse(r["C0"]["official_eligible"])
            self.assertGreater(r["publication_age"]["freshness_as_of_report_48h_half_life"],0)
            self.assertIsNone(r["publication_age"]["strict_C6_score"])
            self.assertEqual(r["C4"]["save_rate_fraction_separate_diagnostic"],
                             "0.001")
            self.assertTrue(r["a_report_present"])
            self.assertEqual(r["provider_numeric_fields"]["average_watch_time"],
                             r["platform_native_id"] and
                             (int(r["platform_native_id"])-7600000000000000000)/2)

    def test_missing_engagement_is_not_zero_and_does_not_fabricate_C4(self):
        rows,sel,videos,evidence=fixture()
        rows[0].pop("share_count")
        # Views, rank, URL and kinds remain unchanged.
        report=analyze_single_profile(PROFILE,sel,videos,rows,
                                     frozen_evidence=evidence,now=NOW)
        self.assertEqual(report["coverage"]["C4_diagnostic"],99)
        affected=[r for r in report["posts"]
                  if r["platform_native_id"].endswith("001")]
        self.assertEqual(len(affected),1)
        self.assertIsNone(affected[0]["shares"])
        self.assertIsNone(affected[0]["C4"])

    def test_no_publication_time_does_not_invent_C1_or_C6(self):
        rows,sel,videos,evidence=fixture()
        for row in rows:row.pop("create_time")
        report=analyze_single_profile(PROFILE,sel,videos,rows,
                                     frozen_evidence=evidence,now=NOW)
        self.assertNotIn("C1_diagnostic",report["coverage"])
        self.assertNotIn("publication_age_descriptive",report["coverage"])
        self.assertEqual(report["coverage"]["C4_diagnostic"],100)
        for post in report["posts"]:
            self.assertIsNone(post["C1"])
            self.assertIsNone(post["publication_age"])
            self.assertIsNone(post["C6"])

    def test_changes_and_wrong_author_fail_closed(self):
        rows,sel,videos,evidence=fixture()
        bad=[dict(x) for x in rows]
        bad[0]["play_count"]+=10**12
        with self.assertRaisesRegex(ValueError,"snapshot_does_not_match"):
            analyze_single_profile(PROFILE,sel,videos,bad,now=NOW)
        bad=[dict(x) for x in rows]
        bad[0]["author_username"]="unrelated_account"
        with self.assertRaises(ValueError):
            analyze_single_profile(PROFILE,sel,videos,bad,now=NOW)
        bad=[dict(x) for x in rows]
        bad.append({**bad[0],"share_count":999999})
        with self.assertRaises(ValueError):
            analyze_single_profile(PROFILE,sel,videos,bad,now=NOW)

    def test_cli_joins_existing_frozen_a_b_and_fetches_only_existing_snapshot(self):
        rows,sel,videos,evidence=fixture()
        active={"project_id":PROJECT,"batch_id":2,
                "selection_sha256":"b"*64,
                "videos":videos,
                "state":{"group_c_snapshot_id":"frozen-report-id"}}
        service=type("StubService",(),{"get":lambda self,pid,sid:{
            "snapshot":{"coverage":{"matched_discovery_videos":100},
                        "video_evidence":evidence}}})()
        provider_reads=[]
        def provider_get(snapshot_id):
            provider_reads.append(snapshot_id)
            return rows
        with TemporaryDirectory() as temp,patch.object(
             cli,"existing_launched_batch",return_value=active),patch(
             "intelligence.market_intelligence.tiktok_top100_register.read_selection",
             return_value=(sel,"b"*64)),patch(
             "intelligence.market_intelligence.service.MarketIntelligenceService",
             return_value=service),patch.object(
             cli.bd,"_api",side_effect=AssertionError("POST forbidden")):
            result=cli.analyze_saved_single_profile_c(
                PROFILE,PROJECT,fetch=provider_get,output_root=Path(temp))
            doc=json.loads(Path(result["report_path"]).read_text("utf-8"))
        self.assertEqual(provider_reads,["s_reused_abc456"])
        self.assertEqual(result["counts"]["C4_diagnostic"],100)
        self.assertEqual(result["counts"]["C1_diagnostic"],100)
        self.assertEqual(result["new_provider_POST_requests"],0)
        self.assertEqual(result["new_downloads"],0)
        self.assertEqual(result["model_requests"],0)
        self.assertEqual(result["database_writes"],0)
        self.assertFalse(result["c0_c7_fully_certified"])
        self.assertEqual(doc["pinned_selection_sha256"],"b"*64)
        self.assertEqual(doc["existing_group_c_snapshot_id"],"frozen-report-id")


if __name__=="__main__":
    unittest.main()
