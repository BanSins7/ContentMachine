"""No-network Group A/B/C qualified analytics unit regression."""
from __future__ import annotations
import copy
import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from intelligence.analytics.contracts import (
    AnalyticsError, canonical_hash, evaluate_qualification, validate_request,
)
from intelligence.analytics.metrics import MetricError, compute_c_metrics

PID="a"*32
T=datetime(2026,10,20,12,0,tzinfo=timezone.utc)
WEIGHTS={"creator_ratio":0.45,"views_growth_per_hour":0.30,
         "weighted_engagement_smoothed":0.15,"freshness_score":0.10}

def metric_fixture():
    current={"id":11,"video_id":7,"source_id":"legal_source","views":200,
             "likes":20,"comments":4,"shares":2,
             "observed_at":T,"data_quality_status":"ok",
             "source_metric_definition":"video_views/v1"}
    previous={**current,"id":10,"views":100,"observed_at":T-timedelta(hours=4)}
    born={"published_at":T-timedelta(hours=12)}
    config={"alpha":5,"bayesian_k":50,"normalization_enabled":False,
            "freshness_half_life_h":Decimal(48),"weights_json":WEIGHTS}
    creator={"purpose":"creator","is_frozen":True,"post_count":12,"median_views":100}
    prior={"purpose":"engagement_prior","is_frozen":True,"post_count":25,
           "prior_like_rate":Decimal("0.1"),"prior_comment_rate":Decimal("0.02"),
           "prior_share_rate":Decimal("0.01")}
    niche={"purpose":"niche","is_frozen":True,"post_count":15,"median_views":250}
    return current,previous,born,config,creator,prior,niche

def evidence():
    return {
        "video_id":7,"content_kind":"video","project_link":True,
        "source":{"id":"legal_source","status":"enabled",
                  "can_store_metadata":True,"can_analyze_media":True,
                  "can_store_metrics":True,"can_derive_scores":True},
        "policy":{"source_id":"legal_source","is_active":True,"permissions":{
                  "can_store_metadata":True,"can_analyze_media":True,
                  "can_store_metrics":True,"can_derive_scores":True}},
        "a":{"video_id":7,"source_id":"legal_source","analysis_id":3,
             "state":"completed","duration_sec":Decimal("24"),
             "scene_status":"complete","speech_status":"complete",
             "screen_text_status":"complete","has_subtitles":"false"},
        "b":{"id":4,"video_id":7,"source_id":"legal_source",
             "video_a_analysis_id":3,"content_type":"video",
             "state":"completed","needs_review":False},
        "features_b":{
            "hook":{"classification_status":"inferred"},
            "format":{"classification_status":"inferred"},
            "story":{"frames":[{"needs_review":False}],"intervals":[]},
            "visual":{"ai_generated":None,"person_visible":True},
            "editing":{"cut_count":0},"subtitles":{"subtitle_type":"none"},
            "cta_summary":{"analysis_status":"complete","has_cta":False},
            "topic":{"topic_id":14,"needs_review":False}},
        "c":{"post_id":7,"snapshot_id":11,"score_status":"complete",
             "breakout_score":Decimal("13.250")},
        "metric":{"id":11,"video_id":7,"source_id":"legal_source",
                  "valid_retention":True,"data_quality_status":"ok"},
        "relevance":{"match":True,"label_id":2},
    }

