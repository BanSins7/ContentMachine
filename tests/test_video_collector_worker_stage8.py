"""Stage 8 regression: explicit rights/consent, safe worker transitions and recovery.

No live TikTok calls, credentials or third-party media storage.
"""
from __future__ import annotations

import os
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from intelligence.collector.contracts import CollectorError
from intelligence.collector import execution, worker
from intelligence.collector import http_api

PID="a"*32
RUN=101


def release_request():
    return {
        "project_id":PID,"run_id":RUN,"confirm":True,
        "idempotency_key":"stage8-explicit-authorization-01",
        "max_spend_usd":"0.00","max_provider_requests":1,
        "acknowledge_archive_configuration":True,
    }


def live_environ():
    return {
        "CM_COLLECTOR_LIVE_ENABLED":"1",
        "CM_DTK_ARCHIVE_DISABLED_CONFIRMED":"1",
    }


def fake_task():
    return {
        "id":4,"run_id":RUN,"job_id":45,"request_id":46,
        "generation":7,"worker_id":"worker-one","state":"not_submitted",
        "provider_task_id":None,
        "prepared":{"params":{"keyword":"home workout"},
                    "task_fingerprint":"b"*64},
    }


class PreviewOnlyIsNotPermission(unittest.TestCase):
    def test_release_needs_real_consent_no_false_flags(self):
        raw=release_request()
        self.assertEqual(execution._validate_release(raw),(PID,RUN,raw["idempotency_key"],1))
        for key,value in (
            ("confirm",False),("max_spend_usd","0.1"),
            ("max_provider_requests",True),("run_id","101"),
            ("acknowledge_archive_configuration",False),
            ("idempotency_key","short"),
            ("project_id","not-scope"),
        ):
            obj=dict(raw);obj[key]=value
            with self.subTest(field=key),self.assertRaises(CollectorError):
                execution._validate_release(obj)
        obj=dict(raw,is_admin=True)
        with self.assertRaises(CollectorError):
            execution._validate_release(obj)

    def test_default_live_gate_closed(self):
        with patch.dict(os.environ,{},clear=True),patch(
            "intelligence.collector.execution.config.setting",
            side_effect=lambda name,default="0":default,
        ):
            self.assertFalse(execution.worker_enabled())
            self.assertFalse(execution.archive_attested())
            with self.assertRaises(CollectorError) as exc:
                execution.authorize_execution(release_request())
            self.assertEqual(exc.exception.code,"execution_disabled")

    def test_read_scope_checked_before_db_connect(self):
        with patch("intelligence.collector.execution.worker_enabled",return_value=True),patch(
            "intelligence.collector.execution.archive_attested",return_value=True
        ),patch("intelligence.collector.execution._connection",
                side_effect=AssertionError("No database call expected")):
            with self.assertRaises(CollectorError) as exc:
                execution.authorize_execution(release_request(),
                    readiness=lambda:{"available":True,"read_scope":False})
            self.assertEqual(exc.exception.code,"dtk_not_ready")

    def test_collector_http_release_is_explicit_and_route_scoped(self):
        class Handler:
            def __init__(self,body):
                from io import BytesIO
                import json
                b=json.dumps(body).encode()
                self.headers={"Content-Length":str(len(b))}
                self.rfile=BytesIO(b)
                self.status=None
                self.response=None
            def json_response(self,status,response):
                self.status=status;self.response=response
        import json
        req=release_request()
        handler=Handler(req)
        with patch("intelligence.collector.execution.authorize_execution",
                   return_value={"status":"released","run_id":RUN}) as call:
            self.assertTrue(http_api.handle_post(
                handler,"/api/intelligence/collector/runs/101/release"))
            self.assertEqual(handler.status,200)
            call.assert_called_once_with(req)
        with patch("intelligence.collector.execution.authorize_execution") as call:
            handler=Handler(dict(req,run_id=102))
            self.assertTrue(http_api.handle_post(
                handler,"/api/intelligence/collector/runs/101/release"))
            self.assertEqual(handler.status,409)
            call.assert_not_called()


