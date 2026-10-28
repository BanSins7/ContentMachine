"""GC25: persisted C7 Analytics handoff (no second scorer).

Disposable PostgreSQL17 tests use a same-DB transactional synthetic sink,
not an actual Unified Analytics branch implementation.
"""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from uuid import uuid4

from intelligence.group_c.analytics_bridge import (
    ANALYTICS_HANDOFF_VERSION, OUTBOX_EVENT_TYPE,
    PostgreSQLAnalyticsBridge, PersistedC7Handoff,
    _event_payload, _validate_outbox_payload,
)
from intelligence.group_c.contracts import ContractError,canonical_hash
from intelligence.group_c.d1 import local_d1_ports
from intelligence.group_c.ports import (
    GroupCPorts, IntegrationCapability, CapabilityState,
)


class TestAnalyticsBridgePure(unittest.TestCase):
    def test_handoff_never_accepts_float_or_guessed_score(self):
        valid=dict(
            project_id="a"*32,run_id=str(uuid4()),video_id=7,
            calculated_metric_id=8,snapshot_id=22,previous_snapshot_id=21,
            published_metadata_id=44,calculation_config_id=5,
            source_id="src",source_policy_version_id=2,
            metric_definition="platform_post_views",metric_version="v1",
            creator_cohort_id=2,prior_cohort_id=3,niche_cohort_id=None,
            creator_freeze_id=11,prior_freeze_id=12,niche_freeze_id=None,
            review_request_id=str(uuid4()),review_decision_id=str(uuid4()),
            activation_event_id=str(uuid4()),evidence_sha256="b"*64,
            breakout_score=Decimal("10.000000000000"),
            score_creator_component=Decimal("1.000000000000"),
            score_growth_component=Decimal("2.000000000000"),
            score_engagement_component=Decimal("3.000000000000"),
            score_freshness_component=Decimal("4.000000000000"),
        )
        h=PersistedC7Handoff(**valid)
        self.assertEqual(h.version,ANALYTICS_HANDOFF_VERSION)
        self.assertEqual(h.breakout_score,Decimal("10.000000000000"))
        with self.assertRaises(FrozenInstanceError):
            h.breakout_score=Decimal(0)
        for bad in (
            {"breakout_score":10.0},
            {"score_creator_component":1.0},
            {"breakout_score":Decimal(9)},
            {"metric_definition":"likes"},
            {"project_id":"B"*32},
            {"niche_cohort_id":True},
        ):
            with self.subTest(bad=bad),self.assertRaises(ContractError):
                PersistedC7Handoff(**{**valid,**bad})

    def test_baseline_payload_is_actual_four_components_and_sha(self):
        from decimal import Decimal
        h=PersistedC7Handoff(
            project_id="a"*32,run_id=str(uuid4()),video_id=10,
            calculated_metric_id=20,snapshot_id=101,previous_snapshot_id=100,
            published_metadata_id=200,calculation_config_id=300,
            source_id="policy-src",source_policy_version_id=400,
            metric_definition="platform_post_views",metric_version="v2",
            creator_cohort_id=11,prior_cohort_id=12,niche_cohort_id=None,
            creator_freeze_id=21,prior_freeze_id=22,niche_freeze_id=None,
            review_request_id=str(uuid4()),review_decision_id=str(uuid4()),
            activation_event_id=str(uuid4()),evidence_sha256="f"*64,
            breakout_score=Decimal("32.000000000000"),
            score_creator_component=Decimal("12.000000000000"),
            score_growth_component=Decimal("10.000000000000"),
            score_engagement_component=Decimal("6.000000000000"),
            score_freshness_component=Decimal("4.000000000000"),
        )
        payload=_event_payload(h)
        self.assertEqual(payload["breakout_score"],"32.000000000000")
        self.assertEqual(payload["source_policy_version_id"],400)
        self.assertEqual(payload["creator_freeze_id"],21)
        self.assertEqual(payload["score_growth_component"],"10.000000000000")
        self.assertEqual(len(canonical_hash(payload)),64)
        row={**dict(project_id=h.project_id,run_id=h.run_id,
                    video_id=h.video_id,calculated_metric_id=h.calculated_metric_id,
                    event_type=OUTBOX_EVENT_TYPE),
             "payload":payload,"payload_sha256":canonical_hash(payload)}
        _validate_outbox_payload(row,h)
        with self.assertRaises(ContractError):
            _validate_outbox_payload({**row,"payload_sha256":"a"*64},h)
        with self.assertRaises(ContractError):
            _validate_outbox_payload({**row,"payload":{**payload,"snapshot_id":999}},h)

    def test_legacy_revision_event_validated_against_same_c7(self):
        h=PersistedC7Handoff(
            project_id="a"*32,run_id=str(uuid4()),video_id=7,
            calculated_metric_id=8,snapshot_id=23,previous_snapshot_id=22,
            published_metadata_id=30,calculation_config_id=2,
            source_id="src",source_policy_version_id=11,
            metric_definition="platform_post_views",metric_version="v1",
            creator_cohort_id=5,prior_cohort_id=6,niche_cohort_id=None,
            creator_freeze_id=5,prior_freeze_id=6,niche_freeze_id=None,
            review_request_id=str(uuid4()),review_decision_id=str(uuid4()),
            activation_event_id=str(uuid4()),evidence_sha256="a"*64,
            breakout_score=Decimal("4"),
            score_creator_component=Decimal("1"),
            score_growth_component=Decimal("1"),
            score_engagement_component=Decimal("1"),
            score_freshness_component=Decimal("1"),
        )
        payload=_event_payload(h)
        for field in ("creator_cohort_id","prior_cohort_id","niche_cohort_id",
                      "score_creator_component","score_growth_component",
                      "score_engagement_component","score_freshness_component"):
            del payload[field]
        payload["revision_kind"]="config_changed"
        payload["revision_id"]=str(uuid4())
        row=dict(project_id=h.project_id,run_id=h.run_id,
                 video_id=h.video_id,calculated_metric_id=h.calculated_metric_id,
                 event_type=OUTBOX_EVENT_TYPE,payload=payload,
                 payload_sha256=canonical_hash(payload))
        _validate_outbox_payload(row,h)

    def test_port_missing_remains_explicit_unavailable(self):
        p=local_d1_ports()
        self.assertIsNone(p.analytics)
        self.assertIsInstance(PostgreSQLAnalyticsBridge(),PostgreSQLAnalyticsBridge)


