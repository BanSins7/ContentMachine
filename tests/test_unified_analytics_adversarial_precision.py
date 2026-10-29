"""UA31 Decimal/quantile reproducibility across ordering, concurrency, edges.

Pure authorized *math only*; no Group C peer read, no qualified source
snapshot, no significance/CI or population-causal claims.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import random
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics import descriptive_aggregation as agg


class AdversarialPrecision(unittest.TestCase):
    def test_fifty_values_known_type7_exact(self):
        xs=tuple(Decimal(i) for i in range(1,51))
        s=agg._statistic(xs)
        self.assertEqual(s.sample_size,50)
        self.assertEqual((s.minimum,s.p25,s.median,s.p75,s.maximum,s.mean),
                         ("1","13.25","25.5","37.75","50","25.5"))

    def test_50_shuffles_identical_sha_stable_decimal_stats(self):
        original=list(map(Decimal,range(1,51)))
        expected=agg._statistic(tuple(original))
        for seed in range(50):
            values=original.copy()
            random.Random(seed).shuffle(values)
            self.assertEqual(agg._statistic(tuple(values)),expected)

    def test_concurrent_decimal_calculations_deterministic(self):
        xs=tuple(Decimal(i) for i in range(1,51))
        with ThreadPoolExecutor(max_workers=16) as pool:
            outputs=list(pool.map(lambda _:agg._statistic(xs),range(80)))
        self.assertEqual(len(set((x.mean,x.median,x.p25,x.p75)
                                 for x in outputs)),1)

    def test_fifty_one_rejected_before_stat_math(self):
        with self.assertRaises(AnalyticsBoundaryError):
            agg._statistic(tuple(Decimal(i) for i in range(51)))

    def test_bool_float_nan_inf_disallowed(self):
        for bad in (True,False,1.0,float("nan"),Decimal("Infinity"),
                    Decimal("NaN")):
            with self.subTest(bad=str(bad)):
                with self.assertRaises(AnalyticsBoundaryError):
                    agg._statistic((bad,)*5)

    def test_negative_and_over100_remain_unclipped(self):
        items=tuple(map(Decimal,("-100","-10","0","120","900")))
        out=agg._statistic(items)
        self.assertEqual(out.minimum,"-100")
        self.assertEqual(out.maximum,"900")
        self.assertEqual(out.mean,"182")
        self.assertEqual(out.p75,"120")

    def test_very_large_numeric_exponent_denied(self):
        with self.assertRaises(AnalyticsBoundaryError):
            agg._statistic((Decimal("1e999999"),)*5)

    def test_high_precision_fraction_is_not_rounded_to_float(self):
        data=tuple(Decimal(x) for x in (
            "0.0000000000000000000000000000001",
            "0.0000000000000000000000000000002",
            "0.0000000000000000000000000000003",
            "0.0000000000000000000000000000004",
            "0.0000000000000000000000000000005",
        ))
        out=agg._statistic(data)
        self.assertEqual(Decimal(out.median),data[2])
        self.assertEqual(Decimal(out.mean),data[2])

    def test_unsorted_explicit_percentile_denied(self):
        with self.assertRaises(AnalyticsBoundaryError):
            agg._percentile_linear(
                (Decimal("3"),Decimal("2"),Decimal("1")),1,2
            )

    def test_quantile_zero_spread_cannot_claim_zero_uncertainty(self):
        x=agg._statistic((Decimal(7),)*5)
        self.assertEqual(x.p25,x.p75)
        self.assertEqual(x.mean,"7")

    def test_exact_four_sample_under_min_n_denied(self):
        with self.assertRaises(AnalyticsBoundaryError):
            agg._statistic(tuple(Decimal(i) for i in range(1,5)))

    def test_all_inputs_are_numeric_decimal_not_binary_float(self):
        vals=(Decimal("0.1"),Decimal("0.2"),Decimal("0.3"),
              Decimal("0.4"),Decimal("0.5"))
        result=agg._statistic(vals)
        self.assertEqual(result.p25,"0.2")
        self.assertEqual(result.p75,"0.4")


if __name__=="__main__":
    unittest.main()
