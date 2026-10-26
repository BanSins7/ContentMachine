"""GC-MOD-11: pinned human review contract, IAM fail-closed and PG17 decision audit.

The demo verifier in this test file is NOT a production authentication system:
in production a separate trusted connector must check signatures, project ACL,
human identity, event replay and exact request/decision binding.
"""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from intelligence.group_c.contracts import ContractError
from intelligence.group_c.human_review import (
    REVIEW_SCHEMA_VERSION, HumanReviewSubject, VerifiedHumanReviewer,
    PostgreSQLHumanReview, build_human_review_subject,
)
from intelligence.group_c.cohort_rules import CohortRules
from intelligence.group_c.cohort_freeze import PostgreSQLCohortFreezeWriter

SHA_A, SHA_B, SHA_C = "a"*64,"b"*64,"c"*64
PROJECT = "f"*32


def config(**update):
    row=dict(
        id=55,calculation_version="review-v1",
        alpha=Decimal("5.000000000000"),
        bayesian_k=Decimal("50.000000000000"),
        is_active=False,normalization_enabled=False,
        freshness_half_life_h=Decimal("48"),
        weights_json={"creator_ratio":0.45,"views_growth_per_hour":0.30,
                      "weighted_engagement_smoothed":0.15,"freshness_score":0.10},
    )
    row.update(update)
    return row


def freeze(purpose="creator", **update):
    row=dict(
        id={"creator":11,"niche":12,"engagement_prior":13}[purpose],
        legacy_cohort_id={"creator":111,"niche":112,"engagement_prior":113}[purpose],
        project_id=PROJECT,target_video_id=99,platform="youtube",content_type="video",
        purpose=purpose,source_id="fixture",source_policy_version_id=8,
        source_metric_definition="platform_post_views",source_metric_version="v3",
        member_count=5,state="frozen_unreviewed",
        rules_sha256=SHA_A,selection_sha256=SHA_B,evidence_sha256=SHA_C,
        median_views=Decimal("150.0") if purpose!="engagement_prior" else None,
        prior_like_rate=Decimal("0.1") if purpose=="engagement_prior" else None,
        prior_comment_rate=Decimal("0.02") if purpose=="engagement_prior" else None,
        prior_share_rate=Decimal("0.01") if purpose=="engagement_prior" else None,
    )
    row.update(update)
    return row


def subject(*, cfg=None, creator=None, prior=None, niche=None,
            minimum_creator_posts=3,minimum_prior_posts=3,
            minimum_niche_posts=None,project_id=PROJECT):
    return build_human_review_subject(
        cfg if cfg is not None else config(),
        creator if creator is not None else freeze(),
        prior if prior is not None else freeze("engagement_prior"),
        niche,project_id=project_id,
        minimum_creator_posts=minimum_creator_posts,
        minimum_prior_posts=minimum_prior_posts,
        minimum_niche_posts=minimum_niche_posts,
    )


