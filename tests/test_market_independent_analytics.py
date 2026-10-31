"""Offline MI independent analytics tests: no remote accounts, keys or spend."""
import hashlib
import re
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path

from intelligence.market_intelligence.creative import parse_srt,analyze_captions
from intelligence.market_intelligence.performance import enrich
from intelligence.market_intelligence.media_guard import verified_media
from intelligence.market_intelligence.service import SQL_VIDEO_EVIDENCE,SQL_METRIC_HISTORY
from intelligence.market_intelligence.technical import parse_cuts
from intelligence.market_intelligence.technical_contracts import Scene
from decimal import Decimal


class IndependentMarketAnalysis(unittest.TestCase):
    def test_no_cross_module_analysis_tables(self):
        for query in (SQL_VIDEO_EVIDENCE,SQL_METRIC_HISTORY):
            for table in ("content_b_analyses","content_b_formats","calculated_metrics",
                          "comparison_cohorts","video_feature_a_analyses","ga_tasks"):
                self.assertNotIn(table,query)
        self.assertIn("mi_video_analyses",SQL_VIDEO_EVIDENCE)
        self.assertIn("metric_snapshots",SQL_METRIC_HISTORY)
        self.assertIn("can_derive_scores",SQL_METRIC_HISTORY)
        self.assertIn("observation_time_assumed",SQL_METRIC_HISTORY)

    def test_creator_baseline_uses_five_age_matched_peers(self):
        ts=datetime(2026,10,30,tzinfo=timezone.utc)
        rows=[];hist={}
        for i,views in enumerate([400,100,100,100,100,100]):
            obs=(ts-timedelta(hours=48)).isoformat()
            rows.append({"video_id":i+1,"platform":"youtube","creator_id":"author1",
                 "published_at":obs,"metric_snapshot_id":None})
            hist[i+1]=[{"id":i+1,"views":views,"observed_at":ts.isoformat(),
              "source_id":"official","source_metric_definition":"yt_public_view",
              "source_metric_version":"1","likes":2,"comments":1,"shares":1}]
        scored=enrich(rows,hist)
        self.assertEqual(scored[0]["creator_baseline_post_count"],5)
        self.assertEqual(scored[0]["creator_baseline_views"],100)
        self.assertEqual(scored[0]["creator_ratio"],4.0)
        self.assertAlmostEqual(scored[0]["engagement_rate"],.01)

    def test_cross_source_history_never_makes_growth(self):
        ts=datetime(2026,10,30,tzinfo=timezone.utc)
        obs=[{"video_id":1,"platform":"tiktok","creator_id":"x",
               "published_at":(ts-timedelta(hours=48)).isoformat()}]
        previous={"id":1,"views":100,"observed_at":(ts-timedelta(hours=6)).isoformat(),
          "source_id":"provider1","source_metric_definition":"views","source_metric_version":"1"}
        newest={**previous,"id":2,"views":400,"observed_at":ts.isoformat(),"source_id":"provider2"}
        self.assertIsNone(enrich(obs,{1:[previous,newest]})[0]["views_growth_per_hour"])
        newest["source_id"]="provider1"
        self.assertEqual(enrich(obs,{1:[previous,newest]})[0]["views_growth_per_hour"],50.0)

    def test_missing_publication_date_no_invented_creator_ratio(self):
        ts=datetime(2026,10,30,tzinfo=timezone.utc).isoformat()
        obs=[{"video_id":i+1,"platform":"tiktok","creator_id":"x",
              "published_at":None} for i in range(8)]
        hist={i+1:[{"id":i+1,"views":100,"observed_at":ts,
           "source_id":"tiktok","source_metric_definition":"views",
           "source_metric_version":"1"}] for i in range(8)}
        result=enrich(obs,hist)
        self.assertTrue(all(row["creator_ratio"] is None for row in result))

    def test_timed_caption_analysis_evidence_and_no_visual_claim(self):
        captions=("1\n00:00:00,000 --> 00:00:01,200\nПочему у меня отёк?\n\n"
                  "2\n00:00:02,100 --> 00:00:03,000\nШаг 1: делаем массаж\n\n"
                  "3\n00:00:04,000 --> 00:00:05,000\nВот результат")
        rows=parse_srt(captions)
        self.assertEqual(len(rows),3)
        result=analyze_captions(rows,duration=7)
        self.assertEqual(result["format_family"],"captioned_explainer")
        self.assertIn("hook",result["story_roles"])
        self.assertIn("step",result["story_roles"])
        self.assertIn("visual_mechanics_unknown",result["limitations"])

    def test_hash_and_allowed_roots_before_local_media(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/"clip.mp4";p.write_bytes(b"mock video bytes")
            sha=hashlib.sha256(p.read_bytes()).hexdigest()
            self.assertEqual(verified_media(str(p),sha,roots=[str(root)]),p)
            with self.assertRaises(ValueError):
                verified_media(str(p),"0"*64,roots=[str(root)])
            with self.assertRaises(ValueError):
                verified_media(str(p),sha,roots=[])

    def test_no_scene_semantics_from_technical_cuts(self):
        result=parse_cuts("",Decimal("10.000"),Decimal("0.250"))
        self.assertEqual(len(result),1)
        self.assertEqual(result[0].start_sec,Decimal("0"))
        self.assertEqual(result[0].end_sec,Decimal("10.000"))


if __name__=="__main__":
    unittest.main()
