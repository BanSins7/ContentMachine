"""GC-MOD-07 gold-set and reverse-parity tests; no I/O or configured peers.

Expected values and fixture originate from Analytics reference metrics.py and
tests/test_analytics_engine.py in the sibling Analytics branch; this is a
frozen independent golden output, not a runtime import of the old scorer.
"""
from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, getcontext, localcontext
from pathlib import Path

from intelligence.group_c.pure_math import (
    CALCULATOR_VERSION, MetricError, compute_c_metrics, d, q,
)

T = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
WEIGHTS = dict(creator_ratio=0.45, views_growth_per_hour=0.30,
               weighted_engagement_smoothed=0.15, freshness_score=0.10)


def reference_fixture():
    current = dict(id=11, video_id=7, source_id="legal_source", views=200,
                   likes=20, comments=4, shares=2, observed_at=T,
                   data_quality_status="ok", source_metric_definition="video_views/v1",
                   source_metric_version="1", observation_time_assumed=False)
    previous = {**current, "id":10, "views":100, "observed_at":T-timedelta(hours=4)}
    published = {"published_at":T-timedelta(hours=12)}
    config = dict(alpha=5, bayesian_k=50, normalization_enabled=False,
                  freshness_half_life_h=Decimal("48"), weights_json=dict(WEIGHTS))
    creator = dict(purpose="creator", is_frozen=True, post_count=12, median_views=100)
    prior = dict(purpose="engagement_prior", is_frozen=True, post_count=25,
                 prior_like_rate=Decimal("0.1"), prior_comment_rate=Decimal("0.02"),
                 prior_share_rate=Decimal("0.01"))
    niche = dict(purpose="niche", is_frozen=True, post_count=15, median_views=250)
    return current, previous, published, config, creator, prior, niche


# Frozen outputs from the Analytics reference C1..C7 formula, NUMERIC(30,12).
GOLD = dict(
    creator_median_views="100.000000000000",
    niche_median_views="250.000000000000",
    alpha_used="5.000000000000", k_used="50.000000000000",
    p_l_used="0.100000000000", p_c_used="0.020000000000",
    p_s_used="0.010000000000", like_rate_pct="10.000000000000",
    comment_rate_pct="2.000000000000", share_rate_pct="1.000000000000",
    engagement_rate_pct="13.000000000000",
    weighted_engagement_raw="0.160000000000",
    like_rate_smoothed="0.100000000000",
    comment_rate_smoothed="0.020000000000",
    share_rate_smoothed="0.010000000000",
    weighted_engagement_smoothed="0.160000000000",
    creator_ratio="1.952380952381", niche_outlier="0.803921568627",
    views_growth_per_hour="25.000000000000",
    freshness_score="84.089641525371",
    score_creator_component="0.878571428571",
    score_growth_component="7.500000000000",
    score_engagement_component="0.024000000000",
    score_freshness_component="8.408964152537",
    breakout_score="16.811535581108",
)


