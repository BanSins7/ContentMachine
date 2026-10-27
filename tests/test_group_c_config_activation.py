"""GC-MOD-12: explicit global C activation tests, with synthetic IAM only in DB QA.

The verifier here is an UNTRUSTED test fixture; no production provider or
human signatures are wired in this feature branch.
"""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import uuid4

from intelligence.group_c.config_activation import (
    ACTIVATION_VERSION, ConfigActivationIntent, ConfigActivationReceipt,
    PostgreSQLConfigActivation, validate_activation_intent,
)
from intelligence.group_c.contracts import ContractError

PROJECT="a"*32
REVIEW=str(uuid4())
ROOT=Path(__file__).resolve().parents[1]
_DEFAULT_ASSERTION=object()


def intent(**change):
    args=dict(project_id=PROJECT,review_request_id=REVIEW,
              expected_config_id=11,replace_active_config_id=None)
    args.update(change)
    return validate_activation_intent(**args)


class TestActivationPure(unittest.TestCase):
    def test_explicit_no_previous_config(self):
        i=intent()
        self.assertIsInstance(i,ConfigActivationIntent)
        self.assertEqual(i.schema_version,ACTIVATION_VERSION)
        self.assertIsNone(i.replace_active_config_id)
        with self.assertRaises(FrozenInstanceError):
            i.expected_config_id=10

    def test_explicit_replacement_has_different_id(self):
        self.assertEqual(intent(replace_active_config_id=10).replace_active_config_id,10)
        with self.assertRaises(ContractError) as e:
            intent(replace_active_config_id=11)
        self.assertEqual(e.exception.code,"invalid_activation_identifier")

    def test_strict_canonical_project(self):
        for val in (None,"A"*32,"a"*31,"z"*32,"a"*33,True):
            with self.subTest(val=val),self.assertRaises(ContractError):
                intent(project_id=val)

    def test_strict_review_uuid_no_guess_or_alias(self):
        for val in (None,"not-a-uuid",REVIEW.upper(),REVIEW.replace("-",""),123):
            with self.subTest(val=val),self.assertRaises(ContractError) as e:
                intent(review_request_id=val)
            self.assertEqual(e.exception.code,"missing_review_subject")

    def test_strict_positive_int_config_ids(self):
        for value in (None,0,-1,True,"11",1.5):
            with self.subTest(value=value),self.assertRaises(ContractError):
                intent(expected_config_id=value)
            with self.subTest(value=value),self.assertRaises(ContractError):
                intent(replace_active_config_id=value)

    def test_intent_stable_without_defaults(self):
        self.assertEqual(intent(),intent())
        self.assertNotEqual(intent(),intent(expected_config_id=20))
        self.assertNotEqual(intent(),intent(replace_active_config_id=30))

    def test_static_sql_same_transaction_audit(self):
        sql=(ROOT/"intelligence/migrations/059_group_c_config_activation.sql").read_text()
        self.assertIn("CREATE TABLE group_c_config_activation_events",sql)
        self.assertEqual(sql.count("CREATE TABLE "),1)
        self.assertEqual(sql.count("CREATE FUNCTION "),3)
        self.assertEqual(
            sql.count("CREATE TRIGGER ")+sql.count("CREATE CONSTRAINT TRIGGER "),4)
        self.assertEqual(sql.count("LANGUAGE plpgsql AS $$"),3)
        self.assertIn("CHECK (activation_assertion_sha256 ~ '^[0-9a-f]{64}$')",sql)
        self.assertIn("DEFAULT pg_current_xact_id()",sql)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED",sql)
        self.assertIn("AFTER UPDATE OF is_active ON metric_calculation_configs",sql)
        self.assertIn("AFTER INSERT ON metric_calculation_configs",sql)
        self.assertIn("BEFORE UPDATE OR DELETE ON group_c_config_activation_events",sql)
        self.assertIn("UNIQUE (review_request_id,kind,config_id)",sql)
        self.assertIn("uq_gc_global_activation_signature",sql)
        self.assertIn("activation_assertion_sha256",sql)
        self.assertNotIn("UPDATE calculated_metrics",sql)

    def test_activation_not_a_score(self):
        receipt=ConfigActivationReceipt(
            project_id=PROJECT,config_id=11,calculation_version="v1",
            review_request_id=REVIEW,review_decision_id=str(uuid4()),
            subject_sha256="f"*64,activation_event_id=str(uuid4()),
            previous_config_id=None,
        )
        self.assertEqual(receipt.status,"active")
        self.assertFalse(hasattr(receipt,"breakout_score"))
        self.assertFalse(hasattr(receipt,"score_status"))
        with self.assertRaises(FrozenInstanceError):
            receipt.config_id=12

    def test_no_default_approval_verifier(self):
        with self.assertRaises(ContractError) as e:
            PostgreSQLConfigActivation().activate(
                object(),project_id=PROJECT,review_request_id=REVIEW,
                expected_config_id=11,replace_active_config_id=None,
                signed_activation_assertion="synthetic-signed-global-config-activation-action",
                approval_verifier=None)
        self.assertEqual(e.exception.code,"transaction_contract_unavailable")


