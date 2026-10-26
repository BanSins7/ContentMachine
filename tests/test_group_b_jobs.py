"""GB-MOD-11 pure DAG/fencing, dry-run, fake result, and paid denial tests."""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock,patch
from uuid import uuid4
import unittest

from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.dependency_rules import (
    BLOCKS, BlockReadiness,ReadinessReport,ReadinessState
)
from intelligence.group_b.errors import DependencyUnavailable,ExecutionNotApproved
from intelligence.group_b.orchestrator import (
    DEPENDENCIES,KIND,JobClaim,enqueue_jobs,claim_local,finish_job,
    heartbeat,cancel_run
)
from intelligence.group_b.worker import (
    LocalWorker,LocalResult,WorkerSources,PaidWorker
)

P="a"*32
R=str(uuid4())
SHA="b"*64
J=str(uuid4())
TOKEN=str(uuid4())

def report(*blocks):
    states=tuple(BlockReadiness(b,ReadinessState.READY if b in blocks
                                else ReadinessState.PARTIAL,
                                () if b in blocks else ("not_measured",),
                                SHA if b in blocks else None) for b in BLOCKS)
    return ReadinessReport(P,R,SHA,states,())


class Response:
    def __init__(self,first=None,many=()):
        self.first=first
        self.many=many
    def fetchone(self):
        return self.first
    def fetchall(self):
        return self.many


class EnqueueConn:
    def __init__(self):
        self.sql=[]
        self.jobs={}
    def execute(self,sql,args=()):
        self.sql.append((sql,args))
        if "SELECT status,evidence_sha256 FROM group_b_block_states" in sql:
            return Response({"status":"not_run","evidence_sha256":None})
        if "UPDATE group_b_block_states" in sql and "RETURNING block" in sql:
            return Response({"block":args[3]})
        if "SELECT r.id::text AS id,r.state" in sql:
            return Response({"id":R,"state":"draft",
                             "a_snapshot_sha256":"c"*64,
                             "media_manifest_sha256":"d"*64,
                             "inbox_status":"accepted"})
        if "INSERT INTO group_b_jobs" in sql:
            block=args[2]
            if block in self.jobs:
                return Response()
            job={"id":str(uuid4()),"lease_generation":0}
            self.jobs[block]={**job,"block":block,"kind":"local",
                              "depends_on":list(DEPENDENCIES[block]),
                              "attempt_id":None,"evidence_sha256":SHA,
                              "state":"ready"}
            return Response(job)
        if "SELECT id::text AS id,block,kind" in sql:
            return Response(self.jobs.get(args[2]))
        if "FROM group_b_model_attempts a" in sql:
            return Response()
        return Response()


class FakeReaderConn:
    def __init__(self,claim):
        self.claim=claim
        self.calls=[]
    def execute(self,sql,args=()):
        self.calls.append((sql,args))
        if "SELECT r.id::text AS run_id" in sql:
            return Response({"run_id":R})
        if "SELECT state FROM group_b_runs" in sql:
            return Response({"state":"running"})
        if "SELECT j.id::text AS id" in sql:
            return Response({
                "id":self.claim.id,"project_id":P,"run_id":R,"block":"B5",
                "kind":"local","evidence_sha256":SHA,"state":"ready",
                "attempts":0,"max_attempts":3,"lease_generation":0
            })
        if "UPDATE group_b_jobs" in sql and "RETURNING lease_token" in sql:
            return Response({"lease_token":TOKEN,"lease_generation":1,"attempts":1})
        if "SELECT j.id::text AS id,j.project_id" in sql:
            return Response({
                "id":self.claim.id,"project_id":P,"run_id":R,"block":"B5",
                "kind":"local","state":"running","evidence_sha256":SHA,
                "lease_token":TOKEN,"lease_generation":1,
                "lease_until":"future","run_state":"running"
            })
        if "SELECT %s > now()" in sql:
            return Response((True,))
        return Response()


def claim():
    return JobClaim(J,P,R,"B5","local",SHA,TOKEN,1,1,3)