class AnalyticsPureTests(unittest.TestCase):
    def test_exact_c1_to_c7_weighted_raw_no_normalization(self):
        args=metric_fixture()
        got=compute_c_metrics(*args)
        self.assertEqual(got["creator_ratio"],Decimal("1.952380952381"))
        self.assertEqual(got["views_growth_per_hour"],Decimal("25.000000000000"))
        self.assertEqual(got["niche_outlier"],Decimal("0.803921568627"))
        self.assertEqual(got["weighted_engagement_raw"],Decimal("0.160000000000"))
        self.assertEqual(got["weighted_engagement_smoothed"],Decimal("0.160000000000"))
        self.assertTrue(80 < got["freshness_score"] < 90)
        self.assertEqual(got["breakout_score"],sum(got[key] for key in (
            "score_creator_component","score_growth_component",
            "score_engagement_component","score_freshness_component")))
        self.assertEqual(got["score_status"],"complete")

    def test_correct_missing_unknown_and_mismatched_source_block(self):
        orig=metric_fixture()
        checks=[
            (0,{"likes":None},"missing_likes"),
            (0,{"source_metric_definition":"reach"},"incompatible_definition"),
            (0,{"views":1},"counter_correction"),
            (0,{"views":0},"counter_correction"),
            (0,{"observation_time_assumed":True},"assumed_observation_time"),
            (1,{"source_metric_version":"2"},"incompatible_metric_version"),
            (1,{"observed_at":T},"invalid_timestamp"),
            (3,{"alpha":None},"missing_alpha"),
            (3,{"normalization_enabled":True},"normalization_not_allowed"),
            (4,{"is_frozen":False},"creator_cohort_not_approved"),
            (5,{"prior_share_rate":None},"missing_prior_share_rate"),
        ]
        for idx,mod,err in checks:
            inputs=copy.deepcopy(orig)
            inputs[idx].update(mod)
            with self.subTest(idx=idx,mod=mod),self.assertRaises(MetricError) as ctx:
                compute_c_metrics(*inputs)
            self.assertEqual(ctx.exception.code,err)

    def test_complete_qualification_and_fail_closed(self):
        e=evidence()
        result=evaluate_qualification(e)
        self.assertEqual(result["status"],"qualified")
        self.assertEqual(result["blockers"],[])
        for field,mod,expected in (
            ("source",{"can_derive_scores":False},"blocked"),
            ("policy",{"is_active":False},"blocked"),
            ("c",{"score_status":"insufficient_data","breakout_score":None},"partial"),
            ("a",{"scene_status":"partial"},"partial"),
            ("a",{"scene_count":0},"partial"),
            ("b",{"video_a_analysis_id":12},"partial"),
            ("b",{"taxonomy_version":"different"},"partial"),
            ("relevance",{"match":False},"partial"),
        ):
            x=copy.deepcopy(e)
            x[field].update(mod)
            with self.subTest(field=field):
                self.assertEqual(evaluate_qualification(x)["status"],expected)
        x=copy.deepcopy(e)
        x["features_b"]["cta_summary"]["analysis_status"]="not_run"
        self.assertIn("b7_cta_unresolved",evaluate_qualification(x)["blockers"])

    def test_canonical_digest_request_validation(self):
        e=evidence()
        self.assertEqual(canonical_hash(e),canonical_hash(copy.deepcopy(e)))
        self.assertEqual(len(canonical_hash(e)),64)
        validate_request(PID,7,"qualified:video7")
        for pid,vid,key in ((PID,True,"qualified:video7"),
                            (PID,7,"x"),("INVALID",7,"qualified:video7")):
            with self.assertRaises(AnalyticsError):
                validate_request(pid,vid,key)

class AnalyticsSchemaTests(unittest.TestCase):
    def test_additive_schema_and_source_first_view(self):
        path=Path(__file__).resolve().parents[1]/"intelligence/migrations/016_analytics_unified.sql"
        s=path.read_text(encoding="utf-8").lower()
        self.assertNotIn("drop table",s)
        self.assertNotIn("truncate",s)
        self.assertIn("analytics_visible_snapshots",s)
        self.assertIn("can_derive_scores",s)
        self.assertIn("immutable",s)

@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"Disposable PostgreSQL required")
class AnalyticsPostgresTests(unittest.TestCase):
    def test_migration_reentrant_and_view_installed(self):
        import psycopg
        from intelligence import migrate
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True) as conn:
            migrate.apply_migrations(conn)
            self.assertEqual(migrate.apply_migrations(conn),[])
            self.assertIsNotNone(conn.execute(
                "SELECT to_regclass('analytics_visible_snapshots')").fetchone()[0])
            self.assertIsNotNone(conn.execute(
                "SELECT to_regclass('analytics_unified_snapshots')").fetchone()[0])

if __name__=="__main__":
    unittest.main()