class _SyntheticSameDBAnalytics:
    """Test double writes only a durable exact ID/digest, never raw C math."""

    def __init__(self,invalid_ack=False):
        self.invalid_ack=invalid_ack
        self.calls=0

    def capability(self,conn):
        available=conn.execute(
            "SELECT to_regclass('public.gc25_disposable_analytics_receipts') IS NOT NULL"
        ).fetchone()[0]
        return IntegrationCapability(
            port="analytics",
            state=CapabilityState.AVAILABLE if available else CapabilityState.UNAVAILABLE,
            observed_schema="group-c-analytics-outbox/v1" if available else None,
            reason_code="disposable_same_db_sink",
        )

    def enqueue_persisted_result(self,conn,project_id,calculated_metric_id,
                                 idempotency_sha256):
        self.calls+=1
        h=PostgreSQLAnalyticsBridge().load_persisted(
            conn,project_id=project_id,calculated_metric_id=calculated_metric_id,
            transaction_managed=True,
        )
        if h.evidence_sha256 is None or h.breakout_score is None:
            raise ContractError("analytics_score_unverified","No complete SQL score")
        written=conn.execute(
            """INSERT INTO gc25_disposable_analytics_receipts(
                 project_id,calculated_metric_id,event_sha256,
                 original_score)
               VALUES(%s,%s,%s,%s)
               ON CONFLICT(project_id,event_sha256) DO NOTHING RETURNING id""",
            (project_id,calculated_metric_id,idempotency_sha256,h.breakout_score),
        ).fetchone()
        if written:
            new_id=written[0]
        else:
            old=conn.execute(
                """SELECT id,calculated_metric_id,original_score
                   FROM gc25_disposable_analytics_receipts
                   WHERE project_id=%s AND event_sha256=%s""",
                (project_id,idempotency_sha256),
            ).fetchone()
            if (old is None or old[1]!=calculated_metric_id
                    or old[2]!=h.breakout_score):
                raise ContractError("analytics_accepted_different_score","Idempotency conflict")
            new_id=old[0]
        return dict(
            accepted=True,project_id=project_id,
            calculated_metric_id=calculated_metric_id,
            idempotency_sha256="f"*64 if self.invalid_ack else idempotency_sha256,
            analytics_snapshot_id=new_id,
        )


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "GC25 disposable PG17 explicitly gated by CM_TEST_PG_DSN+GC_TEST_DISPOSABLE_DB=YES",
)
class TestAnalyticsBridgePG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        TestDurableWorkerPG17.setUpClass.__func__(cls)
        cls.conn.execute(
            """CREATE TABLE IF NOT EXISTS gc25_disposable_analytics_receipts(
                 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 project_id TEXT NOT NULL,
                 calculated_metric_id BIGINT NOT NULL,
                 event_sha256 TEXT NOT NULL,
                 original_score NUMERIC(30,12) NOT NULL,
                 UNIQUE(project_id,event_sha256))""",
        )

    @classmethod
    def tearDownClass(cls):
        cls.conn.execute("DROP TABLE IF EXISTS gc25_disposable_analytics_receipts")
        from test_group_c_durable_worker import TestDurableWorkerPG17
        TestDurableWorkerPG17.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        self.base=TestDurableWorkerPG17()
        self.base.conn=self.conn
        self.base.psycopg=self.psycopg
        self.base.setUp()
        self.project=self.base.project
        self.bridge=PostgreSQLAnalyticsBridge()

    def tearDown(self):
        self.base.tearDown()

    def completed(self):
        from test_group_c_durable_worker import _SyntheticC7IAM
        request=self.base.request()
        lease=self.base.claim()
        result=self.base.worker.execute_score(
            self.conn,lease=lease,review_request_id=request.request_id,
            approval_verifier=_SyntheticC7IAM(),
        )
        self.metric_id=result.score.calculated_metric_id
        self.event_id=result.analytics_outbox_id
        self.assertIsNotNone(self.event_id)
        return result

    def ports(self,sink=None):
        return replace(local_d1_ports(),analytics=sink or _SyntheticSameDBAnalytics())

    def count_receipts(self):
        return self.conn.execute(
            "SELECT count(*) FROM gc25_disposable_analytics_receipts WHERE project_id=%s",
            (self.project,),
        ).fetchone()[0]

    def test_original_C7_worker_creates_one_atomic_pending_event(self):
        done=self.completed()
        self.assertIsNone(done.revision_outbox_id)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM group_c_outbox WHERE project_id=%s",
            (self.project,),
        ).fetchone()[0],1)
        state=self.conn.execute(
            "SELECT state,payload_sha256,payload FROM group_c_outbox WHERE id=%s",
            (self.event_id,),
        ).fetchone()
        self.assertEqual(state[0],"pending")
        self.assertEqual(state[1],canonical_hash(state[2]))
        self.assertEqual(state[2]["calculated_metric_id"],self.metric_id)
        self.assertEqual(state[2]["score_creator_component"],
                         str(self.conn.execute(
                             "SELECT score_creator_component FROM calculated_metrics WHERE id=%s",
                             (self.metric_id,),
                         ).fetchone()[0]))

    def test_available_same_db_sink_acks_and_delivers_once(self):
        done=self.completed()
        sink=_SyntheticSameDBAnalytics()
        first=self.bridge.deliver_one(
            self.conn,project_id=self.project,ports=self.ports(sink),
        )
        self.assertEqual(first.state,"delivered")
        self.assertEqual(first.outbox_id,self.event_id)
        self.assertEqual(first.calculated_metric_id,self.metric_id)
        self.assertIsInstance(first.analytics_snapshot_id,int)
        self.assertEqual(sink.calls,1)
        self.assertEqual(self.count_receipts(),1)
        self.assertIsNone(self.bridge.deliver_one(
            self.conn,project_id=self.project,ports=self.ports(sink),
        ))
        self.assertEqual(sink.calls,1)
        row=self.conn.execute(
            "SELECT state,delivered_at,delivery_attempts FROM group_c_outbox WHERE id=%s",
            (self.event_id,),
        ).fetchone()
        self.assertEqual(row[0],"delivered")
        self.assertIsNotNone(row[1])
        self.assertEqual(row[2],1)
        self.assertEqual(self.conn.execute(
            """SELECT original_score FROM gc25_disposable_analytics_receipts
                 WHERE project_id=%s""",(self.project,),
        ).fetchone()[0],done.score.breakout_score)

    def test_unconnected_analytics_keeps_event_pending(self):
        self.completed()
        with self.assertRaises(ContractError) as e:
            self.bridge.deliver_one(
                self.conn,project_id=self.project,ports=local_d1_ports(),
            )
        self.assertEqual(e.exception.code,"dependency_unavailable")
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_outbox WHERE id=%s",(self.event_id,),
        ).fetchone()[0],"pending")

    def test_rejected_ack_rolls_back_analytics_and_outbox(self):
        self.completed()
        bad=_SyntheticSameDBAnalytics(invalid_ack=True)
        with self.assertRaises(ContractError) as e:
            self.bridge.deliver_one(
                self.conn,project_id=self.project,ports=self.ports(bad),
            )
        self.assertEqual(e.exception.code,"analytics_ack_invalid")
        self.assertEqual(self.count_receipts(),0)
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_outbox WHERE id=%s",(self.event_id,),
        ).fetchone()[0],"pending")

    def test_revoked_source_before_dispatch_blocks_numeric_and_delivery(self):
        self.completed()
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.base.source,),
        )
        result=self.bridge.deliver_one(
            self.conn,project_id=self.project,ports=self.ports(),
        )
        self.assertEqual(result.state,"blocked")
        self.assertEqual(result.reason_code,"source_policy_unavailable")
        self.assertIsNone(result.analytics_snapshot_id)
        self.assertEqual(self.count_receipts(),0)
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_outbox WHERE id=%s",(self.event_id,),
        ).fetchone()[0],"failed")

    def test_expired_cohort_peer_before_dispatch_blocks_analytics(self):
        self.completed()
        member=self.conn.execute(
            """SELECT m.snapshot_id FROM group_c_calculation_receipts r
               JOIN group_c_cohort_freeze_members m ON m.freeze_id=r.creator_freeze_id
               WHERE r.calculated_metric_id=%s LIMIT 1""",(self.metric_id,),
        ).fetchone()[0]
        self.conn.execute(
            "UPDATE metric_snapshots SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",(member,),
        )
        result=self.bridge.deliver_one(
            self.conn,project_id=self.project,ports=self.ports(),
        )
        self.assertEqual(result.state,"blocked")
        self.assertEqual(result.reason_code,"metric_snapshot_expired")
        self.assertEqual(self.count_receipts(),0)

    def test_cross_project_cannot_load_score_or_claim_event(self):
        self.completed()
        alien=uuid4().hex
        with self.assertRaises(ContractError):
            self.bridge.load_persisted(
                self.conn,project_id=alien,calculated_metric_id=self.metric_id,
            )
        self.assertIsNone(self.bridge.deliver_one(
            self.conn,project_id=alien,ports=self.ports(),
        ))
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_outbox WHERE id=%s",(self.event_id,),
        ).fetchone()[0],"pending")

    def test_original_baseline_enqueue_idempotent_even_after_delivery(self):
        self.completed()
        self.bridge.deliver_one(
            self.conn,project_id=self.project,ports=self.ports(),
        )
        receipt=self.bridge.enqueue_baseline(
            self.conn,project_id=self.project,calculated_metric_id=self.metric_id,
        )
        self.assertTrue(receipt.replayed)
        self.assertEqual(receipt.outbox_id,self.event_id)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM group_c_outbox WHERE project_id=%s",
            (self.project,),
        ).fetchone()[0],1)

    def test_persisted_read_returns_original_sql_values_no_new_C7(self):
        self.completed()
        a=self.bridge.load_persisted(
            self.conn,project_id=self.project,calculated_metric_id=self.metric_id,
        )
        original=self.conn.execute(
            """SELECT breakout_score,score_creator_component,
                      score_growth_component,score_engagement_component,
                      score_freshness_component FROM calculated_metrics WHERE id=%s""",
            (self.metric_id,),
        ).fetchone()
        self.assertEqual((
            a.breakout_score,a.score_creator_component,a.score_growth_component,
            a.score_engagement_component,a.score_freshness_component,
        ),original)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM calculated_metrics WHERE post_id=%s",
            (self.base.target[0],),
        ).fetchone()[0],1)

    def test_after_erasure_request_analytics_refuses_dispatch(self):
        self.completed()
        from intelligence.group_c.retention import PostgreSQLScoreRetention
        class TestIAM:
            def verify_erasure_request(self,conn,**kwargs):
                return True
        PostgreSQLScoreRetention().request_erasure(
            self.conn,project_id=self.project,calculated_metric_id=self.metric_id,
            authorization_event_id=str(uuid4()),
            reason_code="legal_erasure_required",
            signed_assertion="test-only-verified-legal-erasure-approval",
            verifier=TestIAM(),
        )
        self.assertIsNone(self.bridge.deliver_one(
            self.conn,project_id=self.project,ports=self.ports(),
        ))
        self.assertEqual(self.count_receipts(),0)

    def test_migration_checksum_and_repeat_apply(self):
        from pathlib import Path
        import hashlib
        from intelligence import migrate
        path=Path(__file__).resolve().parents[1]/"intelligence/migrations/062_group_c_retention_revocation.sql"
        self.assertEqual(self.conn.execute(
            "SELECT sha256 FROM schema_migrations WHERE version=%s",
            (path.name,),
        ).fetchone()[0],hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(migrate.apply_migrations(self.conn),[])


if __name__=="__main__":
    unittest.main()