class _SyntheticIAMApproval:
    """Never install in production: synthetic test authorizer only."""
    def __init__(self,approved=True):
        self.approved=approved
        self.calls=0
    def verify_approval(self,conn,*,project_id,subject_sha256,decision_id,
                        reviewer_id,identity_provider,authentication_event_id,
                        attestation_sha256,signed_activation_assertion,capability,
                        activating_config_id,replacing_config_id):
        self.calls+=1
        return (self.approved is True
                and project_id and len(subject_sha256)==64
                and reviewer_id=="reviewer-d1"
                and identity_provider=="synthetic-test-idp"
                and authentication_event_id.startswith("iam-event-")
                and attestation_sha256=="9"*64
                and signed_activation_assertion.startswith(
                    "synthetic-global-action:"+str(activating_config_id)+":")
                and capability=="group_c.activate_global_config"
                and type(activating_config_id) is int and activating_config_id>0
                and (replacing_config_id is None or
                     (type(replacing_config_id) is int and replacing_config_id>0)))


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "requires disposable PostgreSQL17 / GC_TEST_DISPOSABLE_DB=YES",
)
class TestActivationPG17(unittest.TestCase):
    """Reuse *fixtures* of Stage11, not its tests, to verify real DB wiring."""
    @classmethod
    def setUpClass(cls):
        from test_group_c_human_review import TestHumanReviewPG17
        TestHumanReviewPG17.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from test_group_c_human_review import TestHumanReviewPG17
        TestHumanReviewPG17.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_human_review import TestHumanReviewPG17
        self._fixture=TestHumanReviewPG17()
        self._fixture.conn=self.conn
        self._fixture.psycopg=self.psycopg
        self._fixture.setUp()
        for key in ("project","source","policy","creator","target","peers","config"):
            setattr(self,key,getattr(self._fixture,key))

    def tearDown(self):
        # TEST-ONLY reset in a disposable DB. This administrative action is
        # expressly forbidden in production (it bypasses activation audit).
        if os.getenv("GC_TEST_DISPOSABLE_DB")!="YES":
            raise AssertionError("Unsafe activation test cleanup")
        self.conn.execute(
            "ALTER TABLE metric_calculation_configs DISABLE TRIGGER trg_gc_config_toggle_update")
        try:
            self.conn.execute(
                "UPDATE metric_calculation_configs SET is_active=FALSE WHERE is_active=TRUE")
        finally:
            self.conn.execute(
                "ALTER TABLE metric_calculation_configs ENABLE TRIGGER trg_gc_config_toggle_update")

    def freeze(self):
        return self._fixture.freeze()

    def signed(self, *, config_id=None, minimum_creator=2):
        from intelligence.group_c.human_review import PostgreSQLHumanReview
        from test_group_c_human_review import _TestVerifier
        c,p=self.freeze()
        req=PostgreSQLHumanReview().create_request(
            self.conn,project_id=self.project,
            config_id=self.config if config_id is None else config_id,
            creator_freeze_id=c.id,prior_freeze_id=p.id,
            minimum_creator_posts=minimum_creator,minimum_prior_posts=2,
        )
        signed=PostgreSQLHumanReview().record_decision(
            self.conn,request_id=req.request_id,decision="approved",
            signed_assertion="test-signed-human-assertion-token",
            note="I inspected the exact peer evidence for this version",
            reviewer_verifier=_TestVerifier(),
        )
        self.assertTrue(signed.approved)
        return req

    def activate(self,request,*,config_id=None,replace=None,verifier=None,
                 project_id=None,activation_assertion=_DEFAULT_ASSERTION):
        if activation_assertion is _DEFAULT_ASSERTION:
            activation_assertion=("synthetic-global-action:"
                                  +str(self.config if config_id is None else config_id)
                                  +":"+request.request_id)
        return PostgreSQLConfigActivation().activate(
            self.conn,project_id=self.project if project_id is None else project_id,
            review_request_id=request.request_id,
            expected_config_id=self.config if config_id is None else config_id,
            replace_active_config_id=replace,
            signed_activation_assertion=activation_assertion,
            approval_verifier=verifier if verifier is not None else _SyntheticIAMApproval(),
        )

    def new_config(self):
        return self.conn.execute(
            """INSERT INTO metric_calculation_configs
               (calculation_version,alpha,bayesian_k,is_active)
               VALUES(%s,6,50,FALSE) RETURNING id""",
            ("gc12-"+uuid4().hex,),
        ).fetchone()[0]

    def test_activation_requires_explicit_human_and_switch_is_idempotent(self):
        pending=self.signed()
        receipt=self.activate(pending)
        self.assertTrue(receipt.approved_review)
        self.assertEqual(receipt.status,"active")
        self.assertIsNone(receipt.previous_config_id)
        self.assertEqual(self.activate(pending),receipt)
        self.assertEqual(self.conn.execute(
            "SELECT id FROM metric_calculation_configs WHERE is_active=TRUE"
        ).fetchone()[0],self.config)
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM group_c_config_activation_events
                 WHERE review_request_id=%s AND kind='activated'""",
            (pending.request_id,),
        ).fetchone()[0],1)

    def test_without_trusted_approval_verifier_cannot_activate(self):
        pending=self.signed()
        with self.assertRaises(ContractError) as e:
            PostgreSQLConfigActivation().activate(
                self.conn,project_id=self.project,review_request_id=pending.request_id,
                expected_config_id=self.config,replace_active_config_id=None,
                signed_activation_assertion="synthetic-signed-global-config-activation-action",
                approval_verifier=None,
            )
        self.assertEqual(e.exception.code,"human_auth_unavailable")
        with self.assertRaises(ContractError):
            self.activate(pending,verifier=_SyntheticIAMApproval(False))
        self.assertFalse(self.conn.execute(
            "SELECT is_active FROM metric_calculation_configs WHERE id=%s",
            (self.config,),
        ).fetchone()[0])

    def test_project_review_without_separate_global_admin_signature_denied(self):
        pending=self.signed()
        for invalid in ("project-review-token-only", "", None, 123):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError) as err:
                self.activate(pending,activation_assertion=invalid)
            self.assertEqual(err.exception.code,"human_auth_unavailable")
        self.assertFalse(self.conn.execute(
            "SELECT is_active FROM metric_calculation_configs WHERE id=%s",
            (self.config,),
        ).fetchone()[0])

    def test_explicit_global_replacement_preserves_old_config_inputs(self):
        first=self.signed()
        self.activate(first)
        new=self.new_config()
        second=self.signed(config_id=new,minimum_creator=1)
        with self.assertRaises(ContractError) as e:
            self.activate(second,config_id=new)
        self.assertEqual(e.exception.code,"active_config_conflict")
        r=self.activate(second,config_id=new,replace=self.config)
        self.assertEqual(r.previous_config_id,self.config)
        self.assertEqual(self.conn.execute(
            "SELECT id FROM metric_calculation_configs WHERE is_active=TRUE"
        ).fetchone()[0],new)
        self.assertEqual(self.conn.execute(
            "SELECT alpha FROM metric_calculation_configs WHERE id=%s",
            (self.config,),
        ).fetchone()[0],6-1)
        self.assertFalse(self.conn.execute(
            "SELECT is_active FROM metric_calculation_configs WHERE id=%s",
            (self.config,),
        ).fetchone()[0])
        self.assertEqual(self.activate(second,config_id=new,replace=self.config),r)

    def test_reused_global_signature_rolls_back_switch_atomically(self):
        # Deliberately permissive *test-only* verifier demonstrates a second
        # independent SQL barrier against reusing the same signed action SHA.
        class UnsafeFixtureVerifier:
            def verify_approval(self,*args,**kwargs):
                return True
        reused="signed-global-config-action-replay-defense-12345"
        first=self.signed()
        self.activate(first,verifier=UnsafeFixtureVerifier(),
                      activation_assertion=reused)
        new=self.new_config()
        second=self.signed(config_id=new,minimum_creator=1)
        with self.assertRaises(self.psycopg.Error):
            self.activate(second,config_id=new,replace=self.config,
                          verifier=UnsafeFixtureVerifier(),
                          activation_assertion=reused)
        self.assertEqual(self.conn.execute(
            "SELECT id FROM metric_calculation_configs WHERE is_active=TRUE"
        ).fetchone()[0],self.config)
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM group_c_config_activation_events
               WHERE config_id=%s AND kind='activated'""",
            (new,),
        ).fetchone()[0],0)

    def test_historical_approval_cannot_reactivate_superseded_config(self):
        r1=self.signed()
        self.activate(r1)
        new=self.new_config()
        r2=self.signed(config_id=new,minimum_creator=1)
        self.activate(r2,config_id=new,replace=self.config)
        with self.assertRaises(ContractError) as e:
            self.activate(r1,replace=new)
        self.assertEqual(e.exception.code,"activation_superseded")

    def test_pending_or_rejected_review_denied(self):
        from intelligence.group_c.human_review import PostgreSQLHumanReview
        c,p=self.freeze()
        pending=PostgreSQLHumanReview().create_request(
            self.conn,project_id=self.project,config_id=self.config,
            creator_freeze_id=c.id,prior_freeze_id=p.id,
            minimum_creator_posts=2,minimum_prior_posts=2)
        with self.assertRaises(ContractError) as e:
            self.activate(pending)
        self.assertEqual(e.exception.code,"review_not_approved")

    def test_rights_revocation_blocks_even_approved_human_review(self):
        request=self.signed()
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.source,),
        )
        with self.assertRaises(ContractError):
            self.activate(request)
        self.assertFalse(self.conn.execute(
            "SELECT is_active FROM metric_calculation_configs WHERE id=%s",
            (self.config,),
        ).fetchone()[0])

    def test_config_id_and_project_must_match_review(self):
        r=self.signed()
        for args in ({"config_id":self.new_config()},
                     {"project_id":uuid4().hex}):
            with self.subTest(args=args),self.assertRaises(ContractError):
                self.activate(r,**args)

    def test_direct_config_toggle_without_same_tx_event_denied_by_sql(self):
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                "UPDATE metric_calculation_configs SET is_active=TRUE WHERE id=%s",
                (self.config,),
            )
        self.assertFalse(self.conn.execute(
            "SELECT is_active FROM metric_calculation_configs WHERE id=%s",
            (self.config,),
        ).fetchone()[0])

    def test_direct_active_insert_without_event_denied_by_sql(self):
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                """INSERT INTO metric_calculation_configs
                   (calculation_version,alpha,bayesian_k,is_active)
                   VALUES(%s,5,50,TRUE)""",
                ("gc12-"+uuid4().hex,),
            )

    def test_activation_event_is_append_only(self):
        r=self.signed()
        receipt=self.activate(r)
        for sql in (
            "DELETE FROM group_c_config_activation_events WHERE id=%s",
            """UPDATE group_c_config_activation_events
               SET kind='activated' WHERE id=%s""",
        ):
            with self.subTest(sql=sql),self.assertRaises(self.psycopg.Error):
                self.conn.execute(sql,(receipt.activation_event_id,))

    def test_read_approval_is_historical_evidence_not_score(self):
        request=self.signed()
        self.activate(request)
        status=PostgreSQLConfigActivation().read_approval(
            self.conn,project_id=self.project,config_id=self.config,
        )
        self.assertTrue(status["currently_active"])
        self.assertNotIn("score_status",status)
        self.assertNotIn("breakout_score",status)

    def test_migration_checksums_and_reentry(self):
        from intelligence import migrate
        import hashlib
        path=ROOT/"intelligence/migrations/059_group_c_config_activation.sql"
        got=self.conn.execute(
            "SELECT sha256 FROM schema_migrations WHERE version=%s",
            (path.name,),
        ).fetchone()[0]
        self.assertEqual(got,hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(migrate.apply_migrations(self.conn),[])


if __name__=="__main__":
    unittest.main()
