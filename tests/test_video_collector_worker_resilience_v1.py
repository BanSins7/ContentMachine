"""VC-MOD-07: DTK worker lease fences, submit uncertainty and GET-only retry QA.

No live provider/paid network calls. This validates DB boundaries with
deterministic fakes, separate from PG17 integration and actual DTK smoke.
"""
from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch
from datetime import datetime, timezone

from intelligence.collector import worker
from intelligence.collector.contracts import CollectorError
from test_video_collector_worker_stage8 import fake_task

TASK_ID = "12345678-1234-4234-8234-123456789abc"


class Cursor:
    def __init__(self, row=None, rows=None):
        self.row=row
        self.rows=rows or []
    def fetchone(self):
        return self.row
    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, fence=True):
        self.fence=fence
        self.commands=[]
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def transaction(self):
        return self
    def execute(self, sql, params=None):
        self.commands.append((sql,params))
        if "JOIN collector_execution_grants g" in sql:
            return Cursor((1,) if self.fence else None)
        if "SELECT id FROM jobs WHERE" in sql:
            return Cursor((123,) if self.fence else None)
        return Cursor()


class Stage07WorkerTests(TestCase):
    def test_accepted_submit_response_canonical_uuid_and_request_id(self):
        got=worker._validate_submission({
            "task_id":TASK_ID.upper(), "provider_request_id":"request-123",
        })
        self.assertEqual(got,(TASK_ID,"request-123"))
        self.assertEqual(worker._validate_submission({"task_id":TASK_ID}),
                         (TASK_ID,None))

    def test_bad_provider_submit_response_never_authorizes_a_resubmit(self):
        for value in (None,{},{"task_id":"fake"},{"task_id":123},
                      {"task_id":TASK_ID,"provider_request_id":"bad\nrequest"}):
            with self.subTest(value=value), self.assertRaises(CollectorError):
                worker._validate_submission(value)
        class Bad:
            calls=0
            def submit(self, *args):
                self.calls+=1
                return {"state":"queued"}  # no task_id, outcome uncertain
        api=Bad()
        with patch("intelligence.collector.worker._assert_worker_config"),patch(
             "intelligence.collector.worker._preflight_submit",return_value=True
        ),patch("intelligence.collector.worker._claim_next",return_value=fake_task()
        ),patch("intelligence.collector.worker._update_after_provider",
                return_value=True) as saved:
            value=worker.process_one(worker_id="worker-one",provider=api)
        self.assertEqual(value["status"],"unknown")
        self.assertTrue(value["manual_reconciliation_required"])
        self.assertEqual(api.calls,1)
        self.assertEqual(saved.call_args.kwargs["next_state"],"unknown")

    def test_claim_revoked_between_intent_and_submit_prevents_post(self):
        class Provider:
            def submit(self,*args):
                raise AssertionError("No POST allowed after revocation")
        with patch("intelligence.collector.worker._assert_worker_config"),patch(
             "intelligence.collector.worker._preflight_submit",return_value=False
        ),patch("intelligence.collector.worker._claim_next",return_value=fake_task()
        ),patch("intelligence.collector.worker._update_after_provider",
                return_value=True) as saved:
            result=worker.process_one(worker_id="worker-one",provider=Provider())
        self.assertEqual(result["status"],"blocked_before_submit")
        self.assertEqual(result["external_calls"],0)
        self.assertEqual(saved.call_args.kwargs["next_state"],"unknown")
        self.assertEqual(saved.call_args.kwargs["error_code"],
                         "pre_submit_fence_rejected")

    def test_preflight_compares_owner_generation_and_active_policy(self):
        ctx=fake_task()
        db=FakeConn(fence=True)
        self.assertTrue(worker._preflight_submit(ctx,connect=lambda:db))
        self.assertEqual(len(db.commands),3)
        self.assertIn("FOR UPDATE",db.commands[0][0])
        self.assertIn("FOR UPDATE",db.commands[1][0])
        sql,params=db.commands[2]
        self.assertIn("lease_generation=%s",sql)
        self.assertIn("j.lease_until>now()",sql)
        self.assertIn("r.cancel_requested=FALSE",sql)
        self.assertIn("c.stage='released'",sql)
        self.assertIn("p.is_active=TRUE",sql)
        self.assertIn("automated_collection_authorized",sql)
        self.assertIn("provider_usage_no_charge_verified",sql)
        self.assertIn("upstream_archive_disabled_confirmed",sql)
        self.assertEqual(params,(ctx["id"],ctx["run_id"],ctx["job_id"],
                                 ctx["worker_id"],ctx["generation"]))
        self.assertFalse(worker._preflight_submit(ctx,connect=lambda:FakeConn(False)))

    def test_get_wrong_task_id_is_deferred_and_never_submitted_again(self):
        ctx=dict(fake_task(),state="submitted",provider_task_id=TASK_ID)
        class Provider:
            submitted=0
            polled=0
            def submit(self,*args):
                self.submitted+=1
                raise AssertionError("Duplicate POST")
            def task(self,task_id):
                self.polled+=1
                return {"task_id":"87654321-4321-4321-8321-123456789abc",
                        "state":"done","data":{}}
        api=Provider()
        with patch("intelligence.collector.worker._assert_worker_config"),patch(
             "intelligence.collector.completion.finish_from_checkpoint",return_value=None),patch(
             "intelligence.collector.worker._claim_next",return_value=ctx
        ),patch("intelligence.collector.worker._defer_poll",return_value=True) as deferred:
            result=worker.process_one(worker_id="worker-one",provider=api)
        self.assertEqual(result["status"],"poll_deferred")
        self.assertEqual(api.submitted,0)
        self.assertEqual(api.polled,1)
        self.assertEqual(deferred.call_args.kwargs["error_code"],"poll_unavailable")

    def test_bad_get_state_deferred_without_auto_submit(self):
        ctx=dict(fake_task(),state="submitted",provider_task_id=TASK_ID)
        class Unknown:
            def submit(self,*args):raise AssertionError("Never resubmit")
            def task(self,task_id):return {"task_id":task_id,"state":"nonsense"}
        with patch("intelligence.collector.worker._assert_worker_config"),patch(
             "intelligence.collector.completion.finish_from_checkpoint",return_value=None),patch(
             "intelligence.collector.worker._claim_next",return_value=ctx
        ),patch("intelligence.collector.worker._defer_poll",return_value=True):
            result=worker.process_one(worker_id="worker-one",provider=Unknown())
        self.assertEqual(result["status"],"poll_deferred")

    def test_poll_fence_excludes_dead_worker_and_locks_in_cancel_order(self):
        ctx=dict(fake_task(),state="submitted",provider_task_id=TASK_ID)
        dead=FakeConn(fence=False)
        self.assertFalse(worker._defer_poll(ctx,connect=lambda:dead))
        self.assertEqual(len(dead.commands),3)
        self.assertNotIn("UPDATE",dead.commands[-1][0])
        live=FakeConn(fence=True)
        self.assertTrue(worker._defer_poll(ctx,connect=lambda:live))
        self.assertIn("FROM search_runs",live.commands[0][0])
        self.assertIn("FROM collector_runs",live.commands[1][0])
        self.assertIn("FROM jobs",live.commands[2][0])
        self.assertTrue(any("SET state='queued'" in sql for sql,_ in live.commands))

    def test_lost_lease_before_submit_does_not_override_new_owner(self):
        ctx=fake_task()
        class Provider:
            def submit(self,*args):
                raise AssertionError("No POST")
        with patch("intelligence.collector.worker._assert_worker_config"),patch(
             "intelligence.collector.completion.finish_from_checkpoint",return_value=None),patch(
             "intelligence.collector.worker._claim_next",return_value=ctx
        ),patch("intelligence.collector.worker._preflight_submit",return_value=False
        ),patch("intelligence.collector.worker._update_after_provider",
                return_value=False):
            state=worker.process_one(worker_id="worker-one",provider=Provider())
        self.assertEqual(state["status"],"lost_lease")

    def test_submitted_state_must_have_valid_persisted_provider_id(self):
        class DB:
            def execute(self,sql,params=None):
                if "SELECT t.id FROM collector_provider_tasks t" in sql:
                    return Cursor((fake_task()["id"],))
                if "SELECT t.id,t.run_id" in sql:
                    item=fake_task()
                    return Cursor((
                        item["id"],item["run_id"],"a"*64,"submitted",None,
                        item["job_id"],3,item["request_id"],
                        {"tasks":[{"task_fingerprint":"a"*64}]},11,
                        "dtk_tiktok",datetime(2026,10,26,tzinfo=timezone.utc),0
                    ))
                return Cursor()
        with self.assertRaises(CollectorError) as err:
            worker._claim(DB(),"worker-one")
        self.assertEqual(err.exception.code,"provider_task_id_missing")

    def test_claim_quota_must_change_both_tables_or_rollback(self):
        ctx=fake_task()
        class DB:
            def __init__(self):self.commands=[]
            def execute(self,sql,params=None):
                self.commands.append(sql)
                if "SELECT t.id FROM collector_provider_tasks t" in sql:
                    return Cursor((ctx["id"],))
                if "SELECT t.id,t.run_id" in sql:
                    return Cursor((
                        ctx["id"],ctx["run_id"],"a"*64,"not_submitted",None,
                        ctx["job_id"],3,ctx["request_id"],
                        {"tasks":[{"task_fingerprint":"a"*64}]},11,
                        "dtk_tiktok",datetime(2026,10,26,tzinfo=timezone.utc),1
                    ))
                if "SELECT permissions FROM source_policy_versions" in sql:
                    return Cursor(({
                        "automated_collection_authorized":True,
                        "provider_usage_no_charge_verified":True,
                        "upstream_archive_disabled_confirmed":True},))
                if "SELECT 1 FROM source_quota_ledger" in sql:
                    return Cursor((1,))
                if "UPDATE jobs SET state='running'" in sql:
                    return Cursor((4,))
                if "UPDATE collector_execution_grants SET" in sql:
                    return Cursor(None)
                if "UPDATE source_quota_ledger SET" in sql:
                    return Cursor(("dtk_tiktok",))
                return Cursor()
        db=DB()
        with patch("intelligence.collector.worker.prepare_keyword_task",
                   return_value={"params":{"keyword":"workout"}}),patch(
             "intelligence.collector.worker.inspect_source_policy",
             return_value={"allowed_for_held_plan":True,"policy_version_id":11}):
            with self.assertRaises(CollectorError) as exc:
                worker._claim(db,"worker-one")
        self.assertEqual(exc.exception.code,"quota_race")
        self.assertTrue(any("RETURNING run_id" in q for q in db.commands))
        self.assertTrue(any("RETURNING source_id" in q for q in db.commands))

    def test_invalid_submitted_state_does_not_hit_db(self):
        with patch("intelligence.collector.worker._connection",
                   side_effect=AssertionError("No DB")):
            with self.assertRaises(CollectorError):
                worker._update_after_provider(fake_task(),next_state="submitted",
                                              provider_task_id="wrong")

if __name__=="__main__":
    import unittest
    unittest.main()
