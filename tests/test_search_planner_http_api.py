"""SP-13 owner HTTP router: bounded request parsing, zero-spend GET, consent gates.

Isolated stdlib tests; never bind a socket, install Collector, create paid
providers, write a real database or reveal operator credentials.
"""
from __future__ import annotations

from io import BytesIO
import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from intelligence.search_planner.http_api import (
    _dispatch_get, _dispatch_post, _make_service, _owner_guard,
    _local_transport, _json_body, handle_get, handle_post,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from test_search_planner_service_stage01 import PID, GEN, PUB, QV

SECRET="s"*48
TOKEN_HEADER="X-CM-SP-Owner-Token"
EID="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CID="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def local_project(pid):
    return {"id":pid,"name":"QA-local"}


class FakeHandler:
    def __init__(self, value=None, *, host=True, origin=True, csrf=True,
                 token=SECRET, peer="127.0.0.1", method="POST"):
        self.client_address=(peer,41234)
        self.ok_host,self.ok_origin,self.ok_csrf=host,origin,csrf
        self.responses=[]
        self.headers={
            "Host":"127.0.0.1:8765" if host else "attacker.example",
            "Origin":"http://127.0.0.1:8765" if origin else "https://attacker.example",
            "X-ContentMachine":"1" if csrf else "forged",
            TOKEN_HEADER:token,
            "Content-Type":"application/json",
        }
        raw=json.dumps({} if value is None else value,ensure_ascii=False).encode()
        self.rfile=BytesIO(raw)
        self.headers["Content-Length"]=str(len(raw))
        self.method=method
    def authorized(self, mutation=False):
        return self.ok_host and self.ok_origin and (
            not mutation or self.ok_csrf)
    def json_response(self, status, response):
        self.responses.append((status,response))
    @property
    def last(self):
        return self.responses[-1]


class RecordingSP:
    def __init__(self, guard=None):
        self.guard=guard
        self.calls=[]
    def _call(self,method,*args,**kwargs):
        self.calls.append((method,args,kwargs))
        return {"method":method,"arguments":list(args),
                "paid_provider_calls":0,"collector_receipt":None}
    def __getattr__(self,attr):
        if attr.startswith("_"):
            raise AttributeError(attr)
        def callback(*args,**kwargs):
            # The test's fake owner guard is invoked to inspect requested
            # action, not to replace production SQL and financial gates.
            if attr=="authorize_generation":
                if self.guard is None or not self.guard(
                        "search_planner.generation.authorize",args[0],args[1]):
                    raise PipelineError("Denied by owner gate",403)
            if attr=="deliver_next_handoff_chunk":
                if self.guard is None or not self.guard(
                        "search_planner.handoff.deliver",args[0],args[1]):
                    raise PipelineError("Denied by owner gate",403)
            return self._call(attr,*args,**kwargs)
        return callback
    def status(self):
        return {"schema_version":"search-planner-service/v1",
                "paid_calls_in_this_operation":0}


class RouterFactory:
    def __init__(self):
        self.services=[]
        self.guards=[]
    def __call__(self, guard):
        self.guards.append(guard)
        inst=RecordingSP(guard)
        self.services.append(inst)
        return inst


def get(route, *, handler=None, factory=None, lookup=local_project):
    h=handler or FakeHandler(method="GET")
    url=urlparse("/api/projects/"+PID+"/search-planner"+route)
    result=handle_get(h,url,service_factory=factory,
                      project_lookup=lookup)
    return result,h


def post(route, value=None, *, handler=None, factory=None,
         lookup=local_project, paid=False):
    h=handler or FakeHandler(value)
    full="/api/projects/"+PID+"/search-planner"+route
    with patch.dict(os.environ,{"CM_SP_OWNER_TOKEN":SECRET,
                                "CM_SP_PAID_HTTP_ENABLED":"1" if paid else "0"}):
        result=handle_post(h,full,service_factory=factory,
                           project_lookup=lookup)
    return result,h


class SearchPlannerHTTPStage13(unittest.TestCase):
    def test_mounted_status_read_is_local_and_never_contacts_providers(self):
        factory=RouterFactory()
        handled,h=get("/status",factory=factory)
        self.assertTrue(handled)
        self.assertEqual(h.last[0],200)
        self.assertEqual(h.last[1]["result"]["paid_calls_in_this_operation"],0)
        self.assertIsNone(factory.guards[0])
        self.assertEqual(factory.services[0].calls,[])

    def test_get_sources_gens_qv_handoff_inbox_are_read_only(self):
        cases={
            "/capabilities":("source_capabilities",(PID,)),
            "/generation/"+GEN:("get_generation",(PID,GEN)),
            "/generation/"+GEN+"/candidates":("get_generation_candidates",(PID,GEN)),
            "/generation/"+GEN+"/budget":("generation_budget",(PID,GEN)),
            "/generation/"+GEN+"/execution":("generation_execution_status",(PID,GEN)),
            "/generation/"+GEN+"/preflight":("preflight_generation_execution",(PID,GEN)),
            "/qv/"+QV+"/validated":("get_validated_queries",(PID,QV)),
            "/qv/"+QV+"/links":("get_validated_query_links",(PID,QV)),
            "/tasks/"+QV+"/preview":("preview_source_tasks",(PID,QV)),
            "/handoff/"+QV+"/preview":("preview_verified_handoff",(PID,QV)),
            "/inbox/"+EID+"/status":("get_handoff_status",(PID,EID)),
        }
        factory=RouterFactory()
        for path,(method,args) in cases.items():
            with self.subTest(path=path):
                matched,h=get(path,factory=factory)
                self.assertTrue(matched)
                self.assertEqual(h.last[0],200)
                self.assertEqual(factory.services[-1].calls[0][:2],(method,args))
                self.assertEqual(h.last[1]["result"]["paid_provider_calls"],0)
        self.assertTrue(all(g is None for g in factory.guards))

    def test_read_cannot_smuggle_mutation_or_unsupported_query_fields(self):
        factory=RouterFactory()
        for path in (
            "/generation/"+GEN+"/execute-one",
            "/generation/"+GEN+"/consent",
            "/generation/"+GEN+"/recover",
            "/inbox/"+EID+"/deliver",
            "/qv/"+QV+"/validated?approved=1",
            "/capabilities?owner=1",
            "/handoff/"+QV+"/preview?chunk_size=51",
            "/handoff/"+QV+"/preview?chunk_size=2&chunk_size=3",
        ):
            with self.subTest(path=path):
                _,h=get(path,factory=factory)
                self.assertIn(h.last[0],(404,400,422))
        self.assertFalse(any(method in ("execute_generation_batch",
                "authorize_generation") for svc in factory.services
                for method,*_ in svc.calls))

    def test_post_rejects_missing_env_secret_wrong_token_and_csrf(self):
        f=RouterFactory()
        for tok in ("wrong","s"*47,""):
            with self.subTest(token=tok):
                _,h=post("/generation/"+GEN+"/quote",handler=FakeHandler({},token=tok),
                         factory=f)
                self.assertEqual(h.last[0],403)
        for config in (
            {"host":False},{"origin":False},{"csrf":False},
            {"peer":"192.168.0.22"},
        ):
            with self.subTest(config=config):
                _,h=post("/generation/"+GEN+"/quote",
                         handler=FakeHandler({},**config),factory=f)
                self.assertEqual(h.last[0],403)
        self.assertEqual(f.services,[])
        with patch.dict(os.environ,{"CM_SP_OWNER_TOKEN":""}):
            h=FakeHandler({})
            handle_post(h,"/api/projects/"+PID+"/search-planner/generation/"+GEN+"/quote",
                        service_factory=f,project_lookup=local_project)
            self.assertEqual(h.last[0],403)

    def test_cross_project_local_workspace_and_url_enforced(self):
        f=RouterFactory()
        _,h=post("/generation/"+GEN+"/quote",
                 factory=f,lookup=lambda _:None)
        self.assertEqual(h.last[0],404)
        self.assertEqual(f.services,[])
        h=FakeHandler({})
        self.assertFalse(handle_post(
            h,"/api/projects/"+"b"*32+"/search-planner-evil/status",
            service_factory=f,project_lookup=local_project))
        self.assertEqual(h.responses,[])

    def test_post_truncated_excess_large_or_unknown_fields_are_rejected(self):
        f=RouterFactory()
        configs=[
            {"extra":1},
            {"configuration":True}, # nested config validates in service
        ]
        _,h=post("/generation/"+GEN+"/quote",{"extra":1},factory=f)
        self.assertEqual(h.last[0],400)
        h=FakeHandler({})
        h.headers["Content-Length"]="90000"
        _,r=post("/generation/"+GEN+"/quote",handler=h,factory=f)
        self.assertEqual(r.last[0],413)
        h=FakeHandler({})
        h.headers["Transfer-Encoding"]="chunked"
        _,r=post("/generation/"+GEN+"/quote",handler=h,factory=f)
        self.assertEqual(r.last[0],400)
        h=FakeHandler({})
        h.headers["Content-Type"]="text/plain"
        _,r=post("/generation/"+GEN+"/quote",handler=h,factory=f)
        self.assertEqual(r.last[0],415)
        _,r=post("/tasks/"+QV+"/commit",{"idempotency_key":"too-short"},
                 factory=f)
        self.assertEqual(r.last[0],422)

    def test_quote_and_preview_do_not_authorize_financial_execution(self):
        f=RouterFactory()
        for path,payload,method in (
            ("/generation/"+GEN+"/quote",{},"quote_generation"),
            ("/tasks/"+QV+"/preview",{},"preview_source_tasks"),
            ("/handoff/"+QV+"/preview",{"chunk_size":50},"preview_verified_handoff"),
            ("/contexts/preview",{"publication_id":PUB,"search_policy":{}},"preview_contexts"),
        ):
            _,h=post(path,payload,factory=f)
            self.assertEqual(h.last[0],200)
            self.assertEqual(f.services[-1].calls[-1][0],method)
        self.assertFalse(f.guards[-1]("unregistered.action",PID,EID))
        self.assertFalse(f.guards[-1]("search_planner.handoff.deliver","b"*32,EID))

    def test_approved_owner_consent_is_server_guard_not_payload_boolean(self):
        f=RouterFactory()
        _,h=post("/generation/"+GEN+"/consent",{
            "quote_id":CID,"authorized_usd":"0.20"},factory=f)
        self.assertEqual(h.last[0],200)
        self.assertEqual(f.services[-1].calls[-1][0],"authorize_generation")
        _,h=post("/generation/"+GEN+"/consent",{
            "quote_id":CID,"authorized_usd":"0.20","approved":True},factory=f)
        self.assertEqual(h.last[0],400)
        _,h=post("/inbox/"+EID+"/deliver",{},factory=f)
        self.assertEqual(h.last[0],200)
        self.assertEqual(f.services[-1].calls[-1][0],"deliver_next_handoff_chunk")

    def test_paid_one_batch_stays_disabled_by_default_even_with_owner_token(self):
        f=RouterFactory()
        _,h=post("/generation/"+GEN+"/execute-one",{},factory=f,paid=False)
        self.assertEqual(h.last[0],403)
        self.assertFalse(any(x[0]=="execute_generation_batch"
                             for inst in f.services for x in inst.calls))
        _,h=post("/generation/"+GEN+"/execute-one",{},factory=f,paid=True)
        self.assertEqual(h.last[0],200)
        self.assertEqual(f.services[-1].calls[-1][0],"execute_generation_batch")
        # Actual paid worker itself checks owner action + DB quote/consent
        # and will not pay without both; fake service does not run it.

    def test_no_acceptance_json_or_browser_ports_for_qv_inbox(self):
        f=RouterFactory()
        _,h=post("/handoff/"+QV+"/ingest",{
            "validated":True,"collector_accepted":True},factory=f)
        self.assertEqual(h.last[0],400)
        _,h=post("/handoff/"+QV+"/ingest",{},factory=f)
        self.assertEqual(h.last[0],200)
        self.assertEqual(f.services[-1].calls[0][0],"ingest_verified_qv_handoff")
        _,h=post("/handoff/drain",{"limit":True},factory=f)
        self.assertEqual(h.last[0],422)
        _,h=post("/inbox/"+EID+"/reconcile",{"chunk_index":-1},factory=f)
        self.assertEqual(h.last[0],422)

    def test_production_service_owner_guard_is_injected_not_client_supplied(self):
        with patch.dict(os.environ,{"CM_SP_OWNER_TOKEN":SECRET}):
            h=FakeHandler({})
            guard=_owner_guard(h,PID)
            self.assertTrue(guard("search_planner.generation.authorize",PID,GEN))
            self.assertTrue(guard("search_planner.generation.execute_paid_batch",PID,GEN))
            self.assertFalse(guard("search_planner.generation.execute_batch",PID,GEN))
            self.assertFalse(guard("search_planner.handoff.deliver",PID,"forged"))
            service=_make_service(guard)
            self.assertIsNotNone(service)
            self.assertEqual(service.status().paid_calls_in_this_operation,0)

    def test_not_found_routes_do_not_consume_body(self):
        h=FakeHandler({})
        f=RouterFactory()
        self.assertFalse(handle_get(h,urlparse("/api/projects/"+PID+"/sources"),
                                    service_factory=f,project_lookup=local_project))
        self.assertEqual(h.responses,[])
        self.assertEqual(f.services,[])

    def test_status_is_read_only_even_with_no_owner_env(self):
        f=RouterFactory()
        with patch.dict(os.environ,{"CM_SP_OWNER_TOKEN":""}):
            _,h=get("/status",factory=f)
            self.assertEqual(h.last[0],200)
            self.assertEqual(f.services[0].calls,[])


if __name__=="__main__":
    unittest.main()
