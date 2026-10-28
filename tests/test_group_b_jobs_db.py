"""GB-MOD-11 disposable PostgreSQL 17: claims, leases, cancellation, replay.

Uses real B 055 and 058 schemas, mocked/preexisting QDP/A lineage only.
No model/FFmpeg/paid worker execution, no fake B analysis content.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4
import os
import unittest

from intelligence.migrate import apply_migrations
from intelligence.group_b.orchestrator import (
    enqueue_jobs,claim_local,heartbeat,finish_job,cancel_run,reap_expired_jobs,
)
from intelligence.group_b.dependency_rules import (
    BLOCKS,BlockReadiness,ReadinessReport,ReadinessState,
)
from intelligence.group_b.errors import DependencyUnavailable

P_HASH="a"*64
PROOF="b"*64


def safe_dsn():
    from psycopg.conninfo import conninfo_to_dict
    dsn=os.environ["CM_TEST_PG_DSN"]
    name=conninfo_to_dict(dsn).get("dbname","").lower()
    if "test" not in name and "_ci" not in name:
        raise RuntimeError("Refusing non-disposable PG17 DB")
    return dsn


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"No disposable PostgreSQL DSN")
class TestJobPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg
        cls.dsn=safe_dsn()
        with psycopg.connect(cls.dsn) as conn:
            apply_migrations(conn)

    def setUp(self):
        self.project=uuid4().hex
        self.source="job-source-"+uuid4().hex
        self.run=None
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """INSERT INTO project_registry(id,name,profile_revision,profile_hash,
                       created_at,updated_at)
                     VALUES(%s,'Worker PG test',1,%s,now(),now())""",
                (self.project,P_HASH)
            )
            conn.execute(
                """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
                   VALUES(%s,'test','owned','enabled',TRUE)""",(self.source,)
            )
            key=uuid4().hex
            self.video=conn.execute(
                """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
                   VALUES ('tiktok',%s,%s,'video') RETURNING id""",
                (key,"https://example.test/worker/"+key)
            ).fetchone()[0]
        self.run=self.new_run()

    def new_run(self):
        with self.psycopg.connect(self.dsn) as conn:
            inbox=conn.execute(
                """INSERT INTO group_b_inbox(project_id,video_id,source_id,event_key,
                      qdp_run_id,producer_schema,event_sha256,preparation_sha256)
                   VALUES(%s,%s,%s,%s,%s,'qdp-handoff/v1',%s,%s) RETURNING id""",
                (self.project,self.video,self.source,str(uuid4()),str(uuid4()),
                 "a"*64,"c"*64)
            ).fetchone()[0]
            conn.execute("UPDATE group_b_inbox SET status='accepted' WHERE id=%s",(inbox,))
            run=str(conn.execute(
                """INSERT INTO group_b_runs(project_id,inbox_id,video_id,source_id,
                     content_type,a_snapshot_sha256,media_manifest_sha256,
                     config_sha256,idempotency_key)
                   VALUES(%s,%s,%s,%s,'video',%s,%s,%s,%s) RETURNING id""",
                (self.project,inbox,self.video,self.source,"e"*64,"f"*64,
                 "d"*64,uuid4().hex)
            ).fetchone()[0])
            for block in BLOCKS:
                conn.execute(
                    """INSERT INTO group_b_block_states(project_id,run_id,block)
                       VALUES(%s,%s::uuid,%s)""",
                    (self.project,run,block)
                )
        return run

    def ready(self,run=None):
        run=run or self.run
        return ReadinessReport(self.project,run,PROOF,
            tuple(BlockReadiness(b,ReadinessState.READY if b=="B5"
                                 else ReadinessState.PARTIAL,
                                 () if b=="B5" else ("not_measured",),
                                 PROOF if b=="B5" else None) for b in BLOCKS),())

    def queue(self,run=None):
        run=run or self.run
        with self.psycopg.connect(self.dsn) as conn:
            with conn.transaction():
                return enqueue_jobs(
                    conn,project_id=self.project,run_id=run,
                    readiness=self.ready(run),selected_blocks=("B5",)
                )

    def claim(self):
        with self.psycopg.connect(self.dsn) as conn:
            with conn.transaction():
                return claim_local(conn,project_id=self.project)

    def test_paid_reserved_job_cannot_be_claimed_or_directly_run(self):
        with self.psycopg.connect(self.dsn) as conn:
            quote=conn.execute(
                """INSERT INTO group_b_model_quotes(project_id,run_id,model_id,
                     request_sha256,evidence_sha256,quote_sha256,quoted_usd,
                     pricing_snapshot,quote_details,expires_at)
                   VALUES(%s,%s::uuid,'test/fake',%s,%s,%s,0.001,
                          '{}'::jsonb,'{"block":"B1"}'::jsonb,
                          now()+INTERVAL '10 minutes')
                   RETURNING id""",
                (self.project,self.run,"e"*64,PROOF,"1"*64)
            ).fetchone()[0]
            approval=conn.execute(
                """INSERT INTO group_b_model_approvals(project_id,run_id,quote_id,
                     approved_by,authorized_usd)
                   VALUES(%s,%s::uuid,%s,'authenticated-fixture',0.001)
                   RETURNING id""",
                (self.project,self.run,quote)
            ).fetchone()[0]
            attempt=conn.execute(
                """INSERT INTO group_b_model_attempts(project_id,run_id,quote_id,
                       approval_id,request_key,request_sha256,reserved_usd)
                   VALUES(%s,%s::uuid,%s,%s,%s,%s,0.001)
                   RETURNING id""",
                (self.project,self.run,quote,approval,"fake-"+uuid4().hex,"e"*64)
            ).fetchone()[0]
        readiness=ReadinessReport(self.project,self.run,PROOF,
            tuple(BlockReadiness(b,ReadinessState.READY if b=="B1"
                                 else ReadinessState.PARTIAL,
                                 () if b=="B1" else ("not_measured",),
                                 PROOF if b=="B1" else None) for b in BLOCKS),())
        with self.psycopg.connect(self.dsn) as conn:
            ids=enqueue_jobs(conn,project_id=self.project,run_id=self.run,
                             readiness=readiness,selected_blocks=("B1",),
                             paid_attempts={"B1":str(attempt)})
        self.assertEqual(len(ids),1)
        self.assertIsNone(self.claim())
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute(
                        """UPDATE group_b_jobs SET state='running',attempts=1,
                                  lease_generation=1,lease_token=gen_random_uuid(),
                                  lease_until=now()+INTERVAL '1 minute'
                           WHERE id=%s::uuid""",(ids[0],)
                    )
            self.assertEqual(conn.execute(
                "SELECT charge_state FROM group_b_model_attempts WHERE id=%s",
                (attempt,)
            ).fetchone()[0],"reserved")

    def test_dry_run_no_mutation_and_replayed_enqueue(self):
        with self.psycopg.connect(self.dsn) as conn:
            selected=enqueue_jobs(
                conn,project_id=self.project,run_id=self.run,
                readiness=self.ready(),selected_blocks=("B5",),dry_run=True
            )
            self.assertEqual(selected,("B5",))
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_jobs WHERE project_id=%s",
                (self.project,)
            ).fetchone()[0],0)
        initial=self.queue()
        self.assertEqual(len(initial),1)
        self.assertEqual(self.queue(),initial)
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_job_events WHERE project_id=%s AND event_type='enqueued'",
                (self.project,)
            ).fetchone()[0],1)

    def test_two_workers_only_one_claim(self):
        self.queue()
        barrier=Barrier(2)
        def worker():
            barrier.wait(timeout=12)
            return self.claim()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(worker) for _ in range(2)]
            results=[f.result(timeout=20) for f in futures]
        self.assertEqual(sum(x is not None for x in results),1)
        winner=next(x for x in results if x is not None)
        self.assertEqual(winner.block,"B5")
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute(
                "SELECT attempts FROM group_b_jobs WHERE id=%s::uuid",
                (winner.id,)
            ).fetchone()[0],1)

    def test_stolen_expired_local_lease_fences_old_worker(self):
        self.queue()
        first=self.claim()
        self.assertIsNotNone(first)
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """UPDATE group_b_jobs SET lease_until=now()-INTERVAL '5 seconds'
                    WHERE id=%s::uuid""",(first.id,)
            )
        second=self.claim()
        self.assertEqual(second.id,first.id)
        self.assertNotEqual(first.lease_token,second.lease_token)
        self.assertEqual(second.generation,first.generation+1)
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(DependencyUnavailable):
                finish_job(conn,first,state="complete",result_sha256="1"*64)
            heartbeat(conn,second)
            finish_job(conn,second,state="partial",reason="engine_not_connected")
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute(
                "SELECT state FROM group_b_jobs WHERE id=%s::uuid",
                (second.id,)
            ).fetchone()[0],"partial")

    def test_cancel_run_invalidates_running_lease_and_preserves_history(self):
        self.queue()
        claimed=self.claim()
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(cancel_run(
                conn,project_id=self.project,run_id=self.run
            ),1)
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(DependencyUnavailable):
                heartbeat(conn,claimed)
            self.assertIsNone(claim_local(conn,project_id=self.project))
            self.assertEqual(conn.execute(
                "SELECT state FROM group_b_runs WHERE id=%s::uuid",
                (self.run,)
            ).fetchone()[0],"cancelled")
            kinds=[r[0] for r in conn.execute(
                """SELECT event_type FROM group_b_job_events
                   WHERE project_id=%s ORDER BY id""",(self.project,)
            ).fetchall()]
            self.assertEqual(kinds,["enqueued","claimed","cancelled"])
            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute(
                        "DELETE FROM group_b_job_events WHERE project_id=%s",
                        (self.project,)
                    )

    def test_lost_worker_exhausts_retry_without_false_completion(self):
        self.queue()
        for i in range(3):
            claim=self.claim()
            self.assertIsNotNone(claim)
            with self.psycopg.connect(self.dsn) as conn:
                conn.execute(
                    """UPDATE group_b_jobs SET lease_until=now()-INTERVAL '5 seconds'
                       WHERE id=%s::uuid""",(claim.id,)
                )
        self.assertIsNone(self.claim())
        with self.psycopg.connect(self.dsn) as conn:
            row=conn.execute(
                "SELECT state,attempts,result_sha256 FROM group_b_jobs WHERE id=%s::uuid",
                (claim.id,)
            ).fetchone()
            self.assertEqual(row,("running",3,None))

    def test_power_off_exhaustion_reaper_is_not_complete(self):
        self.queue()
        for _ in range(3):
            worker=self.claim()
            self.assertIsNotNone(worker)
            with self.psycopg.connect(self.dsn) as conn:
                conn.execute(
                    """UPDATE group_b_jobs SET lease_until=now()-INTERVAL '1 second'
                         WHERE id=%s::uuid""",(worker.id,)
                )
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(reap_expired_jobs(
                conn,project_id=self.project,run_id=self.run
            ),1)
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute(
                "SELECT state,result_sha256 FROM group_b_jobs WHERE id=%s::uuid",
                (worker.id,)
            ).fetchone(),("failed",None))
            self.assertEqual(conn.execute(
                "SELECT state FROM group_b_runs WHERE id=%s::uuid",
                (self.run,)
            ).fetchone()[0],"partial")
            self.assertEqual(conn.execute(
                """SELECT count(*) FROM group_b_job_events
                   WHERE job_id=%s::uuid AND event_type='lease_expired'""",
                (worker.id,)
            ).fetchone()[0],1)
        self.assertIsNone(self.claim())

    def test_only_claimed_generation_can_finish_and_no_run_completed(self):
        self.queue()
        selected=self.claim()
        with self.psycopg.connect(self.dsn) as conn:
            finish_job(conn,selected,state="partial",reason="not_measured")
        with self.psycopg.connect(self.dsn) as conn:
            row=conn.execute(
                """SELECT r.state,j.state,j.result_sha256,b.status
                   FROM group_b_runs r JOIN group_b_jobs j ON j.run_id=r.id
                   JOIN group_b_block_states b ON b.run_id=r.id AND b.block=j.block
                   WHERE r.id=%s::uuid""",(self.run,)
            ).fetchone()
        self.assertEqual(row,("partial","partial",None,"partial"))
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(DependencyUnavailable):
                finish_job(conn,selected,state="complete",result_sha256="f"*64)


if __name__=="__main__":
    unittest.main()
