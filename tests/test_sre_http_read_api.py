"""M38 localhost-only WSGI negative security and routing tests."""
import io
import json
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from intelligence.relevance.http_read_api import (
    LocalSREReadAPI,ROOT,_query,create_loopback_server,
)
from intelligence.relevance.v1_contracts import SREContractError

PID="a"*32
OTHER="b"*32
RID="11111111-1111-4111-8111-111111111111"
PUB="22222222-2222-4222-8222-222222222222"


class FakeConn:
    autocommit=True
    info=SimpleNamespace(transaction_status=SimpleNamespace(name="IDLE"))
    def __init__(self):
        self.sql=[]
        self.open_count=0
    def __enter__(self):self.open_count+=1;return self
    def __exit__(self,*a):return False
    @contextmanager
    def transaction(self):
        yield self
    def execute(self,statement,args=None):
        self.sql.append((statement,args))
        if statement=="SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY":
            return SimpleNamespace(fetchone=lambda:None)
        if "sre_workspaces WHERE project_id" in statement:
            return SimpleNamespace(fetchone=lambda:(1,))
        raise AssertionError("Unexpected database SQL: "+statement[:120])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.db=FakeConn()
        self.access=[]
        self.calls=[]
        def auth(pid,actor,permission):
            self.access.append((pid,actor,permission))
            return actor=="verified:local" and pid in (None,PID)
        self.api=LocalSREReadAPI(
            connect=lambda:self.db,
            actor_resolver=lambda env:env.get("HOST_VERIFIED_ACTOR"),
            actor_authorizer=auth,enabled=True)
        self.disabled=LocalSREReadAPI(
            connect=lambda:self.db,
            actor_resolver=lambda env:env.get("HOST_VERIFIED_ACTOR"),
            actor_authorizer=auth)

    def send(self,path,*,method="GET",headers=None,actor="verified:local",query=""):
        status=[]
        env={
            "REMOTE_ADDR":"127.0.0.1",
            "HTTP_HOST":"127.0.0.1:8765",
            "REQUEST_METHOD":method,
            "PATH_INFO":path,
            "QUERY_STRING":query,
            "wsgi.input":io.BytesIO(),
        }
        if actor is not None: env["HOST_VERIFIED_ACTOR"]=actor
        if headers:env.update(headers)
        response=b"".join(self.api(env,lambda s,h:status.append((s,h))))
        return status[0][0],dict(status[0][1]),(
            json.loads(response) if response else None
        )

    def test_module_disabled_by_default_does_not_touch_auth_or_database(self):
        response=[]
        data=b"".join(self.disabled({
            "REMOTE_ADDR":"127.0.0.1","REQUEST_METHOD":"GET",
            "PATH_INFO":ROOT+"/manifest"},lambda status,h:response.append(status)))
        self.assertEqual(response,["503 Service Unavailable"])
        self.assertIn(b"sre_api_disabled",data)
        self.assertEqual(self.db.open_count,0)
        self.assertEqual(self.access,[])

    def test_manifest_requires_trusted_host_identity_and_host_authorization(self):
        status,headers,payload=self.send(ROOT+"/manifest")
        self.assertEqual(status,"200 OK")
        self.assertEqual(headers["Cache-Control"],"no-store, max-age=0")
        self.assertTrue(payload["read_only"])
        self.assertFalse(payload["paid_dispatch_allowed"])
        self.assertTrue(any("review-queue" in x for x in payload["routes"]))
        self.assertEqual(self.access,[(None,"verified:local","read_sre_api_manifest")])
        self.assertEqual(self.db.open_count,0)
        self.assertEqual(self.send(ROOT+"/manifest",actor=None)[0],"403 Forbidden")
        self.assertEqual(self.send(ROOT+"/manifest",actor="forged:actor")[0],
                         "403 Forbidden")

    def test_untrusted_http_actor_or_project_headers_never_override_host(self):
        path=ROOT+"/projects/"+PID+"/health"
        status,_,_=self.send(path,actor=None,headers={
            "HTTP_X_ACTOR_ID":"verified:local",
            "HTTP_X_PROJECT_ID":PID,
        })
        self.assertEqual(status,"403 Forbidden")
        self.assertEqual(self.db.open_count,0)
        status,_,_=self.send(ROOT+"/projects/"+OTHER+"/health")
        self.assertEqual(status,"403 Forbidden")
        self.assertEqual(self.db.open_count,0)

    def test_non_loopback_proxy_host_and_query_smuggling_refused_before_db(self):
        path=ROOT+"/projects/"+PID+"/health"
        for headers in (
            {"REMOTE_ADDR":"192.168.1.5"},
            {"REMOTE_ADDR":"8.8.8.8"},
            {"HTTP_HOST":"api.example.org"},
            {"HTTP_X_FORWARDED_FOR":"127.0.0.1"},
            {"HTTP_FORWARDED":"for=127.0.0.1"},
            {"HTTP_X_REAL_IP":"127.0.0.1"},
        ):
            status,_,_=self.send(path,headers=headers)
            self.assertEqual(status,"403 Forbidden",headers)
        self.assertEqual(self.db.open_count,0)
        self.assertEqual(self.send(path,query="limit=50&limit=51")[0],
                         "400 Bad Request")
        self.assertEqual(self.send(path,query="after=")[0],"400 Bad Request")
        self.assertEqual(self.send(path,query="not_allowed=1")[0],"400 Bad Request")

    def test_mutating_methods_rejected_even_with_authenticated_actor(self):
        for method in ("POST","PUT","PATCH","DELETE","OPTIONS"):
            status,headers,data=self.send(
                ROOT+"/projects/"+PID+"/runs",method=method)
            self.assertEqual(status,"405 Method Not Allowed",method)
            self.assertEqual(headers["Allow"],"GET, HEAD")
            self.assertEqual(data["error"],"read_only_api")
        self.assertEqual(self.db.open_count,0)

    def test_all_route_handlers_use_single_read_only_db_transaction(self):
        table={
            "health":"read_schema_health",
            "taxonomy/"+PUB:"read_taxonomy_preview",
            "content/45/preview":"read_content_preview",
            "runs":"list_live_runs",
            "runs/"+RID:"read_run_status",
            "runs/"+RID+"/results":"read_verified_results",
            "runs/"+RID+"/history":"read_review_history",
            "runs/"+RID+"/review-queue":"read_review_queue",
            "runs/"+RID+"/quotes":"read_quote_preview",
        }
        for resource,method in table.items():
            with self.subTest(resource=resource):
                with patch("intelligence.relevance.http_read_api.read_api."+method,
                           return_value={"schema_version":"fake-read-only-fixture/v1",
                                         "rows":[]}) as fn:
                    status,headers,payload=self.send(
                        ROOT+"/projects/"+PID+"/"+resource,
                        query=("limit=5" if method in (
                            "read_taxonomy_preview","list_live_runs",
                            "read_verified_results","read_review_history",
                            "read_review_queue","read_quote_preview") else ""))
                    self.assertEqual(status,"200 OK")
                    self.assertEqual(payload["rows"],[])
                    fn.assert_called_once()
                    self.assertEqual(
                        self.db.sql[-2][0],
                        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                    )
        self.assertTrue(all(sql.startswith(("SET ","SELECT "))
                            for sql,_ in self.db.sql))
        self.assertEqual(len(self.access),9)

    def test_get_and_head_never_cache_or_leak_sql_exception(self):
        path=ROOT+"/projects/"+PID+"/health"
        with patch("intelligence.relevance.http_read_api.read_api.read_schema_health",
                   return_value={"ok":True}):
            status,headers,data=self.send(path)
            self.assertEqual(status,"200 OK")
            self.assertEqual(data,{"ok":True})
            # HEAD returns no body while Content-Length matches GET.
            state=[]
            output=b"".join(self.api({
                "REMOTE_ADDR":"127.0.0.1","HTTP_HOST":"localhost",
                "REQUEST_METHOD":"HEAD","PATH_INFO":path,
                "QUERY_STRING":"","HOST_VERIFIED_ACTOR":"verified:local",
            },lambda code,h:state.append((code,dict(h)))))
            self.assertEqual(output,b"")
            self.assertEqual(state[0][0],"200 OK")
            self.assertEqual(state[0][1]["Content-Length"],headers["Content-Length"])
        with patch("intelligence.relevance.http_read_api.read_api.read_schema_health",
                   side_effect=RuntimeError("postgres://secret:pwd@dbhost/")):
            status,_,payload=self.send(path)
            self.assertEqual(status,"503 Service Unavailable")
            self.assertNotIn("postgres",json.dumps(payload))
            self.assertNotIn("secret",json.dumps(payload))

    def test_noncanonical_paths_and_invalid_uuid_do_not_query_db(self):
        for path in (
            ROOT+"/projects/"+PID+"/runs/"+RID.upper(),
            ROOT+"/projects/"+PID+"/runs/"+str(uuid4())+"/dispatch",
            ROOT+"/projects/"+PID+"/content/0/preview",
            ROOT+"/projects/"+PID+"/../../other",
            ROOT+"/projects/"+PID+"//runs",
        ):
            status,_,_=self.send(path)
            self.assertIn(status,("400 Bad Request","404 Not Found"))
        self.assertEqual(self.db.open_count,0)

    def test_server_requires_explicit_opt_in_and_loopback(self):
        with self.assertRaises(ValueError):
            create_loopback_server(self.disabled,0)
        with self.assertRaises(ValueError):
            create_loopback_server(self.api,65536)
        # No server created, no socket bound for default disabled API.
        self.assertEqual(self.db.open_count,0)

    def test_invalid_query_bounds_are_fail_closed(self):
        for raw in ("limit=0","limit=101","limit=1000",
                    "limit=1&limit=2","after=","cursor=x",
                    "limit=1&after=a&other=x"):
            with self.assertRaises(SREContractError):
                _query(raw)


if __name__=="__main__":
    unittest.main()
