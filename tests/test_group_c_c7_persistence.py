"""GC-MOD-20: exact C7 persistence, independent math and disposable PG17 QA.

Synthetic IAM in this file is a fixture ONLY, never a production connector.
"""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, localcontext
from types import SimpleNamespace
from uuid import uuid4

from intelligence.group_c.c7_persistence import (
    PERSISTED_C7_VERSION, C7PersistenceApprovalVerifier, PersistedC7Receipt,
    PostgreSQLC7Persistence, _calculate_rows, _evidence_fingerprint,
    _SQL_FIELDS,
)
from intelligence.group_c.contracts import ContractError
from intelligence.group_c.publication_time import validate_publication_time
from intelligence.group_c.pure_math import q

T=datetime(2026,10,20,12,0,tzinfo=timezone.utc)
PROJECT="a"*32


def gold_math():
    prior=SimpleNamespace(views=100,observed_at=T-timedelta(hours=4),
                          snapshot_id=10)
    current=SimpleNamespace(views=200,likes=20,comments=4,shares=2,
                            observed_at=T,snapshot_id=11)
    proof=validate_publication_time(
        dict(id=21,video_id=7,source_id="src",
             source_policy_version_id=5,
             published_at=T-timedelta(hours=12),
             fetched_at=T-timedelta(hours=2),
             expires_at=T+timedelta(days=2),is_current=True,deleted_at=None),
        video_id=7,metadata_id=21,source_id="src",
        source_policy_version_id=5,
        previous_snapshot_id=10,snapshot_id=11,
        previous_observed_at=prior.observed_at,observed_at=current.observed_at,
        as_of=T+timedelta(minutes=1))
    return dict(
        video_id=7,snapshot_id=11,metadata_id=21,previous_snapshot_id=10,
        config_id=33,previous=prior,current=current,proof=proof,
        subject=SimpleNamespace(alpha=Decimal(5),bayesian_k=Decimal(50),
                                subject_sha256="f"*64,calculation_version="reviewed-v1"),
        creator=dict(legacy_cohort_id=100,median_views=Decimal(100),
                     evidence_sha256="a"*64),
        prior=dict(legacy_cohort_id=101,evidence_sha256="b"*64),
        niche=None,p_l=Decimal(".1"),p_c=Decimal(".02"),p_s=Decimal(".01"),
    )


