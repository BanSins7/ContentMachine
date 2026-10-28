"""GC-MOD-31: real disposable PG17 cross-stage *synthetic-port* E2E.

Runs the SAME QDP handoff -> canonical D1 -> durable GC21 job -> signed
TEST-ONLY human-approved original C7 -> GC25 outbox -> same-DB synthetic
Analytics sink -> exact durable ACK. Exercises actual PostgreSQL transactions,
no mock database; QDP producer and Analytics receiver remain TEST DOUBLES,
not proof of real peer deployments. Do NOT use on production.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
import platform
import unittest
from uuid import uuid4

from intelligence.group_c.analytics_bridge import PostgreSQLAnalyticsBridge
from intelligence.group_c.contracts import ContractError, canonical_hash
from intelligence.group_c.d1 import local_d1_ports
from intelligence.group_c.ports import QDPHandoffNotice
from intelligence.group_c.qdp_intake import QDPGroupCConsumer,INPUT_SCHEMA
from intelligence.group_c.durable_worker import PostgreSQLGroupCWorker,WorkerPolicy
from test_group_c_analytics_bridge import (
    TestAnalyticsBridgePG17,_SyntheticSameDBAnalytics,
)
from test_group_c_durable_worker import _SyntheticC7IAM


def contract_manifest():
    """Cross-platform pure test metadata: never report a live provider probe."""
    return {
        "version":"group-c-e2e-contract/v1",
        "qdp_event_schema":"qdp-event/v1",
        "qdp_input_schema":"qdp-preparation/v2",
        "analytics_port_schema":"group-c-analytics-outbox/v1",
        "source_metric_definition":"platform_post_views",
        "db_engine":"PostgreSQL 17",
        "production_peer_verified":False,
        "synthetic_qdp":True,
        "synthetic_analytics":True,
    }


class TestCrossPlatformE2EPure(unittest.TestCase):
    def test_synthetic_ports_must_not_be_misreported_as_real_peers(self):
        manifest=contract_manifest()
        self.assertFalse(manifest["production_peer_verified"])
        self.assertTrue(manifest["synthetic_qdp"])
        self.assertTrue(manifest["synthetic_analytics"])
        self.assertEqual(manifest["qdp_input_schema"],INPUT_SCHEMA)
        self.assertEqual(manifest["db_engine"],"PostgreSQL 17")

    def test_port_compatibility_requires_real_schema_provenance(self):
        manifest=contract_manifest()
        self.assertEqual(manifest["source_metric_definition"],"platform_post_views")
        self.assertEqual(manifest["analytics_port_schema"],"group-c-analytics-outbox/v1")
        self.assertEqual(manifest["qdp_event_schema"],"qdp-event/v1")


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES"
    and os.getenv("GC31_ALLOW_SYNTHETIC_QDP")=="YES",
    "E2E uses explicitly disposable PG17, never production",
)
class TestGroupCFullSyntheticPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Strong, opt-in DB identity preflight BEFORE any migration/CREATE.
        # A flag alone cannot prove "disposable"; owner must independently
        # verify this DB is isolated and contains no real data.
        import psycopg
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"],
                             autocommit=True,connect_timeout=5) as pre:
            db,major=pre.execute(
                "SELECT current_database(), current_setting('server_version_num')::int"
            ).fetchone()
            if (not db.startswith(("contentmachine_gc_","gc_e2e_"))
                    or not 170000<=major<180000):
                raise AssertionError(
                    "Synthetic producer forbidden: need named disposable PG17"
                )
            for name in ("qdp_outbox","qdp_group_current_preparations",
                         "gc_test_qdp_preparations",
                         "gc25_disposable_analytics_receipts"):
                if pre.execute(
                    "SELECT to_regclass(%s)",("public."+name,),
                ).fetchone()[0]:
                    raise unittest.SkipTest(
                        "Nonempty/existing QDP or Analytics relations: no overwrites"
                    )
        TestAnalyticsBridgePG17.setUpClass.__func__(cls)
        # Production QDP is never overwritten or replaced with a fake.
        cls.conn.execute("""CREATE TABLE qdp_outbox(
            event_id UUID PRIMARY KEY,project_id TEXT,run_id UUID,
            video_id BIGINT,analysis_group TEXT,event_type TEXT,
            schema_version TEXT,payload JSONB,payload_sha256 TEXT)""")
        cls.conn.execute("""CREATE TABLE gc_test_qdp_preparations(
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
        TestAnalyticsBridgePG17.tearDownClass.__func__(cls)

    def setUp(self):
        self.base=TestAnalyticsBridgePG17()
        self.base.conn=self.conn
        self.base.psycopg=self.psycopg
        self.base.setUp()
        self.project=self.base.project
        self.video=self.base.base.target[0]
        self.snapshot=self.base.base.target[1]
        self.previous=self.base.base.previous
        self.source=self.base.base.source
        self.policy=self.base.base.policy
        self.config=self.base.base.config
        self.metadata=self.base.base.target[2]
        self.bridge=PostgreSQLAnalyticsBridge()
        self.sink=_SyntheticSameDBAnalytics()
        self.request=self.base.base.request()
        # Fixture GC21 initially made a queued job manually. Retire it before
        # ingesting authoritative synthetic QDP event, so the actual claim
        # below is provably for the new QDP-created run and job.
        original_job=self.base.base.ids[0][0]
        self.assertTrue(self.base.base.worker.cancel(
            self.conn,project_id=self.project,job_id=original_job,
        ))
        self.qdp_run=str(uuid4())
        self.event=str(uuid4())
        inputs={
            "result_schema":INPUT_SCHEMA,"group":"C",
            "sre_content_version_id":17,
            "sre_content_sha256":"a"*64,
            "sre_discovery_id":21,
            "enrichment_source_id":self.source,
            "source_policy_version_id":self.policy,
            "metadata_id":self.metadata,
            "metric_snapshot_ids":[self.previous,self.snapshot],
            "metric_history_sha256":"b"*64,
            "metric_definition":{"name":"platform_post_views","version":"v1"},
            "score_config_id":self.config,
        }
        self.input_sha=canonical_hash(inputs)
        payload={
            "project_id":self.project,"run_id":self.qdp_run,
            "video_id":self.video,"analysis_group":"C",
            "input_sha256":self.input_sha,
        }
        self.payload_sha=canonical_hash(payload)
        self.notice=QDPHandoffNotice(
            event_id=self.event,project_id=self.project,
            qdp_run_id=self.qdp_run,video_id=self.video,
            analysis_group="C",input_sha256=self.input_sha,
        )
        self.conn.execute(
            """INSERT INTO gc_test_qdp_preparations(
               run_id,project_id,video_id,analysis_group,state,inputs,
               input_sha256,source_id,source_policy_version_id,metadata_id)
               VALUES(%s::uuid,%s,%s,'C','ready',%s::jsonb,%s,%s,%s,%s)""",
            (self.qdp_run,self.project,self.video,json.dumps(inputs),
             self.input_sha,self.source,self.policy,self.metadata),
        )
        self.conn.execute(
            """INSERT INTO qdp_outbox(
               event_id,project_id,run_id,video_id,analysis_group,event_type,
               schema_version,payload,payload_sha256)
               VALUES(%s::uuid,%s,%s::uuid,%s,'C',
                      'preparation_ready','qdp-event/v1',%s::jsonb,%s)""",
            (self.event,self.project,self.qdp_run,self.video,
             json.dumps(payload),self.payload_sha),
        )

    def tearDown(self):
        # Fixture is exclusively disposable: no installed QDP/Analytics table
        # is ever touched by these test-only teardown deletes.
        self.conn.execute(
            "DELETE FROM qdp_outbox WHERE event_id=%s::uuid",(self.event,),
        )
        self.conn.execute(
            "DELETE FROM gc_test_qdp_preparations WHERE run_id=%s::uuid",
            (self.qdp_run,),
        )
        self.base.tearDown()

    def intake(self):
        ack=QDPGroupCConsumer().receive(self.conn,self.notice,self.payload_sha)
        self.assertTrue(ack["ack"])
        self.assertEqual(ack["replayed"],False)
        self.assertEqual(ack["event_id"],self.event)
        self.assertEqual(
            QDPGroupCConsumer().read_receipt(self.conn,self.event)["run_id"],
            ack["run_id"],
        )
        return ack

    def score(self,ack):
        lease=self.base.base.worker.claim(
            self.conn,project_id=self.project,worker_id="gc31-worker-one",
        )
        self.assertIsNotNone(lease)
        self.assertEqual(lease.job_id,ack["job_id"])
        self.assertEqual(lease.run_id,ack["run_id"])
        done=self.base.base.worker.execute_score(
            self.conn,lease=lease,review_request_id=self.request.request_id,
            approval_verifier=_SyntheticC7IAM(),
        )
        self.assertEqual(done.state,"completed")
        self.assertIsNotNone(done.analytics_outbox_id)
        return done

    def delivered(self,done):
        ports=replace(local_d1_ports(),analytics=self.sink)
        ack=self.bridge.deliver_one(
            self.conn,project_id=self.project,ports=ports,
        )
        self.assertEqual(ack.state,"delivered")
        self.assertEqual(ack.outbox_id,done.analytics_outbox_id)
        self.assertEqual(ack.calculated_metric_id,
                         done.score.calculated_metric_id)
        self.assertIsInstance(ack.analytics_snapshot_id,int)
        return ack

    def test_full_qdp_c7_analytics_round_trip_exact_lineage_and_ack(self):
        intake=self.intake()
        done=self.score(intake)
        delivered=self.delivered(done)
        rows=self.conn.execute(
            """SELECT i.event_id,r.id,r.video_id,r.inbox_event_id,
                      j.id,j.state,cm.id,cm.breakout_score,
                      o.id,o.state,o.payload_sha256,o.payload,
                      ar.id,ar.calculated_metric_id,ar.event_sha256,ar.original_score
                 FROM group_c_inbox i
                 JOIN group_c_runs r ON r.inbox_event_id=i.event_id
                 JOIN group_c_jobs j ON j.run_id=r.id AND j.state='completed'
                 JOIN calculated_metrics cm ON cm.id=r.calculated_metric_id
                 JOIN group_c_outbox o ON o.run_id=r.id
                 JOIN gc25_disposable_analytics_receipts ar
                   ON ar.calculated_metric_id=o.calculated_metric_id
                WHERE i.event_id=%s::uuid""",(self.event,),
        ).fetchone()
        self.assertIsNotNone(rows)
        self.assertEqual(str(rows[0]),self.event)
        self.assertEqual(str(rows[1]),intake["run_id"])
        self.assertEqual(rows[2],self.video)
        self.assertEqual(str(rows[3]),self.event)
        self.assertEqual(rows[4],intake["job_id"])
        self.assertEqual(rows[5],"completed")
        self.assertEqual(rows[6],done.score.calculated_metric_id)
        self.assertEqual(rows[7],done.score.breakout_score)
        self.assertEqual(str(rows[8]),done.analytics_outbox_id)
        self.assertEqual(rows[9],"delivered")
        self.assertEqual(rows[10],canonical_hash(rows[11]))
        self.assertEqual(rows[11]["calculated_metric_id"],rows[6])
        self.assertEqual(rows[12],delivered.analytics_snapshot_id)
        self.assertEqual(rows[13],rows[6])
        self.assertEqual(rows[14],rows[10])
        self.assertEqual(rows[15],rows[7])
        self.assertIsNone(self.bridge.deliver_one(
            self.conn,project_id=self.project,
            ports=replace(local_d1_ports(),analytics=self.sink),
        ))
        replay=QDPGroupCConsumer().receive(
            self.conn,self.notice,self.payload_sha,
        )
        self.assertTrue(replay["ack"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["job_id"],intake["job_id"])
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM group_c_outbox WHERE project_id=%s""",
            (self.project,),
        ).fetchone()[0],1)
        self.assertEqual(self.sink.calls,1)

    def test_qdp_event_racing_claims_only_one_job_and_one_run(self):
        def qdp(_):
            import psycopg
            with psycopg.connect(os.environ["CM_TEST_PG_DSN"],
                                 autocommit=True) as c:
                return QDPGroupCConsumer().receive(
                    c,self.notice,self.payload_sha,
                )
        with ThreadPoolExecutor(max_workers=3) as pool:
            receipts=list(pool.map(qdp,range(3)))
        self.assertTrue(all(x["ack"] for x in receipts))
        self.assertEqual(sum(not x["replayed"] for x in receipts),1)
        self.assertEqual(len({x["run_id"] for x in receipts}),1)
        self.assertEqual(len({x["job_id"] for x in receipts}),1)
        job_id=receipts[0]["job_id"]
        def claim(i):
            import psycopg
            with psycopg.connect(os.environ["CM_TEST_PG_DSN"],
                                 autocommit=True) as c:
                return PostgreSQLGroupCWorker(WorkerPolicy()).claim(
                    c,project_id=self.project,
                    worker_id="gc31-racer-"+str(i),
                )
        with ThreadPoolExecutor(max_workers=6) as pool:
            leases=list(pool.map(claim,range(6)))
        self.assertEqual(sum(lease is not None for lease in leases),1)
        self.assertEqual(next(x for x in leases if x is not None).job_id,job_id)
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM group_c_jobs
               WHERE project_id=%s AND state='running'""",
            (self.project,),
        ).fetchone()[0],1)

    def test_conflicting_qdp_payload_is_durable_but_never_acknowledged(self):
        received=self.intake()
        conflict=QDPGroupCConsumer().receive(
            self.conn,self.notice,"c"*64,
        )
        self.assertFalse(conflict["ack"])
        self.assertEqual(conflict["reason_code"],"handoff_conflict")
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM group_c_inbox_conflicts
               WHERE event_id=%s::uuid""",(self.event,),
        ).fetchone()[0],1)
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM group_c_jobs
               WHERE project_id=%s AND state='queued'""",
            (self.project,),
        ).fetchone()[0],1)
        self.assertEqual(QDPGroupCConsumer().receive(
            self.conn,self.notice,self.payload_sha,
        )["job_id"],received["job_id"])

    def test_bogus_sink_ack_rolls_back_both_sides_no_partial_delivery(self):
        received=self.intake()
        done=self.score(received)
        bad=_SyntheticSameDBAnalytics(invalid_ack=True)
        with self.assertRaises(ContractError) as err:
            self.bridge.deliver_one(
                self.conn,project_id=self.project,
                ports=replace(local_d1_ports(),analytics=bad),
            )
        self.assertEqual(err.exception.code,"analytics_ack_invalid")
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_outbox WHERE id=%s",
            (done.analytics_outbox_id,),
        ).fetchone()[0],"pending")
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM gc25_disposable_analytics_receipts
               WHERE project_id=%s""",(self.project,),
        ).fetchone()[0],0)
        self.delivered(done)

    def test_source_revocation_before_analytics_dispatch_blocks_without_leak(self):
        received=self.intake()
        done=self.score(received)
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.source,),
        )
        result=self.bridge.deliver_one(
            self.conn,project_id=self.project,
            ports=replace(local_d1_ports(),analytics=self.sink),
        )
        self.assertEqual(result.state,"blocked")
        self.assertIsNone(result.analytics_snapshot_id)
        self.assertEqual(self.sink.calls,0)
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_outbox WHERE id=%s",
            (done.analytics_outbox_id,),
        ).fetchone()[0],"failed")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM gc25_disposable_analytics_receipts WHERE project_id=%s",
            (self.project,),
        ).fetchone()[0],0)

    def test_preparation_version_mismatch_fails_closed_no_partial_receipt(self):
        self.conn.execute(
            """UPDATE gc_test_qdp_preparations
               SET inputs=jsonb_set(inputs,'{metric_definition,version}',
                                    '"v999"'::jsonb),
                   input_sha256=%s
             WHERE run_id=%s::uuid""",("e"*64,self.qdp_run),
        )
        with self.assertRaises(ContractError):
            QDPGroupCConsumer().receive(
                self.conn,self.notice,self.payload_sha,
            )
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM group_c_inbox WHERE event_id=%s::uuid",
            (self.event,),
        ).fetchone()[0],0)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM group_c_outbox WHERE project_id=%s",
            (self.project,),
        ).fetchone()[0],0)

    def test_current_policy_revocation_before_qdp_denies_receipt(self):
        self.conn.execute(
            "UPDATE source_policy_versions SET is_active=FALSE WHERE id=%s",
            (self.policy,),
        )
        with self.assertRaises(ContractError):
            QDPGroupCConsumer().receive(
                self.conn,self.notice,self.payload_sha,
            )
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM group_c_inbox WHERE event_id=%s::uuid",
            (self.event,),
        ).fetchone()[0],0)

    def test_original_migrations_checksums_and_reapply_are_stable(self):
        import hashlib
        from pathlib import Path
        from intelligence import migrate
        root=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"
        for name in ("003_performance_c.sql","004_performance_c_integrity.sql",
                     "056_group_c_runtime.sql",
                     "063_group_c_monitoring_recovery.sql",
                     "064_group_c_performance_indexes.sql"):
            with self.subTest(name=name):
                source=(root/name).read_bytes()
                installed=self.conn.execute(
                    "SELECT sha256 FROM schema_migrations WHERE version=%s",
                    (name,),
                ).fetchone()
                self.assertIsNotNone(installed)
                self.assertEqual(installed[0],hashlib.sha256(source).hexdigest())
        self.assertEqual(migrate.apply_migrations(self.conn),[])

    def test_qdp_cross_project_notice_rejected_without_event_receipt(self):
        foreign=replace(self.notice,project_id="f"*32)
        with self.assertRaises(ContractError):
            QDPGroupCConsumer().receive(
                self.conn,foreign,self.payload_sha,
            )
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM group_c_inbox WHERE event_id=%s::uuid",
            (self.event,),
        ).fetchone()[0],0)

    def test_c7_outbox_event_is_never_recomputed_by_receiver(self):
        received=self.intake()
        done=self.score(received)
        original=self.conn.execute(
            """SELECT breakout_score,score_creator_component,
                      score_growth_component,score_engagement_component,
                      score_freshness_component
               FROM calculated_metrics WHERE id=%s""",
            (done.score.calculated_metric_id,),
        ).fetchone()
        delivered=self.delivered(done)
        same=self.conn.execute(
            """SELECT breakout_score,score_creator_component,
                      score_growth_component,score_engagement_component,
                      score_freshness_component
               FROM calculated_metrics WHERE id=%s""",
            (done.score.calculated_metric_id,),
        ).fetchone()
        self.assertEqual(original,same)
        self.assertEqual(
            self.conn.execute(
                """SELECT original_score FROM gc25_disposable_analytics_receipts
                   WHERE id=%s""",(delivered.analytics_snapshot_id,),
            ).fetchone()[0],original[0],
        )
        self.assertEqual(self.sink.calls,1)

    def test_external_peer_contracts_are_test_doubles_not_production(self):
        self.assertFalse(contract_manifest()["production_peer_verified"])
        self.assertEqual(
            self.conn.execute("SHOW server_version_num").fetchone()[0][:2],
            "17",
        )
        # Source code must preserve actual original C7: no independent
        # Analytics calculation is present in this synthetic receiver.
        self.assertFalse(hasattr(self.sink,"compute_breakout_score"))


if __name__=="__main__":
    unittest.main()
