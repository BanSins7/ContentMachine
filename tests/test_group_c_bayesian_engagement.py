"""GC-MOD-17: deterministic Bayesian Decimal C5 + disposable PostgreSQL17 IAM."""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal, ROUND_DOWN, localcontext
from uuid import uuid4

from intelligence.group_c.bayesian_engagement import (
    BAYESIAN_ENGAGEMENT_VERSION, BayesianEngagementRates,
    BayesianEngagementResult, PostgreSQLBayesianEngagement,
    _approved_prior_context, compute_bayesian_engagement,
)
from intelligence.group_c.contracts import ContractError

PROJECT = "a" * 32
SHA = "b" * 64


def prior_context():
    freeze = dict(
        id=11, project_id=PROJECT, target_video_id=100,
        target_snapshot_id=101, target_metadata_id=102,
        source_id="D1", source_policy_version_id=99,
        source_metric_definition="platform_post_views",
        source_metric_version="v1", purpose="engagement_prior",
        state="frozen_unreviewed", median_views=None,
        member_count=2, legacy_cohort_id=51,
        rules_sha256=SHA, selection_sha256=SHA, evidence_sha256=SHA,
        prior_like_rate=Decimal("0.050000000000"),
        prior_comment_rate=Decimal("0.015000000000"),
        prior_share_rate=Decimal("0.010000000000"),
    )
    parent = dict(
        id=51, purpose="engagement_prior", is_frozen=True, post_count=2,
        source_id="D1", source_metric_definition="platform_post_views",
        platform="youtube", content_type="video",
        prior_like_rate=freeze["prior_like_rate"],
        prior_comment_rate=freeze["prior_comment_rate"],
        prior_share_rate=freeze["prior_share_rate"],
    )
    members = []
    for vid, views in ((201, 100), (202, 300)):
        data = dict(
            project_id=PROJECT, video_id=vid, snapshot_id=vid + 1000,
            metadata_id=vid + 2000, platform="youtube", c_content_type="video",
            source_id="D1", source_policy_version_id=99,
            metric_definition="platform_post_views", metric_version="v1",
            observed_at="now", views=views, likes=10, comments=3, shares=2,
            raw_snapshot_id=vid + 1000, raw_video_id=vid,
            raw_source_id="D1", raw_policy_id=99,
            raw_definition="platform_post_views", raw_version="v1",
            raw_observed_at="now", raw_views=views, raw_likes=10,
            raw_comments=3, raw_shares=2,
            raw_quality="ok", raw_precision="exact", raw_assumed=False,
            legacy_video_id=vid, legacy_snapshot_id=vid + 1000,
        )
        members.append(data)
    args = dict(
        project_id=PROJECT, video_id=100, snapshot_id=101,
        metadata_id=102, source_id="D1", source_policy_version_id=99,
        metric_version="v1", platform="youtube", content_type="video",
        prior_freeze=freeze, legacy_prior=parent, members=members,
    )
    return args