class TestHumanReviewPure(unittest.TestCase):
    def test_exact_frozen_review_subject_is_pending_only(self):
        r=subject()
        self.assertIsInstance(r,HumanReviewSubject)
        self.assertEqual(r.schema_version,REVIEW_SCHEMA_VERSION)
        self.assertEqual(r.creator_freeze_id,11)
        self.assertEqual(r.prior_freeze_id,13)
        self.assertEqual(r.niche_freeze_id,None)
        self.assertEqual(r.alpha,Decimal(5))
        self.assertEqual(r.bayesian_k,Decimal(50))
        self.assertEqual(r.payload["metric_version"],"v3")
        self.assertNotIn("decision",r.payload)
        self.assertNotIn("approved",r.payload)
        with self.assertRaises(FrozenInstanceError):
            r.alpha=Decimal(10)

    def test_same_evidence_replay_has_same_sha(self):
        self.assertEqual(subject().subject_sha256,subject().subject_sha256)
        self.assertEqual(len(subject().subject_sha256),64)
        self.assertNotEqual(subject(cfg=config(alpha=Decimal(6))).subject_sha256,
                            subject().subject_sha256)

    def test_cohort_payload_change_does_not_inherit_approval(self):
        baseline=subject()
        for changed in (
            subject(creator=freeze(evidence_sha256="d"*64)),
            subject(prior=freeze("engagement_prior",member_count=6)),
            subject(minimum_creator_posts=4),
            subject(cfg=config(calculation_version="review-v2")),
            subject(cfg=config(bayesian_k=Decimal(51))),
        ):
            self.assertNotEqual(changed.subject_sha256,baseline.subject_sha256)

    def test_explicit_niche_needs_reviewed_threshold_and_hash(self):
        with self.assertRaises(ContractError):
            subject(niche=freeze("niche"))
        with self.assertRaises(ContractError):
            subject(minimum_niche_posts=1)
        r=subject(niche=freeze("niche"),minimum_niche_posts=2)
        self.assertEqual(r.niche_freeze_id,12)
        self.assertEqual(len(r.payload["frozen_cohorts"]),3)
        self.assertNotEqual(r.subject_sha256,subject().subject_sha256)

    def test_cross_project_and_platform_mismatch_fail(self):
        for spec in (
            {"creator":freeze(project_id="e"*32)},
            {"prior":freeze("engagement_prior",platform="tiktok")},
            {"prior":freeze("engagement_prior",content_type="carousel")},
            {"prior":freeze("engagement_prior",target_video_id=100)},
            {"prior":freeze("engagement_prior",source_metric_version="v4")},
            {"prior":freeze("engagement_prior",source_policy_version_id=9)},
        ):
            with self.subTest(spec=spec),self.assertRaises(ContractError):
                subject(**spec)

    def test_frozen_unreviewed_required_not_legacy_approval(self):
        for row in (
            freeze(state="approved"),
            freeze(state="unfrozen"),
            freeze(source_metric_definition="reach"),
        ):
            with self.assertRaises(ContractError):
                subject(creator=row)

    def test_no_unknown_zero_or_negative_alpha_k(self):
        for cfg in (
            config(alpha=None),config(alpha=0),config(alpha=-1),
            config(bayesian_k=None),config(bayesian_k=0),
            config(alpha=True),config(bayesian_k=float("nan")),
            config(alpha="1e100"),config(alpha="5.00000000000001"),
        ):
            with self.subTest(cfg=cfg),self.assertRaises(ContractError):
                subject(cfg=cfg)

    def test_unreviewed_config_never_inherits_active_flag(self):
        for cfg in (
            config(is_active=True),config(is_active=None),
            config(normalization_enabled=True),
            config(freshness_half_life_h=Decimal(24)),
            config(weights_json={"creator_ratio":0.5}),
            config(weights_json={**config()["weights_json"],"creator_ratio":0.46}),
            config(calculation_version=None),
        ):
            with self.subTest(cfg=cfg),self.assertRaises(ContractError):
                subject(cfg=cfg)

    def test_no_implicit_cohort_minimums(self):
        for n in (0,None,True,501,1.5):
            with self.subTest(n=n),self.assertRaises(ContractError):
                subject(minimum_creator_posts=n)
        with self.assertRaises(ContractError) as e:
            subject(minimum_prior_posts=6)
        self.assertEqual(e.exception.code,"review_cohort_too_small")

    def test_noncanonical_fingerprint_rejected(self):
        for changed in (
            {"rules_sha256":"r"*64},
            {"evidence_sha256":None},
            {"selection_sha256":"ABC"*21+"D"},
        ):
            with self.subTest(changed=changed),self.assertRaises(ContractError):
                subject(creator=freeze(**changed))

    def test_no_human_review_authority_is_built_in(self):
        reviewer=PostgreSQLHumanReview()
        self.assertFalse(hasattr(reviewer,"autoapprove"))
        with self.assertRaises(ContractError) as e:
            reviewer.record_decision(
                object(),request_id="not-a-uuid",decision="approved",
                signed_assertion="verified-token-should-be-long",
                note="Reviewer explicitly inspected the sample",
                reviewer_verifier=None,
            )
        self.assertEqual(e.exception.code,"transaction_contract_unavailable")

    def test_human_review_sql_append_only(self):
        from pathlib import Path
        sql=(Path(__file__).resolve().parents[1]/
             "intelligence/migrations/058_group_c_human_reviews.sql").read_text()
        self.assertIn("CREATE TABLE group_c_review_requests",sql)
        self.assertIn("CREATE TABLE group_c_review_decisions",sql)
        self.assertIn("BEFORE UPDATE OR DELETE ON group_c_review_requests",sql)
        self.assertIn("BEFORE UPDATE OR DELETE ON group_c_review_decisions",sql)
        self.assertIn("UNIQUE(identity_provider,authentication_event_id)",sql)
        self.assertIn("UNIQUE(project_id,subject_sha256)",sql)
        self.assertIn("human review needs exact inactive c config",sql.lower())
        self.assertNotIn("UPDATE metric_calculation_configs",sql)


