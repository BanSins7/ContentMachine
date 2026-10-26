"""GC-MOD-04 strict QDP -> Group C intake and disposable DB QA.

The PostgreSQL fixture intentionally simulates a QDP provider ONLY in a
throwaway PG17 database. It is not a claim of a live QDP outbox publisher.
"""
from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from intelligence.group_c.contracts import ContractError, canonical_hash
from intelligence.group_c.ports import QDPHandoffNotice, CapabilityState
from intelligence.group_c.qdp_intake import (
    INPUT_SCHEMA, QDPCurrentPreparationAdapter, QDPGroupCConsumer,
    _verify_preparation_payload,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def preparation():
    return {
        "result_schema": INPUT_SCHEMA, "group": "C",
        "sre_content_version_id": 53, "sre_content_sha256": SHA_A,
        "sre_discovery_id": 77, "enrichment_source_id": "gc-source",
        "source_policy_version_id": 8, "metadata_id": 4,
        "metric_snapshot_ids": [13, 15], "metric_history_sha256": SHA_B,
        "metric_definition": {"name": "platform_post_views", "version": "v2"},
        "score_config_id": 3,
    }


def fixture():
    inputs = preparation()
    notice = QDPHandoffNotice(
        event_id=str(uuid4()),project_id="a"*32,qdp_run_id=str(uuid4()),
        video_id=10,analysis_group="C",input_sha256=canonical_hash(inputs),
    )
    row = {
        "run_id":notice.qdp_run_id,"project_id":notice.project_id,
        "video_id":notice.video_id,"analysis_group":"C","state":"ready",
        "inputs":inputs,"input_sha256":notice.input_sha256,
        "source_id":"gc-source","source_policy_version_id":8,"metadata_id":4,
    }
    return notice,row


class GroupCQDPPure(unittest.TestCase):
    def test_exact_v2_and_hash(self):
        notice,row=fixture()
        self.assertEqual(_verify_preparation_payload(row,notice),preparation())

    def test_reject_modified_input_and_cross_project(self):
        notice,row=fixture()
        for mutation in (
            {"input_sha256": SHA_A},
            {"project_id": "b"*32},
            {"analysis_group": "B"}, {"state": "blocked"},
            {"source_id": "other-source"},
            {"metadata_id": 999},
        ):
            with self.subTest(mutation=mutation),self.assertRaises(ContractError):
                _verify_preparation_payload({**row,**mutation},notice)

    def test_no_implicit_defaults_for_invalid_metric_pair(self):
        notice,row=fixture()
        for field,value in (
            ("metric_snapshot_ids",[13]),("metric_snapshot_ids",[13,13]),
            ("metric_snapshot_ids",[True,15]),("metric_snapshot_ids",[13,"15"]),
            ("metric_history_sha256","unknown"),("metric_definition",None),
            ("score_config_id",None),("score_config_id",0),
            ("sre_discovery_id",None),("sre_content_sha256","A"*64),
            ("result_schema","qdp-preparation/v1"),
        ):
            bad=dict(row["inputs"])
            bad[field]=value
            altered={**row,"inputs":bad,"input_sha256":canonical_hash(bad)}
            new=QDPHandoffNotice(
                event_id=notice.event_id,project_id=notice.project_id,
                qdp_run_id=notice.qdp_run_id,video_id=notice.video_id,
                analysis_group="C",input_sha256=canonical_hash(bad),
            )
            with self.subTest(field=field,value=value),self.assertRaises(ContractError):
                _verify_preparation_payload(altered,new)

    def test_missing_qdp_capability_returns_unavailable(self):
        class NoQDP:
            autocommit = True
            def execute(self, *args):
                raise RuntimeError("QDP migrations absent")
        conn=NoQDP()
        self.assertEqual(QDPCurrentPreparationAdapter().capability(conn).state,
                         CapabilityState.UNAVAILABLE)
        notice,_=fixture()
        with self.assertRaises(ContractError) as error:
            QDPGroupCConsumer().receive(conn,notice,SHA_A)
        self.assertEqual(error.exception.code,"dependency_unavailable")

    def test_ack_cannot_escape_uncommitted_transaction(self):
        class Conn:
            autocommit = False
        notice,_=fixture()
        with self.assertRaises(ContractError) as error:
            QDPGroupCConsumer().receive(Conn(),notice,SHA_A)
        self.assertEqual(error.exception.code,"transaction_contract_unavailable")


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "requires isolated PG17 + GC_TEST_DISPOSABLE_DB=YES",
)
class GroupCQDPDisposablePostgres(unittest.TestCase):
    """Purely artificial QDP event/current-view fixture on a fresh PG17 DB."""

    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.conn=psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True)
        version=int(cls.conn.execute("SHOW server_version_num").fetchone()[0])
        if not 170000<=version<180000:
            raise AssertionError("PostgreSQL 17 required for Group C QA")
        migrate.apply_migrations(cls.conn)
        if cls.conn.execute("SELECT to_regclass('public.qdp_outbox')").fetchone()[0]:
            raise unittest.SkipTest("Existing real QDP schema; synthetic fixture not allowed")
        cls.conn.execute("""CREATE TABLE qdp_outbox (
          event_id UUID PRIMARY KEY,project_id TEXT,run_id UUID,video_id BIGINT,
          analysis_group TEXT,event_type TEXT,schema_version TEXT,
          payload JSONB,payload_sha256 TEXT)""")
        cls.conn.execute("""CREATE TABLE gc_test_qdp_preparations (
          run_id UUID,project_id TEXT,video_id BIGINT,analysis_group TEXT,
          state TEXT,inputs JSONB,input_sha256 TEXT,source_id TEXT,
          source_policy_version_id BIGINT,metadata_id BIGINT)""")
        cls.conn.execute("""CREATE VIEW qdp_group_current_preparations AS
          SELECT * FROM gc_test_qdp_preparations WHERE state='ready'""")

    @classmethod
    def tearDownClass(cls):
        cls.conn.execute("DROP VIEW IF EXISTS qdp_group_current_preparations")
        cls.conn.execute("DROP TABLE IF EXISTS gc_test_qdp_preparations")
        cls.conn.execute("DROP TABLE IF EXISTS qdp_outbox")
        cls.conn.close()

    def setUp(self):
        self.project=uuid4().hex
        self.source="gc-intake-"+uuid4().hex
        self.event=str(uuid4())
        self.qdp_run=str(uuid4())
        self.conn.execute("""INSERT INTO project_registry
          (id,name,profile_revision,profile_hash,created_at,updated_at)
          VALUES(%s,'QDP disposable',1,%s,now(),now())""",(self.project,SHA_A))
        self.conn.execute("""INSERT INTO sources
          (id,provider,source_type,status,can_store_metadata,can_store_metrics,
           can_derive_scores) VALUES (%s,'gc_fixture','manual','enabled',TRUE,TRUE,TRUE)""",
          (self.source,))
        self.policy=self.conn.execute("""INSERT INTO source_policy_versions
          (source_id,version,is_active,permissions)
          VALUES(%s,1,TRUE,%s::jsonb) RETURNING id""",
          (self.source,json.dumps({"can_store_metadata":True,
                                   "can_store_metrics":True,"can_derive_scores":True}))).fetchone()[0]
        token=uuid4().hex
        self.video=self.conn.execute("""INSERT INTO videos
          (platform,platform_video_id,canonical_url,c_content_type)
          VALUES('youtube',%s,%s,'video') RETURNING id""",
          (token,"https://youtube.com/watch?v="+token)).fetchone()[0]
        self.metadata=self.conn.execute("""INSERT INTO source_video_metadata
          (video_id,source_id,source_policy_version_id,fetched_at,published_at)
          VALUES(%s,%s,%s,now()-interval '1 hour',
                 now()-interval '2 days') RETURNING id""",
          (self.video,self.source,self.policy)).fetchone()[0]
        self.snapshots=[]
        for hours,views in ((10,50),(2,90)):
            sid=self.conn.execute("""INSERT INTO metric_snapshots
              (video_id,source_id,source_policy_version_id,observed_at,views,
               likes,comments,shares,source_metric_definition,source_metric_version,
               observation_time_assumed,data_quality_status,precision)
              VALUES(%s,%s,%s,now()-(%s*interval '1 hour'),%s,2,1,1,
                     'platform_post_views','v2',FALSE,'ok','exact')
              RETURNING id""",
              (self.video,self.source,self.policy,hours,views)).fetchone()[0]
            self.snapshots.append(sid)
        self.config=self.conn.execute("""INSERT INTO metric_calculation_configs
          (calculation_version,alpha,bayesian_k,is_active)
          VALUES(%s,10,100,TRUE) RETURNING id""",
          ("gc-intake-"+uuid4().hex,)).fetchone()[0]
        self.inputs={
            "result_schema":INPUT_SCHEMA,"group":"C",
            "sre_content_version_id":17,"sre_content_sha256":SHA_A,
            "sre_discovery_id":21,
            "enrichment_source_id":self.source,
            "source_policy_version_id":self.policy,
            "metadata_id":self.metadata,"metric_snapshot_ids":self.snapshots,
            "metric_history_sha256":SHA_B,
            "metric_definition":{"name":"platform_post_views","version":"v2"},
            "score_config_id":self.config,
        }
        self.input_sha=canonical_hash(self.inputs)
        self.payload={"project_id":self.project,"run_id":self.qdp_run,
                      "video_id":self.video,"analysis_group":"C",
                      "input_sha256":self.input_sha}
        self.payload_sha=canonical_hash(self.payload)
        self.notice=QDPHandoffNotice(
            event_id=self.event,project_id=self.project,qdp_run_id=self.qdp_run,
            video_id=self.video,analysis_group="C",input_sha256=self.input_sha,
        )
        self.conn.execute("""INSERT INTO gc_test_qdp_preparations
          (run_id,project_id,video_id,analysis_group,state,inputs,input_sha256,
           source_id,source_policy_version_id,metadata_id)
          VALUES (%s::uuid,%s,%s,'C','ready',%s::jsonb,%s,%s,%s,%s)""",
          (self.qdp_run,self.project,self.video,json.dumps(self.inputs),
           self.input_sha,self.source,self.policy,self.metadata))
        self.conn.execute("""INSERT INTO qdp_outbox
          (event_id,project_id,run_id,video_id,analysis_group,event_type,
           schema_version,payload,payload_sha256)
          VALUES(%s::uuid,%s,%s::uuid,%s,'C','preparation_ready','qdp-event/v1',
                 %s::jsonb,%s)""",
          (self.event,self.project,self.qdp_run,self.video,
           json.dumps(self.payload),self.payload_sha))

    def tearDown(self):
        self.conn.execute("DELETE FROM qdp_outbox WHERE event_id=%s::uuid",(self.event,))
        self.conn.execute("DELETE FROM gc_test_qdp_preparations WHERE run_id=%s::uuid",(self.qdp_run,))
        self.conn.execute("DELETE FROM project_registry WHERE id=%s",(self.project,))
        self.conn.execute("DELETE FROM videos WHERE id=%s",(self.video,))
        self.conn.execute("DELETE FROM metric_calculation_configs WHERE id=%s",(self.config,))
        self.conn.execute("DELETE FROM sources WHERE id=%s",(self.source,))

    def counts(self):
        return [self.conn.execute("SELECT count(*) FROM "+table+
            " WHERE project_id=%s",(self.project,)).fetchone()[0]
            for table in ("group_c_inbox","group_c_runs","group_c_jobs")]

    def test_exact_commit_then_idempotent_ack(self):
        consumer=QDPGroupCConsumer()
        first=consumer.receive(self.conn,self.notice,self.payload_sha)
        self.assertTrue(first["ack"])
        self.assertFalse(first["replayed"])
        self.assertEqual(self.counts(),[1,1,1])
        self.assertEqual(consumer.read_receipt(self.conn,self.event)["run_id"],first["run_id"])
        again=consumer.receive(self.conn,self.notice,self.payload_sha)
        self.assertTrue(again["ack"])
        self.assertTrue(again["replayed"])
        self.assertEqual(again["run_id"],first["run_id"])
        self.assertEqual(self.counts(),[1,1,1])

    def test_conflicting_event_payload_is_durable_no_ack(self):
        consumer=QDPGroupCConsumer()
        consumer.receive(self.conn,self.notice,self.payload_sha)
        conflict=consumer.receive(self.conn,self.notice,SHA_C)
        self.assertEqual(conflict["reason_code"],"handoff_conflict")
        self.assertFalse(conflict["ack"])
        self.assertEqual(self.counts(),[1,1,1])
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM group_c_inbox_conflicts WHERE event_id=%s::uuid",
            (self.event,)).fetchone()[0],1)

    def test_current_revocation_and_no_partial_receipt(self):
        self.conn.execute("UPDATE source_policy_versions SET is_active=FALSE WHERE id=%s",(self.policy,))
        with self.assertRaises(ContractError):
            QDPGroupCConsumer().receive(self.conn,self.notice,self.payload_sha)
        self.assertEqual(self.counts(),[0,0,0])

    def test_mismatched_hash_and_cross_project(self):
        bad=QDPHandoffNotice(
            event_id=self.event,project_id="b"*32,qdp_run_id=self.qdp_run,
            video_id=self.video,analysis_group="C",input_sha256=self.input_sha)
        for notice,sha in ((bad,self.payload_sha),(self.notice,SHA_C)):
            with self.subTest(project=notice.project_id,sha=sha),self.assertRaises(ContractError):
                QDPGroupCConsumer().receive(self.conn,notice,sha)
        self.assertEqual(self.counts(),[0,0,0])

    def test_retracted_snapshot_and_missing_live_qdp_event(self):
        self.conn.execute("""UPDATE metric_snapshots SET data_quality_status='retracted'
             WHERE id=%s""",(self.snapshots[1],))
        with self.assertRaises(ContractError):
            QDPGroupCConsumer().receive(self.conn,self.notice,self.payload_sha)
        self.assertEqual(self.counts(),[0,0,0])
        self.conn.execute("DELETE FROM qdp_outbox WHERE event_id=%s::uuid",(self.event,))
        with self.assertRaises(ContractError) as e:
            QDPGroupCConsumer().receive(self.conn,self.notice,self.payload_sha)
        self.assertEqual(e.exception.code,"dependency_unavailable")
        self.assertEqual(self.counts(),[0,0,0])


if __name__=="__main__":
    unittest.main()
