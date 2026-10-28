"""GC-MOD-19: fixed C7 math, receipt agreement and disposable PG17 integration.

Synthetic receipts below are NOT signed approvals; they exist only to test
pure mathematical/lineage invariants. Real PostgreSQL fixtures use the existing
GC11/12 signed-test decision and independent C1/C3/C5/C6 read-only adapters.
"""
from __future__ import annotations

import inspect
import os
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, localcontext
from uuid import uuid4

from intelligence.group_c.bayesian_engagement import (
    BAYESIAN_ENGAGEMENT_VERSION, BayesianEngagementResult,
    PostgreSQLBayesianEngagement, compute_bayesian_engagement,
)
from intelligence.group_c.breakout_score import (
    BREAKOUT_VERSION, BreakoutComponents, BreakoutPreview,
    assemble_breakout_preview, compute_breakout_components,
)
from intelligence.group_c.contracts import ContractError, canonical_hash
from intelligence.group_c.creator_ratio import (
    C1_VERSION, CreatorRatioResult, PostgreSQLCreatorRatio,
    compute_creator_ratio,
)
from intelligence.group_c.freshness import (
    FRESHNESS_VERSION, FreshnessResult, PostgreSQLFreshness,
    compute_freshness_score,
)
from intelligence.group_c.views_growth import (
    VIEW_GROWTH_VERSION, ViewsGrowthResult, PostgreSQLViewsGrowth,
    compute_views_growth_per_hour,
)
from intelligence.group_c.pure_math import q

P = "a" * 32
SRC = "synthetic"
SH = "b" * 64
T = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
BEFORE = T - timedelta(hours=2)
PUB = T - timedelta(hours=48)


def _c1_sha(v):
    return canonical_hash({
        "version": C1_VERSION, "project_id": v.project_id,
        "video_id": v.video_id, "snapshot_id": v.snapshot_id,
        "metadata_id": v.metadata_id, "source_id": v.source_id,
        "source_policy_version_id": v.source_policy_version_id,
        "metric_version": v.metric_version, "creator_id": v.creator_id,
        "config_id": v.config_id, "calculation_version": v.calculation_version,
        "creator_freeze_id": v.creator_freeze_id,
        "cohort_evidence_sha256": v.cohort_evidence_sha256,
        "review_subject_sha256": v.review_subject_sha256,
        "decision_id": v.review_decision_id,
        "activation_event_id": v.activation_event_id,
        "raw_views": v.views,
        "creator_median_views": str(q(v.creator_median_views)),
        "alpha": str(q(v.alpha_used)), "ratio": str(v.creator_ratio),
    })


