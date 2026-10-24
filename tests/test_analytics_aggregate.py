"""Zero-network descriptive Unified Analytics tests. No causal/predictive claims."""
from __future__ import annotations
import copy
import unittest
from decimal import Decimal
from unittest.mock import patch
import intelligence.analytics.aggregate as mod
from intelligence.analytics.contracts import AnalyticsError

def row(video_id,score,*,taxonomy_version="1.0",hook="curiosity",config=4):
    return {
        "video_id":video_id, "platform":"tiktok","content_kind":"video",
        "status":"qualified", "score_status":"complete",
        "source_metric_definition":"view_count/v1",
        "source_metric_version":"1",
        "creator_cohort_definition_version":"creator/1",
        "prior_cohort_definition_version":"prior/1",
        "niche_cohort_definition_version":None,
        "calculation_config_id":config,
        "breakout_score":Decimal(str(score)),
        "features_a":{"analysis_version":"a/v1"},
        "features_b":{"analysis_version":"b/v2",
                      "taxonomy_version":taxonomy_version,
                      "hook":{"mechanisms":[hook],"classification_status":"inferred"},
                      "format":{"video_primary":"talking_head","classification_status":"inferred"},
                      "story":{"frames":[{"role":"hook"}],"intervals":[],"slides":[]},
                      "editing":{"editing_type":"fast_cuts"},
                      "subtitles":{"subtitle_type":"dynamic"},
                      "ctas":[{"cta_type":"follow"}],
                      "cta_summary":{"has_cta":True},
                      "topic":{"topic_id":33}},
        "lineage":{"relevance_taxonomy_version_id":17},
    }

class DescriptiveTests(unittest.TestCase):
    def test_verified_strata_and_decimal_quantiles(self):
        rows=[row(1,1),row(2,2),row(3,3),row(4,4)]
        out=mod.summarize_qualified(rows,"hook_mechanism",min_sample=2)
        self.assertTrue(out["descriptive_only"])
        self.assertFalse(out["causal_claims_allowed"])
        self.assertEqual(out["qualified_video_count"],4)
        self.assertEqual(len(out["segments"]),1)
        segment=out["segments"][0]
        self.assertEqual(segment["sample_size"],4)
        self.assertEqual(Decimal(segment["breakout_median"]),Decimal("2.5"))
        self.assertEqual(Decimal(segment["breakout_p25"]),Decimal("1.75"))
        self.assertEqual(Decimal(segment["breakout_p75"]),Decimal("3.25"))
        self.assertEqual(Decimal(segment["breakout_mean"]),Decimal("2.5"))

    def test_distinct_source_metric_and_cohort_versions_not_pooled(self):
        rows=[row(1,1),row(2,2),row(3,3)]
        rows[2]["source_metric_version"]="2"
        out=mod.summarize_qualified(rows,"video_format",2)
        self.assertEqual(len(out["segments"]),1)
        self.assertEqual(out["segments"][0]["sample_size"],2)
        rows[1]["creator_cohort_definition_version"]="creator/2"
        out=mod.summarize_qualified(rows,"video_format",2)
        self.assertEqual(out["segments"],[])

    def test_mismatched_taxonomy_never_pooled(self):
        rows=[row(1,1),row(2,2),row(3,3,taxonomy_version="2.0")]
        out=mod.summarize_qualified(rows,"video_format",min_sample=2)
        self.assertEqual(len(out["segments"]),1)
        self.assertEqual(out["segments"][0]["sample_size"],2)
        self.assertEqual(out["low_sample_groups_suppressed"],1)

    def test_multilabel_hook_and_valid_no_cta(self):
        a=row(1,3)
        b=row(2,5)
        a["features_b"]["hook"]["mechanisms"]=["curiosity","surprise","curiosity"]
        b["features_b"]["hook"]["mechanisms"]=["curiosity","surprise"]
        out=mod.summarize_qualified([a,b],"hook_mechanism",min_sample=2)
        self.assertEqual({x["value"] for x in out["segments"]},{"curiosity","surprise"})
        for x in (a,b):
            x["features_b"]["ctas"]=[]
            x["features_b"]["cta_summary"]["has_cta"]=False
        out=mod.summarize_qualified([a,b],"cta_type",min_sample=2)
        self.assertEqual(out["segments"][0]["value"],"none_observed")

    def test_incomplete_unaudited_or_duplicate_inputs_rejected(self):
        sample=row(1,5)
        for mutation in (
            {"status":"partial"},
            {"score_status":"insufficient_data"},
            {"source_metric_definition":None},
            {"breakout_score":None},
            {"calculation_config_id":None},
        ):
            bad={**copy.deepcopy(sample),**mutation}
            with self.subTest(mutation=mutation),self.assertRaises(AnalyticsError):
                mod.summarize_qualified([bad],"content_kind",2)
        with self.assertRaises(AnalyticsError):
            mod.summarize_qualified([sample,sample],"content_kind",2)
        with self.assertRaises(AnalyticsError):
            mod.summarize_qualified([sample],"content_kind",1)
        with self.assertRaises(AnalyticsError):
            mod.summarize_qualified([sample],"unknown_dimension",2)
        with patch.object(mod,"MAX_INPUT_POSTS",1):
            with self.assertRaises(AnalyticsError):
                mod.summarize_qualified([sample,row(2,6)],"content_kind",2)

    def test_empty_qualified_group_not_fake_signal(self):
        out=mod.summarize_qualified([],"content_kind",2)
        self.assertEqual(out["segments"],[])
        self.assertEqual(out["qualified_video_count"],0)
        self.assertTrue(out["descriptive_only"])

if __name__=="__main__":
    unittest.main()