class TestC7PersistencePure(unittest.TestCase):
    def test_exact_stage07_27_field_gold_vector(self):
        from test_group_c_pure_math import GOLD
        vals=_calculate_rows(**gold_math())
        for key,value in GOLD.items():
            if key in vals:
                with self.subTest(field=key):
                    self.assertEqual(vals[key],Decimal(value))
        self.assertEqual(vals["calculation_status"],"complete")
        self.assertEqual(vals["score_status"],"complete")
        self.assertIsNone(vals["niche_cohort_id"])
        self.assertIsNone(vals["niche_outlier"])

    def test_complete_has_every_SQL_column_not_tempting_default(self):
        row=_calculate_rows(**gold_math())
        self.assertEqual(set(row),set(_SQL_FIELDS))
        self.assertEqual(len(_SQL_FIELDS),len(set(_SQL_FIELDS)))
        for col in ("previous_snapshot_id","published_metadata_id",
                    "creator_cohort_id","prior_cohort_id","k_used",
                    "score_creator_component","score_growth_component",
                    "score_engagement_component","score_freshness_component",
                    "breakout_score"):
            self.assertIsNotNone(row[col])

    def test_no_unreviewed_alpha_or_k_fallback(self):
        args=gold_math()
        args["subject"].alpha=None
        with self.assertRaises(ContractError):
            _calculate_rows(**args)
        args=gold_math()
        args["subject"].bayesian_k=None
        with self.assertRaises(ContractError):
            _calculate_rows(**args)

    def test_source_math_uses_observation_time_not_wall_clock(self):
        row=_calculate_rows(**gold_math())
        self.assertEqual(row["views_growth_per_hour"],Decimal("25.000000000000"))
        self.assertEqual(row["freshness_score"],Decimal("84.089641525371"))
        self.assertEqual(row["breakout_score"],Decimal("16.811535581108"))

    def test_context_precision_isolation(self):
        before=_calculate_rows(**gold_math())
        with localcontext() as ctx:
            ctx.prec=5
            ctx.rounding=ROUND_DOWN
            self.assertEqual(_calculate_rows(**gold_math()),before)

    def test_c7_does_not_guess_unknown_data(self):
        for change in ({"likes":None},{"comments":None},{"shares":None},
                       {"views":0},{"views":-1}):
            args=gold_math()
            for field,value in change.items():
                setattr(args["current"],field,value)
            with self.subTest(change=change),self.assertRaises(ContractError):
                _calculate_rows(**args)

    def test_immutable_receipt_and_exact_version(self):
        self.assertEqual(PERSISTED_C7_VERSION,"group-c-persisted-c7/v1")
        r=PersistedC7Receipt(
            calculated_metric_id=1,project_id=PROJECT,video_id=7,
            snapshot_id=11,previous_snapshot_id=10,published_metadata_id=21,
            config_id=33,creator_freeze_id=44,prior_freeze_id=55,
            niche_freeze_id=None,review_request_id="review",
            review_decision_id="decision",activation_event_id="activation",
            source_policy_version_id=5,metric_version="v1",
            breakout_score=Decimal("16.811535581108"),
            evidence_sha256="a"*64,inserted=True)
        self.assertEqual(r.status,"persisted_complete_not_published")
        with self.assertRaises(FrozenInstanceError):
            r.evidence_sha256="0"*64

    def test_fingerprint_binds_original_review_and_calculation(self):
        kwargs=gold_math()
        row=_calculate_rows(**kwargs)
        attrs=dict(
            project_id=PROJECT,source_id="src",policy_id=5,
            metric_version="v1",previous=kwargs["previous"],
            current=kwargs["current"],proof=kwargs["proof"],
            subject=kwargs["subject"],
            review=dict(id="review-1",decision_id="decision-1"),
            activation=dict(id="activation-1"),creator=kwargs["creator"],
            prior=kwargs["prior"],niche=None,row=row)
        a=_evidence_fingerprint(**attrs)
        self.assertEqual(a,_evidence_fingerprint(**attrs))
        self.assertEqual(len(a),64)
        changed={**attrs,"metric_version":"v2"}
        self.assertNotEqual(a,_evidence_fingerprint(**changed))
        changed={**attrs,"activation":dict(id="activation-2")}
        self.assertNotEqual(a,_evidence_fingerprint(**changed))

    def test_precision_and_niche_null_semantics(self):
        rows=_calculate_rows(**gold_math())
        self.assertEqual(rows["alpha_used"],q(Decimal(5)))
        self.assertEqual(rows["creator_cohort_id"],100)
        self.assertIsNone(rows["niche_cohort_id"])
        self.assertEqual(rows["prior_cohort_id"],101)

    def test_write_contract_is_explicit_not_default_authorized(self):
        from inspect import signature
        params=signature(PostgreSQLC7Persistence.persist).parameters
        self.assertIn("approval_verifier",params)
        self.assertIsNone(params["ports"].default)
        self.assertFalse(hasattr(PostgreSQLC7Persistence,"publish_to_analytics"))
        self.assertFalse(hasattr(PostgreSQLC7Persistence,"ack_qdp"))


