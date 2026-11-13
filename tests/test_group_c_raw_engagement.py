"""GC-MOD-16: точные доли C4, единицы измерения и PG17 D1 provenance."""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, localcontext
from uuid import uuid4

from intelligence.group_c.c0 import C0Pair, C0Snapshot
from intelligence.group_c.contracts import ContractError
from intelligence.group_c.raw_engagement import (
    RAW_ENGAGEMENT_VERSION, RawEngagementRates, RawEngagementResult,
    PostgreSQLRawEngagement, compute_raw_engagement,
    compute_verified_raw_engagement,
)

T = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def snapshot(sid=2, *, views=200, likes=20, comments=10, shares=5,
             saves=999, version="v2", source="d1") -> C0Snapshot:
    observed = T if sid == 2 else T - timedelta(hours=4)
    return C0Snapshot(
        snapshot_id=sid, video_id=71, source_id=source,
        source_policy_version_id=11, metric_definition="platform_post_views",
        metric_version=version, observed_at=observed, collected_at=observed,
        expires_at=None, views=views, likes=likes, comments=comments,
        shares=shares, saves=saves, followers=None,
    )


class TestRawEngagementPure(unittest.TestCase):
    def test_gold_fraction_and_explicit_percent_not_weighted_saves(self):
        r = compute_raw_engagement(200, 20, 10, 5)
        self.assertIsInstance(r, RawEngagementRates)
        self.assertEqual(r.unit, "fraction")
        self.assertEqual(r.like_rate_fraction, Decimal("0.100000000000"))
        self.assertEqual(r.comment_rate_fraction, Decimal("0.050000000000"))
        self.assertEqual(r.share_rate_fraction, Decimal("0.025000000000"))
        self.assertEqual(r.engagement_rate_fraction, Decimal("0.175000000000"))
        self.assertEqual(r.weighted_engagement_raw_fraction, Decimal("0.250000000000"))
        p = r.as_percentages()
        self.assertEqual(p.unit, "percent")
        self.assertEqual(p.engagement_rate_percent, Decimal("17.500000000000"))
        self.assertEqual(p.weighted_engagement_percent, Decimal("25.000000000000"))

    def test_zero_interactions_valid_zero_views_forbidden(self):
        r = compute_raw_engagement(1, 0, 0, 0)
        self.assertEqual(r.engagement_rate_fraction, Decimal("0E-12"))
        with self.assertRaises(ContractError) as e:
            compute_raw_engagement(0, 0, 0, 0)
        self.assertEqual(e.exception.code, "zero_views_rate_undefined")

    def test_nonzero_counters_and_zero_views_fail(self):
        with self.assertRaises(ContractError) as e:
            compute_raw_engagement(0, 5, 3, 1)
        self.assertEqual(e.exception.code, "zero_views_rate_undefined")

    def test_invalid_bigint_null_float_bool_and_string_rejected(self):
        for bad in (None, -1, True, False, 1.0, "1", 2**63, float("nan")):
            for pos in range(4):
                args = [10, 1, 2, 3]
                args[pos] = bad
                with self.subTest(bad=bad, pos=pos), self.assertRaises(ContractError):
                    compute_raw_engagement(*args)

    def test_isolation_from_caller_decimal_context(self):
        with localcontext() as ctx:
            ctx.prec = 4
            ctx.rounding = ROUND_DOWN
            r = compute_raw_engagement(3, 1, 0, 0)
            self.assertEqual(r.like_rate_fraction, Decimal("0.333333333333"))
            self.assertEqual(r.as_percentages().like_rate_percent,
                             Decimal("33.333333333333"))

    def test_no_double_rounding_percentage(self):
        r = compute_raw_engagement(3, 1, 1, 1)
        self.assertEqual(r.weighted_engagement_raw_fraction,
                         Decimal("1.833333333333"))
        self.assertEqual(r.as_percentages().weighted_engagement_percent,
                         Decimal("183.333333333333"))

    def test_over_100_percent_is_not_clipped(self):
        r = compute_raw_engagement(1, 2, 1, 3)
        self.assertEqual(r.engagement_rate_fraction, Decimal("6.000000000000"))
        self.assertEqual(r.weighted_engagement_raw_fraction,
                         Decimal("12.500000000000"))
        self.assertEqual(r.as_percentages().engagement_rate_percent,
                         Decimal("600.000000000000"))

    def test_bigint_boundary_and_decimal_overflow(self):
        with self.assertRaises(ContractError) as e:
            compute_raw_engagement(1, 2**63-1, 2**63-1, 2**63-1)
        self.assertEqual(e.exception.code, "numeric_overflow")
        r = compute_raw_engagement(2**63-1, 2**63-1, 0, 0)
        self.assertEqual(r.like_rate_fraction, Decimal("1.000000000000"))

    def test_output_is_immutable(self):
        r = compute_raw_engagement(10, 1, 2, 3)
        with self.assertRaises(FrozenInstanceError):
            r.likes = 100
        with self.assertRaises(FrozenInstanceError):
            r.as_percentages().unit = "fraction"

    def test_c0_pair_math_uses_current_only_saves_excluded(self):
        pair = C0Pair(snapshot(1, views=100), snapshot())
        self.assertEqual(compute_verified_raw_engagement(pair),
                         compute_raw_engagement(200, 20, 10, 5))
        with_saves = C0Pair(snapshot(1, views=100),
                            replace(snapshot(), saves=2**63-1))
        self.assertEqual(compute_verified_raw_engagement(with_saves),
                         compute_verified_raw_engagement(pair))

    def test_missing_counters_and_untrusted_mapping_rejected(self):
        with self.assertRaises(ContractError) as e:
            compute_verified_raw_engagement({"current": snapshot()})
        self.assertEqual(e.exception.code, "missing_snapshot")
        for field in ("views", "likes", "comments", "shares"):
            pair = C0Pair(snapshot(1, views=100),
                          replace(snapshot(), **{field: None}))
            with self.subTest(field=field), self.assertRaises(ContractError):
                compute_verified_raw_engagement(pair)

    def test_pair_cross_source_version_and_counter_correction_fail(self):
        for field, value in (
            ("video_id", 72), ("source_id", "different"),
            ("source_policy_version_id", 12), ("metric_version", "v3"),
            ("metric_definition", "impressions"), ("snapshot_id", 1),
            ("observed_at", T-timedelta(hours=5)), ("views", 99),
        ):
            pair = C0Pair(snapshot(1, views=100),
                          replace(snapshot(), **{field: value}))
            with self.subTest(field=field), self.assertRaises(ContractError):
                compute_verified_raw_engagement(pair)

    def test_diagnostic_module_does_not_expose_score_writes(self):
        self.assertEqual(RAW_ENGAGEMENT_VERSION, "group-c-raw-engagement/v1")
        for method in ("persist", "approve", "publish"):
            self.assertFalse(hasattr(PostgreSQLRawEngagement, method))


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB") == "YES",
    "Disposable PostgreSQL17 only: CM_TEST_PG_DSN + GC_TEST_DISPOSABLE_DB=YES",
)
class TestRawEngagementPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_group_c_c0 import TestC0Postgres
        TestC0Postgres.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from test_group_c_c0 import TestC0Postgres
        TestC0Postgres.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_c0 import TestC0Postgres
        self.fixture = TestC0Postgres()
        self.fixture.conn = self.conn
        self.fixture.setUp()
        for name in ("project", "source", "policy", "video", "metadata", "snapshots"):
            setattr(self, name, getattr(self.fixture, name))

    def tearDown(self):
        self.fixture.tearDown()

    def derive(self, **changes):
        params = dict(
            conn=self.conn, project_id=self.project, video_id=self.video,
            source_id=self.source, source_policy_version_id=self.policy,
            previous_snapshot_id=self.snapshots[0], snapshot_id=self.snapshots[1],
            metadata_id=self.metadata, metric_version="v2",
        )
        params.update(changes)
        return PostgreSQLRawEngagement().derive(**params)

    def test_exact_d1_fraction_percent_and_no_persistence(self):
        result = self.derive()
        self.assertIsInstance(result, RawEngagementResult)
        self.assertEqual((result.rates.views, result.rates.likes,
                          result.rates.comments, result.rates.shares), (90, 0, 2, 1))
        self.assertEqual(result.rates.engagement_rate_fraction,
                         Decimal("0.033333333333"))
        self.assertEqual(result.rates.weighted_engagement_raw_fraction,
                         Decimal("0.066666666667"))
        self.assertEqual(result.percentages.engagement_rate_percent,
                         Decimal("3.333333333333"))
        self.assertEqual(result.status, "raw_engagement_derived_unapproved")
        self.assertFalse(result.ready_for_breakout)
        with self.assertRaises(FrozenInstanceError):
            result.status = "complete"
        self.assertFalse(self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM calculated_metrics WHERE post_id=%s)",
            (self.video,),
        ).fetchone()[0])

    def test_idempotent_replay_and_sha256(self):
        first, again = self.derive(), self.derive()
        self.assertEqual(first, again)
        self.assertEqual(len(first.evidence_sha256), 64)

    def test_saves_excluded_from_c4_and_digest(self):
        first = self.derive()
        self.conn.execute("UPDATE metric_snapshots SET saves=45 WHERE id=%s",
                          (self.snapshots[1],))
        second = self.derive()
        self.assertEqual(first.rates, second.rates)
        self.assertEqual(first.evidence_sha256, second.evidence_sha256)

    def test_zero_views_denominator_denied(self):
        self.conn.execute("UPDATE metric_snapshots SET views=0 WHERE id=%s",
                          (self.snapshots[1],))
        with self.assertRaises(ContractError) as e:
            self.derive()
        self.assertEqual(e.exception.code, "zero_views_rate_undefined")

    def test_null_interactions_not_fabricated_zero(self):
        for field in ("likes", "comments", "shares"):
            self.conn.execute(
                "UPDATE metric_snapshots SET " + field + "=NULL WHERE id=%s",
                (self.snapshots[1],),
            )
            with self.subTest(field=field), self.assertRaises(ContractError) as e:
                self.derive()
            self.assertEqual(e.exception.code, "missing_raw_counter")
            self.conn.execute(
                "UPDATE metric_snapshots SET " + field + "=0 WHERE id=%s",
                (self.snapshots[1],),
            )

    def test_latest_retraction_cannot_fall_back_to_older_pair(self):
        self.fixture.insert_snapshot(1, 100, status="retracted", version="v3")
        with self.assertRaises(ContractError) as e:
            self.derive()
        self.assertEqual(e.exception.code, "metric_history_changed")

    def test_wrong_project_metric_version_source_policy_denied(self):
        for change in (
            {"project_id": uuid4().hex}, {"metric_version": "v99"},
            {"source_id": "wrong"}, {"source_policy_version_id": 99999},
            {"metadata_id": 99999}, {"video_id": 99999},
        ):
            with self.subTest(change=change), self.assertRaises(ContractError):
                self.derive(**change)

    def test_expired_publication_metadata_denied(self):
        self.conn.execute(
            "UPDATE source_video_metadata SET expires_at=now()-interval '1 second' WHERE id=%s",
            (self.metadata,),
        )
        with self.assertRaises(ContractError):
            self.derive()

    def test_revoked_source_rights_denied(self):
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.source,),
        )
        with self.assertRaises(ContractError):
            self.derive()

    def test_current_retraction_denied(self):
        self.conn.execute(
            "UPDATE metric_snapshots SET data_quality_status='retracted' WHERE id=%s",
            (self.snapshots[1],),
        )
        with self.assertRaises(ContractError):
            self.derive()

    def test_never_creates_c7_or_analytics_output(self):
        self.derive()
        self.assertFalse(self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM calculated_metrics WHERE post_id=%s)",
            (self.video,),
        ).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
