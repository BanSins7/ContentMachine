"""GB-MOD-26 local HTTP protections without network or PostgreSQL.

HTTP stubs are syntactic transport tests, NOT proof of authentic QDP,
paid user consent, complete media or a verified human reviewer.
"""
from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
from unittest.mock import patch
import json
import unittest
from urllib.parse import urlparse

from intelligence.group_b.http_api import (
    handle_get,handle_post,_body,_query,configure_operator_service,
)
from intelligence.group_b.studio_service import (
    GroupBStudioService,StudioProblem,_identity,_page,
)

PID="f"*32
RID="00000000-0000-0000-0000-000000000001"
INBOX="00000000-0000-0000-0000-000000000002"
BASE="/api/projects/"+PID+"/group-b"


class Handler:
    def __init__(self,data=None,*,host="127.0.0.1:8765",
                 origin="http://127.0.0.1:8765",csrf=True):
        self.messages=[]
        self.headers={
            "Host":host,"Origin":origin,
            "Content-Type":"application/json",
        }
        if csrf:
            self.headers["X-ContentMachine"]="1"
        if data is not None:
            encoded=json.dumps(data,allow_nan=False).encode("utf-8")
            self.headers["Content-Length"]=str(len(encoded))
            self.rfile=BytesIO(encoded)
        else:
            self.rfile=BytesIO()
    def authorized(self,mutation=False):
        if self.headers.get("Host") not in (
            "127.0.0.1:8765","localhost:8765",
        ):
            return False
        if self.headers.get("Origin") not in (
            "http://127.0.0.1:8765","http://localhost:8765",
        ):
            return False
        return not mutation or self.headers.get("X-ContentMachine")=="1"
    def json_response(self,status,data):
        self.messages.append((status,data))


class ServiceStub:
    def __init__(self):
        self.calls=[]
    def list_runs(self,project_id,*,offset,limit):
        self.calls.append(("list",project_id,offset,limit))
        return {"items":[],"offset":offset,"limit":limit,"total":0}
    def detail(self,project_id,run_id,*,offset,limit):
        self.calls.append(("detail",project_id,run_id))
        return {"run":{"run_id":run_id},"blocks":[]}
    def evidence(self,project_id,run_id,*,block,offset,limit):
        self.calls.append(("evidence",project_id,run_id,block))
        return {"items":[]}
    def results(self,project_id,run_id,*,block,offset,limit):
        self.calls.append(("results",project_id,run_id,block))
        return {"items":[]}
    def create(self,project_id,data):
        self.calls.append(("create",project_id,data))
        return {"run":{"run_id":RID}}
    def quote(self,project_id,run_id,data):
        self.calls.append(("quote",project_id,run_id))
        return {"quote":{"quote_id":INBOX}}
    def approve(self,project_id,run_id,data):
        self.calls.append(("approve",project_id,run_id))
        return {"approval":{}}
    def start(self,project_id,run_id,data):
        self.calls.append(("start",project_id,run_id))
        return {"jobs":[]}
    def cancel(self,project_id,run_id,data):
        self.calls.append(("cancel",project_id,run_id))
        return {"cancelled_jobs":0}
    def review(self,project_id,run_id,data):
        self.calls.append(("review",project_id,run_id))
        return {"revision":1}


class Cursor:
    def __init__(self,rows,columns=("exists",)):
        self.rows=rows;self.idx=0
        self.description=[type("D",(),{"name":k}) for k in columns]
    def fetchone(self):
        if self.idx>=len(self.rows):return None
        row=self.rows[self.idx];self.idx+=1;return row
    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self,project_exists=True):
        self.project_exists=project_exists;self.sql=[]
    @contextmanager
    def transaction(self):
        yield
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql,args=()):
        self.sql.append((sql,args))
        if "SELECT EXISTS" in sql and "project_registry" in sql:
            return Cursor([(self.project_exists,)])
        raise AssertionError("Only the project admission SQL is allowed")


