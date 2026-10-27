"""UA-MOD-15: real disposable PostgreSQL 17 duplicate/concurrent/rollback.

The injected assembler returns an explicitly MOCKED unapproved group-C
selection: these tests verify SQL atomicity, NOT SRE/QDP/ABC authority.
No LIVE source data is accepted, no qualified snapshot is produced.

All database tests are conditional on CM_TEST_PG_DSN; never mutate CM_DB_DSN.
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import os
import threading
from pathlib import Path
import unittest
from types import SimpleNamespace
from uuid import uuid4

from intelligence.unified_analytics import InputScope, AnalyticsBoundaryError
from intelligence.unified_analytics.joined_context import (
    JoinedContextAssembler, JoinedSelection, CandidateExclusion,
)
from intelligence.unified_analytics.snapshot_assembly import (
    ImmutableSnapshotRepository, INSERT_PAYLOAD_SQL, INSERT_SNAPSHOT_SQL,
)


class UnapprovedSyntheticAssembler(JoinedContextAssembler):
    """Only for isolated DB integrity tests, never real UAE authority."""

    def __init__(self, db, scope:InputScope, as_of:datetime):
        self._conn=db
        self._scope=scope
        self._as_of=as_of
        self._candidates=(SimpleNamespace(fingerprint="c"*64),)
        self.count=0

    def select(self):
        self.count+=1
        return JoinedSelection(
            "blocked",None,
            (CandidateExclusion("c"*64,"join_group_c_not_current"),),1,
        )


class FaultOnPayload:
    """Delegates actual PG transaction and queries except a single write."""

    def __init__(self, db):
        self.db=db
        self.info=db.info
    def transaction(self):
        return self.db.transaction()
    def execute(self, sql, args=()):
        if sql==INSERT_PAYLOAD_SQL:
            raise RuntimeError("injected disposable PG17 payload failure")
        return self.db.execute(sql,args)


class TransactionGuardStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base=Path(__file__).resolve().parents[1] / "intelligence" / "migrations"
        cls.sql85=(base/"085_unified_analytics_transaction_guards.sql").read_text(
            encoding="utf-8").lower()
        cls.sql86=(base/"086_unified_analytics_payload_delete_integrity.sql").read_text(
            encoding="utf-8").lower()

    def test_085_direct_unapproved_qualified_and_complete_denied(self):
        self.assertIn("if new.status = 'qualified'",self.sql85)
        self.assertIn("new.checked is true or new.is_current is true",self.sql85)
        self.assertIn("or new.state = 'complete'",self.sql85)
        self.assertIn("before insert on uae_unified_snapshots",self.sql85)
        self.assertIn("before insert on uae_snapshot_evidence",self.sql85)

    def test_085_deferred_exact_payload_matrix_and_086_deletion_guard(self):
        self.assertIn("deferrable initially deferred",self.sql85)
        self.assertIn("n <> 7",self.sql85)
        self.assertIn("existing_payload->'evidence'",self.sql86)
        self.assertIn("after delete on uae_snapshot_evidence",self.sql86)
        self.assertIn("deferrable initially deferred",self.sql86)

    def test_migrations_are_additive_owner_only_and_do_not_publish(self):
        for sql in (self.sql85,self.sql86):
            self.assertNotIn("create table calculated_metrics",sql)
            self.assertNotIn("create table metric_snapshots",sql)
            self.assertNotIn("compute_c_metrics(",sql)
            self.assertNotIn("grant select",sql)
            self.assertNotIn("drop table",sql)
            self.assertNotIn("truncate table",sql)


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "CM_TEST_PG_DSN disposable PostgreSQL 17 required")
class TransactionalPersistencePG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.psycopg=psycopg
        cls.dsn=os.environ["CM_TEST_PG_DSN"]
        if cls.dsn==os.environ.get("CM_DB_DSN"):
            raise RuntimeError("Refusing to use production DSN")
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            version=int(conn.execute("SHOW server_version_num").fetchone()[0])
            if not 170000<=version<180000:
                raise RuntimeError("Disposable PostgreSQL 17 required")
            migrate.apply_migrations(conn)
            self_check=conn.execute(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgname IN ("
                "'uae_v1_no_forged_qualified_insert',"
                "'uae_v1_no_forged_complete_evidence_insert',"
                "'uae_payload_seven_evidence_at_commit',"
                "'uae_payload_evidence_deletion_guard')"
            ).fetchone()[0]
            if self_check!=4:
                raise RuntimeError("UA15 migration 085 transaction guards missing")

    def connect(self):
        from psycopg.rows import dict_row
        return self.psycopg.connect(
            self.dsn,autocommit=True,row_factory=dict_row,
        )

    def setUp(self):
        token=uuid4().hex
        self.project=token
        self.source="uae-ua15-"+token
        self.key1="ua15.request.one."+token
        self.key2="ua15.request.two."+token
        self.video_platform_id=token
        self.sha="0"*64
        self.run=str(uuid4())
        with self.connect() as conn:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO project_registry("
                    "id,name,profile_revision,profile_hash,created_at,updated_at)"
                    " VALUES(%s,'UAE 15 disposable QA',1,%s,now(),now())",
                    (self.project,self.sha),
                )
                conn.execute(
                    "INSERT INTO sources("
                    "id,provider,source_type,status,can_store_metrics,"
                    "can_derive_scores,can_analyze_media,can_store_metadata)"
                    " VALUES(%s,'local','manual','enabled',"
                    "TRUE,TRUE,TRUE,TRUE)",
                    (self.source,),
                )
                self.policy=conn.execute(
                    "INSERT INTO source_policy_versions("
                    "source_id,version,is_active,permissions)"
                    " VALUES(%s,1,TRUE,%s::jsonb) RETURNING id",
                    (self.source,
                     '{"can_store_metadata":true,"can_analyze_media":true,'
                     '"can_store_metrics":true,"can_derive_scores":true}'),
                ).fetchone()["id"]
                self.video=conn.execute(
                    "INSERT INTO videos("
                    "platform,platform_video_id,canonical_url,content_kind)"
                    " VALUES('youtube',%s,'https://example.invalid/uae15','video')"
                    " RETURNING id",
                    (self.video_platform_id,),
                ).fetchone()["id"]
                conn.execute("INSERT INTO sre_workspaces(project_id) VALUES(%s)",
                             (self.project,))
                conn.execute(
                    "INSERT INTO sre_project_videos(project_id,video_id)"
                    " VALUES(%s,%s)",(self.project,self.video),
                )
                self.requests=[]
                for key in (self.key1,self.key2):
                    request=conn.execute(
                        "INSERT INTO uae_qualification_requests("
                        "project_id,video_id,source_id,source_policy_version_id,"
                        "qdp_run_id,qdp_input_sha256,content_kind,"
                        "request_key,request_sha256)"
                        " VALUES(%s,%s,%s,%s,%s::uuid,%s,'video',%s,%s)"
                        " RETURNING id",
                        (self.project,self.video,self.source,self.policy,
                         self.run,self.sha,key,self.sha),
                    ).fetchone()["id"]
                    self.requests.append(str(request))
        self.scope=InputScope(
            self.project,self.video,self.source,self.policy,
            "video",self.run,self.sha,
        )
        self.as_of=datetime.now(timezone.utc)-timedelta(seconds=2)

    def tearDown(self):
        # Project membership/request/snapshots/payloads cascade together.
        # Video/source rows are owned uniquely by this disposable fixture.
        with self.connect() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM project_registry WHERE id=%s",
                             (self.project,))
                conn.execute("DELETE FROM sources WHERE id=%s",(self.source,))
                conn.execute("DELETE FROM videos WHERE id=%s",(self.video,))

    def save(self,request_index=0,when=None,db=None):
        if db is None:
            with self.connect() as conn:
                return self.save(request_index,when,conn)
        assembler=UnapprovedSyntheticAssembler(
            db,self.scope,self.as_of if when is None else when,
        )
        return ImmutableSnapshotRepository(
            assembler,self.requests[request_index],
        ).assemble_and_save()

    def count(self,table):
        assert table in ("uae_unified_snapshots","uae_snapshot_evidence",
                         "uae_snapshot_payloads")
        with self.connect() as conn:
            return conn.execute(
                "SELECT count(*) AS n FROM "+table+" WHERE project_id=%s",
                (self.project,),
            ).fetchone()["n"]

    def test_actual_pg17_idempotent_replay_checksum_and_all_seven_refs(self):
        first=self.save()
        second=self.save()
        self.assertEqual(first.snapshot_id,second.snapshot_id)
        self.assertEqual(first.snapshot_version,1)
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(first.payload_sha256,second.payload_sha256)
        self.assertEqual((self.count("uae_unified_snapshots"),
                          self.count("uae_snapshot_evidence"),
                          self.count("uae_snapshot_payloads")),(1,7,1))
        with self.connect() as conn:
            saved=conn.execute(
                "SELECT payload->>'status' AS status, "
                "jsonb_array_length(payload->'evidence') AS evidence_count"
                " FROM uae_snapshot_payloads WHERE project_id=%s",
                (self.project,),
            ).fetchone()
            self.assertEqual(saved,{"status":"partial","evidence_count":7})

    def test_actual_pg17_parallel_same_request_one_version(self):
        barrier=threading.Barrier(2)
        def worker(_):
            with self.connect() as conn:
                barrier.wait(timeout=15)
                return self.save(0,db=conn)
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts=list(pool.map(worker,range(2)))
        self.assertEqual(
            {r.snapshot_id for r in receipts},{receipts[0].snapshot_id},
        )
        self.assertEqual({r.snapshot_version for r in receipts},{1})
        self.assertEqual(sorted(r.replayed for r in receipts),[False,True])
        self.assertEqual(self.count("uae_unified_snapshots"),1)
        self.assertEqual(self.count("uae_snapshot_evidence"),7)

    def test_actual_pg17_parallel_distinct_requests_serial_versions(self):
        barrier=threading.Barrier(2)
        def worker(i):
            with self.connect() as conn:
                barrier.wait(timeout=15)
                return self.save(i,db=conn)
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts=list(pool.map(worker,range(2)))
        self.assertEqual(len({r.snapshot_id for r in receipts}),2)
        self.assertEqual(sorted(r.snapshot_version for r in receipts),[1,2])
        self.assertTrue(all(r.status=="partial" for r in receipts))
        self.assertEqual(self.count("uae_snapshot_evidence"),14)

    def test_actual_pg17_version_history_and_updated_at_retry(self):
        first=self.save()
        second=self.save(0,self.as_of+timedelta(milliseconds=50))
        self.assertEqual((first.snapshot_version,second.snapshot_version),(1,2))
        self.assertNotEqual(first.snapshot_id,second.snapshot_id)
        self.assertEqual(self.count("uae_snapshot_payloads"),2)
        self.assertFalse(second.qualified)

    def test_actual_pg17_mid_insert_error_rolls_back_every_table(self):
        with self.connect() as conn:
            faulty=FaultOnPayload(conn)
            with self.assertRaises(AnalyticsBoundaryError) as err:
                self.save(0,db=faulty)
            self.assertEqual(err.exception.code,"snapshot_write_unavailable")
        self.assertEqual(self.count("uae_unified_snapshots"),0)
        self.assertEqual(self.count("uae_snapshot_evidence"),0)
        self.assertEqual(self.count("uae_snapshot_payloads"),0)
        result=self.save()
        self.assertEqual(result.snapshot_version,1)

    def test_actual_pg17_deferred_payload_requires_seven_real_children(self):
        import json
        with self.connect() as conn:
            with self.assertRaises(self.psycopg.errors.CheckViolation):
                with conn.transaction():
                    # A structurally plausible partial snapshot by itself is
                    # insufficient to attach a 084 payload with seven JSON
                    # descriptors but NO actual evidence child rows.
                    snap=conn.execute(
                        "INSERT INTO uae_unified_snapshots("
                        "project_id,request_id,video_id,source_id,"
                        "source_policy_version_id,qdp_run_id,qdp_input_sha256,"
                        "content_kind,content_type,snapshot_version,status,"
                        "scope_sha256,inputs_sha256,decision_sha256,"
                        "blockers,readiness,verified_as_of)"
                        " VALUES(%s,%s::uuid,%s,%s,%s,%s::uuid,%s,'video','video',"
                        "1,'partial',%s,%s,%s,%s::jsonb,'{}'::jsonb,now())"
                        " RETURNING id",
                        (self.project,self.requests[0],self.video,self.source,
                         self.policy,self.run,self.sha,self.scope.scope_sha256,
                         "1"*64,"2"*64,'[{"code":"unreviewed"}]'),
                    ).fetchone()["id"]
                    payload={
                        "schema_version":"uae-snapshot-assembly/v1",
                        "status":"partial","scope_sha256":self.scope.scope_sha256,
                        "project_id":self.project,"video_id":self.video,
                        "source_id":self.source,
                        "source_policy_version_id":self.policy,
                        "content_kind":"video","input_sha256":"1"*64,
                        "decision_sha256":"2"*64,
                        "evidence_set_sha256":"3"*64,
                        "readiness":{},"blockers":[{"code":"unreviewed"}],
                        "evidence":[{"port":p} for p in (
                            "source_policy","d1","qdp","sre",
                            "group_a","group_b","group_c"
                        )],
                    }
                    conn.execute(
                        "INSERT INTO uae_snapshot_payloads("
                        "project_id,snapshot_id,assembly_schema,payload,"
                        "payload_sha256,evidence_set_sha256,decision_sha256)"
                        " VALUES(%s,%s,'uae-snapshot-assembly/v1',"
                        "%s::jsonb,%s,%s,%s)",
                        (self.project,snap,json.dumps(payload),
                         "4"*64,"3"*64,"2"*64),
                    )
        self.assertEqual(self.count("uae_unified_snapshots"),0)
        self.assertEqual(self.count("uae_snapshot_payloads"),0)

    def test_actual_pg17_cannot_forge_qualified_via_direct_sql(self):
        with self.connect() as conn:
            with self.assertRaises(self.psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO uae_unified_snapshots("
                        "project_id,request_id,video_id,source_id,"
                        "source_policy_version_id,qdp_run_id,qdp_input_sha256,"
                        "content_kind,content_type,snapshot_version,status,"
                        "scope_sha256,inputs_sha256,decision_sha256,"
                        "sre_run_id,sre_decision_sha256,group_a_analysis_id,"
                        "group_b_analysis_id,calculated_metric_id,metric_snapshot_id,"
                        "blockers,readiness,verified_as_of)"
                        " VALUES(%s,%s::uuid,%s,%s,%s,%s::uuid,%s,'video','video',"
                        "1,'qualified',%s,%s,%s,%s::uuid,%s,1,2,3,4,"
                        "'[]'::jsonb,%s::jsonb,now())",
                        (self.project,self.requests[0],self.video,self.source,
                         self.policy,self.run,self.sha,self.scope.scope_sha256,
                         "1"*64,"2"*64,self.run,"3"*64,
                         '{"source_policy":"ready","d1":"ready",'
                         '"qdp":"ready","sre":"ready","group_a":"ready",'
                         '"group_b":"ready","group_c":"ready"}'),
                    )
        self.assertEqual(self.count("uae_unified_snapshots"),0)

    def test_actual_pg17_cannot_insert_checked_true_in_unapproved_snapshot(self):
        receipt=self.save()
        with self.connect() as conn:
            with self.assertRaises(self.psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO uae_snapshot_evidence("
                        "project_id,snapshot_id,port,state,checked,is_current,"
                        "record_key,producer_schema,producer_version,"
                        "evidence_sha256,lineage_sha256)"
                        " VALUES(%s,%s,'sre','partial',TRUE,TRUE,"
                        "'source:sre','fixture/v1','v1',%s,%s)",
                        (self.project,receipt.snapshot_id,"a"*64,"b"*64),
                    )
        self.assertEqual(self.count("uae_snapshot_evidence"),7)

    def test_actual_pg17_cannot_remove_one_evidence_but_parent_retention_purge_works(self):
        receipt=self.save()
        with self.connect() as conn:
            with self.assertRaises(self.psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute(
                        "DELETE FROM uae_snapshot_evidence "
                        "WHERE project_id=%s AND snapshot_id=%s AND port='sre'",
                        (self.project,receipt.snapshot_id),
                    )
            self.assertEqual(self.count("uae_snapshot_evidence"),7)
            with conn.transaction():
                conn.execute(
                    "DELETE FROM uae_unified_snapshots "
                    "WHERE project_id=%s AND id=%s",
                    (self.project,receipt.snapshot_id),
                )
        self.assertEqual(self.count("uae_unified_snapshots"),0)
        self.assertEqual(self.count("uae_snapshot_evidence"),0)
        self.assertEqual(self.count("uae_snapshot_payloads"),0)

    def test_actual_pg17_source_revocation_replay_and_outbox_atomicity(self):
        from intelligence.unified_analytics.revision_events import (
            SOURCE_RIGHTS_SQL,RevisionEvent,RevisionEventProcessor,
        )
        from intelligence.unified_analytics.contracts import canonical_hash
        receipt=self.save()
        with self.connect() as conn:
            with conn.transaction():
                conn.execute(
                    "UPDATE sources SET status='revoked' WHERE id=%s",
                    (self.source,),
                )
            source=conn.execute(SOURCE_RIGHTS_SQL,(
                self.project,self.video,self.policy,self.source,
            )).fetchone()
            sha=canonical_hash({
                "source":self.source,"policy":self.policy,
                "status":source["source_status"],
                "active":source["policy_active"],
                "rights":[source[x] for x in (
                    "can_store_metadata","can_analyze_media",
                    "can_store_metrics","can_derive_scores",
                )],
                "permissions":source["permissions"],
                "retention_days":source["retention_days"],
                "retention_policy":source["retention_policy"],
            })
            observed=datetime.now(timezone.utc)-timedelta(milliseconds=5)
            ev=RevisionEvent(
                self.project,self.video,self.source,self.policy,
                self.run,str(uuid4()),"source_policy_revoked",str(self.policy),
                0,sha,observed,
            )
            processor=RevisionEventProcessor(
                conn,as_of=datetime.now(timezone.utc),
            )
            accepted=processor.consume(ev)
            self.assertEqual(accepted.state,"accepted")
            self.assertEqual(accepted.invalidated_snapshot_ids,
                             (receipt.snapshot_id,))
            again=processor.consume(ev)
            self.assertEqual(again.state,"replayed")
            invalidations=conn.execute(
                "SELECT count(*) AS n FROM uae_snapshot_invalidations "
                "WHERE project_id=%s AND snapshot_id=%s",
                (self.project,receipt.snapshot_id),
            ).fetchone()["n"]
            outbox=conn.execute(
                "SELECT count(*) AS n FROM uae_revision_outbox "
                "WHERE project_id=%s AND snapshot_id=%s",
                (self.project,receipt.snapshot_id),
            ).fetchone()["n"]
            self.assertEqual((invalidations,outbox),(1,1))
            self.assertEqual(
                conn.execute(
                    "SELECT status FROM uae_unified_snapshots WHERE id=%s",
                    (receipt.snapshot_id,),
                ).fetchone()["status"],
                "partial",
            )
            with self.assertRaises(self.psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute(
                        "UPDATE uae_revision_inbox SET event_sha256=%s "
                        "WHERE project_id=%s AND event_id=%s::uuid",
                        ("0"*64,self.project,ev.event_id),
                    )
            with self.assertRaises(self.psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO uae_revision_outbox("
                        "project_id,snapshot_id,invalidation_id,event_sha256)"
                        " VALUES(%s,%s,%s::uuid,%s)",
                        (self.project,receipt.snapshot_id,str(uuid4()),"1"*64),
                    )

    def test_actual_pg17_update_history_is_forbidden(self):
        receipt=self.save()
        with self.connect() as conn:
            for statement,args in (
                ("UPDATE uae_unified_snapshots SET status='blocked'"
                 " WHERE project_id=%s AND id=%s",
                 (self.project,receipt.snapshot_id)),
                ("UPDATE uae_snapshot_payloads SET assembly_schema="
                 "'uae-snapshot-assembly/v1' WHERE project_id=%s AND snapshot_id=%s",
                 (self.project,receipt.snapshot_id)),
                ("UPDATE uae_snapshot_evidence SET checked=TRUE"
                 " WHERE project_id=%s AND snapshot_id=%s",
                 (self.project,receipt.snapshot_id)),
            ):
                with self.subTest(statement=statement):
                    with self.assertRaises(self.psycopg.errors.CheckViolation):
                        with conn.transaction():
                            conn.execute(statement,args)
        self.assertEqual(self.count("uae_snapshot_evidence"),7)


if __name__=="__main__":
    unittest.main()