class _TestVerifier:
    """Only synthetic fixture: never wire into a production worker."""
    def __init__(self):
        self.calls=0
        self.event_id="iam-event-"+uuid4().hex
    def verify(self,conn,*,signed_assertion,project_id,subject_sha256,decision):
        self.calls+=1
        if signed_assertion!="test-signed-human-assertion-token" \
                or project_id is None or len(subject_sha256)!=64 \
                or decision not in ("approved","rejected"):
            raise ContractError("human_auth_unavailable","Signature not valid")
        return VerifiedHumanReviewer(
            reviewer_id="reviewer-d1",identity_provider="synthetic-test-idp",
            authentication_event_id=self.event_id,
            attestation_sha256="9"*64,
        )


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "Requires disposable PostgreSQL17 and GC_TEST_DISPOSABLE_DB=YES",
)
class TestHumanReviewPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.psycopg=psycopg
        cls.conn=psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True)
        if int(cls.conn.execute("SHOW server_version_num").fetchone()[0])//10000 !=17:
            raise AssertionError("Only disposable PostgreSQL17")
        migrate.apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.project=uuid4().hex
        self.source="gc11-"+uuid4().hex
        self.conn.execute("""INSERT INTO project_registry
          (id,name,profile_revision,profile_hash,created_at,updated_at)
          VALUES(%s,'GC11 fixture',1,%s,now(),now())""",(self.project,"a"*64))
        self.conn.execute("INSERT INTO sre_workspaces(project_id) VALUES(%s)",(self.project,))
        self.conn.execute("""INSERT INTO sources
          (id,provider,source_type,status,can_store_metadata,
           can_store_metrics,can_derive_scores,retention_days)
          VALUES(%s,'fixture','manual','enabled',TRUE,TRUE,TRUE,30)""",(self.source,))
        self.policy=self.conn.execute("""INSERT INTO source_policy_versions
          (source_id,version,is_active,permissions,retention_policy)
          VALUES(%s,1,TRUE,%s::jsonb,'{"retention_days":30}'::jsonb)
          RETURNING id""",(self.source,
          '{"can_store_metadata":true,"can_store_metrics":true,"can_derive_scores":true}')
        ).fetchone()[0]
        self.creator=self.conn.execute("""INSERT INTO creators(platform,platform_creator_id)
          VALUES('youtube',%s) RETURNING id""",(uuid4().hex,)).fetchone()[0]
        self.target=self.video(100)
        self.peers=[self.video(100),self.video(300)]
        self.config=self.conn.execute("""INSERT INTO metric_calculation_configs
          (calculation_version,alpha,bayesian_k,is_active)
          VALUES(%s,5,50,FALSE) RETURNING id""",("gc11-"+uuid4().hex,)).fetchone()[0]

    def video(self,views):
        token=uuid4().hex
        vid=self.conn.execute("""INSERT INTO videos
          (platform,platform_video_id,canonical_url,content_kind,c_content_type,creator_id)
          VALUES('youtube',%s,%s,'video','video',%s) RETURNING id""",
          (token,"https://youtube.com/watch?v="+token,self.creator)).fetchone()[0]
        self.conn.execute("INSERT INTO sre_project_videos(project_id,video_id) VALUES(%s,%s)",
                          (self.project,vid))
        md=self.conn.execute("""INSERT INTO source_video_metadata
          (video_id,source_id,source_policy_version_id,fetched_at,published_at)
          VALUES(%s,%s,%s,now()-interval '3 hour',
                 now()-interval '5 day') RETURNING id""",
          (vid,self.source,self.policy)).fetchone()[0]
        sid=self.conn.execute("""INSERT INTO metric_snapshots
          (video_id,source_id,source_policy_version_id,observed_at,collected_at,
           views,likes,comments,shares,source_metric_definition,source_metric_version,
           precision,data_quality_status,observation_time_assumed)
          VALUES(%s,%s,%s,now()-interval '2 hour',now()-interval '1 hour',
                 %s,10,3,2,'platform_post_views','v1','exact','ok',FALSE)
          RETURNING id""",(vid,self.source,self.policy,views)).fetchone()[0]
        return vid,sid,md

    def rule(self,purpose):
        at=datetime.now(timezone.utc)-timedelta(minutes=1)
        return CohortRules(
            project_id=self.project,target_video_id=self.target[0],
            purpose=purpose,platform="youtube",content_type="video",
            source_id=self.source,source_policy_version_id=self.policy,
            metric_definition="platform_post_views",metric_version="v1",
            evaluated_as_of=at,published_from=at-timedelta(days=9),
            published_before=at-timedelta(days=1),
            min_age_hours=Decimal(0),max_age_hours=Decimal(168),
            creator_id=self.creator,topic_id=None,max_candidates=10)

    def freeze(self):
        writer=PostgreSQLCohortFreezeWriter()
        c=writer.freeze(self.conn,rules=self.rule("creator"),
                        target_snapshot_id=self.target[1],
                        target_metadata_id=self.target[2])
        p=writer.freeze(self.conn,rules=self.rule("engagement_prior"),
                        target_snapshot_id=self.target[1],
                        target_metadata_id=self.target[2])
        return c,p

    def create(self,*, min_creator=2,min_prior=2,other_project=None):
        c,p=self.freeze()
        result=PostgreSQLHumanReview().create_request(
            self.conn,project_id=other_project or self.project,
            config_id=self.config,creator_freeze_id=c.id,
            prior_freeze_id=p.id,minimum_creator_posts=min_creator,
            minimum_prior_posts=min_prior)
        return result

    def decision(self,request,*,choice="approved",verifier=None,note=None,assertion=None):
        return PostgreSQLHumanReview().record_decision(
            self.conn,request_id=request.request_id,decision=choice,
            signed_assertion=assertion or "test-signed-human-assertion-token",
            note=note or "I checked the exact reviewed cohort and policy",
            reviewer_verifier=verifier,
        )

    def test_pending_request_does_not_activate_config(self):
        pending=self.create()
        self.assertEqual(pending.status,"pending_human_review")
        self.assertFalse(pending.approved)
        self.assertEqual(PostgreSQLHumanReview().read_status(
            self.conn,request_id=pending.request_id),pending)
        self.assertFalse(self.conn.execute(
            "SELECT is_active FROM metric_calculation_configs WHERE id=%s",
            (self.config,)).fetchone()[0])

    def test_external_verified_human_decision_and_exact_replay(self):
        pending=self.create()
        verifier=_TestVerifier()
        approved=self.decision(pending,verifier=verifier)
        self.assertEqual(approved.status,"approved")
        self.assertTrue(approved.approved)
        self.assertEqual(PostgreSQLHumanReview().read_status(
            self.conn,request_id=pending.request_id),approved)
        self.assertEqual(self.decision(pending,verifier=verifier),approved)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM group_c_review_decisions WHERE request_id=%s",
            (pending.request_id,)).fetchone()[0],1)

    def test_missing_verified_identity_cannot_approve(self):
        request=self.create()
        with self.assertRaises(ContractError) as err:
            self.decision(request,verifier=None)
        self.assertEqual(err.exception.code,"human_auth_unavailable")
        with self.assertRaises(ContractError) as err:
            self.decision(request,verifier=_TestVerifier(),assertion="invalid-signed-assertion")
        self.assertEqual(err.exception.code,"human_auth_unavailable")
        self.assertEqual(PostgreSQLHumanReview().read_status(
            self.conn,request_id=request.request_id).status,"pending_human_review")

    def test_rejection_is_terminal_and_immutable(self):
        pending=self.create()
        reviewer=_TestVerifier()
        rejected=self.decision(pending,choice="rejected",verifier=reviewer)
        self.assertFalse(rejected.approved)
        with self.assertRaises(ContractError) as err:
            self.decision(pending,choice="approved",verifier=reviewer)
        self.assertEqual(err.exception.code,"review_decision_conflict")
        for sql,ident in (
            ("UPDATE group_c_review_decisions SET reviewer_note='changed' WHERE id=%s",
             rejected.decision_id),
            ("DELETE FROM group_c_review_decisions WHERE id=%s",rejected.decision_id),
            ("DELETE FROM group_c_review_requests WHERE id=%s",pending.request_id),
        ):
            with self.subTest(sql=sql),self.assertRaises(self.psycopg.Error):
                self.conn.execute(sql,(ident,))

    def test_exact_review_subject_changes_with_minimums(self):
        c,p=self.freeze()
        review=PostgreSQLHumanReview()
        kwargs=dict(
            conn=self.conn,project_id=self.project,config_id=self.config,
            creator_freeze_id=c.id,prior_freeze_id=p.id,
            minimum_prior_posts=2,
        )
        first=review.create_request(**kwargs,minimum_creator_posts=1)
        second=review.create_request(**kwargs,minimum_creator_posts=2)
        self.assertNotEqual(first.subject_sha256,second.subject_sha256)
        self.assertEqual(review.create_request(**kwargs,minimum_creator_posts=1),
                         first)

    def test_source_revocation_and_wrong_project_block_request(self):
        c,p=self.freeze()
        review=PostgreSQLHumanReview()
        for project in (uuid4().hex,):
            with self.assertRaises(ContractError):
                review.create_request(
                    self.conn,project_id=project,config_id=self.config,
                    creator_freeze_id=c.id,prior_freeze_id=p.id,
                    minimum_creator_posts=2,minimum_prior_posts=2)
        self.conn.execute("UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
                          (self.source,))
        with self.assertRaises(ContractError):
            review.create_request(
                self.conn,project_id=self.project,config_id=self.config,
                creator_freeze_id=c.id,prior_freeze_id=p.id,
                minimum_creator_posts=2,minimum_prior_posts=2)

    def test_policy_revoked_after_request_blocks_later_approval(self):
        pending=self.create()
        self.conn.execute("UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
                          (self.source,))
        with self.assertRaises(ContractError):
            self.decision(pending,verifier=_TestVerifier())
        status=PostgreSQLHumanReview().read_status(
            self.conn,request_id=pending.request_id)
        self.assertEqual(status.status,"pending_human_review")
        self.assertFalse(status.approved)

    def test_config_change_creates_new_version_and_exact_subject(self):
        c,p=self.freeze()
        review=PostgreSQLHumanReview()
        original=review.create_request(
            self.conn,project_id=self.project,config_id=self.config,
            creator_freeze_id=c.id,prior_freeze_id=p.id,
            minimum_creator_posts=2,minimum_prior_posts=2)
        changed=self.conn.execute("""INSERT INTO metric_calculation_configs
          (calculation_version,alpha,bayesian_k,is_active)
          VALUES(%s,7,50,FALSE) RETURNING id""",
          ("gc11-"+uuid4().hex,)).fetchone()[0]
        later=review.create_request(
            self.conn,project_id=self.project,config_id=changed,
            creator_freeze_id=c.id,prior_freeze_id=p.id,
            minimum_creator_posts=2,minimum_prior_posts=2)
        self.assertNotEqual(original.subject_sha256,later.subject_sha256)
        self.assertEqual(review.read_status(self.conn,request_id=later.request_id).status,
                         "pending_human_review")

    def test_unreviewed_legacy_cohort_alone_cannot_be_reviewed(self):
        c,p=self.freeze()
        with self.assertRaises(ContractError):
            PostgreSQLHumanReview().create_request(
                self.conn,project_id=self.project,config_id=self.config,
                creator_freeze_id=c.legacy_cohort_id,
                prior_freeze_id=p.legacy_cohort_id,
                minimum_creator_posts=1,minimum_prior_posts=1)

    def test_upgrade_checksum_and_no_sql_reentry(self):
        from intelligence import migrate
        import hashlib
        from pathlib import Path
        path=Path(__file__).resolve().parents[1]/"intelligence/migrations/058_group_c_human_reviews.sql"
        expected=hashlib.sha256(path.read_bytes()).hexdigest()
        actual=self.conn.execute(
            "SELECT sha256 FROM schema_migrations WHERE version=%s",
            (path.name,)).fetchone()[0]
        self.assertEqual(actual,expected)
        self.assertEqual(migrate.apply_migrations(self.conn),[])


if __name__=="__main__":
    unittest.main()
