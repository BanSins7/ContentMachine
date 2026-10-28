"""UA-MOD-23 independent golds and safe DB-backed report integration.

Fake future-positive SRE/QDP/C only for unit fixtures; NO real qualified
approval is manufactured. Actual UA13/UA84/UA85 still deny positives.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics import statistical_uncertainty as quality
from intelligence.unified_analytics import descriptive_aggregation as agg
from intelligence.unified_analytics import typed_segmentation as seg
from intelligence.unified_analytics import cohort_comparison as cmp
from test_unified_analytics_descriptive_aggregation import (
    fixture as base_fixture, Pins, MockVisible,
)
from test_unified_analytics_typed_segmentation import (
    fixture as segment_fixture, approved_mock,
)
from test_unified_analytics_cohort_comparison import (
    make_pins, CohortFakeDB, authorization_mock, WINDOW,
)


class PureUncertaintyGolds(unittest.TestCase):
    def stat(self,*items):
        return agg._statistic(tuple(Decimal(str(v)) for v in items))

    def test_known_iqr_order_statistic(self):
        # Sorted [1,2,3,4,5] => p25=2, p50=3, p75=4, IQR=2.
        x=quality._interval(self.stat(1,2,3,4,5))
        self.assertEqual((x.p25,x.median,x.p75,x.iqr_width),
                         ("2","3","4","2"))
        self.assertIsNone(x.confidence_interval)
        self.assertEqual(x.method,quality.UNCERTAINTY_METHOD)

    def test_negative_and_unbounded_score_iqr(self):
        v=quality._interval(self.stat(-20,-10,0,100,300))
        self.assertEqual(v.p25,"-10")
        self.assertEqual(v.p75,"100")
        self.assertEqual(v.iqr_width,"110")
        self.assertIsNone(v.confidence_interval)

    def test_repeated_zero_spread_not_zero_uncertainty(self):
        s=quality._interval(self.stat(7,7,7,7,7))
        self.assertEqual(s.iqr_width,"0")
        self.assertIsNone(s.confidence_interval)
        self.assertEqual(s.method,quality.UNCERTAINTY_METHOD)

    def test_decimal_fraction_iqr_exact(self):
        s=quality._interval(self.stat("0.1","0.2","0.3","0.4","0.5"))
        self.assertEqual(s.iqr_width,"0.2")
        self.assertEqual(Decimal(s.median),Decimal("0.3"))

    def test_quality_coverage_only_conditional_visible(self):
        m=quality._coverage(self.stat(1,2,3,4,5))
        self.assertEqual(m.included_qualified_n,5)
        self.assertEqual(m.breakout_observed_n,5)
        self.assertEqual(m.views_observed_n,5)
        self.assertEqual(m.breakout_missing_within_included_n,0)
        self.assertEqual(m.views_missing_within_included_n,0)
        self.assertIsNone(m.whole_population_n)
        self.assertIsNone(m.excluded_or_unqualified_n)
        self.assertEqual(m.scope,quality.MISSINGNESS_SCOPE)
        self.assertEqual(m.outside_status,quality.UNKNOWN_POPULATION)

    def test_cannot_claim_no_missingness_project_wide(self):
        with self.assertRaises(AnalyticsBoundaryError):
            quality.ObservedMissingness(5,5,5,whole_population_n=5)
        with self.assertRaises(AnalyticsBoundaryError):
            quality.ObservedMissingness(5,5,5,excluded_or_unqualified_n=0)

    def test_sample_n_below_floor_never_reported(self):
        for n in (0,1,2,3,4,True):
            with self.assertRaises(AnalyticsBoundaryError):
                quality.ObservedMissingness(n,n,n)
        for n in (0,1,4,True):
            with self.assertRaises(AnalyticsBoundaryError):
                quality.EmpiricalInterquartileRange(
                    n,"1","2","3","2"
                )

    def test_forged_observed_counts_rejected(self):
        for counts in ((5,4,5),(5,5,4),(5,True,5),(5,5,False)):
            with self.assertRaises(AnalyticsBoundaryError):
                quality.ObservedMissingness(*counts)

    def test_cannot_invent_unknown_population_count(self):
        with self.assertRaises(AnalyticsBoundaryError):
            quality.ObservedMissingness(5,5,5,scope="project_all")
        with self.assertRaises(AnalyticsBoundaryError):
            quality.ObservedMissingness(
                5,5,5,outside_status="all_fully_observed",
            )

    def test_no_ci_p_value_or_false_significance(self):
        n=quality.UncertaintyDisclosure(1)
        self.assertEqual(n.inferential_status,quality.INFERENCE_STATUS)
        self.assertEqual(n.statistical_tests_performed,0)
        self.assertIsNone(n.confidence_level)
        self.assertIsNone(n.p_value)
        self.assertIsNone(n.statistically_significant)
        self.assertFalse(n.causal_claims_allowed)

    def test_multiplicity_warning_one_vs_more(self):
        self.assertFalse(quality.UncertaintyDisclosure(0).multiple_testing_warning)
        self.assertFalse(quality.UncertaintyDisclosure(1).multiple_testing_warning)
        self.assertTrue(quality.UncertaintyDisclosure(2).multiple_testing_warning)
        self.assertTrue(quality.UncertaintyDisclosure(8).multiple_testing_warning)

    def test_no_user_supplied_confidence_or_pvalues(self):
        with self.assertRaises(AnalyticsBoundaryError):
            quality.UncertaintyDisclosure(2,p_value="0.01")
        with self.assertRaises(AnalyticsBoundaryError):
            quality.UncertaintyDisclosure(2,statistically_significant=True)
        with self.assertRaises(AnalyticsBoundaryError):
            quality.UncertaintyDisclosure(2,confidence_level="95%")
        with self.assertRaises(AnalyticsBoundaryError):
            quality.UncertaintyDisclosure(2,multiple_testing_warning=False)

    def test_empirical_bounds_strict_and_consistent(self):
        for kwargs in (
            dict(sample_size=5,p25="2",median="1",p75="3",iqr_width="1"),
            dict(sample_size=5,p25="1",median="2",p75="3",iqr_width="1"),
            dict(sample_size=5,p25="2",median="3",p75="1",iqr_width="-1"),
        ):
            with self.assertRaises(AnalyticsBoundaryError):
                quality.EmpiricalInterquartileRange(**kwargs)

    def test_nonfinite_or_float_interval_rejected(self):
        for bad in ("NaN","Infinity","-Infinity","1e999999",0.5):
            with self.assertRaises((AnalyticsBoundaryError,OverflowError)):
                quality.EmpiricalInterquartileRange(
                    5,"1","2","3",bad,
                )

    def test_interval_not_confidence_interval(self):
        with self.assertRaises(AnalyticsBoundaryError):
            quality.EmpiricalInterquartileRange(
                5,"1","2","3","2",confidence_interval=("1","3")
            )


class ProtectedQualityReader(unittest.TestCase):
    def test_summary_qualified_golden_iqr(self):
        pins,db=base_fixture()
        with patch.object(agg,"VisibilityPins",Pins),\
             patch.object(agg,"VisibleSnapshotReader",MockVisible):
            res=quality.QualifiedUncertaintyReader(db).summarize(pins)
        self.assertIsInstance(res,quality.QualifiedSummaryQuality)
        self.assertEqual(res.aggregate.qualified_video_count,5)
        self.assertEqual(res.aggregate.breakout.median,"3")
        self.assertEqual(res.breakout_iqr.iqr_width,"2")
        self.assertEqual(res.views_iqr.iqr_width,"20")
        self.assertEqual(res.missingness.included_qualified_n,5)
        self.assertIsNone(res.missingness.whole_population_n)
        self.assertFalse(res.uncertainty.multiple_testing_warning)
        self.assertEqual(db.auth_count,5)
        self.assertEqual(db.query_count,5)

    def test_summary_rejected_visibility_returns_none_no_data(self):
        pins,db=base_fixture()
        db.denied.add(pins[-1].snapshot_id)
        with patch.object(agg,"VisibilityPins",Pins),\
             patch.object(agg,"VisibleSnapshotReader",MockVisible):
            self.assertIsNone(quality.QualifiedUncertaintyReader(db).summarize(pins))

    def test_summary_small_n_0_1_has_no_db_access(self):
        pins,db=base_fixture()
        with patch.object(agg,"VisibilityPins",Pins),\
             patch.object(agg,"VisibleSnapshotReader",MockVisible):
            reader=quality.QualifiedUncertaintyReader(db)
            self.assertIsNone(reader.summarize(()))
            self.assertIsNone(reader.summarize(pins[:1]))
            self.assertIsNone(reader.summarize(pins[:4]))
        self.assertEqual(db.sql,[])

    def test_summary_unknown_metric_suppresses_not_missing_total(self):
        pins,db=base_fixture()
        db.rows[pins[0].snapshot_id]["views"]=None
        with patch.object(agg,"VisibilityPins",Pins),\
             patch.object(agg,"VisibleSnapshotReader",MockVisible):
            self.assertIsNone(quality.QualifiedUncertaintyReader(db).summarize(pins))

    def test_segmented_iqr_and_duplicate_membership(self):
        pins,db=segment_fixture()
        with approved_mock():
            res=quality.QualifiedUncertaintyReader(db).segment(pins,"cta_type")
        self.assertIsInstance(res,quality.QualifiedSegmentationQuality)
        self.assertEqual([a.value for a in res.segments],["follow","like"])
        self.assertTrue(res.uncertainty.multiple_testing_warning)
        self.assertEqual(res.uncertainty.exploratory_contrasts,2)
        self.assertEqual(res.segments[0].missingness.included_qualified_n,5)
        self.assertEqual(res.segments[0].breakout_iqr.iqr_width,"2")
        self.assertIsNone(res.segments[0].missingness.whole_population_n)
        self.assertIsNone(res.segments[0].breakout_iqr.confidence_interval)

    def test_single_segment_does_not_claim_significance(self):
        pins,db=segment_fixture()
        with approved_mock():
            res=quality.QualifiedUncertaintyReader(db).segment(pins,"video_format")
        self.assertEqual(len(res.segments),1)
        self.assertFalse(res.uncertainty.multiple_testing_warning)
        self.assertIsNone(res.uncertainty.statistically_significant)

    def test_segmentation_hidden_first_prevents_report(self):
        pins,db=segment_fixture()
        db.denied.add(pins[0].snapshot_id)
        with approved_mock():
            self.assertIsNone(
                quality.QualifiedUncertaintyReader(db).segment(pins,"story_role")
            )
        self.assertEqual(db.query_count,0)
        self.assertEqual(db.b_reads,0)

    def test_segmentation_rare_group_not_annotated(self):
        pins,db=segment_fixture(n=6)
        db.b_rows[pins[-1].candidate.group_b.b_analysis_id]["cta_types"]=[
            "follow","purchase"
        ]
        with approved_mock():
            res=quality.QualifiedUncertaintyReader(db).segment(pins,"cta_type")
        self.assertIsNotNone(res)
        self.assertEqual([p.value for p in res.segments],["follow","like"])
        self.assertNotIn("purchase",repr(res))
        self.assertFalse(hasattr(res,"suppressed_count"))

    def test_comparison_golden_two_arms_and_iqr(self):
        left,right=make_pins()
        db=CohortFakeDB(left,right)
        spec=cmp.ComparisonSpec(WINDOW,WINDOW,creator_id=111)
        with authorization_mock():
            res=quality.QualifiedUncertaintyReader(db).compare(left,right,spec)
        self.assertIsInstance(res,quality.QualifiedComparisonQuality)
        self.assertEqual(len(res.bins),1)
        v=res.bins[0]
        self.assertEqual(v.age_bin,"age_days_000_007")
        self.assertEqual(v.left_missingness.included_qualified_n,5)
        self.assertEqual(v.right_missingness.included_qualified_n,5)
        self.assertEqual(v.left_breakout_iqr.iqr_width,"2")
        self.assertEqual(v.right_breakout_iqr.iqr_width,"2")
        self.assertEqual(res.comparison.bins[0].breakout_median_difference,"2")
        self.assertIsNone(v.difference_confidence_interval)
        self.assertFalse(res.uncertainty.multiple_testing_warning)
        self.assertEqual(db.auth_count,10)

    def test_comparison_two_bins_has_multiplicity_warning(self):
        left,right=make_pins(10)
        db=CohortFakeDB(left,right)
        from datetime import timedelta
        for arm in (left,right):
            for p in arm[5:]:
                meta=db.metadata[p.snapshot_id]
                meta["observed_at"]=meta["published_at"]+timedelta(days=10)
        spec=cmp.ComparisonSpec(WINDOW,WINDOW,creator_id=111)
        with authorization_mock():
            res=quality.QualifiedUncertaintyReader(db).compare(left,right,spec)
        self.assertEqual(len(res.bins),2)
        self.assertTrue(res.uncertainty.multiple_testing_warning)
        self.assertEqual(res.uncertainty.exploratory_contrasts,2)
        self.assertEqual(
            tuple(x.age_bin for x in res.bins),
            ("age_days_000_007","age_days_007_014"),
        )

    def test_denied_comparison_member_no_quality_or_author_data(self):
        left,right=make_pins()
        db=CohortFakeDB(left,right)
        db.denied.add(right[-1].snapshot_id)
        with authorization_mock():
            res=quality.QualifiedUncertaintyReader(db).compare(
                left,right,cmp.ComparisonSpec(WINDOW,WINDOW,creator_id=111)
            )
        self.assertIsNone(res)
        self.assertEqual(db.query_count,0)
        self.assertEqual(db.author_reads,0)

    def test_comparison_small_n_no_db_queries(self):
        left,right=make_pins()
        db=CohortFakeDB(left,right)
        with authorization_mock():
            x=quality.QualifiedUncertaintyReader(db)
            self.assertIsNone(x.compare(left[:1],right,
                          cmp.ComparisonSpec(WINDOW,WINDOW,creator_id=111)))
        self.assertEqual(db.sql,[])

    def test_invalid_reader_connection_cannot_be_a_forged_input(self):
        with self.assertRaises(AnalyticsBoundaryError):
            quality.QualifiedUncertaintyReader(object())
        with self.assertRaises(AnalyticsBoundaryError):
            quality.QualifiedUncertaintyReader(None)

    def test_user_supplied_stat_object_not_accepted_as_reader_data(self):
        pins,db=base_fixture()
        reader=quality.QualifiedUncertaintyReader(db)
        with self.assertRaises(AnalyticsBoundaryError):
            reader.compare(pins,pins,object())
        with self.assertRaises(AnalyticsBoundaryError):
            reader.segment(pins,"unrecognized_dimension")

    def test_repeated_read_is_deterministic_without_new_pvalue(self):
        pins,db=base_fixture()
        with patch.object(agg,"VisibilityPins",Pins),\
             patch.object(agg,"VisibleSnapshotReader",MockVisible):
            reader=quality.QualifiedUncertaintyReader(db)
            a=reader.summarize(pins)
            b=reader.summarize(pins)
        self.assertEqual(a,b)
        self.assertIsNone(a.uncertainty.p_value)

    def test_no_ci_independent_sample_design_absent(self):
        pins,db=base_fixture()
        with patch.object(agg,"VisibilityPins",Pins),\
             patch.object(agg,"VisibleSnapshotReader",MockVisible):
            v=quality.QualifiedUncertaintyReader(db).summarize(pins)
        self.assertIsNone(v.breakout_iqr.confidence_interval)
        self.assertEqual(v.uncertainty.inferential_status,
                         quality.INFERENCE_STATUS)

    def test_summary_wrong_c_input_denies_quality(self):
        pins,db=base_fixture()
        db.rows[pins[2].snapshot_id]["calculation_config_id"]=999
        with patch.object(agg,"VisibilityPins",Pins),\
             patch.object(agg,"VisibleSnapshotReader",MockVisible):
            self.assertIsNone(quality.QualifiedUncertaintyReader(db).summarize(pins))

    def test_no_data_cache_or_public_endpoint_in_module(self):
        from pathlib import Path
        source=quality.__doc__
        self.assertIn("NEVER by accepting a user-supplied DTO",source)
        self.assertFalse(hasattr(quality,"publish_quality"))
        self.assertFalse(hasattr(quality,"create_report_from_rows"))


if __name__=="__main__":
    unittest.main()
