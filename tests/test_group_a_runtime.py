"""GA20 pure DAG and disposable PG17 multi-worker/restart/revocation tests.

No public provider or paid model calls. Real QDP authorization, file hash
verification and Group B ACK are injected fake ports in these tests only.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4
import os
import unittest

from intelligence.group_a.cost_resources import (
    RuntimeDenied, ResourceBudget, Stage, plan_for_kind, verify_plan,
    retry_seconds, step_input_sha,
)
from intelligence.group_a.task_queue import (
    install_run_dag, claim_next_step, finish_step, heartbeat_step,
    recover_worker_leases, lease_is_live, pause_run, reuse_verified_cache,
)
from intelligence.group_a.invalidation import (
    invalidate_run,cancel_run,read_current_result
)
from intelligence.group_a.delivery import (
    claim_outbox,acknowledge_delivery,fail_delivery,
    recover_outbox,deliver_once
)
from intelligence.group_a.worker import (
    LocalGAOrchestrator,LocalStepOutput,run_bounded_local_command
)


def allow(_):
    return True


class GA20Pure(unittest.TestCase):
    def test_each_media_kind_has_acyclic_explicit_dag(self):
        for kind in ("video","slideshow_video","photo_carousel",
                     "mixed_carousel","image_post"):
            with self.subTest(kind=kind):
                dag=verify_plan(plan_for_kind(kind))
                self.assertEqual(dag[0].key,"initial:v1")
                self.assertEqual(dag[-1].kind,"assembly")
                self.assertIn("assembly:v1",{step.key for step in dag})
                self.assertTrue(all(st.max_runtime_sec<=3600 for st in dag))
                self.assertTrue(all(st.kind!="paid_llm" for st in dag))

    def test_unknown_dependency_cycle_and_duplicate_stage_denied(self):
        for bad in (
            (Stage("initial:v1","technical"),Stage("assembly:v1","assembly",
                                                   ("does_not_exist",))),
            (Stage("initial:v1","technical"),Stage("one","frames",("two",)),
             Stage("two","ocr",("one",))),
            (Stage("initial:v1","technical"),Stage("initial:v1","technical")),
        ):
            with self.assertRaises(RuntimeDenied):
                verify_plan(bad)

    def test_budget_gpu_and_noncanonical_input_validation(self):
        with self.assertRaises(RuntimeDenied):
            ResourceBudget(cpu_slots=0)
        with self.assertRaises(RuntimeDenied):
            ResourceBudget(gpu_slots=9)
        with self.assertRaises(RuntimeDenied):
            Stage("bad stage","ocr")
        with self.assertRaises(RuntimeDenied):
            Stage("bad:v1","subtitles",gpu_slots=1)
        self.assertEqual(step_input_sha("a"*64,Stage("initial:v1","technical")),
                         "a"*64)
        self.assertEqual(step_input_sha("a"*64,Stage("ocr:v1","ocr",("initial:v1",))),
                         step_input_sha("a"*64,Stage("ocr:v1","ocr",("initial:v1",))))
        with self.assertRaises(RuntimeDenied):
            step_input_sha("Z"*64,Stage("initial:v1","technical"))

    def test_bounded_retry_and_subprocess_without_shell(self):
        self.assertEqual([retry_seconds(n) for n in (1,2,3,8,20)],
                         [2,4,8,256,256])
        with self.assertRaises(RuntimeDenied):
            run_bounded_local_command(["python","-c","print('ok')"],timeout_sec=0)
        with self.assertRaises(RuntimeDenied):
            run_bounded_local_command("echo unsafe",timeout_sec=1)
        with self.assertRaises(RuntimeDenied):
            LocalStepOutput("invalid")
        self.assertEqual(LocalStepOutput("d"*64).partial,False)

    def test_worker_is_explicitly_injected_no_automatic_start(self):
        def c():
            raise AssertionError("must not connect at constructor")
        instance=LocalGAOrchestrator(
            connect=c,executors={"technical":lambda claim,stop:LocalStepOutput("a"*64)},
            verify_current=allow,verify_output=lambda *_:True,
            budget=ResourceBudget(cpu_slots=2),heartbeat_period_sec=5
        )
        self.assertIsNotNone(instance)
        with self.assertRaises(RuntimeDenied):
            LocalGAOrchestrator(connect=c,executors={"paid_llm":lambda *_:None},
                                verify_current=allow,verify_output=allow)


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),
                     "Disposable PostgreSQL 17 DSN not configured")
class GA20Postgres(unittest.TestCase):
    """Independent schema per test via GA04 fixture; real SQL057 needs PG17."""
    def setUp(self):
        from test_group_a_handoff import GA04Postgres,envelope,signed
        self.connect=GA04Postgres.connect.__get__(self)
        self.receiver=GA04Postgres.receiver.__get__(self)
        self.tear_down_peer=GA04Postgres.tearDown.__get__(self)
        GA04Postgres.setUp(self)
        self.event=envelope(self.p)
        self.run=self.receiver().receive(self.event,signed(self.event))["run_id"]
        with self.connect() as conn:
            self.plan=install_run_dag(
                conn,self.m,run_id=self.run,verify_current=allow,
                budget=ResourceBudget(cpu_slots=4)
            )

    def tearDown(self):
        self.tear_down_peer()

    def claim(self,worker):
        with self.connect() as conn:
            return claim_next_step(
                conn,worker_id=worker,
                budget=ResourceBudget(cpu_slots=4,max_concurrent_jobs=3),
                verify_current=allow,
            )

    def finish(self,claim,sha="d"*64,*,partial=False):
        with self.connect() as conn:
            return finish_step(
                conn,claim,output_sha256=sha,partial=partial,
                verify_current=allow,verify_output=lambda *_:True,
            )

    def test_install_idempotent_dag_and_multiple_workers_once(self):
        with self.connect() as conn:
            duplicate=install_run_dag(conn,self.m,run_id=self.run,
                 verify_current=allow,budget=ResourceBudget(cpu_slots=4))
        self.assertEqual(duplicate,self.plan)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a,b=list(pool.map(self.claim,("ga20worker-a","ga20worker-b")))
        self.assertIsNotNone(a)
        self.assertIsNone(b)  # Only initial:v1 dependency-free.
        self.assertEqual(a.step_key,"initial:v1")
        self.assertEqual(self.finish(a),"completed")
        with self.assertRaisesRegex(RuntimeDenied,"stale_or_revoked_step_lease"):
            self.finish(a)
        c=self.claim("ga20worker-b")
        self.assertIsNotNone(c)
        self.assertIn(c.step_key,("frames:v1","audio_decode:v1","subtitles:v1"))
        self.assertEqual(c.attempt_no,1)

    def test_lease_renewal_death_recovery_and_fenced_old_worker(self):
        a=self.claim("ga20dead")
        with self.connect() as conn:
            lease=heartbeat_step(
                conn,a,lease_seconds=45,verify_current=allow
            )
        self.assertIsNotNone(lease)
        with self.connect() as conn:
            conn.execute(
                """UPDATE ga_steps SET lease_until=now()-interval '1 second'
                   WHERE id=%s""",(a.step_id,)
            )
            recover_worker_leases(conn)
            status=conn.execute(
                "SELECT state,worker_id,lease_until FROM ga_steps WHERE id=%s",
                (a.step_id,)
            ).fetchone()
            self.assertEqual(status,("failed",None,None))
            self.assertEqual(conn.execute(
                "SELECT state FROM ga_step_attempts WHERE id=%s",(a.attempt_id,)
            ).fetchone()[0],"lost")
            self.assertEqual(conn.execute(
                "SELECT state FROM ga_resource_claims WHERE attempt_id=%s",
                (a.attempt_id,)
            ).fetchone()[0],"released")
            conn.execute(
                """UPDATE ga_step_attempts
                   SET finished_at=now()-interval '10 seconds'
                   WHERE id=%s""",(a.attempt_id,)
            )
            recover_worker_leases(conn)
            self.assertEqual(conn.execute(
                "SELECT state FROM ga_steps WHERE id=%s",(a.step_id,)
            ).fetchone()[0],"queued")
        renewed=self.claim("ga20replacement")
        self.assertEqual(renewed.step_id,a.step_id)
        self.assertNotEqual(renewed.token,a.token)
        with self.connect() as conn:
            with self.assertRaisesRegex(RuntimeDenied,"stale_or_revoked_step_lease"):
                finish_step(
                    conn,a,output_sha256="d"*64,
                    verify_current=allow,verify_output=lambda *_:True,
                )
        self.assertEqual(self.finish(renewed),"completed")

    def test_parallel_resource_budget_and_priority_limits(self):
        start=self.claim("starter")
        self.finish(start)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(self.claim,("worker-a","worker-b","worker-c")))
        active=[x for x in results if x is not None]
        self.assertGreaterEqual(len(active),1)
        self.assertLessEqual(sum(x.cpu_slots for x in active),4)
        self.assertLessEqual(len(active),3)
        self.assertEqual(len({x.step_id for x in active}),len(active))
        with self.connect() as conn:
            used=conn.execute(
                "SELECT coalesce(sum(cpu_slots),0) FROM ga_resource_claims WHERE state='active'"
            ).fetchone()[0]
            self.assertLessEqual(used,4)

    def test_pause_resume_does_not_cancel_a_running_attempt(self):
        with self.connect() as conn:
            pause_run(conn,self.run,paused=True,verify_current=allow)
        self.assertIsNone(self.claim("paused-worker"))
        with self.connect() as conn:
            pause_run(conn,self.run,paused=False,verify_current=allow)
        self.assertIsNotNone(self.claim("unpaused-worker"))

    def test_invalidate_with_active_worker_and_disallow_current_access(self):
        claim=self.claim("revoked-worker")
        with self.connect() as conn:
            invalidate_run(
                conn,run_id=self.run,reason_code="qdp_revoked",
                verify_authority=allow
            )
            self.assertFalse(lease_is_live(conn,claim))
            r=conn.execute(
                "SELECT state,invalidated_reason FROM ga_runs WHERE id=%s::uuid",
                (self.run,)
            ).fetchone()
            self.assertEqual(r,("invalidated","qdp_revoked"))
            self.assertEqual(conn.execute(
                "SELECT state FROM ga_step_attempts WHERE id=%s",
                (claim.attempt_id,)
            ).fetchone()[0],"cancelled")
            with self.assertRaises(RuntimeDenied):
                read_current_result(
                    conn,run_id=self.run,result_id=1,
                    expected_sha256="a"*64,verify_current=allow,
                    verify_result=allow
                )
        self.assertIsNone(self.claim("after-revocation"))

    def test_durable_partial_result_without_auto_retrying_payment(self):
        a=self.claim("failure")
        with self.connect() as conn:
            state=finish_step(
                conn,a,error_code="local_decoder_failed",
                verify_current=allow
            )
            self.assertEqual(state,"failed")
            cnt=conn.execute(
                "SELECT attempts FROM ga_steps WHERE id=%s",(a.step_id,)
            ).fetchone()[0]
            self.assertEqual(cnt,1)
            self.assertEqual(conn.execute(
                "SELECT state FROM ga_resource_claims WHERE attempt_id=%s",
                (a.attempt_id,)
            ).fetchone()[0],"released")

    def test_verified_cache_reuse_is_exact_and_requires_source_proof(self):
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO ga_cache_refs
                   (run_id,project_id,media_asset_id,cache_kind,
                    input_sha256,result_sha256)
                   VALUES(%s::uuid,%s,%s,'probe',%s,%s)""",
                (self.run,self.pid,self.asset,self.m["manifest_sha256"],
                 "e"*64)
            )
            with self.assertRaisesRegex(RuntimeDenied,"cached_media_bytes_or_origin_invalid"):
                reuse_verified_cache(
                    conn,run_id=self.run,step_key="initial:v1",
                    media_asset_id=self.asset,verify_current=allow,
                    verify_cache=lambda _:False,
                )
            result=reuse_verified_cache(
                conn,run_id=self.run,step_key="initial:v1",
                media_asset_id=self.asset,verify_current=allow,
                verify_cache=allow,
            )
            self.assertEqual(result["state"],"completed")
            self.assertEqual(result["output_sha256"],"e"*64)
            self.assertEqual(conn.execute(
                "SELECT attempts FROM ga_steps WHERE run_id=%s::uuid AND step_key='initial:v1'",
                (self.run,)
            ).fetchone()[0],1)
            with self.assertRaisesRegex(RuntimeDenied,"cache_step_not_fresh_queued"):
                reuse_verified_cache(
                    conn,run_id=self.run,step_key="initial:v1",
                    media_asset_id=self.asset,
                    verify_current=allow,verify_cache=allow,
                )

    def test_exhausted_retries_block_run_and_free_resources(self):
        a=self.claim("exhausted-worker")
        with self.connect() as conn:
            conn.execute(
                "UPDATE ga_steps SET max_attempts=1 WHERE id=%s",
                (a.step_id,)
            )
            self.assertEqual(finish_step(
                conn,a,error_code="local_decoder_failed",
                verify_current=allow
            ),"failed")
            recover_worker_leases(conn)
            self.assertEqual(conn.execute(
                "SELECT state FROM ga_runs WHERE id=%s::uuid",
                (self.run,)
            ).fetchone()[0],"blocked")
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM ga_resource_claims WHERE state='active'"
            ).fetchone()[0],0)
        self.assertIsNone(self.claim("no-more-claims"))

    def test_cancel_run_is_terminal_and_preserves_completed_history(self):
        a=self.claim("cancel-worker")
        with self.connect() as conn:
            self.assertEqual(cancel_run(
                conn,run_id=self.run,verify_authority=allow
            ),"cancelled")
            with self.assertRaises(RuntimeDenied):
                finish_step(
                    conn,a,output_sha256="a"*64,verify_current=allow,
                    verify_output=lambda *_:True
                )
            self.assertEqual(conn.execute(
                "SELECT state FROM ga_runs WHERE id=%s::uuid",
                (self.run,)
            ).fetchone()[0],"cancelled")
