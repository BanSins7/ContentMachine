"""GC-MOD-21 worker lease, expiry, retry, concurrency and atomic C7 QA.

DB fixtures require an explicitly disposable PostgreSQL 17 database. Any IAM
approval here is a synthetic test double, NEVER a production grant.
"""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from uuid import uuid4

from intelligence.group_c.contracts import ContractError
from intelligence.group_c.durable_worker import (
    WORKER_VERSION, WorkerPolicy, WorkerLease, WorkerCompletion,
    WorkerFailure, PostgreSQLGroupCWorker, retry_delay_seconds,
)


class TestWorkerPolicyPure(unittest.TestCase):
    def test_defaults_and_exponential_retry_with_cap(self):
        p = WorkerPolicy()
        self.assertEqual(p.max_running_per_project, 2)
        self.assertGreaterEqual(p.max_running_global,p.max_running_per_project)
        self.assertEqual([retry_delay_seconds(i,p) for i in (1,2,3,4,10)],
                         [5,10,20,40,300])

    def test_policy_requires_exact_bounded_positive_ints(self):
        for key,bad in (
            ("lease_seconds",0),("lease_seconds",True),("lease_seconds",1.5),
            ("max_running_per_project",0),("max_running_global",False),
            ("recovery_batch",1001),("retry_base_seconds",-1),
            ("retry_max_seconds",0),
        ):
            with self.subTest(key=key,bad=bad),self.assertRaises(ContractError):
                WorkerPolicy(**{key:bad})
        with self.assertRaises(ContractError):
            WorkerPolicy(retry_base_seconds=30,retry_max_seconds=20)

    def test_delay_validates_policy_and_attempt_count(self):
        for i in (0,101,True,1.5,"1",None):
            with self.subTest(attempt=i),self.assertRaises(ContractError):
                retry_delay_seconds(i,WorkerPolicy())
        with self.assertRaises(ContractError):
            retry_delay_seconds(1,object())

    def test_policy_is_frozen_and_cannot_be_mutated_at_runtime(self):
        p=WorkerPolicy()
        with self.assertRaises(FrozenInstanceError):
            p.lease_seconds=30

    def test_no_external_scheduler_or_guessed_identity(self):
        obj=PostgreSQLGroupCWorker()
        self.assertFalse(hasattr(obj,"background_loop"))
        self.assertFalse(hasattr(obj,"publish_to_analytics"))
        self.assertFalse(hasattr(obj,"ack_qdp"))
        for typevalue in (True,1,"foo"):
            with self.subTest(typevalue=typevalue),self.assertRaises(ContractError):
                PostgreSQLGroupCWorker(typevalue)

    def test_lease_validation_rejects_noncanonical_uuid_or_bool_ids(self):
        worker=PostgreSQLGroupCWorker()
        lease=WorkerLease(
            job_id=1,project_id="a"*32,run_id=str(uuid4()),video_id=2,
            worker_id="node-001",token=str(uuid4()),generation=1,
            attempt=1,expires_at=datetime.now(timezone.utc),
        )
        self.assertEqual(lease.schema_version,WORKER_VERSION)
        worker._lease(lease)
        for edit in ({"job_id":True},{"generation":0},{"token":"not-a-uuid"},
                     {"project_id":"not hex"},{"worker_id":"X"},
                     {"run_id":"NOT-UUID"},{"attempt":101},
                     {"expires_at":datetime(2026,1,1)}):
            with self.subTest(edit=edit),self.assertRaises(ContractError):
                worker._lease(replace(lease,**edit))

    def test_explicit_transient_error_allowlist(self):
        class DBFault(Exception):
            sqlstate="40001"
        class Unrelated(Exception):
            sqlstate="23505"
        self.assertTrue(PostgreSQLGroupCWorker._transient(DBFault()))
        self.assertFalse(PostgreSQLGroupCWorker._transient(Unrelated()))
        self.assertFalse(PostgreSQLGroupCWorker._transient(
            ContractError("invalid_review","invalid reviewer")))

    def test_error_codes_never_reflect_arbitrary_exception_text(self):
        worker=PostgreSQLGroupCWorker()
        self.assertFalse(worker._transient(ValueError("do not store this token")))
        self.assertFalse(worker._transient(ContractError("review_not_approved","not eligible")))


