"""GC-MOD-24 retention/revocation: safe C7 reads, invalidation and erasure QA.

Disposable PG17-only fixtures; no production IAM mocks. Historic complete
scores must never surface as valid after target/peer source permissions expire.
"""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError
from uuid import uuid4

from intelligence.group_c.contracts import ContractError
from intelligence.group_c.retention import (
    RETENTION_VERSION,ERASURE_VERSION,REVOCABLE_CAUSES,
    RetentionRead,ScoreInvalidation,ErasureRequest,PostgreSQLScoreRetention,
    _uuid,_positive,_project,
)


class TestRetentionPure(unittest.TestCase):
    def test_project_scope_never_accepts_unverified_project(self):
        self.assertEqual(_project("a"*32),"a"*32)
        for value in ("abc","A"*32,"g"*32,None,False,1,["a"]):
            with self.subTest(value=value),self.assertRaises(ContractError):
                _project(value)

    def test_score_id_must_be_real_int_never_bool_or_string(self):
        self.assertEqual(_positive(31,"score_id"),31)
        for value in (0,-1,True,False,None,"31",3.14):
            with self.subTest(value=value),self.assertRaises(ContractError):
                _positive(value,"score_id")

    def test_erasure_authorization_uuid_exact_canonical(self):
        ident=str(uuid4())
        self.assertEqual(_uuid(ident),ident)
        for value in ("",None,22,ident.upper(),"{" + ident + "}"):
            with self.subTest(value=value),self.assertRaises(ContractError):
                _uuid(value)

    def test_retention_read_denied_never_includes_score(self):
        denied=RetentionRead("a"*32,4,"unavailable","metadata_expired",None,None)
        self.assertFalse(denied.valid)
        self.assertIsNone(denied.breakout_score)
        self.assertIsNone(denied.evidence_sha256)
        self.assertEqual(denied.version,RETENTION_VERSION)

    def test_true_current_score_is_explicit(self):
        from decimal import Decimal
        result=RetentionRead("a"*32,5,"valid_current",None,
                             Decimal("18.000000000000"),"b"*64)
        self.assertTrue(result.valid)

    def test_receipts_are_frozen_and_erasure_status_is_not_completed(self):
        inv=ScoreInvalidation(str(uuid4()),"a"*32,51,"metadata_expired","c"*64,False)
        self.assertEqual(inv.state,"invalidated")
        with self.assertRaises(FrozenInstanceError):
            inv.reason_code="new_reason"
        erasure=ErasureRequest(str(uuid4()),"a"*32,51,str(uuid4()),
                               "legal_erasure_required",False)
        self.assertEqual(erasure.state,"external_erasure_execution_required")
        self.assertEqual(erasure.version,ERASURE_VERSION)
        with self.assertRaises(FrozenInstanceError):
            erasure.state="completed"

    def test_revoke_codes_specific_not_superseded_by_new_snapshot(self):
        for key in ("metadata_expired","source_policy_expired",
                    "metric_snapshot_expired","source_policy_unavailable",
                    "review_cohort_incompatible","retention_peer_ineligible"):
            self.assertIn(key,REVOCABLE_CAUSES)
        self.assertNotIn("retention_version_superseded",REVOCABLE_CAUSES)
        self.assertNotIn("human_auth_unavailable",REVOCABLE_CAUSES)
        self.assertNotIn("score_not_found",REVOCABLE_CAUSES)

    def test_service_has_no_unreviewed_hard_delete_or_external_delivery(self):
        adapter=PostgreSQLScoreRetention()
        self.assertFalse(hasattr(adapter,"force_erase"))
        self.assertFalse(hasattr(adapter,"publish_analytics"))
        self.assertFalse(hasattr(adapter,"skip_source_policy"))


