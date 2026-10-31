"""Market-native Group C C0–C7 reproducibility and source-quality regressions."""
from __future__ import annotations
import copy
import unittest
from datetime import datetime,timedelta,timezone
from decimal import Decimal

from intelligence.market_intelligence.extended_c import enrich_exact_c
from intelligence.market_intelligence.group_c_quality import validate_history
from intelligence.market_intelligence.group_c_cohorts import (
    freeze_diagnostic_peers,compute_c2_niche_diagnostic)
from intelligence.market_intelligence.group_c_report import (
    build_report,verify_report)
from intelligence.market_intelligence._copied.group_c.creator_ratio import compute_creator_ratio
from intelligence.market_intelligence._copied.group_c.views_growth import compute_views_growth_per_hour

TZ=timezone.utc
PUBLISHED=datetime(2026,9,1,tzinfo=TZ)
OBSERVED=PUBLISHED+timedelta(hours=24)
SOURCE="observed-source"
Q=Decimal("0.000000000001")


def _data():
    rows=[]
    histories={}
    for i in range(1,22):
        author="a" if i<=7 else "b" if i<=14 else "c"
        views=1000+i*11
        prev=views-100
        earlier=OBSERVED-timedelta(hours=1)
        rows.append({
            "video_id":i,"platform":"tiktok",
            "creator_id":author,
            "published_at":PUBLISHED.isoformat(),
            "publication_age_hours":24,
            "metric_snapshot_id":i*10+1,
            "metric_source_id":SOURCE,
            "metric_definition":"platform_post_views",
            "metric_version":"v1",
            "creator_baseline_views":1000,
            "creator_ratio_exact":str(compute_creator_ratio(views,1000,Q)),
            "views_growth_per_hour_exact":str(
                compute_views_growth_per_hour(prev,views,earlier,OBSERVED)),
        })
        base={
            "video_id":i,"source_id":SOURCE,
            "source_policy_version_id":1,
            "source_metric_definition":"platform_post_views",
            "source_metric_version":"v1",
            "data_quality_status":"ok",
            "precision":"exact",
            "observation_time_assumed":False,
            "collected_at":(OBSERVED+timedelta(minutes=5)).isoformat(),
            "expires_at":None,
            "likes":90,"comments":12,"shares":5}
        histories[i]=[
            {**base,"id":i*10,"views":prev,
             "observed_at":earlier.isoformat()},
            {**base,"id":i*10+1,"views":views,
             "observed_at":OBSERVED.isoformat()},
        ]
    return rows,histories