class _SyntheticC7IAM:
    """Disposability guard is above the PG test class, not this fake."""
    def __init__(self,allowed=True):
        self.allowed=allowed
        self.calls=0
    def verify_c7_persistence(self,conn,**kwargs):
        self.calls+=1
        return self.allowed


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "Explicit disposable PG17 only: CM_TEST_PG_DSN + GC_TEST_DISPOSABLE_DB=YES",
)
class TestDurableWorkerPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_group_c_config_activation import TestActivationPG17
        TestActivationPG17.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from test_group_c_config_activation import TestActivationPG17
        TestActivationPG17.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_config_activation import TestActivationPG17
        self.fixture=TestActivationPG17()
        self.fixture.conn=self.conn
        self.fixture.psycopg=self.psycopg
        self.fixture.setUp()
        for field in ("project","source","policy","target","config"):
            setattr(self,field,getattr(self.fixture,field))
        self.previous=self.conn.execute(
            """INSERT INTO metric_snapshots (
                  video_id,source_id,source_policy_version_id,
                  observed_at,collected_at,views,likes,comments,shares,
                  source_metric_definition,source_metric_version,
                  precision,data_quality_status,observation_time_assumed)
               VALUES(%s,%s,%s,now()-interval '4 hours',
                      now()-interval '3 hours',80,8,2,1,
                      'platform_post_views','v1','exact','ok',FALSE)
               RETURNING id""",
            (self.target[0],self.source,self.policy),
        ).fetchone()[0]
        self.worker=PostgreSQLGroupCWorker(
            WorkerPolicy(max_running_per_project=1,max_running_global=2,
                         lease_seconds=180,retry_base_seconds=1,
                         retry_max_seconds=8)
        )
        self.ids=[]
        self.make_job()

    def tearDown(self):
        self.fixture.tearDown()

    def make_job(self,*,video_id=None,score=True,max_attempts=3):
        video_id=self.target[0] if video_id is None else video_id
        event=str(uuid4())
        rid=str(uuid4())
        digest=uuid4().hex+uuid4().hex
        self.conn.execute(
            """INSERT INTO group_c_inbox (
                 event_id,project_id,video_id,qdp_run_id,
                 analysis_group,producer_schema,input_sha256,payload_sha256,
                 state,accepted_at)
               VALUES(%s::uuid,%s,%s,%s::uuid,'C','qdp-handoff/v1',
                      %s,%s,'accepted',now())""",
            (event,self.project,video_id,str(uuid4()),digest,digest),
        )
        idem=uuid4().hex+uuid4().hex
        self.conn.execute(
            """INSERT INTO group_c_runs(
                 id,project_id,video_id,inbox_event_id,qdp_input_sha256,
                 source_id,idempotency_sha256,snapshot_id,previous_snapshot_id,
                 published_metadata_id,calculation_config_id,
                 source_policy_version_id)
               VALUES(%s::uuid,%s,%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (rid,self.project,video_id,event,digest,self.source,idem,
             self.target[1],self.previous,self.target[2],self.config,self.policy),
        )
        job=self.conn.execute(
            """INSERT INTO group_c_jobs(
                 project_id,run_id,video_id,job_kind,idempotency_sha256,
                 max_attempts)
               VALUES(%s,%s::uuid,%s,%s,%s,%s) RETURNING id""",
            (self.project,rid,video_id,"score" if score else "invalidate",
             idem,max_attempts),
        ).fetchone()[0]
        self.ids.append((job,rid))
        return job,rid

    def request(self):
        req=self.fixture.signed()
        self.fixture.activate(req)
        return req

    def claim(self,name="node-001"):
        return self.worker.claim(self.conn,project_id=self.project,worker_id=name)

    def status(self,job_id):
        return self.conn.execute(
            """SELECT state,attempts,lease_generation,lease_token,lease_until,
                      worker_id,available_at FROM group_c_jobs WHERE id=%s""",
            (job_id,),
        ).fetchone()

    def test_queue_claim_skip_locked_and_reject_second_claim(self):
        first=self.claim()
        self.assertIsInstance(first,WorkerLease)
        self.assertEqual(first.job_id,self.ids[0][0])
        self.assertEqual(first.attempt,1)
        self.assertIsNone(self.claim("node-002"))
        self.assertEqual(self.status(first.job_id)[0],"running")
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_runs WHERE id=%s",(first.run_id,),
        ).fetchone()[0],"running")

    def test_skip_locked_real_second_connection(self):
        import psycopg
        job=self.ids[0][0]
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True) as lock_conn:
            with lock_conn.transaction():
                locked=lock_conn.execute(
                    "SELECT id FROM group_c_jobs WHERE id=%s FOR UPDATE",
                    (job,),
                ).fetchone()
                self.assertEqual(locked[0],job)
                # The second connection must not stall on the held row.
                self.assertIsNone(self.claim("node-002"))
            selected=self.claim("node-002")
            self.assertIsNotNone(selected)
            self.assertEqual(selected.job_id,job)

    def test_heartbeat_updates_only_live_fence(self):
        lease=self.claim()
        later=self.worker.heartbeat(self.conn,lease=lease)
        self.assertGreaterEqual(later,lease.expires_at)
        with self.assertRaises(ContractError) as e:
            self.worker.heartbeat(self.conn,lease=replace(lease,generation=lease.generation+1))
        self.assertEqual(e.exception.code,"worker_lease_lost")
        with self.assertRaises(ContractError):
            self.worker.heartbeat(self.conn,lease=replace(lease,token=str(uuid4())))

    def test_cancel_running_and_queued_fail_closed_and_replay(self):
        lease=self.claim()
        self.assertTrue(self.worker.cancel(
            self.conn,project_id=self.project,job_id=lease.job_id))
        self.assertFalse(self.worker.cancel(
            self.conn,project_id=self.project,job_id=lease.job_id))
        self.assertEqual(self.status(lease.job_id)[0],"cancelled")
        with self.assertRaises(ContractError):
            self.worker.heartbeat(self.conn,lease=lease)
        with self.assertRaises(ContractError):
            self.worker.execute_score(
                self.conn,lease=lease,review_request_id=str(uuid4()),
                approval_verifier=_SyntheticC7IAM(),
            )
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_runs WHERE id=%s",(lease.run_id,),
        ).fetchone()[0],"cancelled")

    def test_retryable_failure_schedules_backoff_and_fences(self):
        lease=self.claim()
        failure=self.worker.fail(self.conn,lease=lease,
                                 error_code="pg_retryable",retryable=True)
        self.assertIsInstance(failure,WorkerFailure)
        self.assertEqual(failure.state,"queued")
        self.assertIsNotNone(failure.next_attempt_at)
        self.assertEqual(self.status(lease.job_id)[0],"queued")
        with self.assertRaises(ContractError):
            self.worker.heartbeat(self.conn,lease=lease)
        self.conn.execute(
            "UPDATE group_c_jobs SET available_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",(lease.job_id,),
        )
        again=self.claim("node-002")
        self.assertEqual(again.generation,lease.generation+2)
        self.assertEqual(again.attempt,lease.attempt+1)

    def test_expired_lease_recovered_then_fenced_and_reclaimed(self):
        lease=self.claim()
        self.conn.execute(
            "UPDATE group_c_jobs SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",(lease.job_id,),
        )
        self.assertEqual(self.worker.recover_expired(
            self.conn,project_id=self.project),1)
        self.assertEqual(self.status(lease.job_id)[0],"queued")
        with self.assertRaises(ContractError):
            self.worker.heartbeat(self.conn,lease=lease)
        self.conn.execute(
            "UPDATE group_c_jobs SET available_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",(lease.job_id,),
        )
        again=self.claim("node-002")
        self.assertEqual(again.attempt,2)
        self.assertEqual(again.generation,3)
        with self.assertRaises(ContractError):
            self.worker.fail(self.conn,lease=lease,
                             error_code="old_lease",retryable=False)

    def test_exhausted_expired_attempt_fails_terminal(self):
        job,_=self.ids[0]
        self.conn.execute(
            "UPDATE group_c_jobs SET max_attempts=1 WHERE id=%s",(job,),
        )
        lease=self.claim()
        self.conn.execute(
            "UPDATE group_c_jobs SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",(job,),
        )
        self.assertEqual(self.worker.recover_expired(
            self.conn,project_id=self.project),1)
        self.assertEqual(self.status(job)[0],"failed")
        self.assertIsNone(self.claim("node-002"))
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_runs WHERE id=%s",(lease.run_id,),
        ).fetchone()[0],"failed")

    def test_budget_and_other_job_kind_remain_unclaimed(self):
        self.make_job(score=False)
        self.make_job()
        lease=self.claim()
        self.assertIsNone(self.claim("node-002"))
        self.worker.cancel(self.conn,project_id=self.project,job_id=lease.job_id)
        fresh=self.claim("node-002")
        self.assertIsNotNone(fresh)
        self.assertNotEqual(fresh.job_id,lease.job_id)
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_jobs WHERE id=%s",(self.ids[1][0],),
        ).fetchone()[0],"queued")

    def test_persistence_and_job_completion_are_same_commit(self):
        req=self.request()
        lease=self.claim()
        iam=_SyntheticC7IAM()
        completed=self.worker.execute_score(
            self.conn,lease=lease,review_request_id=req.request_id,
            approval_verifier=iam,
        )
        self.assertIsInstance(completed,WorkerCompletion)
        self.assertTrue(completed.score.inserted)
        self.assertEqual(completed.score.status,"persisted_complete_not_published")
        self.assertEqual(iam.calls,1)
        saved=self.conn.execute(
            """SELECT j.state,r.state,r.calculated_metric_id,r.completed_at,
                      m.score_status,c.evidence_sha256
                 FROM group_c_jobs j
                 JOIN group_c_runs r ON r.id=j.run_id
                 JOIN calculated_metrics m ON m.id=r.calculated_metric_id
                 JOIN group_c_calculation_receipts c
                   ON c.calculated_metric_id=m.id
                WHERE j.id=%s""",(lease.job_id,),
        ).fetchone()
        self.assertEqual(saved[0:2],("completed","completed"))
        self.assertEqual(saved[2],completed.score.calculated_metric_id)
        self.assertIsNotNone(saved[3])
        self.assertEqual(saved[4],"complete")
        self.assertEqual(saved[5],completed.score.evidence_sha256)
        self.assertIsNone(self.claim("node-002"))
        with self.assertRaises(ContractError):
            self.worker.heartbeat(self.conn,lease=lease)

    def test_untrusted_iam_rolls_back_score_and_run_completion(self):
        req=self.request()
        lease=self.claim()
        with self.assertRaises(ContractError):
            self.worker.execute_score(
                self.conn,lease=lease,review_request_id=req.request_id,
                approval_verifier=_SyntheticC7IAM(False),
            )
        self.assertEqual(self.status(lease.job_id)[0],"running")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM calculated_metrics WHERE snapshot_id=%s",
            (self.target[1],),
        ).fetchone()[0],0)
        failure=self.worker.fail(
            self.conn,lease=lease,error_code="human_auth_unavailable",
            retryable=False,
        )
        self.assertEqual(failure.state,"failed")

    def test_wrong_review_request_or_stale_fence_never_writes(self):
        req=self.request()
        lease=self.claim()
        for candidate in (
            str(uuid4()),
            req.request_id.upper(),
        ):
            with self.subTest(candidate=candidate),self.assertRaises(ContractError):
                self.worker.execute_score(
                    self.conn,lease=lease,review_request_id=candidate,
                    approval_verifier=_SyntheticC7IAM(),
                )
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM calculated_metrics WHERE snapshot_id=%s",
            (self.target[1],),
        ).fetchone()[0],0)

    def test_budget_and_project_ids_fail_closed(self):
        with self.assertRaises(ContractError):
            self.worker.claim(self.conn,project_id="cross-project",worker_id="node-001")
        with self.assertRaises(ContractError):
            self.worker.claim(self.conn,project_id=self.project,worker_id="x")
        with self.assertRaises(ContractError):
            self.worker.cancel(self.conn,project_id=uuid4().hex,
                               job_id=self.ids[0][0])
        with self.assertRaises(ContractError):
            self.worker.execute_score(
                self.conn,lease=object(),review_request_id=str(uuid4()),
                approval_verifier=_SyntheticC7IAM(),
            )

    def test_run_once_authorization_failure_is_terminal_not_success(self):
        req=self.request()
        result=self.worker.run_once(
            self.conn,project_id=self.project,worker_id="node-001",
            review_request_id=req.request_id,approval_verifier=_SyntheticC7IAM(False),
        )
        self.assertIsInstance(result,WorkerFailure)
        self.assertEqual(result.state,"failed")
        self.assertEqual(self.status(self.ids[0][0])[0],"failed")
        self.assertIsNone(self.claim("node-002"))

    def test_run_once_no_claim_does_not_fabricate_job(self):
        result=self.worker.run_once(
            self.conn,project_id=uuid4().hex,worker_id="node-001",
            review_request_id=str(uuid4()),approval_verifier=_SyntheticC7IAM(),
        )
        self.assertIsNone(result)


if __name__=="__main__":
    unittest.main()