class WorkerTransitions(unittest.TestCase):
    def test_submit_once_and_persisted_task_identifier(self):
        class FakeDTK:
            def __init__(self):self.calls=0
            def submit(self,action,params):
                self.calls+=1
                self.assertion=(action,params)
                return {"task_id":"12345678-1234-4234-8234-123456789abc",
                        "provider_request_id":"test-request-1"}
        provider=FakeDTK()
        with patch("intelligence.collector.worker._assert_worker_config"),patch(
            "intelligence.collector.worker._claim_next",return_value=fake_task()
        ),patch("intelligence.collector.worker._update_after_provider",
                return_value=True) as recorded:
            result=worker.process_one(worker_id="worker-one",provider=provider)
        self.assertEqual(provider.calls,1)
        self.assertEqual(provider.assertion,("search_posts",{"keyword":"home workout"}))
        self.assertEqual(result["status"],"submitted")
        self.assertEqual(recorded.call_args.kwargs["next_state"],"submitted")
        self.assertEqual(recorded.call_args.kwargs["provider_task_id"],
                         "12345678-1234-4234-8234-123456789abc")

    def test_submit_failure_is_unknown_not_auto_retry(self):
        class BadDTK:
            def __init__(self):self.calls=0
            def submit(self,*args):
                self.calls+=1
                raise TimeoutError("unknown provider submit outcome")
        api=BadDTK()
        with patch("intelligence.collector.worker._assert_worker_config"),patch(
            "intelligence.collector.worker._claim_next",return_value=fake_task()
        ),patch("intelligence.collector.worker._update_after_provider",
                return_value=True) as stored:
            result=worker.process_one(worker_id="worker-one",provider=api)
        self.assertEqual(result["status"],"unknown")
        self.assertEqual(api.calls,1)
        self.assertEqual(stored.call_args.kwargs["next_state"],"unknown")
        self.assertTrue(result["manual_reconciliation_required"])

    def test_existing_task_is_polled_without_another_submit(self):
        original=fake_task()
        original["state"]="submitted"
        original["provider_task_id"]="12345678-1234-4234-8234-123456789abc"
        class FakeDTK:
            def __init__(self,response):self.response=response;self.reads=0
            def submit(self,*args):raise AssertionError("Duplicate submit")
            def task(self,task_id):
                self.reads+=1
                self.task_id=task_id
                return self.response
        queued=FakeDTK({"state":"running","task_id":original["provider_task_id"]})
        with patch("intelligence.collector.worker._assert_worker_config"),patch(
            "intelligence.collector.worker._claim_next",return_value=original
        ),patch("intelligence.collector.worker._defer_poll",return_value=True) as deferred:
            state=worker.process_one(worker_id="worker-one",provider=queued)
        self.assertEqual(state["status"],"running")
        self.assertEqual(queued.reads,1)
        deferred.assert_called_once()

        done=FakeDTK({"state":"done","task_id":original["provider_task_id"],
                      "data":{"personal_profile":"should not be saved",
                              "items":[{"full_raw_response":"secret"}]}})
        with patch("intelligence.collector.worker._assert_worker_config"),patch(
            "intelligence.collector.worker._claim_next",return_value=original
        ),patch("intelligence.collector.worker._update_after_provider",
                return_value=True) as recorded:
            result=worker.process_one(worker_id="worker-one",provider=done)
        self.assertEqual(result["status"],"completed")
        summary=recorded.call_args.kwargs["summary"]
        self.assertTrue(summary["inspection_pending"])
        self.assertFalse(summary["raw_result_persisted"])
        self.assertNotIn("secret",str(summary))
        self.assertNotIn("personal_profile",str(summary))

    def test_poll_error_deferred_never_causes_submit(self):
        original=dict(fake_task(),state="submitted",
                      provider_task_id="12345678-1234-4234-8234-123456789abc")
        class BadPoll:
            def submit(self,*args):raise AssertionError("No submit")
            def task(self,*args):raise OSError("transport unavailable")
        with patch("intelligence.collector.worker._assert_worker_config"),patch(
            "intelligence.collector.worker._claim_next",return_value=original
        ),patch("intelligence.collector.worker._defer_poll",return_value=True) as deferred:
            result=worker.process_one(worker_id="worker-one",provider=BadPoll())
        self.assertEqual(result["status"],"poll_deferred")
        self.assertEqual(deferred.call_args.kwargs["error_code"],"poll_unavailable")

    def test_worker_disabled_no_claim(self):
        with patch("intelligence.collector.worker.worker_enabled",return_value=False),patch(
            "intelligence.collector.worker._claim_next") as claim:
            with self.assertRaises(CollectorError):
                worker.process_one(worker_id="worker-one")
            claim.assert_not_called()

    def test_unknown_and_completed_are_not_interchanged(self):
        class Fake:
            def __init__(self,rows,cancel=False):
                self.rows=rows;self.cancel=cancel;self.commands=[]
            def execute(self,query,params=None):
                self.commands.append((query,params))
                result=self.rows if "GROUP BY state" in query else (self.cancel,)
                class Cursor:
                    def __init__(self,item):self.item=item
                    def fetchall(self):return self.item
                    def fetchone(self):return self.item
                return Cursor(result)
        data=Fake([("completed",1),("unknown",1)])
        worker._finalize(data,RUN)
        self.assertTrue(any("UPDATE collector_runs SET stage=" in sql and
                            args[0]=="partial" for sql,args in data.commands))
        data=Fake([("completed",2)])
        worker._finalize(data,RUN)
        self.assertTrue(any("UPDATE collector_runs SET stage=" in sql and
                            args[0]=="completed" for sql,args in data.commands))


if __name__=="__main__":
    unittest.main()