def synthetic_receipts():
    ratio = compute_creator_ratio(100, Decimal("200"), Decimal("5"))
    creator = CreatorRatioResult(
        project_id=P, video_id=9, snapshot_id=12, metadata_id=20,
        source_id=SRC, source_policy_version_id=13,
        metric_version="v1", creator_id=8, config_id=44,
        calculation_version="c7-reviewed-v1", creator_freeze_id=55,
        legacy_creator_cohort_id=56, review_request_id="review-one",
        review_decision_id="decision-one", activation_event_id="event-one",
        review_subject_sha256=SH, cohort_evidence_sha256="c"*64,
        views=100, creator_median_views=q(Decimal("200")),
        alpha_used=q(Decimal("5")), creator_ratio=ratio, evidence_sha256=SH,
    )
    creator = replace(creator, evidence_sha256=_c1_sha(creator))
    rate = compute_views_growth_per_hour(80, 100, BEFORE, T)
    growth = ViewsGrowthResult(
        project_id=P, video_id=9, source_id=SRC,
        source_policy_version_id=13, metric_definition="platform_post_views",
        metric_version="v1", previous_snapshot_id=11,
        current_snapshot_id=12, metadata_id=20,
        previous_observed_at=BEFORE, current_observed_at=T,
        previous_views=80, current_views=100, views_delta=20,
        elapsed_microseconds=7200000000, views_growth_per_hour=rate,
        evidence_sha256=SH,
    )
    growth = replace(growth, evidence_sha256=canonical_hash({
        "version": VIEW_GROWTH_VERSION, "project_id": P, "video_id": 9,
        "source_id": SRC, "source_policy_version_id": 13,
        "metric_definition": "platform_post_views", "metric_version": "v1",
        "previous_snapshot_id": 11, "current_snapshot_id": 12,
        "metadata_id": 20, "previous_observed_at": BEFORE.isoformat(),
        "current_observed_at": T.isoformat(), "previous_views": 80,
        "current_views": 100, "views_delta": 20,
        "elapsed_microseconds": 7200000000, "views_growth_per_hour": str(rate),
    }))
    rates = compute_bayesian_engagement(100, 10, 3, 2, Decimal("50"),
                                        Decimal(".05"), Decimal(".015"),
                                        Decimal(".01"))
    engagement = BayesianEngagementResult(
        project_id=P, video_id=9, snapshot_id=12, metadata_id=20,
        source_id=SRC, source_policy_version_id=13, metric_version="v1",
        config_id=44, calculation_version="c7-reviewed-v1",
        prior_freeze_id=60, legacy_prior_cohort_id=61,
        review_request_id="review-one", review_decision_id="decision-one",
        activation_event_id="event-one", review_subject_sha256=SH,
        prior_evidence_sha256="d"*64, rates=rates, evidence_sha256=SH,
    )
    engagement = replace(engagement, evidence_sha256=canonical_hash({
        "version": BAYESIAN_ENGAGEMENT_VERSION,
        "project_id": P, "video_id": 9, "snapshot_id": 12,
        "metadata_id": 20, "source_id": SRC, "source_policy_version_id": 13,
        "metric_version": "v1", "config_id": 44,
        "calculation_version": "c7-reviewed-v1",
        "prior_freeze_id": 60, "prior_evidence_sha256": "d"*64,
        "review_subject_sha256": SH, "decision_id": "decision-one",
        "activation_event_id": "event-one",
        "views": rates.views, "likes": rates.likes,
        "comments": rates.comments, "shares": rates.shares,
        "k": str(rates.k_used), "p_l": str(rates.p_l_used),
        "p_c": str(rates.p_c_used), "p_s": str(rates.p_s_used),
        "like_rate_smoothed": str(rates.like_rate_smoothed),
        "comment_rate_smoothed": str(rates.comment_rate_smoothed),
        "share_rate_smoothed": str(rates.share_rate_smoothed),
        "weighted_engagement_smoothed": str(rates.weighted_engagement_smoothed),
    }))
    fresh = compute_freshness_score(PUB, T)
    freshness = FreshnessResult(
        project_id=P, video_id=9, source_id=SRC,
        source_policy_version_id=13, metric_definition="platform_post_views",
        metric_version="v1", previous_snapshot_id=11, snapshot_id=12,
        metadata_id=20, published_at=PUB, observed_at=T,
        age_microseconds=172800000000,
        age_hours=Decimal("48"), freshness_score=fresh,
        publication_evidence_sha256="e"*64, evidence_sha256=SH,
    )
    freshness = replace(freshness, evidence_sha256=canonical_hash({
        "version": FRESHNESS_VERSION,
        "project_id": P, "video_id": 9, "metadata_id": 20,
        "source_id": SRC, "source_policy_version_id": 13,
        "previous_snapshot_id": 11, "snapshot_id": 12,
        "metric_definition": "platform_post_views",
        "metric_version": "v1", "published_at": PUB.isoformat(),
        "observed_at": T.isoformat(), "age_microseconds": 172800000000,
        "freshness_score": str(fresh), "half_life_hours": "48",
        "publication_evidence_sha256": "e"*64,
    }))
    return creator, growth, engagement, freshness