class TestGroupCPureMath(unittest.TestCase):
    def test_all_reference_outputs_and_status(self):
        result = compute_c_metrics(*reference_fixture())
        self.assertEqual(set(result), set(GOLD) | {"score_status","calculation_status"})
        for name, expected in GOLD.items():
            with self.subTest(name=name):
                self.assertEqual(result[name], Decimal(expected))
                self.assertEqual(result[name].as_tuple().exponent, -12)
        self.assertEqual(result["score_status"], "complete")
        self.assertEqual(result["calculation_status"], "complete")
        self.assertEqual(CALCULATOR_VERSION, "group-c-decimal/v1")

    def test_optional_niche_does_not_change_breakout(self):
        fixture = reference_fixture()
        with_niche = compute_c_metrics(*fixture)
        no_niche = compute_c_metrics(*fixture[:6])
        self.assertIsNone(no_niche["niche_outlier"])
        self.assertIsNone(no_niche["niche_median_views"])
        self.assertEqual(no_niche["breakout_score"], with_niche["breakout_score"])

    def test_unbounded_breakout_is_raw_not_normalized(self):
        fixture = reference_fixture()
        fixture[0].update(views=20000, likes=2000, comments=400, shares=200,
                          observed_at=T+timedelta(minutes=1))
        got = compute_c_metrics(*fixture)
        self.assertGreater(got["breakout_score"], Decimal("100"))
        self.assertEqual(got["breakout_score"], sum((
            got["score_creator_component"], got["score_growth_component"],
            got["score_engagement_component"], got["score_freshness_component"],
        ), Decimal(0)))

    def test_numeric_half_up_and_postgres_column_limit(self):
        self.assertEqual(q(Decimal("0.0000000000005")), Decimal("0.000000000001"))
        self.assertEqual(q(Decimal("-0.0000000000005")), Decimal("-0.000000000001"))
        self.assertEqual(q(Decimal("999999999999999999.999999999999")),
                         Decimal("999999999999999999.999999999999"))
        for value in ("999999999999999999.9999999999995", "1e18", "-1e18", "1e100"):
            with self.subTest(value=value), self.assertRaises(MetricError) as err:
                q(Decimal(value))
            self.assertEqual(err.exception.code, "numeric_overflow")

    def test_global_decimal_context_cannot_change_output(self):
        baseline = compute_c_metrics(*reference_fixture())
        original = getcontext().copy()
        with localcontext() as ctx:
            ctx.prec = 6
            ctx.rounding = ROUND_DOWN
            self.assertEqual(compute_c_metrics(*reference_fixture()), baseline)
            self.assertEqual(ctx.prec, 6)
        self.assertEqual(getcontext().prec, original.prec)
        self.assertEqual(getcontext().rounding, original.rounding)

    def test_no_unsafe_float_facts_or_changed_json_weights(self):
        for value in (float("nan"), float("inf"), 0.25, True, object(),
                      "NaN", "Infinity", -1):
            with self.subTest(value=value), self.assertRaises(MetricError):
                d(value, "sample")
        for weight in (0.4500000001, float("nan"), True, "0.5", None):
            fixture = reference_fixture()
            fixture[3]["weights_json"]["creator_ratio"] = weight
            with self.subTest(weight=weight), self.assertRaises(MetricError) as err:
                compute_c_metrics(*fixture)
            self.assertEqual(err.exception.code, "invalid_weights")

    def test_reference_negative_error_code_parity(self):
        cases = (
            (0, {"likes":None}, "missing_likes"),
            (0, {"source_metric_definition":"reach"}, "incompatible_definition"),
            (0, {"views":1}, "counter_correction"),
            (0, {"views":0}, "counter_correction"),
            (0, {"observation_time_assumed":True}, "assumed_observation_time"),
            (1, {"source_metric_version":"2"}, "incompatible_metric_version"),
            (1, {"observed_at":T}, "invalid_timestamp"),
            (3, {"alpha":None}, "missing_alpha"),
            (3, {"normalization_enabled":True}, "normalization_not_allowed"),
            (4, {"is_frozen":False}, "creator_cohort_not_approved"),
            (5, {"prior_share_rate":None}, "missing_prior_share_rate"),
        )
        for index, changes, expected in cases:
            fixture = copy.deepcopy(reference_fixture())
            fixture[index].update(changes)
            with self.subTest(index=index, changes=changes), self.assertRaises(MetricError) as err:
                compute_c_metrics(*fixture)
            self.assertEqual(err.exception.code, expected)

    def test_absent_parameters_cohort_reviews_and_zero_views(self):
        cases = (
            (3, {"bayesian_k":None}, "missing_k"),
            (3, {"alpha":0}, "invalid_alpha"),
            (3, {"bayesian_k":0}, "invalid_k"),
            (3, {"freshness_half_life_h":24}, "invalid_half_life"),
            (3, {"normalization_enabled":None}, "normalization_not_allowed"),
            (4, {"median_views":None}, "creator_cohort_not_approved"),
            (4, {"post_count":0}, "creator_cohort_not_approved"),
            (5, {"is_frozen":1}, "prior_cohort_not_approved"),
            (5, {"prior_like_rate":None}, "missing_prior_like_rate"),
            (5, {"prior_like_rate":"1.1"}, "invalid_prior_rate"),
        )
        for index, changes, expected in cases:
            fixture = copy.deepcopy(reference_fixture())
            fixture[index].update(changes)
            with self.subTest(changes=changes), self.assertRaises(MetricError) as err:
                compute_c_metrics(*fixture)
            self.assertEqual(err.exception.code, expected)
        fixture = reference_fixture()
        fixture[0]["views"] = fixture[1]["views"] = 0
        with self.assertRaises(MetricError) as err:
            compute_c_metrics(*fixture)
        self.assertEqual(err.exception.code, "zero_views_rate_undefined")

    def test_snapshot_identity_policy_quality_rejections(self):
        cases = ((1, {"video_id":12}, "different_video"),
                 (0, {"source_id":"other"}, "incompatible_source"),
                 (1, {"id":11}, "identical_snapshot"),
                 (1, {"source_policy_version_id":2}, "incompatible_policy_version"),
                 (0, {"precision":"estimated"}, "unverified_observation"),
                 (0, {"data_quality_status":"retracted"}, "unverified_observation"))
        for index, changes, expected in cases:
            fixture = copy.deepcopy(reference_fixture())
            fixture[index].update(changes)
            with self.subTest(changes=changes), self.assertRaises(MetricError) as err:
                compute_c_metrics(*fixture)
            self.assertEqual(err.exception.code, expected)

    def test_exact_microsecond_timedelta(self):
        fixture = reference_fixture()
        fixture[0]["observed_at"] = fixture[1]["observed_at"] + timedelta(microseconds=1)
        fixture[0]["views"] = fixture[1]["views"] + 1
        result = compute_c_metrics(*fixture)
        self.assertEqual(result["views_growth_per_hour"], Decimal("3600000000.000000000000"))

    def test_numeric_storage_scales_and_repeatability(self):
        inputs = reference_fixture()
        original = copy.deepcopy(inputs)
        first, second = compute_c_metrics(*inputs), compute_c_metrics(*inputs)
        self.assertEqual(first, second)
        self.assertEqual(inputs, original)
        for name, value in first.items():
            if isinstance(value, Decimal):
                with self.subTest(name=name):
                    self.assertLess(value.copy_abs(), Decimal("1e18"))
                    self.assertEqual(value.as_tuple().exponent, -12)

    def test_no_placeholder_niche_or_live_root_calculator(self):
        fixture = reference_fixture()
        fixture[6]["purpose"] = "creator"
        with self.assertRaises(MetricError) as err:
            compute_c_metrics(*fixture)
        self.assertEqual(err.exception.code, "niche_cohort_not_approved")
        import intelligence.group_c.pure_math as module
        self.assertNotIn("from intelligence.analytics",
                         Path(module.__file__).read_text(encoding="utf-8"))
        import intelligence.group_c as root
        self.assertFalse(hasattr(root, "compute_c_metrics"))


if __name__ == "__main__":
    unittest.main()
