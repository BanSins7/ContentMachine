"""UA-MOD-30 independent statistics, min-N and final publishing gates.

All numeric golden values are pure, private, synthetic test inputs, not
authentic qualified Group C scores. A current UA19-rights failure must
suppress ENTIRE multi-row result, never leak partial cohort statistics.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics import descriptive_aggregation as agg
from intelligence.unified_analytics import statistical_uncertainty as uncertainty
from test_unified_analytics_descriptive_aggregation import (
    Pins, FakeDB, MockVisible, fixture,
)


class FullFixtureIndependentGoldens(unittest.TestCase):
    def test_pure_five_discrete_values_exact_quantiles(self):
        s=agg._statistic(tuple(Decimal(x) for x in
                               ("1","2","3","4","5")))
        self.assertEqual(s.sample_size,5)
        self.assertEqual((s.minimum,s.p25,s.median,s.p75,s.maximum,s.mean),
                         ("1","2","3","4","5","3"))

    def test_pure_decimal_fraction_statistics_without_float(self):
        s=agg._statistic(tuple(Decimal(x) for x in
                               ("0.1","0.2","0.3","0.4","0.5")))
        self.assertEqual((s.minimum,s.p25,s.median,s.p75,s.maximum,s.mean),
                         ("0.1","0.2","0.3","0.4","0.5","0.3"))

    def test_pure_values_outside_0_to_100_not_clipped(self):
        s=agg._statistic(tuple(Decimal(x) for x in
                               ("-20","0","100","160","400")))
        self.assertEqual(s.minimum,"-20")
        self.assertEqual(s.maximum,"400")
        self.assertEqual(s.median,"100")
        self.assertEqual(s.p25,"0")
        self.assertEqual(s.p75,"160")

    def test_pure_sample_floor_four_rejected(self):
        for n in (0,1,2,3,4):
            with self.subTest(n=n):
                with self.assertRaises(AnalyticsBoundaryError):
                    agg._statistic(tuple(Decimal(x) for x in range(n)))

    def test_pure_nonfinite_rejected(self):
        for item in ("NaN","Infinity","-Infinity"):
            with self.assertRaises(AnalyticsBoundaryError):
                agg._statistic((Decimal(item),)*5)

    def test_uncertainty_iqr_disclosure_no_fake_significance(self):
        s=agg._statistic(tuple(Decimal(x) for x in
                               ("1","2","3","4","5")))
        i=uncertainty._interval(s)
        self.assertEqual(i.iqr_width,"2")
        self.assertIsNone(i.confidence_interval)

    def test_five_mocked_future_visible_get_exact_private_golden(self):
        pins,db=fixture()
        with patch.object(agg,"VisibilityPins",Pins),\
             patch.object(agg,"VisibleSnapshotReader",MockVisible):
            result=agg.QualifiedAggregationReader(db).summarize_qualified(pins)
        self.assertIsNotNone(result)
        self.assertEqual(result.qualified_video_count,5)
        self.assertEqual((result.breakout.p25,result.breakout.median,
                          result.breakout.p75,result.breakout.mean),
                         ("2","3","4","3"))
        self.assertEqual((result.views.median,result.views.mean),("30","30"))
        self.assertTrue(result.descriptive_only)
        self.assertFalse(result.causal_claims_allowed)
        self.assertFalse(result.confidence_intervals_computed)
        self.assertEqual(db.auth_count,5)

    def test_one_revoked_mock_pin_cancels_entire_aggregate(self):
        pins,db=fixture()
        db.denied.add(pins[4].snapshot_id)
        with patch.object(agg,"VisibilityPins",Pins),\
             patch.object(agg,"VisibleSnapshotReader",MockVisible):
            result=agg.QualifiedAggregationReader(db).summarize_qualified(pins)
        self.assertIsNone(result)
        self.assertEqual(db.auth_count,5)
        self.assertEqual(db.query_count,4)

    def test_unqualified_four_never_read_from_database(self):
        pins,db=fixture()
        with patch.object(agg,"VisibilityPins",Pins),\
             patch.object(agg,"VisibleSnapshotReader",MockVisible):
            result=agg.QualifiedAggregationReader(db).summarize_qualified(
                pins[:4]
            )
        self.assertIsNone(result)
        self.assertEqual(db.query_count,0)
        self.assertEqual(db.auth_count,0)

    def test_duplicate_video_sample_not_eligible(self):
        pins,db=fixture()
        with patch.object(agg,"VisibilityPins",Pins),\
             patch.object(agg,"VisibleSnapshotReader",MockVisible):
            with self.assertRaises(AnalyticsBoundaryError) as err:
                agg.QualifiedAggregationReader(db).summarize_qualified(
                    (pins[0],pins[1],pins[2],pins[3],pins[3])
                )
        self.assertEqual(err.exception.code,"aggregation_request_invalid")

    def test_cross_project_sample_not_eligible(self):
        pins,db=fixture()
        mixed=pins[:4]+(Pins(5,project="b"*32),)
        with patch.object(agg,"VisibilityPins",Pins),\
             patch.object(agg,"VisibleSnapshotReader",MockVisible):
            with self.assertRaises(AnalyticsBoundaryError) as err:
                agg.QualifiedAggregationReader(db).summarize_qualified(mixed)
        self.assertEqual(err.exception.code,"aggregation_request_invalid")

    def test_unbounded_sample_more_than_50_refused(self):
        pins=tuple(Pins(i) for i in range(1,52))
        db=FakeDB(pins,tuple((Decimal(i),i) for i in range(1,52)))
        with patch.object(agg,"VisibilityPins",Pins),\
             patch.object(agg,"VisibleSnapshotReader",MockVisible):
            with self.assertRaises(AnalyticsBoundaryError) as err:
                agg.QualifiedAggregationReader(db).summarize_qualified(pins)
        self.assertEqual(err.exception.code,"aggregation_request_invalid")
        self.assertEqual(db.query_count,0)


if __name__=="__main__":
    unittest.main()