class _SyntheticErasureVerifier:
    """Explicitly test-only authority stub, forbidden in a real deployment."""
    def __init__(self,valid=True):
        self.valid=valid
        self.calls=0
    def verify_erasure_request(self,conn,*,project_id,calculated_metric_id,
                               authorization_event_id,reason_code,signed_assertion):
        self.calls+=1
        return self.valid and signed_assertion.startswith("synthetic-legal-erasure:")


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "Requires explicit disposable PG17 CM_TEST_PG_DSN and GC_TEST_DISPOSABLE_DB=YES",
)
class TestRetentionPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        TestDurableWorkerPG17.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        TestDurableWorkerPG17.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        self.fixture=TestDurableWorkerPG17()
        self.fixture.conn=self.conn
        self.fixture.psycopg=self.psycopg
        self.fixture.setUp()
        self.project=self.fixture.project
        self.reader=PostgreSQLScoreRetention()

    def tearDown(self):
        self.fixture.tearDown()

    def baseline(self):
        from test_group_c_durable_worker import _SyntheticC7IAM
        request=self.fixture.request()
        lease=self.fixture.claim()
        complete=self.fixture.worker.execute_score(
            self.conn,lease=lease,review_request_id=request.request_id,
            approval_verifier=_SyntheticC7IAM(),
        )
        self.metric_id=complete.score.calculated_metric_id
        return complete

    def read(self):
        return self.reader.read_current(
            self.conn,project_id=self.project,calculated_metric_id=self.metric_id,
        )

    def invalid(self):
        return self.reader.invalidate(
            self.conn,project_id=self.project,calculated_metric_id=self.metric_id,
        )

    def test_current_complete_score_visible_only_after_live_rights_check(self):
        completed=self.baseline()
        visible=self.read()
        self.assertTrue(visible.valid)
        self.assertEqual(visible.breakout_score,completed.score.breakout_score)
        self.assertEqual(visible.evidence_sha256,completed.score.evidence_sha256)
        self.assertIsNone(visible.reason_code)
        with self.assertRaises(ContractError) as e:
            self.invalid()
        self.assertEqual(e.exception.code,"score_still_valid")

    def test_revoked_source_blocks_read_and_persistent_invalidates(self):
        self.baseline()
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.fixture.source,),
        )
        denied=self.read()
        self.assertFalse(denied.valid)
        self.assertEqual(denied.reason_code,"source_policy_unavailable")
        self.assertIsNone(denied.breakout_score)
        event=self.invalid()
        self.assertEqual(event.reason_code,"source_policy_unavailable")
        self.assertFalse(event.replayed)
        again=self.invalid()
        self.assertEqual(event.id,again.id)
        self.assertTrue(again.replayed)
        # Restoring source rights must NOT silently revive an invalidated C7.
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=TRUE WHERE id=%s",
            (self.fixture.source,),
        )
        self.assertEqual(self.read().reason_code,"score_explicitly_invalidated")
        self.assertEqual(self.conn.execute(
            "SELECT score_status FROM calculated_metrics WHERE id=%s",
            (self.metric_id,),
        ).fetchone()[0],"complete")

    def test_expired_actual_snapshot_blocks_existing_complete_score(self):
        self.baseline()
        self.conn.execute(
            """UPDATE metric_snapshots
               SET expires_at=clock_timestamp()-interval '1 second'
               WHERE id=%s""",(self.fixture.target[1],),
        )
        denied=self.read()
        self.assertFalse(denied.valid)
        self.assertEqual(denied.reason_code,"metric_snapshot_expired")
        self.assertIsNone(denied.breakout_score)
        self.assertEqual(self.invalid().reason_code,"metric_snapshot_expired")

    def test_expired_peer_snapshot_blocks_prior_complete_score(self):
        self.baseline()
        peer=self.conn.execute(
            """SELECT m.snapshot_id FROM group_c_calculation_receipts r
               JOIN group_c_cohort_freeze_members m
                 ON m.freeze_id=r.creator_freeze_id
               WHERE r.calculated_metric_id=%s ORDER BY m.video_id
               LIMIT 1""",(self.metric_id,),
        ).fetchone()[0]
        self.conn.execute(
            "UPDATE metric_snapshots SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",(peer,),
        )
        denied=self.read()
        self.assertFalse(denied.valid)
        self.assertEqual(denied.reason_code,"metric_snapshot_expired")
        self.assertEqual(self.invalid().reason_code,"metric_snapshot_expired")

    def test_expired_publication_blocks_read_without_numeric_fallback(self):
        self.baseline()
        self.conn.execute(
            "UPDATE source_video_metadata SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",(self.fixture.target[2],),
        )
        denied=self.read()
        self.assertFalse(denied.valid)
        self.assertIn(denied.reason_code,("metadata_expired","publication_expired"))
        self.assertIsNone(denied.breakout_score)
        self.assertIsNotNone(self.invalid().id)

    def test_new_snapshot_supersedes_not_permanently_revokes_old_score(self):
        self.baseline()
        self.conn.execute(
            """INSERT INTO metric_snapshots(
                 video_id,source_id,source_policy_version_id,
                 observed_at,collected_at,views,likes,comments,shares,
                 source_metric_definition,source_metric_version,
                 precision,data_quality_status,observation_time_assumed)
               VALUES(%s,%s,%s,now()-interval '15 minute',
                      now()-interval '10 minute',180,19,5,4,
                      'platform_post_views','v1','exact','ok',FALSE)""",
            (self.fixture.target[0],self.fixture.source,self.fixture.policy),
        )
        old=self.read()
        self.assertFalse(old.valid)
        self.assertEqual(old.reason_code,"retention_version_superseded")
        with self.assertRaises(ContractError) as e:
            self.invalid()
        self.assertEqual(e.exception.code,"invalidation_not_proven")

    def test_cross_project_score_not_enumerable(self):
        self.baseline()
        with self.assertRaises(ContractError) as e:
            self.reader.read_current(
                self.conn,project_id=uuid4().hex,
                calculated_metric_id=self.metric_id,
            )
        self.assertEqual(e.exception.code,"score_not_found")
        with self.assertRaises(ContractError):
            self.reader.invalidate(
                self.conn,project_id=uuid4().hex,
                calculated_metric_id=self.metric_id,
            )

    def test_pending_outbox_cannot_be_leased_after_invalidation(self):
        complete=self.baseline()
        run_id=complete.lease.run_id
        event=self.conn.execute(
            """INSERT INTO group_c_outbox (
                 project_id,run_id,video_id,calculated_metric_id,
                 event_key,payload_sha256,payload)
               VALUES(%s,%s::uuid,%s,%s,%s,%s,%s::jsonb) RETURNING id""",
            (self.project,run_id,self.fixture.target[0],self.metric_id,
             "retention-test:"+uuid4().hex,"f"*64,'{"version":"test"}'),
        ).fetchone()[0]
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.fixture.source,),
        )
        self.invalid()
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_outbox WHERE id=%s",(event,),
        ).fetchone()[0],"failed")
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                """UPDATE group_c_outbox SET state='leased',
                          lease_token=%s::uuid,
                          lease_until=clock_timestamp()+interval '1 minute'
                   WHERE id=%s""",
                (str(uuid4()),event),
            )

    def test_erasure_requires_current_external_authority(self):
        self.baseline()
        event=str(uuid4())
        kwargs=dict(project_id=self.project,calculated_metric_id=self.metric_id,
                    authorization_event_id=event,
                    reason_code="legal_erasure_required",
                    signed_assertion="synthetic-legal-erasure:approved-testing")
        with self.assertRaises(ContractError):
            self.reader.request_erasure(self.conn,verifier=None,**kwargs)
        with self.assertRaises(ContractError):
            self.reader.request_erasure(
                self.conn,verifier=_SyntheticErasureVerifier(False),**kwargs)
        self.assertIsNone(self.conn.execute(
            "SELECT id FROM group_c_erasure_requests WHERE project_id=%s",
            (self.project,),
        ).fetchone())

    def test_authenticated_erasure_request_blocks_read_without_claiming_delete(self):
        self.baseline()
        eid=str(uuid4())
        kwargs=dict(project_id=self.project,calculated_metric_id=self.metric_id,
                    authorization_event_id=eid,
                    reason_code="legal_erasure_required",
                    signed_assertion="synthetic-legal-erasure:approved-testing")
        stamp=self.reader.request_erasure(
            self.conn,verifier=_SyntheticErasureVerifier(),**kwargs)
        self.assertEqual(stamp.state,"external_erasure_execution_required")
        self.assertFalse(stamp.replayed)
        self.assertEqual(self.read().reason_code,"score_erasure_requested")
        self.assertIsNone(self.read().breakout_score)
        self.assertTrue(self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM calculated_metrics WHERE id=%s)",
            (self.metric_id,),
        ).fetchone()[0])
        again=self.reader.request_erasure(
            self.conn,verifier=_SyntheticErasureVerifier(),**kwargs)
        self.assertTrue(again.replayed)
        self.assertEqual(again.id,stamp.id)
        with self.assertRaises(ContractError):
            self.reader.request_erasure(
                self.conn,verifier=_SyntheticErasureVerifier(),
                **{**kwargs,"authorization_event_id":str(uuid4())},
            )

    def test_erasure_request_blocks_new_analytics_delivery(self):
        complete=self.baseline()
        self.reader.request_erasure(
            self.conn,project_id=self.project,calculated_metric_id=self.metric_id,
            authorization_event_id=str(uuid4()),
            reason_code="retention_erase_required",
            signed_assertion="synthetic-legal-erasure:approved-testing",
            verifier=_SyntheticErasureVerifier(),
        )
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                """INSERT INTO group_c_outbox (
                     project_id,run_id,video_id,calculated_metric_id,
                     event_key,payload_sha256,payload)
                   VALUES(%s,%s::uuid,%s,%s,%s,%s,%s::jsonb)""",
                (self.project,complete.lease.run_id,
                 self.fixture.target[0],self.metric_id,
                 "erasure-test:"+uuid4().hex,"a"*64,'{"version":"test"}'),
            )

    def test_audit_rows_are_immutable(self):
        self.baseline()
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.fixture.source,),
        )
        audit=self.invalid()
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                "DELETE FROM group_c_score_invalidations WHERE id=%s",
                (audit.id,),
            )
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                "UPDATE group_c_score_invalidations SET reason_code='wrong' WHERE id=%s",
                (audit.id,),
            )

    def test_migration_checksum_and_idempotent_apply(self):
        import hashlib
        from pathlib import Path
        from intelligence import migrate
        path=Path(__file__).resolve().parents[1] / "intelligence/migrations/062_group_c_retention_revocation.sql"
        self.assertEqual(self.conn.execute(
            "SELECT sha256 FROM schema_migrations WHERE version=%s",
            (path.name,),
        ).fetchone()[0],hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(migrate.apply_migrations(self.conn),[])


if __name__=="__main__":
    unittest.main()