class TestJobs(unittest.TestCase):
    def test_dag_explicit_local_and_paid_groups(self):
        self.assertEqual(KIND["B5"],"local")
        self.assertTrue(all(KIND[x]=="paid" for x in BLOCKS if x!="B5"))
        self.assertEqual(DEPENDENCIES["B3"],("B2",))
        self.assertEqual(DEPENDENCIES["B6"],("B4",))
        self.assertEqual(enqueue_jobs(None,project_id=P,run_id=R,
                                      readiness=report("B5"),
                                      selected_blocks=("B5",),dry_run=True),
                         ("B5",))
        self.assertEqual(enqueue_jobs(None,project_id=P,run_id=R,
                                      readiness=report("B3"),
                                      selected_blocks=("B3",),dry_run=True),())
        self.assertEqual(enqueue_jobs(None,project_id=P,run_id=R,
                                      readiness=report("B2","B3"),
                                      selected_blocks=("B2","B3"),dry_run=True),
                         ("B2","B3"))

    def test_only_ready_local_job_enqueued_once(self):
        c=EnqueueConn()
        ids=enqueue_jobs(c,project_id=P,run_id=R,readiness=report("B5"),
                         selected_blocks=("B5",))
        self.assertEqual(len(ids),1)
        self.assertEqual(enqueue_jobs(c,project_id=P,run_id=R,
                                     readiness=report("B5"),
                                     selected_blocks=("B5",)),ids)
        self.assertEqual(len(c.jobs),1)
        self.assertEqual(len([sql for sql,_ in c.sql
                              if "INSERT INTO group_b_job_events" in sql]),1)

    def test_paid_refuses_job_without_real_reserved_approved_attempt(self):
        c=EnqueueConn()
        with self.assertRaises(ExecutionNotApproved):
            enqueue_jobs(c,project_id=P,run_id=R,
                         readiness=report("B1"),selected_blocks=("B1",))
        self.assertFalse(c.jobs)
        self.assertFalse(any("INSERT INTO group_b_jobs" in q for q,_ in c.sql))
        with self.assertRaises(ExecutionNotApproved):
            enqueue_jobs(c,project_id=P,run_id=R,
                         readiness=report("B1"),selected_blocks=("B1",),
                         paid_attempts={"B1":str(uuid4())})
        self.assertFalse(c.jobs)

    def test_claim_uses_skip_locked_and_fences_tokens(self):
        c=FakeReaderConn(claim())
        actual=claim_local(c,project_id=P)
        self.assertEqual(actual.lease_token,TOKEN)
        self.assertEqual(actual.generation,1)
        self.assertTrue(any("FOR UPDATE OF j SKIP LOCKED" in q for q,_ in c.calls))
        heartbeat(c,actual)
        self.assertTrue(any("INSERT INTO group_b_job_events" in q for q,_ in c.calls))
        with self.assertRaises(DependencyUnavailable):
            finish_job(c,replace(actual,lease_token=str(uuid4())),
                       state="complete",result_sha256="d"*64)

    def test_no_fabricated_completed_b_without_sha(self):
        c=FakeReaderConn(claim())
        with self.assertRaises(DependencyUnavailable):
            finish_job(c,claim(),state="complete")
        with self.assertRaises(DependencyUnavailable):
            finish_job(c,claim(),state="partial",result_sha256="d"*64)
        finish_job(c,claim(),state="partial",reason="no_engine")
        self.assertTrue(any("status=%s,evidence_sha256" in q for q,_ in c.calls))

    def test_lease_cancel_and_run_state_fence(self):
        c=FakeReaderConn(claim())
        original=c.execute
        def cancelled(sql,args=()):
            if "SELECT state FROM group_b_runs" in sql:
                return Response({"state":"cancelled"})
            return original(sql,args)
        with patch.object(c,"execute",side_effect=cancelled):
            with self.assertRaises(DependencyUnavailable):
                heartbeat(c,claim())

    def test_result_requires_canonical_output_proof(self):
        payload={"kind":"verified-local-fixture","cuts":[1,3]}
        good=LocalResult("B5",SHA,payload,canonical_hash(payload))
        self.assertTrue(good.verify_digest())
        self.assertFalse(replace(good,output_sha256="0"*64).verify_digest())
        self.assertFalse(replace(good,output={"cuts":[9]}).verify_digest())

    def test_paid_path_disabled_with_any_fabricated_approval(self):
        with self.assertRaises(ExecutionNotApproved):
            PaidWorker().run_once(project_id=P,approval={"approved":True})

    def test_worker_dry_run_without_executor_or_paid_transport(self):
        conn=MagicMock()
        conn.__enter__.return_value=conn
        conn.__exit__.return_value=False
        conn.execute.return_value=Response(many=(("B5",),))
        worker=LocalWorker(lambda:conn)
        result=worker.run_once(project_id=P,sources=None,dry_run=True)
        self.assertEqual(result.status,"dry_run")
        self.assertEqual(result.block,"B5")
        self.assertFalse(any("UPDATE " in str(c) or "INSERT INTO " in str(c)
                             for c in conn.execute.call_args_list))

    def test_worker_revalidates_rights_then_marks_partial_not_complete(self):
        conn=MagicMock()
        conn.__enter__.return_value=conn
        conn.transaction.return_value.__enter__.return_value=conn
        sources=WorkerSources(17,"d"*64,("/valid-root",),object())
        with patch("intelligence.group_b.worker.claim_local",return_value=claim()),patch(
            "intelligence.group_b.worker.require_fresh_block",
            side_effect=DependencyUnavailable("qdp","rights_revoked")
        ) as preflight,patch("intelligence.group_b.worker.finish_job") as finish:
            result=LocalWorker(lambda:conn).run_once(
                project_id=P,sources=sources
            )
        self.assertEqual(result.status,"partial")
        self.assertEqual(result.reason,"preflight_unavailable")
        self.assertEqual(preflight.call_count,1)
        self.assertEqual(finish.call_args.kwargs["state"],"partial")
        self.assertNotIn("result_sha256",finish.call_args.kwargs)

    def test_worker_verifier_required_for_verified_local_completion(self):
        conn=MagicMock()
        conn.__enter__.return_value=conn
        payload={"algorithm":"future-local-fixture","measurements":[1,2]}
        result=LocalResult("B5",SHA,payload,canonical_hash(payload))
        executor=MagicMock()
        executor.execute_local.return_value=result
        verifier=MagicMock()
        verifier.verify_local_result.return_value=True
        sources=WorkerSources(23,"e"*64,("/valid-root",),object())
        with patch("intelligence.group_b.worker.claim_local",return_value=claim()),patch(
            "intelligence.group_b.worker.require_fresh_block",
            return_value=object()
        ) as fresh,patch("intelligence.group_b.worker.finish_job") as finish:
            got=LocalWorker(lambda:conn,executor=executor,
                            result_verifier=verifier).run_once(
                project_id=P,sources=sources
            )
        self.assertEqual(got.status,"complete")
        fresh.assert_called_once()
        verifier.verify_local_result.assert_called_once()
        self.assertEqual(finish.call_args.kwargs["state"],"complete")
        self.assertEqual(finish.call_args.kwargs["result_sha256"],result.output_sha256)

    def test_worker_unverified_output_remains_partial(self):
        conn=MagicMock()
        conn.__enter__.return_value=conn
        content={"observed":True}
        outcome=LocalResult("B5",SHA,content,canonical_hash(content))
        executor=MagicMock()
        executor.execute_local.return_value=outcome
        verifier=MagicMock()
        verifier.verify_local_result.return_value=False
        with patch("intelligence.group_b.worker.claim_local",return_value=claim()),patch(
            "intelligence.group_b.worker.require_fresh_block",return_value=object()
        ),patch("intelligence.group_b.worker.finish_job") as finish:
            result=LocalWorker(lambda:conn,executor=executor,
                               result_verifier=verifier).run_once(
                project_id=P,sources=WorkerSources(23,"e"*64,(),object())
            )
        self.assertEqual(result.status,"partial")
        self.assertEqual(result.reason,"local_result_unverified")
        self.assertEqual(finish.call_args.kwargs["state"],"partial")

    def test_migration_contains_independent_fenced_worker_tables(self):
        from pathlib import Path
        text=(Path(__file__).resolve().parents[1]/
              "intelligence/migrations/058_group_b_worker_jobs.sql").read_text("utf-8")
        self.assertIn("CREATE TABLE group_b_jobs",text)
        self.assertIn("CREATE TABLE group_b_job_events",text)
        self.assertIn("trg_group_b_job_events_immutable",text)
        self.assertIn("lease_generation",text)
        self.assertIn("group_b_job_identity_guard",text)
        self.assertNotIn("DROP TABLE",text)
        self.assertNotIn("ALTER TABLE content_b_",text)


if __name__=="__main__":
    unittest.main()