class TestBreakoutPure(unittest.TestCase):
    def test_gold_legacy_stage07_component_parity_without_niche(self):
        from test_group_c_pure_math import GOLD
        comp = compute_breakout_components(
            Decimal(GOLD["creator_ratio"]), Decimal(GOLD["views_growth_per_hour"]),
            Decimal(GOLD["weighted_engagement_smoothed"]),
            Decimal(GOLD["freshness_score"]),
        )
        for key in (
            "score_creator_component", "score_growth_component",
            "score_engagement_component", "score_freshness_component",
            "breakout_score",
        ):
            with self.subTest(key=key):
                self.assertEqual(getattr(comp, key), Decimal(GOLD[key]))
        self.assertFalse(comp.normalized)

    def test_independent_4_metric_golden(self):
        comp = compute_breakout_components(
            Decimal("2"), Decimal("10"), Decimal("0.2"), Decimal("50"),
        )
        self.assertEqual(
            (comp.score_creator_component, comp.score_growth_component,
             comp.score_engagement_component, comp.score_freshness_component),
            (Decimal("0.900000000000"), Decimal("3.000000000000"),
             Decimal("0.030000000000"), Decimal("5.000000000000")),
        )
        self.assertEqual(comp.breakout_score, Decimal("8.930000000000"))

    def test_precision_half_up_on_individual_components(self):
        c = compute_breakout_components(
            Decimal("0.000000000010"), Decimal("0.000000000005"),
            Decimal("0.000000000010"), Decimal("0.000000000005"),
        )
        self.assertEqual(c.score_creator_component, Decimal("0.000000000005"))
        self.assertEqual(c.score_growth_component, Decimal("0.000000000002"))
        self.assertEqual(c.score_engagement_component, Decimal("0.000000000002"))
        self.assertEqual(c.score_freshness_component, Decimal("0.000000000001"))
        self.assertEqual(c.breakout_score, Decimal("0.000000000010"))

    def test_global_context_isolated_and_deterministic(self):
        args = (Decimal("1.123456789012"), Decimal("2.345678901234"),
                Decimal("0.123456789012"), Decimal("48.987654321098"))
        expected = compute_breakout_components(*args)
        with localcontext() as ctx:
            ctx.prec = 4
            ctx.rounding = ROUND_DOWN
            self.assertEqual(compute_breakout_components(*args), expected)

    def test_score_not_normalized_or_bounded_and_no_c2_input(self):
        comp = compute_breakout_components(Decimal("1"), Decimal("1000"),
                                          Decimal("0.5"), Decimal("100"))
        self.assertGreater(comp.breakout_score, 100)
        self.assertFalse(comp.normalized)
        self.assertEqual(list(inspect.signature(compute_breakout_components).parameters), [
            "creator_ratio", "views_growth_per_hour",
            "weighted_engagement_smoothed", "freshness_score",
        ])

    def test_unknown_bad_null_nan_float_bool_or_negative_rejected(self):
        for value in (None, True, 1, 1.1, "1", Decimal("NaN"),
                      Decimal("Infinity"), Decimal("-0.1")):
            for pos in range(4):
                args = [Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1")]
                args[pos] = value
                with self.subTest(pos=pos, value=value), self.assertRaises(ContractError):
                    compute_breakout_components(*args)

    def test_precision_and_numeric_30_12_overflow_denied(self):
        for value in (Decimal("0.0000000000001"),
                      Decimal("1000000000000000000")):
            with self.subTest(value=value), self.assertRaises(ContractError):
                compute_breakout_components(value, Decimal(0), Decimal(0), Decimal(0))

    def test_negative_growth_not_silently_clamped(self):
        with self.assertRaises(ContractError):
            compute_breakout_components(Decimal(0), Decimal("-1"), Decimal(0), Decimal(0))

    def test_freshness_only_is_bounded_but_raw_c7_is_not(self):
        with self.assertRaises(ContractError):
            compute_breakout_components(Decimal(1), Decimal(0), Decimal(0), Decimal("100.1"))

    def test_immutable_component_and_explicit_untrusted_preview_flags(self):
        c = compute_breakout_components(Decimal(1), Decimal(2), Decimal(3), Decimal(4))
        self.assertIsInstance(c, BreakoutComponents)
        with self.assertRaises(FrozenInstanceError):
            c.breakout_score = Decimal(999)
        preview = assemble_breakout_preview(*synthetic_receipts())
        self.assertIsInstance(preview, BreakoutPreview)
        self.assertEqual(preview.schema_version, BREAKOUT_VERSION)
        self.assertEqual(preview.status, "breakout_preview_unpersisted_unverified")
        self.assertNotEqual(preview.score_status, "complete")
        self.assertFalse(preview.ready_for_persistence)
        self.assertEqual(preview.components.breakout_score,
                         compute_breakout_components(
                            preview.components.creator_ratio,
                            preview.components.views_growth_per_hour,
                            preview.components.weighted_engagement_smoothed,
                            preview.components.freshness_score,
                         ).breakout_score)
        with self.assertRaises(FrozenInstanceError):
            preview.status = "complete"

    def test_synthetic_untrusted_receipt_preview_is_repeatable(self):
        one = assemble_breakout_preview(*synthetic_receipts())
        two = assemble_breakout_preview(*synthetic_receipts())
        self.assertEqual(one, two)
        self.assertEqual(len(one.evidence_sha256), 64)
        self.assertEqual(one.snapshot_id, 12)
        self.assertEqual(one.previous_snapshot_id, 11)

    def test_cross_snapshot_metric_version_source_policy_and_project_denied(self):
        change = (
            (0, {"project_id": "f"*32}),
            (1, {"current_snapshot_id": 1000}),
            (1, {"previous_snapshot_id": 19}),
            (2, {"source_policy_version_id": 99}),
            (3, {"metric_version": "v2"}),
            (3, {"metadata_id": 999}),
            (0, {"source_id": "other"}),
        )
        for index, edits in change:
            receipts = list(synthetic_receipts())
            receipts[index] = replace(receipts[index], **edits)
            with self.subTest(index=index, edits=edits), self.assertRaises(ContractError):
                assemble_breakout_preview(*receipts)

    def test_cross_approval_identity_denied_even_valid_signed_receipt_hash(self):
        receipts = list(synthetic_receipts())
        c1 = replace(receipts[0], review_decision_id="other-decision")
        c1 = replace(c1, evidence_sha256=_c1_sha(c1))
        receipts[0] = c1
        with self.assertRaises(ContractError) as e:
            assemble_breakout_preview(*receipts)
        self.assertEqual(e.exception.code, "invalid_c7_approval")

    def test_tampered_metric_values_and_input_hashes_fail(self):
        changes = (
            (0, {"creator_ratio": Decimal("0.1")}),
            (1, {"views_growth_per_hour": Decimal("100")}),
            (2, {"prior_evidence_sha256": "f"*64}),
            (3, {"freshness_score": Decimal("45")}),
            (0, {"evidence_sha256": "invalid"}),
            (1, {"elapsed_microseconds": 1}),
            (3, {"age_microseconds": 1}),
        )
        for index, edits in changes:
            receipts = list(synthetic_receipts())
            receipts[index] = replace(receipts[index], **edits)
            with self.subTest(index=index, edits=edits), self.assertRaises(ContractError):
                assemble_breakout_preview(*receipts)

    def test_mismatched_views_without_changing_hash_denied(self):
        receipts = list(synthetic_receipts())
        growth = replace(receipts[1], current_views=101)
        receipts[1] = growth
        with self.assertRaises(ContractError):
            assemble_breakout_preview(*receipts)

    def test_derived_without_db_does_not_claim_trusted_iam(self):
        result = assemble_breakout_preview(*synthetic_receipts())
        self.assertFalse(result.ready_for_persistence)
        self.assertNotEqual(result.score_status, "complete")
        self.assertFalse(hasattr(result, "calculated_metrics_id"))
        self.assertFalse(hasattr(result, "published_at_analytics"))


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB") == "YES",
    "Disposable PostgreSQL17 only: CM_TEST_PG_DSN + GC_TEST_DISPOSABLE_DB=YES",
)
class TestBreakoutPG17(unittest.TestCase):
    """Real D1 source lineage + test-only human approval/activation/verification."""

    @classmethod
    def setUpClass(cls):
        from test_group_c_config_activation import TestActivationPG17
        TestActivationPG17.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from test_group_c_config_activation import TestActivationPG17
        TestActivationPG17.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_config_activation import TestActivationPG17
        self.fixture = TestActivationPG17()
        self.fixture.conn = self.conn
        self.fixture.psycopg = self.psycopg
        self.fixture.setUp()
        for field in ("project", "source", "policy", "target", "config"):
            setattr(self, field, getattr(self.fixture, field))
        # GC11 fixture has exactly one current target raw D1 snapshot. Add
        # a genuine earlier raw observation; C3/C6 need the latest two.
        self.previous = self.conn.execute(
            """INSERT INTO metric_snapshots (
                 video_id,source_id,source_policy_version_id,
                 observed_at,collected_at,views,likes,comments,shares,
                 source_metric_definition,source_metric_version,
                 precision,data_quality_status,observation_time_assumed)
               VALUES(%s,%s,%s,now()-interval '4 hours',
                      now()-interval '3 hours',80,8,2,1,
                      'platform_post_views','v1','exact','ok',FALSE)
               RETURNING id""",
            (self.target[0], self.source, self.policy),
        ).fetchone()[0]

    def tearDown(self):
        self.fixture.tearDown()

    def inputs(self):
        from test_group_c_creator_ratio import _SyntheticC1IAM
        from test_group_c_bayesian_engagement import _SyntheticC5IAM
        review = self.fixture.signed()
        self.fixture.activate(review)
        creator_freeze, prior_freeze = self.conn.execute(
            "SELECT creator_freeze_id,prior_freeze_id FROM group_c_review_requests WHERE id=%s",
            (review.request_id,),
        ).fetchone()
        common = dict(
            conn=self.conn, project_id=self.project,
            video_id=self.target[0], snapshot_id=self.target[1],
            metadata_id=self.target[2], source_id=self.source,
            source_policy_version_id=self.policy, metric_version="v1",
        )
        c1 = PostgreSQLCreatorRatio().derive(
            **common, config_id=self.config, creator_freeze_id=creator_freeze,
            review_request_id=review.request_id, approval_verifier=_SyntheticC1IAM(),
        )
        c5 = PostgreSQLBayesianEngagement().derive(
            **common, config_id=self.config, prior_freeze_id=prior_freeze,
            review_request_id=review.request_id, approval_verifier=_SyntheticC5IAM(),
        )
        pair = dict(
            **common, previous_snapshot_id=self.previous,
        )
        c3 = PostgreSQLViewsGrowth().derive(**pair)
        c6 = PostgreSQLFreshness().derive(**pair)
        return c1, c3, c5, c6

    def test_reviewed_four_metric_composition_and_no_score_writes(self):
        receipt = assemble_breakout_preview(*self.inputs())
        self.assertEqual(receipt.snapshot_id, self.target[1])
        self.assertEqual(receipt.previous_snapshot_id, self.previous)
        self.assertEqual(receipt.components.views_growth_per_hour,
                         Decimal("10.000000000000"))
        self.assertGreater(receipt.components.breakout_score, Decimal(0))
        self.assertFalse(receipt.ready_for_persistence)
        self.assertFalse(self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM calculated_metrics WHERE post_id=%s)",
            (self.target[0],),
        ).fetchone()[0])

    def test_replay_receipts_are_deterministic(self):
        receipts = self.inputs()
        first = assemble_breakout_preview(*receipts)
        second = assemble_breakout_preview(*receipts)
        self.assertEqual(first, second)
        self.assertEqual(first.evidence_sha256, second.evidence_sha256)

    def test_different_source_metric_or_snapshot_is_not_merged(self):
        receipts = list(self.inputs())
        for i, change in (
            (1, {"current_snapshot_id": self.previous}),
            (3, {"source_id": "wrong"}),
            (2, {"metric_version": "bad"}),
        ):
            items = receipts.copy()
            items[i] = replace(items[i], **change)
            with self.subTest(change=change), self.assertRaises(ContractError):
                assemble_breakout_preview(*items)

    def test_activation_replaced_after_receipt_creation_requires_stage20_recheck(self):
        receipts = self.inputs()
        old_preview = assemble_breakout_preview(*receipts)
        new_config = self.fixture.new_config()
        # A different approved minimum creates a distinct activation subject
        # while preserving the same creator/prior freeze.
        newer_review = self.fixture.signed(config_id=new_config, minimum_creator=1)
        self.fixture.activate(newer_review, config_id=new_config, replace=self.config)
        # Offline preview is intentionally NOT a current authorization token.
        self.assertEqual(assemble_breakout_preview(*receipts), old_preview)
        self.assertFalse(old_preview.ready_for_persistence)
        with self.assertRaises(ContractError):
            PostgreSQLCreatorRatio().derive(
                self.conn, project_id=self.project,
                video_id=self.target[0], snapshot_id=self.target[1],
                metadata_id=self.target[2], source_id=self.source,
                source_policy_version_id=self.policy, metric_version="v1",
                config_id=self.config,
                creator_freeze_id=receipts[0].creator_freeze_id,
                review_request_id=receipts[0].review_request_id,
                approval_verifier=object(),
            )

    def test_revoked_source_cannot_issue_new_read_receipts(self):
        receipts = self.inputs()
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.source,),
        )
        self.assertFalse(assemble_breakout_preview(*receipts).ready_for_persistence)
        with self.assertRaises(ContractError):
            PostgreSQLViewsGrowth().derive(
                self.conn, project_id=self.project,
                video_id=self.target[0], source_id=self.source,
                source_policy_version_id=self.policy,
                previous_snapshot_id=self.previous,
                snapshot_id=self.target[1], metadata_id=self.target[2],
                metric_version="v1",
            )

    def test_newer_raw_snapshot_blocks_recreating_stale_receipts(self):
        receipts = self.inputs()
        self.conn.execute(
            """INSERT INTO metric_snapshots (
                 video_id,source_id,source_policy_version_id,
                 observed_at,collected_at,views,likes,comments,shares,
                 source_metric_definition,source_metric_version,
                 precision,data_quality_status,observation_time_assumed)
               VALUES(%s,%s,%s,now()-interval '10 minutes',now(),
                      999,10,3,2,'platform_post_views','v2',
                      'exact','retracted',FALSE)""",
            (self.target[0], self.source, self.policy),
        )
        self.assertFalse(assemble_breakout_preview(*receipts).ready_for_persistence)
        with self.assertRaises(ContractError):
            PostgreSQLFreshness().derive(
                self.conn, project_id=self.project, video_id=self.target[0],
                metadata_id=self.target[2], source_id=self.source,
                source_policy_version_id=self.policy,
                previous_snapshot_id=self.previous,
                snapshot_id=self.target[1], metric_version="v1",
            )


if __name__ == "__main__":
    unittest.main()