class _TestOnlyC7IAM:
    def __init__(self,allowed=True):
        self.allowed=allowed
        self.calls=[]

    def verify_c7_persistence(self,conn,**kwargs):
        self.calls.append(kwargs)
        return self.allowed


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "Disposable PG17 only, CM_TEST_PG_DSN and GC_TEST_DISPOSABLE_DB=YES",
)
class TestC7PersistencePG17(unittest.TestCase):
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
        self.fixture=TestActivationPG17()
        self.fixture.conn=self.conn
        self.fixture.psycopg=self.psycopg
        self.fixture.setUp()
        for key in ("project","source","policy","target","config","peers"):
            setattr(self,key,getattr(self.fixture,key))
        self.previous=self.conn.execute(
            """INSERT INTO metric_snapshots (
                 video_id,source_id,source_policy_version_id,
                 observed_at,collected_at,views,likes,comments,shares,
                 source_metric_definition,source_metric_version,
                 precision,data_quality_status,observation_time_assumed)
               VALUES(%s,%s,%s,now()-interval '4 hours',
                      now()-interval '3 hours',80,8,2,1,
                      'platform_post_views','v1','exact','ok',FALSE)
               RETURNING id""",
            (self.target[0],self.source,self.policy),
        ).fetchone()[0]

    def tearDown(self):
        self.fixture.tearDown()

    def approved(self):
        request=self.fixture.signed()
        self.fixture.activate(request)
        return request

    def persist(self,request,**changes):
        args=dict(
            conn=self.conn,project_id=self.project,video_id=self.target[0],
            snapshot_id=self.target[1],previous_snapshot_id=self.previous,
            metadata_id=self.target[2],source_id=self.source,
            source_policy_version_id=self.policy,metric_version="v1",
            config_id=self.config,review_request_id=request.request_id,
            approval_verifier=_TestOnlyC7IAM(),
        )
        args.update(changes)
        return PostgreSQLC7Persistence().persist(**args)

    def test_complete_score_and_atomic_certificate(self):
        request=self.approved()
        result=self.persist(request)
        self.assertIsInstance(result,PersistedC7Receipt)
        self.assertTrue(result.inserted)
        self.assertEqual(result.score_status,"complete")
        row=self.conn.execute(
            """SELECT calculation_status,score_status,creator_ratio,
                      weighted_engagement_smoothed,freshness_score,
                      score_creator_component,score_growth_component,
                      score_engagement_component,score_freshness_component,
                      breakout_score,published_metadata_id,previous_snapshot_id,
                      prior_cohort_id FROM calculated_metrics WHERE id=%s""",
            (result.calculated_metric_id,),
        ).fetchone()
        self.assertEqual(row[0:2],("complete","complete"))
        self.assertEqual(row[9],sum(row[5:9],Decimal(0)))
        self.assertEqual(row[10:12],(self.target[2],self.previous))
        self.assertIsNotNone(row[12])
        saved=self.conn.execute(
            "SELECT evidence_sha256,review_request_id FROM group_c_calculation_receipts "
            "WHERE calculated_metric_id=%s",(result.calculated_metric_id,),
        ).fetchone()
        self.assertEqual(saved[0],result.evidence_sha256)
        self.assertEqual(str(saved[1]),request.request_id)

    def test_same_input_idempotent_no_second_score(self):
        request=self.approved()
        first=self.persist(request)
        again=self.persist(request)
        self.assertTrue(first.inserted)
        self.assertFalse(again.inserted)
        self.assertEqual(first.calculated_metric_id,again.calculated_metric_id)
        self.assertEqual(first.evidence_sha256,again.evidence_sha256)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM calculated_metrics WHERE snapshot_id=%s AND "
            "calculation_config_id=%s",(self.target[1],self.config),
        ).fetchone()[0],1)

    def test_missing_and_refusing_iam_fails_before_write(self):
        request=self.approved()
        for verifier in (None,_TestOnlyC7IAM(False),_TestOnlyC7IAM(1)):
            with self.subTest(verifier=verifier),self.assertRaises(ContractError):
                self.persist(request,approval_verifier=verifier)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM calculated_metrics WHERE snapshot_id=%s",
            (self.target[1],),
        ).fetchone()[0],0)

    def test_pending_review_has_no_complete_score(self):
        from intelligence.group_c.human_review import PostgreSQLHumanReview
        creator,prior=self.fixture.freeze()
        request=PostgreSQLHumanReview().create_request(
            self.conn,project_id=self.project,config_id=self.config,
            creator_freeze_id=creator.id,prior_freeze_id=prior.id,
            minimum_creator_posts=2,minimum_prior_posts=2,
        )
        with self.assertRaises(ContractError):
            self.persist(request)

    def test_source_revoked_after_approval_prevents_persistence(self):
        request=self.approved()
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.source,),
        )
        with self.assertRaises(ContractError):
            self.persist(request)

    def test_new_retracted_raw_observation_denied(self):
        request=self.approved()
        self.conn.execute(
            """INSERT INTO metric_snapshots(
                 video_id,source_id,source_policy_version_id,
                 observed_at,collected_at,views,likes,comments,shares,
                 source_metric_definition,source_metric_version,
                 precision,data_quality_status,observation_time_assumed)
               VALUES(%s,%s,%s,now()-interval '10 minutes',now(),
                      999,10,3,2,'platform_post_views','v2',
                      'exact','retracted',FALSE)""",
            (self.target[0],self.source,self.policy),
        )
        with self.assertRaises(ContractError) as e:
            self.persist(request)
        self.assertEqual(e.exception.code,"metric_history_changed")

    def test_wrong_source_version_video_or_policy_no_write(self):
        request=self.approved()
        for kw in (
            {"project_id":uuid4().hex},
            {"video_id":self.peers[0][0]},
            {"metric_version":"v2"},
            {"source_id":"other"},
            {"source_policy_version_id":999999},
            {"metadata_id":999999},
            {"snapshot_id":self.previous,"previous_snapshot_id":self.target[1]},
            {"config_id":999999},
        ):
            with self.subTest(kw=kw),self.assertRaises(ContractError):
                self.persist(request,**kw)

    def test_complete_immutable_and_certificate_append_only(self):
        request=self.approved()
        result=self.persist(request)
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                "UPDATE calculated_metrics SET breakout_score=123 WHERE id=%s",
                (result.calculated_metric_id,),
            )
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                "UPDATE group_c_calculation_receipts SET evidence_sha256=%s "
                "WHERE calculated_metric_id=%s",
                ("c"*64,result.calculated_metric_id),
            )
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                "DELETE FROM group_c_calculation_receipts WHERE calculated_metric_id=%s",
                (result.calculated_metric_id,),
            )

    def test_ledger_conflict_same_version_different_publication_refuses_reuse(self):
        request=self.approved()
        first=self.persist(request)
        # Metadata remains mutable in old D1 schema; a changed original
        # publication is NOT a reason to overwrite/reuse complete score.
        self.conn.execute(
            "UPDATE source_video_metadata SET published_at=published_at-interval '2 hours' "
            "WHERE id=%s",(self.target[2],),
        )
        with self.assertRaises(ContractError) as e:
            self.persist(request)
        self.assertEqual(e.exception.code,"c7_lineage_conflict")
        self.assertEqual(self.conn.execute(
            "SELECT id FROM calculated_metrics WHERE id=%s",(first.calculated_metric_id,),
        ).fetchone()[0],first.calculated_metric_id)

    def test_migration_hash_idempotency(self):
        import hashlib
        from pathlib import Path
        from intelligence import migrate
        migration=Path(__file__).resolve().parents[1]/"intelligence/migrations/060_group_c_calculation_receipts.sql"
        hashvalue=hashlib.sha256(migration.read_bytes()).hexdigest()
        self.assertEqual(self.conn.execute(
            "SELECT sha256 FROM schema_migrations WHERE version=%s",
            (migration.name,),
        ).fetchone()[0],hashvalue)
        self.assertEqual(migrate.apply_migrations(self.conn),[])


if __name__=="__main__":
    unittest.main()
