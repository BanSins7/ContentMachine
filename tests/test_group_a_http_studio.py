"""GA21 isolated HTTP, CSRF, project authorization and Studio UI smoke."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
import json
import os
import shutil
import subprocess
import unittest

from intelligence.group_a.http_api import GroupAHTTPFacade
from intelligence.group_a.host_registration import mount_group_a_handler
from intelligence.group_a.observability import (
    ObservationDenied,encode_cursor,decode_cursor,
    safe_limit,GroupAMonitor
)

PID="a"*32
OTHER="b"*32
RUN="12345678-1234-4234-9234-123456789abc"
ACTOR="c"*64
BASE=f"/api/projects/{PID}/group-a/v1"


class Handler:
    def __init__(self,path,*,host="127.0.0.1:8765",origin=None,
                 csrf="1",body=None,ctype="application/json"):
        self.path=path
        self.headers={"Host":host}
        if origin is not None:self.headers["Origin"]=origin
        if csrf is not None:self.headers["X-ContentMachine"]=csrf
        if body is not None:
            data=json.dumps(body).encode("utf-8")
            self.rfile=BytesIO(data)
            self.headers["Content-Length"]=str(len(data))
            self.headers["Content-Type"]=ctype
        self.result=None

    def json_response(self,status,data):
        self.result=(status,data)

    def authorized(self,mutation=False):
        return (self.headers.get("Host")=="127.0.0.1:8765"
                and (not mutation or self.headers.get("X-ContentMachine")=="1"))

    def respond(self,status,blob,content_type):
        self.result=(status,blob,content_type)

    def do_GET(self):
        self.result=(599,{"legacy":True})

    def do_POST(self):
        self.result=(599,{"legacy":True})


class ClosedDB:
    def __call__(self):
        raise AssertionError("DB should not be contacted by this test")


class TestGA21HTTP(unittest.TestCase):
    def facade(self,auth=None):
        f=GroupAHTTPFacade(
            connect=ClosedDB(),
            authorize_project=auth or (lambda project,permission:{
                "allowed":project==PID,"actor_ref_sha256":ACTOR,
            }),
            verify_current=lambda _:True,
            verify_result=lambda _:True,
            resolve_manifest=lambda *_:(_ for _ in ()).throw(
                AssertionError("no manifest lookup expected")
            ),
            verify_authority=lambda _:True,
        )
        f.monitor=SimpleNamespace(
            runs=lambda *args,**kw:{
                "schema_version":"ga21-runs/v1",
                "items":[{"run_id":RUN,"state":"queued"}],
                "next_cursor":None,
            },
            overview=lambda *args,**kw:{
                "schema_version":"ga21-run-overview/v1",
                "run":{"run_id":RUN,"state":"queued"},
                "steps":{"queued":1},"delivery":{},
            },
            steps=lambda *args,**kw:{"items":[],"next_cursor":None},
            events=lambda *args,**kw:{"items":[],"next_cursor":None},
            measurements=lambda *args,**kw:{"available":False},
        )
        return f

    def request(self,path,method="GET",**kwargs):
        h=Handler(path,**kwargs)
        found=self.facade().handle(h,urlparse(path),method)
        self.assertTrue(found)
        return h.result

    def test_project_scoped_runs_and_summary(self):
        code,body=self.request(BASE+"/runs?limit=2")
        self.assertEqual(code,200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["items"][0]["run_id"],RUN)
        code,body=self.request(BASE+"/runs/"+RUN)
        self.assertEqual(code,200)
        self.assertEqual(body["run"]["state"],"queued")

    def test_foreign_project_is_denied_before_monitor_db(self):
        path=f"/api/projects/{OTHER}/group-a/v1/runs"
        code,body=self.request(path)
        self.assertEqual(code,403)
        self.assertEqual(body["error"]["code"],"project_access_denied")

    def test_host_origin_guard_and_csrf_before_mutations(self):
        code,body=self.request(
            BASE+"/runs",host="evil.com:8765")
        self.assertEqual(code,403)
        self.assertEqual(body["error"]["code"],"local_origin_required")
        code,body=self.request(
            BASE+"/runs",origin="https://evil.example")
        self.assertEqual(code,403)
        code,body=self.request(BASE+"/runs/"+RUN+"/cancel","POST",body={},csrf=None)
        self.assertEqual(code,403)
        self.assertEqual(body["error"]["code"],"csrf_header_required")
        code,body=self.request(
            BASE+"/runs/"+RUN+"/cancel","POST",body={},ctype="text/plain")
        self.assertEqual(code,415)
        self.assertEqual(body["error"]["code"],"json_content_type_required")

    def test_queries_methods_and_payloads_are_rejected(self):
        for path in (
            BASE+"/runs?limit=999",
            BASE+"/runs?limit=2&limit=3",
            BASE+"/runs?unknown=123",
            BASE+"/runs/"+RUN+"/measurements?cursor=forbidden",
        ):
            with self.subTest(path=path):
                code,body=self.request(path)
                self.assertEqual(code,400)
                self.assertFalse(body["ok"])
        code,body=self.request(BASE+"/runs/"+RUN+"/retry")
        self.assertEqual(code,405)
        code,body=self.request(BASE+"/runs/"+RUN+"/events","POST",body={})
        self.assertEqual(code,404)
        code,body=self.request(
            BASE+"/runs/"+RUN+"/retry?x=1","POST",body={"step_key":"initial:v1"})
        self.assertEqual(code,400)
        code,body=self.request(
            BASE+"/runs/"+RUN+"/retry","POST",body={})
        self.assertEqual(code,400)
        self.assertEqual(body["error"]["code"],"retry_step_key_required")

    def test_no_live_host_actions_without_exact_registration(self):
        class ExistingHandler(Handler):
            pass
        mounted=mount_group_a_handler(ExistingHandler,self.facade())
        h=mounted.__new__(mounted)
        Handler.__init__(h,BASE+"/runs?limit=1")
        h.do_GET()
        self.assertEqual(h.result[0],200)
        h=mounted.__new__(mounted)
        Handler.__init__(h,"/unrelated")
        h.do_GET()
        self.assertEqual(h.result[0],599)
        h=mounted.__new__(mounted)
        Handler.__init__(h,"/group-a.js")
        h.do_GET()
        self.assertEqual(h.result[0],200)
        self.assertIn(b"textContent",h.result[1])

    def test_capped_cursor_and_bad_encoding(self):
        raw=encode_cursor([125])
        self.assertEqual(decode_cursor(raw,"event"),(125,))
        for cur in ("","!!","abc","a"*170):
            with self.assertRaises(ObservationDenied):
                decode_cursor(cur,"event")
        with self.assertRaises(ObservationDenied):
            safe_limit(26)

    def test_error_contract_redacts_host_exception(self):
        def exploding(*_):
            raise RuntimeError("/home/private/user/secret/media.mp4")
        f=self.facade(auth=exploding)
        h=Handler(BASE+"/runs")
        f.handle(h,urlparse(h.path),"GET")
        self.assertEqual(h.result[0],503)
        self.assertNotIn("secret",json.dumps(h.result[1]))
        self.assertNotIn("/home",json.dumps(h.result[1]))

    def test_ui_has_no_innerhtml_or_inline_script_and_node_syntax(self):
        root=Path(__file__).resolve().parents[1]/"intelligence/group_a/site"
        js=(root/"group-a.js").read_text(encoding="utf-8")
        html=(root/"group-a.html").read_text(encoding="utf-8")
        self.assertIn("textContent",js)
        for bad in ("innerHTML","outerHTML","eval(","document.write(",
                    "localStorage","https://cdn."):
            self.assertNotIn(bad,js)
        self.assertNotIn("<script>",html)
        self.assertIn('src="/group-a.js"',html)
        self.assertIn('href="/group-a.css"',html)
        if shutil.which("node"):
            result=subprocess.run(
                ["node","--check",str(root/"group-a.js")],
                capture_output=True,text=True,timeout=10,check=False
            )
            self.assertEqual(result.returncode,0,result.stderr)


@unittest.skipUnless(os.environ.get("CM_TEST_PG_DSN"),
                     "Disposable PostgreSQL 17 DSN required")
class GA21PG17(unittest.TestCase):
    def setUp(self):
        from test_group_a_runtime import GA20Postgres
        self.peer=GA20Postgres
        GA20Postgres.setUp(self)
        self.addCleanup(GA20Postgres.tearDown,self)
        self.monitor=GroupAMonitor(self.connect,lambda _:True,lambda _:True)

    def test_run_steps_telemetry_and_project_isolation(self):
        data=self.monitor.runs(self.pid,limit=10)
        self.assertEqual(len(data["items"]),1)
        self.assertEqual(data["items"][0]["run_id"],self.run)
        with self.assertRaisesRegex(ObservationDenied,"run_not_found"):
            self.monitor.overview(OTHER,self.run)
        summary=self.monitor.overview(self.pid,self.run)
        self.assertGreaterEqual(summary["steps"]["queued"],1)
        first=self.monitor.steps(self.pid,self.run,limit=2)
        self.assertLessEqual(len(first["items"]),2)
        self.assertEqual(first["telemetry_semantics"],
                         "reserved_slots_not_measured_cpu_gpu_usage")
        if first["next_cursor"] is not None:
            next_page=self.monitor.steps(
                self.pid,self.run,limit=2,cursor=first["next_cursor"]
            )
            self.assertFalse(set(x["step_id"] for x in first["items"]) &
                             set(x["step_id"] for x in next_page["items"]))

    def test_operator_action_is_durable_and_repeat_idempotent(self):
        from intelligence.group_a.observability import append_operator_event
        with self.connect() as conn:
            with conn.transaction():
                append_operator_event(
                    conn,project=self.pid,run_id=self.run,
                    actor_ref_sha256=ACTOR,event_kind="dag_scheduled"
                )
                append_operator_event(
                    conn,project=self.pid,run_id=self.run,
                    actor_ref_sha256=ACTOR,event_kind="dag_scheduled"
                )
        page=self.monitor.events(self.pid,self.run,limit=10)
        self.assertEqual(len(page["items"]),1)
        self.assertEqual(page["items"][0]["kind"],"dag_scheduled")
        with self.connect() as conn:
            with self.assertRaises(Exception):
                conn.execute(
                    "UPDATE ga21_operator_events SET event_kind='run_cancelled' WHERE run_id=%s::uuid",
                    (self.run,)
                )

    def test_verified_measurements_denied_for_incomplete_run(self):
        self.assertFalse(self.monitor.measurements(
            self.pid,self.run
        )["available"])
