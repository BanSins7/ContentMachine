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



class RecoveryUnitTests(unittest.TestCase):
    def test_restart_reconciles_unknown_submit_but_only_requeues_poll(self):
        class Response:
            def __init__(self,rows=None):self.rows=rows
            def fetchall(self):return self.rows or []
        class Conn:
            def __init__(self):self.commands=[]
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def transaction(self):return self
            def execute(self,sql,params=None):
                self.commands.append((sql,params))
                if "FROM collector_provider_tasks t JOIN jobs" in sql:
                    return Response([
                        (1,"submit_intent",101,22,3,32),
                        (2,"submitted",101,23,4,33),
                    ])
                return Response()
        db=Conn()
        with patch("intelligence.collector.worker._finalize") as finalize:
            count=worker.recover_expired(connect=lambda:db)
        self.assertEqual(count,2)
        self.assertEqual(finalize.call_count,2)
        sql_text="\\n".join(q for q,_ in db.commands)
        self.assertIn("state='unknown'",sql_text)
        self.assertIn("state='failed'",sql_text)
        self.assertIn("state='queued'",sql_text)
        self.assertNotIn("INSERT INTO collector_provider_tasks",sql_text)
        self.assertNotIn("dtk.submit",sql_text)

    def test_authorization_is_transactional_bounded_and_idempotent(self):
        from datetime import datetime, timezone
        from intelligence.collector.planner import build_preview
        from intelligence.collector.query_intake import parse_validated_query_set
        from test_video_collector_stage2 import query_set
        queryset=parse_validated_query_set(query_set())
        snapshot=build_preview(queryset)
        when=datetime(2026,10,24,tzinfo=timezone.utc)
        class Response:
            def __init__(self,row=None,rows=None):self.row=row;self.rows=rows
            def fetchone(self):return self.row
            def fetchall(self):return self.rows or []
        class Conn:
            def __init__(self):
                self.sql=[]
                self.grant=None
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def transaction(self):return self
            def execute(self,sql,params=None):
                self.sql.append((sql,params))
                if "SELECT consent_key,request_cap,policy_version_id" in sql:
                    return Response(self.grant)
                if "SELECT query_set_id FROM collector_runs" in sql:
                    return Response(("66666666-6666-4666-8666-666666666666",))
                if "SELECT task_sha256 FROM collector_provider_tasks" in sql:
                    return Response(rows=[(snapshot["tasks"][0]["task_fingerprint"],)])
                if "SELECT permissions FROM source_policy_versions" in sql:
                    return Response(({
                        "automated_collection_authorized":True,
                        "provider_usage_no_charge_verified":True,
                        "upstream_archive_disabled_confirmed":True,
                    },))
                if "SELECT window_start_at,limit_units,reserved_units,consumed_units" in sql:
                    return Response((when,10,0,0))
                if "INSERT INTO collector_execution_grants" in sql:
                    self.grant=(params[6],params[4],params[2])
                return Response()
        fake=Conn()
        held=(RUN,"created",False,when,None,"planned_held",
              "77777777-7777-4777-8777-777777777777",
              snapshot["plan_hash"],snapshot,11)
        req=release_request()
        with patch("intelligence.collector.execution.worker_enabled",return_value=True),patch(
             "intelligence.collector.execution.archive_attested",return_value=True),patch(
             "intelligence.collector.execution._run",return_value=held),patch(
             "intelligence.collector.execution._trusted_query_set",
             return_value=("66666666-6666-4666-8666-666666666666",queryset)),patch(
             "intelligence.collector.execution.inspect_source_policy",
             return_value={"allowed_for_held_plan":True,
                           "policy_version_id":11,"blocks":[]}),patch(
             "intelligence.collector.execution._connection",return_value=fake):
            first=execution.authorize_execution(req,
                readiness=lambda:{"available":True,"read_scope":True})
            replay=execution.authorize_execution(req,
                readiness=lambda:{"available":True,"read_scope":True})
            self.assertEqual(first["status"],"released")
            self.assertEqual(first["requests_authorized"],1)
            self.assertTrue(replay["replayed"])
            self.assertEqual(sum("INSERT INTO collector_execution_grants" in q
                                 for q,_ in fake.sql),1)
            self.assertEqual(sum("UPDATE source_quota_ledger SET reserved_units" in q
                                 for q,_ in fake.sql),1)
            wrong=dict(req,idempotency_key="another-auth-consent-002")
            with self.assertRaises(CollectorError) as err:
                execution.authorize_execution(wrong,
                    readiness=lambda:{"available":True,"read_scope":True})
            self.assertEqual(err.exception.code,"execution_consent_conflict")

    def test_run_cancellation_releases_unused_reservations(self):
        from datetime import datetime, timezone
        when=datetime(2026,10,24,tzinfo=timezone.utc)
        class Response:
            def __init__(self,row=None,rows=None):self.row=row;self.rows=rows
            def fetchone(self):return self.row
            def fetchall(self):return self.rows or []
        class Conn:
            def __init__(self):self.commands=[]
            def execute(self,sql,params=None):
                self.commands.append((sql,params))
                if "FROM collector_provider_tasks t WHERE t.run_id" in sql:
                    return Response(rows=[(1,51,61)])
                if "FROM collector_execution_grants" in sql:
                    return Response(("dtk_tiktok",when,1))
                if "SELECT count(*) FROM collector_provider_tasks" in sql:
                    return Response((0,))
                return Response()
        db=Conn()
        info=(RUN,"running",False,when,None,"released","plan-id","b"*64,{},11)
        with patch("intelligence.collector.execution._run",return_value=info):
            result=execution.cancel_released(db,PID,RUN)
        self.assertEqual(result["status"],"cancelled")
        self.assertEqual(result["cancelled_unsubmitted"],1)
        self.assertEqual(result["outstanding_upstream"],0)
        self.assertTrue(any("reserved_units=reserved_units-%s" in q
                            for q,_ in db.commands))
        self.assertTrue(any("SET state='cancelled'" in q for q,_ in db.commands))


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"Requires disposable PostgreSQL")
class Stage8MigrationPostgres(unittest.TestCase):
    def test_execution_grant_composite_quota_fk_and_held_jobs(self):
        import psycopg
        from intelligence.migrate import apply_migrations
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True) as conn:
            apply_migrations(conn)
            constraints=conn.execute(
                """SELECT contype,pg_get_constraintdef(oid) FROM pg_constraint
                   WHERE conrelid='collector_execution_grants'::regclass""").fetchall()
            self.assertTrue(any(kind=='p' for kind,_ in constraints))
            self.assertTrue(any(kind=='f' and
                  'FOREIGN KEY (source_id, quota_bucket_name, quota_window_start)'
                  in definition and 'REFERENCES source_quota_ledger' in definition
                  for kind,definition in constraints),constraints)
            rows=conn.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_name='collector_provider_tasks' AND column_name IN
                   ('response_summary','submitted_at','polled_at','finished_at',
                    'last_error_code')""").fetchall()
            self.assertEqual(len(rows),5)


if __name__=="__main__":
    unittest.main()
