"""Integrated Group C durable outbox -> UAE receipts and current read gate.

Synthetic tests do not prove deployment rights or production-ready UAE data.
PG17 E2E explicitly requires an isolated disposable DB with all migrations.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import unittest

from intelligence.group_c.analytics_bridge import (
    PersistedC7Handoff, OUTBOX_EVENT_TYPE, _event_payload,
)
from intelligence.group_c.contracts import ContractError,canonical_hash
from intelligence.group_c.d1 import local_d1_ports
from intelligence.group_c.ports import GroupCPorts,CapabilityState
from intelligence.group_c_uae_pipeline import (
    UAEGroupCTransactionalSink,UAEReceivedGroupCReader,deliver_group_c_to_uae,
    build_uae_ports_for_group_c,
)


def original_score():
    return PersistedC7Handoff(
        project_id="a"*32,run_id=str(uuid4()),
        video_id=101,calculated_metric_id=991,
        snapshot_id=800,previous_snapshot_id=799,
        published_metadata_id=900,calculation_config_id=12,
        source_id="test.source",source_policy_version_id=3,
        metric_definition="platform_post_views",metric_version="v1",
        creator_cohort_id=20,prior_cohort_id=21,niche_cohort_id=None,
        creator_freeze_id=30,prior_freeze_id=31,niche_freeze_id=None,
        review_request_id=str(uuid4()),review_decision_id=str(uuid4()),
        activation_event_id=str(uuid4()),evidence_sha256="e"*64,
        breakout_score=Decimal("14.000000000000"),
        score_creator_component=Decimal("2.000000000000"),
        score_growth_component=Decimal("3.000000000000"),
        score_engagement_component=Decimal("4.000000000000"),
        score_freshness_component=Decimal("5.000000000000"),
    )


class Cursor:
    def __init__(self, rows): self.rows=rows
    def fetchone(self): return self.rows[0] if self.rows else None


class SyntheticDB:
    def __init__(self, score):
        self.score=score
        self.payload=_event_payload(score)
        self.sha=canonical_hash(self.payload)
        self.outbox=dict(
            id=str(uuid4()),project_id=score.project_id,
            run_id=score.run_id,video_id=score.video_id,
            calculated_metric_id=score.calculated_metric_id,
            event_type=OUTBOX_EVENT_TYPE,event_key="synthetic-run",
            payload=self.payload,payload_sha256=self.sha,state="pending",
        )
        self.inserted=None
        self.calls=[]
        self.force_replay=False

    def execute(self, sql, values=()):
        self.calls.append((sql,values))
        if "FROM group_c_outbox" in sql:
            return Cursor([self.outbox])
        if "INSERT INTO uae_group_c_receptions" in sql:
            if self.force_replay:
                return Cursor([])
            self.inserted=dict(zip((
                "project_id","outbox_id","run_id","video_id","calculated_metric_id",
                "source_id","source_policy_version_id","snapshot_id",
                "previous_snapshot_id","published_metadata_id",
                "calculation_config_id","evidence_sha256","event_sha256",
                "breakout_score",
            ),values))
            return Cursor([{"id":41}])
        if "FROM uae_group_c_receptions" in sql:
            return Cursor([{"id":41,**self.inserted}] if self.inserted else [])
        return Cursor([])


class TestGroupCUAEPure(unittest.TestCase):
    def setUp(self):
        self.score=original_score()
        self.db=SyntheticDB(self.score)
        self.sink=UAEGroupCTransactionalSink(GroupCPorts())

    def _run(self):
        with patch("intelligence.group_c_uae_pipeline._available",return_value=True), \
             patch("intelligence.group_c.analytics_bridge._require_serializable"), \
             patch("intelligence.group_c_uae_pipeline._record",
                   side_effect=lambda cur:cur.fetchone()), \
             patch("intelligence.group_c.analytics_bridge._persisted",
                   return_value=self.score), \
             patch("intelligence.group_c_uae_pipeline.PostgreSQLAnalyticsBridge") as cls:
            cls.return_value.load_persisted.return_value=self.score
            return self.sink.enqueue_persisted_result(
                self.db,self.score.project_id,self.score.calculated_metric_id,
                self.db.sha,
            )

    def test_durable_original_decimal_ack_instead_of_recomputing_c7(self):
        ack=self._run()
        self.assertEqual(ack["analytics_snapshot_id"],41)
        self.assertEqual(ack["calculated_metric_id"],991)
        self.assertEqual(ack["idempotency_sha256"],self.db.sha)
        self.assertEqual(self.db.inserted["breakout_score"],Decimal("14"))
        self.assertEqual(self.db.inserted["evidence_sha256"],"e"*64)
        self.assertTrue(any("INSERT INTO uae_group_c_receptions" in q for q,_ in self.db.calls))
        self.assertFalse(any("UPDATE calculated_metrics" in q for q,_ in self.db.calls))

    def test_idempotent_exact_replay_is_accepted_only_for_same_lineage(self):
        first=self._run()
        self.db.force_replay=True
        second=self._run()
        self.assertEqual(first,second)
        self.db.inserted["calculated_metric_id"]=999
        with self.assertRaises(ContractError) as cm:
            self._run()
        self.assertEqual(cm.exception.code,"analytics_reception_identity_conflict")

    def test_wrong_digest_or_missing_source_rights_fails_closed(self):
        with self.assertRaises(ContractError):
            self.sink.enqueue_persisted_result(
                self.db,self.score.project_id,991,"not-a-digest",
            )
        with patch("intelligence.group_c_uae_pipeline._available",return_value=True), \
             patch("intelligence.group_c.analytics_bridge._require_serializable"), \
             patch("intelligence.group_c_uae_pipeline.PostgreSQLAnalyticsBridge") as cls:
            cls.return_value.load_persisted.side_effect=ContractError(
                "source_policy_expired","Current source denied",
            )
            with self.assertRaises(ContractError):
                self.sink.enqueue_persisted_result(
                    self.db,self.score.project_id,991,self.db.sha,
                )
        self.assertFalse(any("INSERT INTO uae_group_c_receptions" in q for q,_ in self.db.calls))

    def test_schema_absence_is_not_claimed_as_available(self):
        with patch("intelligence.group_c_uae_pipeline._available",return_value=False):
            capability=self.sink.capability(self.db)
            self.assertEqual(capability.state,CapabilityState.UNAVAILABLE)
            with self.assertRaises(ContractError):
                self.sink.enqueue_persisted_result(
                    self.db,self.score.project_id,991,self.db.sha,
                )

    def test_dispatcher_injects_actual_group_c_analytics_port(self):
        with patch("intelligence.group_c_uae_pipeline.PostgreSQLAnalyticsBridge") as cls:
            cls.return_value.deliver_one.return_value="dispatched"
            result=deliver_group_c_to_uae(
                self.db,project_id=self.score.project_id,source_ports=GroupCPorts(),
            )
            self.assertEqual(result,"dispatched")
            arguments=cls.return_value.deliver_one.call_args
            self.assertIsInstance(arguments.kwargs["ports"].analytics,UAEGroupCTransactionalSink)
            self.assertEqual(arguments.kwargs["project_id"],self.score.project_id)

    def test_uae_reader_declines_unverified_qdp_without_reading_c7(self):
        from unittest.mock import MagicMock
        from test_unified_analytics_group_c_adapter import fixture,SCOPE
        from intelligence.unified_analytics.dto import EvidenceRead,ReadState
        from intelligence.unified_analytics.qdp_adapter import QDPQualifiedSetReader
        from intelligence.unified_analytics.ports import (
            CapabilityState as UAEState, PortCapability, PORT_SCHEMAS
        )
        db=MagicMock()
        db.autocommit=True
        db.execute.return_value.fetchone.return_value=tuple(True for _ in range(7))
        qdp=object.__new__(QDPQualifiedSetReader)
        qdp._conn=db
        qdp._pins=SimpleNamespace(
            scope=SCOPE,preparation_shas=("a"*64,"a"*64,"b"*64),
            group_c_snapshot_ids=(201,202),group_c_config_id=12,
        )
        qdp.capability=lambda:PortCapability(
            "qdp",UAEState.DECLARED,PORT_SCHEMAS["qdp"],"qdp-handoff/v1",
        )
        qdp.read_qualified_membership=lambda scope:EvidenceRead(
            "qdp",scope,ReadState.MISSING,reason_code="qdp_revoked",
        )
        reader=UAEReceivedGroupCReader(
            db,fixture()["pins"],qdp,GroupCPorts(),
        )
        self.assertEqual(reader.capability().state,UAEState.DECLARED)
        from psycopg.pq import TransactionStatus
        db.info.transaction_status=TransactionStatus.IDLE
        result=reader.read_persisted_metric(SCOPE)
        self.assertEqual(result.state,ReadState.MISSING)
        self.assertEqual(result.reason_code,"c_qdp_membership_unverified")
        self.assertFalse(any("FROM uae_group_c_receptions" in str(call)
                             for call in db.execute.call_args_list))

    def test_require_explicit_qualified_source_authorities(self):
        with self.assertRaises(ContractError):
            UAEGroupCTransactionalSink(None)
        with self.assertRaises(ContractError):
            deliver_group_c_to_uae(
                self.db,project_id=self.score.project_id,source_ports=None,
            )

    def test_migration_contains_receipt_only_and_immutability(self):
        sql=(Path(__file__).resolve().parents[1] / "intelligence" /
             "migrations" / "091_uae_group_c_receptions.sql").read_text("utf-8")
        self.assertIn("CREATE TABLE uae_group_c_receptions",sql)
        self.assertIn("received_unqualified",sql)
        self.assertIn("BEFORE UPDATE ON uae_group_c_receptions",sql)
        self.assertNotIn("CREATE TABLE calculated_metrics",sql)
        self.assertNotIn("GRANT ALL",sql)


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "Disposable PG17 requires CM_TEST_PG_DSN and GC_TEST_DISPOSABLE_DB=YES",
)
class TestGroupCUAERealDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_group_c_analytics_bridge import TestAnalyticsBridgePG17
        TestAnalyticsBridgePG17.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from test_group_c_analytics_bridge import TestAnalyticsBridgePG17
        TestAnalyticsBridgePG17.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_analytics_bridge import TestAnalyticsBridgePG17
        if self.conn.execute(
            "SELECT to_regclass('public.uae_group_c_receptions')"
        ).fetchone()[0] is None:
            self.skipTest("Apply forward-only UAE091 migration in disposable PG17 first")
        self.base=TestAnalyticsBridgePG17()
        self.base.conn=self.conn
        self.base.psycopg=self.psycopg
        self.base.setUp()

    def tearDown(self):
        self.base.tearDown()

    def test_real_completed_c7_delivers_to_actual_uae_receipt_once(self):
        self.base.completed()
        first=deliver_group_c_to_uae(
            self.conn,project_id=self.base.project,
            source_ports=local_d1_ports(),
        )
        self.assertEqual(first.state,"delivered")
        self.assertEqual(first.calculated_metric_id,self.base.metric_id)
        self.assertEqual(first.outbox_id,self.base.event_id)
        record=self.conn.execute(
            """SELECT id,calculated_metric_id,breakout_score,state
               FROM uae_group_c_receptions
               WHERE project_id=%s AND outbox_id=%s::uuid""",
            (self.base.project,self.base.event_id),
        ).fetchone()
        self.assertEqual(record[0],first.analytics_snapshot_id)
        self.assertEqual(record[1],self.base.metric_id)
        self.assertEqual(record[2],self.conn.execute(
            "SELECT breakout_score FROM calculated_metrics WHERE id=%s",
            (self.base.metric_id,),
        ).fetchone()[0])
        self.assertEqual(record[3],"received_unqualified")
        self.assertIsNone(deliver_group_c_to_uae(
            self.conn,project_id=self.base.project,
            source_ports=local_d1_ports(),
        ))
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM uae_group_c_receptions WHERE project_id=%s",
            (self.base.project,),
        ).fetchone()[0],1)


if __name__=="__main__":
    unittest.main()