class MarketCContracts(unittest.TestCase):
    def test_all_seven_numeric_stages_and_reproducible_c7(self):
        rows,histories=_data()
        output=enrich_exact_c(rows,histories,verify_math=True)
        self.assertEqual(len(output),21)
        c=output[0]["group_c"]
        self.assertEqual(c["c0"],"eligible_latest_snapshot")
        self.assertEqual(c["c4"]["state"],"exact_unapproved")
        self.assertEqual(c["c5"]["state"],"observed_peer_prior_unapproved")
        self.assertEqual(c["c5"]["independent_comparison_authors"],2)
        self.assertEqual(c["c7_breakout_preview"]["state"],
                         "diagnostic_unapproved_not_persistable")
        self.assertTrue(c["c1_baseline_cohort_sha256"])
        self.assertEqual(c["c2_niche_outlier"]["state"],"unavailable")
        self.assertEqual(c["native_report"]["state"],"diagnostic_verified")
        self.assertEqual(c["native_report"]["checked_stages"],
                         ["C1","C3","C4","C5","C6","C7_math"])
        self.assertTrue(verify_report(c["native_report"],c))
        self.assertFalse(c["original_full_c_certified"])
        again=enrich_exact_c(copy.deepcopy(rows),copy.deepcopy(histories),
                             verify_math=True)
        self.assertEqual(again[0]["group_c"]["native_report"]["report_sha256"],
                         c["native_report"]["report_sha256"])

    def test_c0_retracted_newest_does_not_fall_back(self):
        rows,history=_data()
        history[1][-1]["data_quality_status"]="retracted"
        row=enrich_exact_c(rows[:1],{1:history[1]},
                          verify_math=True)[0]
        c=row["group_c"]
        self.assertEqual(c["c0"],"unavailable")
        self.assertEqual(c["c0_quality_gate"]["reason"],"quality_not_exact_or_retracted")
        self.assertIsNone(c["c4"])
        self.assertIsNone(c["c7_breakout_preview"])

    def test_c0_suppressed_counter_correction_prevents_growth(self):
        rows,history=_data()
        row=rows[0]
        history[1][-2]["views"]=history[1][-1]["views"]+100
        c0=validate_history(row,history[1],strict=True)
        self.assertEqual(c0["prior_reason"],"prior_views_decreased_or_invalid")
        row["views_growth_per_hour_exact"]=None
        calculated=enrich_exact_c([row],{1:history[1]},verify_math=True)[0]["group_c"]
        self.assertIsNone(calculated["c7_breakout_preview"])
        self.assertIn("c7_missing_comparable_c1_c3_or_c6",
                      calculated["limitations"] if calculated["c5"] else
                      ["c7_missing_comparable_c1_c3_or_c6"])

    def test_quality_rejects_precision_and_wrong_metric_unit(self):
        rows,history=_data()
        faulty=copy.deepcopy(history[1])
        faulty[-1]["precision"]="estimated"
        self.assertEqual(validate_history(rows[0],faulty,strict=True)["reason"],
                         "precision_not_exact")
        faulty=copy.deepcopy(history[1])
        faulty[-1]["source_metric_definition"]="impressions"
        self.assertEqual(validate_history(rows[0],faulty,strict=True)["reason"],
                         "incompatible_view_unit_or_version")

    def test_disallow_mixed_providers_and_versions_in_peer_freeze(self):
        rows,history=_data()
        observations=[(r,history[r["video_id"]][-1]) for r in rows]
        cohort=freeze_diagnostic_peers(rows[0],observations,purpose="bayesian")
        self.assertEqual(cohort["state"],"frozen_diagnostic_unapproved")
        peer_ids={x["video_id"] for x in cohort["selected_peer_evidence"]}
        self.assertNotIn(1,peer_ids)
        self.assertGreaterEqual(len(peer_ids),5)
        altered=copy.deepcopy(rows)
        for item in altered:
            if item["video_id"]>7:
                item["metric_version"]="v99"
        denied=freeze_diagnostic_peers(altered[0],
             [(r,history[r["video_id"]][-1]) for r in altered],
             purpose="bayesian")
        self.assertEqual(denied["state"],"unavailable")

    def test_c2_original_exact_math_with_same_niche_other_authors(self):
        from unittest.mock import patch
        rows,history=_data()
        observations=[(r,history[r["video_id"]][-1]) for r in rows]
        with patch("intelligence.market_intelligence.group_c_cohorts._topic_id",
                   side_effect=lambda row:(2,"c"*64) if row["video_id"]<=18 else (3,"c"*64)):
            result=compute_c2_niche_diagnostic(rows[0],observations)
        self.assertEqual(result["state"],"observed_niche_comparison_unapproved")
        self.assertGreater(int(result["peer_count"]),5)
        self.assertEqual(result["topic_id"],2)
        self.assertTrue(result["excluded_from_breakout_score"])
        from intelligence.market_intelligence._copied.group_c.niche_comparison import compute_niche_comparison
        self.assertEqual(Decimal(result["outlier_ratio"]),
          compute_niche_comparison(history[1][-1]["views"],
             result["niche_median_views"],Decimal(result["alpha_used"])))

    def test_tamper_or_newer_snapshot_changes_proof(self):
        rows,history=_data()
        result=enrich_exact_c(rows,history,verify_math=True)[0]
        c=result["group_c"]
        bogus=copy.deepcopy(c)
        bogus["c7_breakout_preview"]["score"]="999.000"
        self.assertFalse(verify_report(c["native_report"],bogus))
        with self.assertRaises(ValueError):
            build_report({**result,"group_c":bogus},history[1])


if __name__=="__main__":
    unittest.main()