class TestGroupBHTTP(unittest.TestCase):
    def tearDown(self):
        configure_operator_service(None)

    def test_project_and_uuid_validation_fail_closed(self):
        for pid in ("not-project","A"*32,"f"*33,True):
            with self.assertRaises(StudioProblem):
                _identity(pid)
        for rid in ("other-project","00000000-0000-0000-0000-00000000000X",True):
            with self.assertRaises(StudioProblem):
                _identity(PID,rid)
        _identity(PID,RID)

    def test_pagination_capped(self):
        for offset,limit in ((-1,1),(0,51),(1_000_000,5),
                             (0,0),(True,5),(0,"10")):
            with self.assertRaises(StudioProblem):
                _page(offset,limit)
        self.assertEqual(_page(10,20),(10,20))
        with self.assertRaises(StudioProblem):
            _query(urlparse(BASE+"?limit=999"))

    def test_get_history_metadata_and_evidence_paths(self):
        svc=ServiceStub()
        with patch("intelligence.group_b.http_api._service",return_value=svc):
            h=Handler()
            self.assertTrue(handle_get(h,urlparse(BASE+"/runs?offset=1&limit=5")))
            self.assertEqual(h.messages[-1][0],200)
            self.assertEqual(svc.calls[-1],("list",PID,1,5))
            self.assertTrue(handle_get(h,urlparse(BASE+"/runs/"+RID)))
            self.assertEqual(svc.calls[-1][0],"detail")
            self.assertTrue(handle_get(
                h,urlparse(BASE+"/runs/"+RID+"/evidence?block=B3")
            ))
            self.assertEqual(svc.calls[-1],("evidence",PID,RID,"B3"))
            self.assertTrue(handle_get(
                h,urlparse(BASE+"/runs/"+RID+"/results?block=B8")
            ))
            self.assertEqual(svc.calls[-1],("results",PID,RID,"B8"))
            self.assertTrue(handle_get(h,urlparse(BASE+"/runs?page=3")))
            self.assertEqual(h.messages[-1][0],400)

    def test_foreign_project_access_denied_by_authorizer(self):
        conn=FakeConnection()
        svc=GroupBStudioService(lambda:conn,operator=lambda *args:False)
        with self.assertRaises(StudioProblem) as err:
            svc.list_runs(PID)
        self.assertEqual(err.exception.http_status,403)
        self.assertEqual(conn.sql,[]) # admission BEFORE SQL
        conn=FakeConnection(project_exists=False)
        svc=GroupBStudioService(lambda:conn,operator=lambda *args:True)
        with self.assertRaises(StudioProblem) as err:
            svc.list_runs(PID)
        self.assertEqual(err.exception.http_status,404)

    def test_trusted_server_missing_denies_qdp_create_without_write(self):
        conn=FakeConnection()
        svc=GroupBStudioService(lambda:conn,operator=lambda *args:True,
                                host=None)
        with self.assertRaises(StudioProblem) as err:
            svc.create(PID,{"inbox_id":INBOX,"idempotency_key":"job-12345678901"})
        self.assertEqual(err.exception.http_status,424)
        self.assertTrue(all("INSERT" not in sql for sql,_ in conn.sql))

    def test_mutation_must_have_same_origin_host_and_csrf_marker(self):
        svc=ServiceStub()
        with patch("intelligence.group_b.http_api._service",return_value=svc):
            for params in (
                {"host":"evil.example"},
                {"origin":"https://evil.example"},
                {"csrf":False},
            ):
                h=Handler({"confirm":True},**params)
                self.assertTrue(handle_post(h,BASE+"/runs/"+RID+"/cancel"))
                self.assertEqual(h.messages[-1][0],403)
            self.assertEqual(svc.calls,[])

    def test_mutation_bodies_strictly_json_limited_and_route_scoped(self):
        svc=ServiceStub()
        with patch("intelligence.group_b.http_api._service",return_value=svc):
            h=Handler({"confirm":True})
            h.headers["Content-Length"]="50000"
            handle_post(h,BASE+"/runs/"+RID+"/cancel")
            self.assertEqual(h.messages[-1][0],413)
            h=Handler({"confirm":True})
            h.headers["Content-Type"]="text/plain"
            handle_post(h,BASE+"/runs/"+RID+"/cancel")
            self.assertEqual(h.messages[-1][0],415)
            h=Handler({"confirm":True})
            handle_post(h,BASE+"/runs/"+RID+"/cancel")
            self.assertEqual(h.messages[-1][0],200)
            self.assertEqual(svc.calls[-1][0],"cancel")
            h=Handler({"confirm":True})
            handle_post(h,BASE+"/runs/"+RID+"/unsupported")
            self.assertEqual(h.messages[-1][0],404)

    def test_unexpected_host_error_never_leaks_credentials(self):
        class Broken(ServiceStub):
            def detail(self,*args,**kwargs):
                raise RuntimeError("password=supersecret-local-pg")
        with patch("intelligence.group_b.http_api._service",return_value=Broken()):
            h=Handler()
            handle_get(h,urlparse(BASE+"/runs/"+RID))
            self.assertEqual(h.messages[-1][0],503)
            self.assertNotIn("supersecret",str(h.messages[-1]))

    def test_paid_authorization_does_not_read_operator_id_from_browser(self):
        conn=FakeConnection()
        svc=GroupBStudioService(lambda:conn,operator=lambda *args:True)
        with self.assertRaises(StudioProblem):
            svc.approve(PID,RID,{"quote_id":INBOX,"authorized_usd":"0.10",
                                 "confirmation":True})
        self.assertTrue(all("INSERT" not in sql for sql,_ in conn.sql))


if __name__=="__main__":
    unittest.main()