class TestBayesianEngagementPure(unittest.TestCase):
    def test_reference_gold_c5_math(self):
        out = compute_bayesian_engagement(
            100, 10, 3, 2, Decimal("50"),
            Decimal(".05"), Decimal(".015"), Decimal(".01"),
        )
        self.assertIsInstance(out, BayesianEngagementRates)
        self.assertEqual(out.like_rate_smoothed, Decimal("0.083333333333"))
        self.assertEqual(out.comment_rate_smoothed, Decimal("0.025000000000"))
        self.assertEqual(out.share_rate_smoothed, Decimal("0.016666666667"))
        self.assertEqual(out.weighted_engagement_smoothed,
                         Decimal("0.170833333333"))
        self.assertEqual(out.unit, "fraction")

    def test_k_positive_and_missing_never_default(self):
        for k in (None, 0, -1, "-0.01", True, 5.0, "NaN"):
            with self.subTest(k=k), self.assertRaises(ContractError):
                compute_bayesian_engagement(100, 1, 2, 3, k, "0.1", "0.2", "0.3")

    def test_missing_bad_out_of_range_priors_never_imputed(self):
        for bad in (None, True, 0.1, "NaN", "Infinity", -1, "1.01", "abc"):
            for slot in range(3):
                args = ["0.1", "0.2", "0.3"]
                args[slot] = bad
                with self.subTest(bad=bad, slot=slot), self.assertRaises(ContractError):
                    compute_bayesian_engagement(100, 10, 2, 3, 50, *args)

    def test_zero_or_missing_views_not_scored(self):
        for bad in (0, None, True, 1.0, -1, 2**63):
            with self.subTest(v=bad), self.assertRaises(ContractError):
                compute_bayesian_engagement(bad, 0, 0, 0, 1, 0, 0, 0)

    def test_invalid_interaction_counters_never_clamped(self):
        for bad in (None, False, -1, 0.5, "1", 2**63):
            for slot in range(3):
                row = [0, 0, 0]
                row[slot] = bad
                with self.subTest(bad=bad, slot=slot), self.assertRaises(ContractError):
                    compute_bayesian_engagement(5, *row, 1, 0, 0, 0)

    def test_no_hidden_precision_rounding_in_approved_inputs(self):
        for k, p in (("0.0000000000001", "0.1"),
                     ("1", "0.0000000000001")):
            with self.subTest(k=k, p=p), self.assertRaises(ContractError) as ctx:
                compute_bayesian_engagement(10, 1, 2, 3, k, p, "0", "0")
            self.assertEqual(ctx.exception.code, "invalid_c5_precision")

    def test_context_isolated_and_round_half_up(self):
        with localcontext() as ctx:
            ctx.prec = 4
            ctx.rounding = ROUND_DOWN
            result = compute_bayesian_engagement(3, 1, 0, 0, 1, 0, 0, 0)
        self.assertEqual(result.like_rate_smoothed, Decimal("0.250000000000"))

    def test_no_rounding_component_before_weighted_sum(self):
        result = compute_bayesian_engagement(3, 1, 1, 1, 1, 0, 0, 0)
        self.assertEqual(result.like_rate_smoothed, Decimal("0.250000000000"))
        self.assertEqual(result.weighted_engagement_smoothed,
                         Decimal("1.375000000000"))

    def test_zero_and_one_priors_explicitly_accepted(self):
        result = compute_bayesian_engagement(5, 0, 0, 0, 5, 1, 0, 1)
        self.assertEqual(result.like_rate_smoothed, Decimal("0.500000000000"))
        self.assertEqual(result.share_rate_smoothed, Decimal("0.500000000000"))

    def test_unbounded_interaction_rate_not_clipped(self):
        result = compute_bayesian_engagement(1, 10, 3, 2, 1, 0, 0, 0)
        self.assertEqual(result.like_rate_smoothed, Decimal("5.000000000000"))
        self.assertGreater(result.weighted_engagement_smoothed, Decimal("1"))

    def test_immutable_rates_and_explicit_schema(self):
        result = compute_bayesian_engagement(5, 1, 2, 3, 1, 0, 0, 0)
        with self.assertRaises(FrozenInstanceError):
            result.k_used = Decimal("22")
        self.assertEqual(BAYESIAN_ENGAGEMENT_VERSION,
                         "group-c-bayesian-engagement/v1")

    def test_prior_cohort_gold_and_original_peer_consistency(self):
        a = prior_context()
        self.assertEqual(_approved_prior_context(**a),
                         (Decimal("0.05"), Decimal("0.015"), Decimal("0.01")))

    def test_prior_wrong_target_source_kind_or_metric_denied(self):
        for key, value in (
            ("project_id", "x"*32), ("video_id", 99),
            ("snapshot_id", 900), ("metadata_id", 555),
            ("source_id", "wrong"), ("source_policy_version_id", 7),
            ("platform", "tiktok"), ("content_type", "carousel"),
            ("metric_version", "v2"),
        ):
            args = prior_context()
            args[key] = value
            with self.subTest(key=key), self.assertRaises(ContractError):
                _approved_prior_context(**args)

    def test_peer_cross_project_wrong_source_and_duplicate_self_denied(self):
        for field, value in (
            ("project_id", "c"*32), ("raw_version", "v99"),
            ("raw_views", 9), ("raw_likes", 0), ("legacy_snapshot_id", 0),
            ("raw_quality", "retracted"), ("raw_assumed", True),
            ("video_id", 100), ("source_id", "bad"),
            ("c_content_type", "carousel"),
        ):
            args = prior_context()
            args["members"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                _approved_prior_context(**args)

    def test_prior_legacy_and_frozen_stat_tampering_denied(self):
        for field, value in (
            ("prior_like_rate", Decimal("0.20")),
            ("prior_comment_rate", None),
            ("member_count", 5), ("evidence_sha256", "bad"),
        ):
            args = prior_context()
            args["prior_freeze"][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                _approved_prior_context(**args)

    def test_standalone_has_no_score_writer(self):
        for method in ("persist", "publish", "approve"):
            self.assertFalse(hasattr(PostgreSQLBayesianEngagement, method))


class _SyntheticC5IAM:
    """Test-only untrusted verifier; NOT a production implementation."""
    def __init__(self, allowed=True):
        self.allowed = allowed

    def verify_c5_use(self, *args, **kwargs):
        return self.allowed


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB") == "YES",
    "Disposable PostgreSQL17 only: CM_TEST_PG_DSN + GC_TEST_DISPOSABLE_DB=YES",
)
class TestBayesianEngagementPG17(unittest.TestCase):
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
        for field in ("project", "source", "policy", "target", "peers", "config"):
            setattr(self, field, getattr(self.fixture, field))

    def tearDown(self):
        self.fixture.tearDown()

    def approved(self):
        request = self.fixture.signed()
        self.fixture.activate(request)
        prior_id = self.conn.execute(
            "SELECT prior_freeze_id FROM group_c_review_requests WHERE id=%s",
            (request.request_id,),
        ).fetchone()[0]
        return request, prior_id

    def derive(self, request, prior_id, **changes):
        params = dict(
            conn=self.conn, project_id=self.project,
            video_id=self.target[0], snapshot_id=self.target[1],
            metadata_id=self.target[2], source_id=self.source,
            source_policy_version_id=self.policy, metric_version="v1",
            config_id=self.config, prior_freeze_id=prior_id,
            review_request_id=request.request_id,
            approval_verifier=_SyntheticC5IAM(),
        )
        params.update(changes)
        return PostgreSQLBayesianEngagement().derive(**params)

    def test_approved_prior_k_and_fractional_c5_gold(self):
        request, prior_id = self.approved()
        result = self.derive(request, prior_id)
        self.assertIsInstance(result, BayesianEngagementResult)
        self.assertEqual(result.rates.k_used, Decimal("50.000000000000"))
        self.assertEqual(result.rates.p_l_used, Decimal("0.05"))
        self.assertEqual(result.rates.p_c_used, Decimal("0.015"))
        self.assertEqual(result.rates.p_s_used, Decimal("0.01"))
        self.assertEqual(result.rates.like_rate_smoothed, Decimal("0.083333333333"))
        self.assertEqual(result.rates.comment_rate_smoothed, Decimal("0.025000000000"))
        self.assertEqual(result.rates.share_rate_smoothed, Decimal("0.016666666667"))
        self.assertEqual(result.rates.weighted_engagement_smoothed,
                         Decimal("0.170833333333"))
        self.assertFalse(result.ready_for_breakout)
        self.assertEqual(result.status, "c5_derived_unpersisted")
        with self.assertRaises(FrozenInstanceError):
            result.rates = None

    def test_pending_review_rejected(self):
        from intelligence.group_c.human_review import PostgreSQLHumanReview
        creator, prior = self.fixture.freeze()
        request = PostgreSQLHumanReview().create_request(
            self.conn, project_id=self.project, config_id=self.config,
            creator_freeze_id=creator.id, prior_freeze_id=prior.id,
            minimum_creator_posts=2, minimum_prior_posts=2,
        )
        with self.assertRaises(ContractError) as e:
            self.derive(request, prior.id)
        self.assertEqual(e.exception.code, "review_not_approved")

    def test_no_trusted_iam_and_explicit_denial(self):
        request, prior_id = self.approved()
        for verifier in (None, _SyntheticC5IAM(False), _SyntheticC5IAM(1)):
            with self.subTest(verifier=verifier), self.assertRaises(ContractError):
                self.derive(request, prior_id, approval_verifier=verifier)

    def test_wrong_project_video_version_freeze_policy_denied(self):
        request, prior_id = self.approved()
        for change in (
            {"project_id": uuid4().hex}, {"video_id": self.peers[0][0]},
            {"metric_version": "v2"}, {"source_id": "other"},
            {"source_policy_version_id": 999999},
            {"metadata_id": 999999}, {"snapshot_id": self.peers[0][1]},
            {"prior_freeze_id": 999999}, {"config_id": 999999},
        ):
            with self.subTest(change=change), self.assertRaises(ContractError):
                self.derive(request, prior_id, **change)

    def test_source_rights_revocation_denied(self):
        request, prior_id = self.approved()
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.source,),
        )
        with self.assertRaises(ContractError):
            self.derive(request, prior_id)

    def test_newer_raw_observation_denies_old_selected_snapshot(self):
        request, prior_id = self.approved()
        self.conn.execute(
            """INSERT INTO metric_snapshots
               (video_id,source_id,source_policy_version_id,observed_at,
                collected_at,views,likes,comments,shares,
                source_metric_definition,source_metric_version,
                precision,data_quality_status,observation_time_assumed)
               VALUES(%s,%s,%s,now()-interval '30 minutes',now(),
                      111,10,3,2,'platform_post_views','v2',
                      'exact','retracted',FALSE)""",
            (self.target[0], self.source, self.policy),
        )
        with self.assertRaises(ContractError) as e:
            self.derive(request, prior_id)
        self.assertEqual(e.exception.code, "metric_history_changed")

    def test_immutable_raw_peer_counter_cannot_be_rewritten(self):
        request, prior_id = self.approved()
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                "UPDATE metric_snapshots SET likes=123 WHERE id=%s",
                (self.peers[0][1],),
            )
        self.assertIsInstance(self.derive(request, prior_id), BayesianEngagementResult)

    def test_legacy_frozen_prior_mutation_blocked_at_database(self):
        request, prior_id = self.approved()
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                """UPDATE comparison_cohorts SET prior_like_rate=0.5
                   WHERE id=(SELECT legacy_cohort_id FROM group_c_cohort_freezes
                             WHERE id=%s)""",
                (prior_id,),
            )
        self.assertIsInstance(self.derive(request, prior_id), BayesianEngagementResult)

    def test_config_cannot_derive_after_global_supersession(self):
        request, prior_id = self.approved()
        newer = self.fixture.new_config()
        other = self.fixture.signed(config_id=newer, minimum_creator=1)
        self.fixture.activate(other, config_id=newer, replace=self.config)
        with self.assertRaises(ContractError) as e:
            self.derive(request, prior_id)
        self.assertEqual(e.exception.code, "config_not_activated")

    def test_replay_deterministic_and_no_calculated_metrics(self):
        request, prior_id = self.approved()
        a = self.derive(request, prior_id)
        b = self.derive(request, prior_id)
        self.assertEqual(a, b)
        self.assertEqual(len(a.evidence_sha256), 64)
        self.assertFalse(self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM calculated_metrics WHERE post_id=%s)",
            (self.target[0],),
        ).fetchone()[0])

    def test_expired_current_source_metadata_denied(self):
        request, prior_id = self.approved()
        self.conn.execute(
            "UPDATE source_video_metadata SET expires_at=now()-interval '1 second' WHERE id=%s",
            (self.target[2],),
        )
        with self.assertRaises(ContractError):
            self.derive(request, prior_id)


if __name__ == "__main__":
    unittest.main()
