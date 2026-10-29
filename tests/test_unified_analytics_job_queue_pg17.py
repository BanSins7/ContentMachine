"""UA-MOD-17 optional REAL disposable PostgreSQL 17 queue/lease tests.

Run only with CM_TEST_PG_DSN pointing to a throwaway PG17 database.
No actual QDP/SRE/Group A/B/C producers are asserted: handoff rows in
this fixture are synthetic DB constraints only, never production approval.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import threading
import time
from uuid import uuid4
import unittest

from intelligence.unified_analytics.job_queue import QualificationJobQueue,QueueSpec

@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "CM_TEST_PG_DSN disposable PG17 required")
class QueueRealPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.psycopg=psycopg
        cls.dsn=os.environ["CM_TEST_PG_DSN"]
        if cls.dsn==os.environ.get("CM_DB_DSN"):
            raise RuntimeError("Refusing to test against CM_DB_DSN")
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            version=int(conn.execute("SHOW server_version_num").fetchone()[0])
            if not 170000<=version<180000:
                raise RuntimeError("UA-MOD-17 requires disposable PostgreSQL 17")
            migrate.apply_migrations(conn)
            found=conn.execute(
                "SELECT to_regclass('public.uae_qualification_jobs')"
            ).fetchone()[0]
            if found is None:
                raise RuntimeError("UAE migration 088 not installed")

    def connect(self):
        from psycopg.rows import dict_row
        return self.psycopg.connect(self.dsn,autocommit=True,row_factory=dict_row)

    def setUp(self):
        token=uuid4().hex
        self.project=token
        self.source="uae17-"+token
        self.key="uae17.job."+token
        self.run=str(uuid4())
        self.sha="a"*64
        with self.connect() as db:
            with db.transaction():
                db.execute(
                    "INSERT INTO project_registry("
                    "id,name,profile_revision,profile_hash,created_at,updated_at)"
                    " VALUES(%s,'UAE 17 disposable fixture',1,%s,now(),now())",
                    (self.project,self.sha),
                )
                db.execute(
                    "INSERT INTO sources("
                    "id,provider,source_type,status,retention_days,"
                    "can_store_metrics,can_derive_scores,"
                    "can_analyze_media,can_store_metadata)"
                    " VALUES(%s,'local','manual','enabled',30,"
                    "TRUE,TRUE,TRUE,TRUE)",(self.source,),
                )
                self.policy=db.execute(
                    "INSERT INTO source_policy_versions("
                    "source_id,version,is_active,reviewed_at,"
                    "permissions,retention_policy)"
                    " VALUES(%s,1,TRUE,clock_timestamp()-interval '1 minute',"
                    "%s::jsonb,%s::jsonb) RETURNING id",
                    (self.source,
                     '{"can_store_metadata":true,"can_analyze_media":true,'
                     '"can_store_metrics":true,"can_derive_scores":true}',
                     '{"derived_retention_days":25}'),
                ).fetchone()["id"]
                self.video=db.execute(
                    "INSERT INTO videos("
                    "platform,platform_video_id,canonical_url,content_kind)"
                    " VALUES('youtube',%s,'https://example.invalid/uae17','video')"
                    " RETURNING id",(token,),
                ).fetchone()["id"]
                db.execute("INSERT INTO sre_workspaces(project_id) VALUES(%s)",
                           (self.project,))
                db.execute(
                    "INSERT INTO sre_project_videos(project_id,video_id)"
                    " VALUES(%s,%s)",(self.project,self.video),
                )
                self.request=str(db.execute(
                    "INSERT INTO uae_qualification_requests("
                    "project_id,video_id,source_id,source_policy_version_id,"
                    "qdp_run_id,qdp_input_sha256,content_kind,request_key,"
                    "request_sha256)"
                    " VALUES(%s,%s,%s,%s,%s::uuid,%s,'video',%s,%s)"
                    " RETURNING id",
                    (self.project,self.video,self.source,self.policy,
                     self.run,self.sha,"uae17.request."+token,self.sha),
                ).fetchone()["id"])
                # Synthetic receipts for queue-level integrity only.
                for grp in ("A","B","C"):
                    db.execute(
                        "INSERT INTO uae_qdp_handoff_inbox("
                        "event_id,project_id,qdp_run_id,video_id,analysis_group,"
                        "input_sha256,payload_sha256,verified_reference_sha256)"
                        " VALUES(%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s)",
                        (str(uuid4()),self.project,self.run,self.video,grp,
                         self.sha,self.sha,self.sha),
                    )

    def tearDown(self):
        with self.connect() as db:
            with db.transaction():
                db.execute("DELETE FROM project_registry WHERE id=%s",
                           (self.project,))
                db.execute("DELETE FROM sources WHERE id=%s",(self.source,))
                db.execute("DELETE FROM videos WHERE id=%s",(self.video,))

    def spec(self,suffix="a",max_attempts=2):
        return QueueSpec(
            self.project,self.request,self.sha,self.key+"."+suffix,
            "batch."+suffix,max_attempts=max_attempts,
        )

    def test_real_pg17_duplicate_batch_and_claim_race(self):
        with self.connect() as db:
            queue=QualificationJobQueue(db)
            receipts=queue.enqueue_batch((self.spec("a"),self.spec("b")))
            self.assertEqual(tuple(r.state for r in receipts),
                             ("created","created"))
            self.assertEqual(
                tuple(r.state for r in queue.enqueue_batch((
                    self.spec("a"),self.spec("b")
                ))),("replayed","replayed"))
        barrier=threading.Barrier(2)
        def race(worker):
            with self.connect() as db:
                barrier.wait(timeout=15)
                return QualificationJobQueue(db).claim(
                    self.project,worker,limit=2,lease_seconds=30
                )
        with ThreadPoolExecutor(max_workers=2) as pool:
            first,second=list(pool.map(race,("qa.worker.1","qa.worker.2")))
        ids=[l.job_id for l in first+second]
        self.assertEqual(len(ids),2)
        self.assertEqual(len(set(ids)),2)
        for lease in first+second:
            with self.connect() as db:
                q=QualificationJobQueue(db)
                self.assertFalse(q.complete(lease,snapshot_id=999999999))
                self.assertTrue(q.cancel(self.project,lease.job_id))
                self.assertIsNone(q.fail(lease,error_code="late_ack"))

    def test_real_pg17_source_and_qdp_gates_fail_closed(self):
        with self.connect() as db:
            q=QualificationJobQueue(db)
            self.assertEqual(q.enqueue(self.spec()).state,"created")
            with db.transaction():
                db.execute(
                    "DELETE FROM uae_qdp_handoff_inbox "
                    "WHERE project_id=%s AND analysis_group='C'",
                    (self.project,),
                )
            self.assertEqual(q.claim(self.project,"qa.worker.1"),())
            with self.assertRaises(Exception):
                q.enqueue(self.spec("b"))
            with db.transaction():
                db.execute(
                    "UPDATE sources SET status='revoked' WHERE id=%s",
                    (self.source,),
                )
            self.assertEqual(q.claim(self.project,"qa.worker.1"),())

    def test_real_pg17_expired_lease_is_fenced_and_dead_lettered(self):
        with self.connect() as db:
            q=QualificationJobQueue(db)
            q.enqueue(self.spec(max_attempts=1))
            old=q.claim(self.project,"qa.worker.1",lease_seconds=5)[0]
        # A real clock advance validates PostgreSQL lease expiry; no
        # SQL direct mutation of a protected lease or artificial clock.
        time.sleep(5.2)
        with self.connect() as db:
            q=QualificationJobQueue(db)
            self.assertFalse(q.complete(old,snapshot_id=1))
            self.assertEqual(q.recover_expired(self.project)[0][1],"dead")
            self.assertIsNone(q.fail(old,error_code="stale_token"))
            dlq=q.dead_letters(self.project)
            self.assertEqual(len(dlq),1)
            self.assertEqual(dlq[0]["attempts"],1)
            self.assertFalse(q.cancel(self.project,old.job_id))


if __name__=="__main__":
    unittest.main()
