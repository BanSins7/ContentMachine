"""GC-MOD-26: HTTP authorization, Host/Origin/CSRF, scope and PG17 routes.

Synthetic IAM is only for disposable unit/PG17 QA; production requires a real
external token verifier and separately signed human review/activation verifiers.
"""
from __future__ import annotations

import io
import json
import os
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from intelligence.group_c.contracts import ContractError
from intelligence.group_c.http_api import (
    API_VERSION,GroupCHTTPAPI,HTTPGrant,
    _jsonable,_pagination,_body,_json_response,
)

PROJ="a"*32
OTHER="b"*32
HOST="cm.test.local"
ORIGIN="https://"+HOST
TOKEN="test-bearer-issuer-signed-"+("z"*32)
CSRF="test-verified-csrf-"+("y"*32)


class _TestIAM:
    """Only a synthetic source of signed project/action decisions in tests."""
    def __init__(self,*,reject=False,scope=PROJ,csrf=True):
        self.reject=reject
        self.scope=scope
        self.csrf=csrf
        self.actions=[]
    def authorize(self,*,bearer_token,project_id,action,method,path):
        self.actions.append((project_id,action,method,path))
        if self.reject or bearer_token!=TOKEN:
            return None
        return HTTPGrant(principal_id="signed-human-1",
                         project_id=self.scope,action=action,
                         authentication_event_id="auth-"+str(uuid4()))
    def verify_csrf(self,*,bearer_token,project_id,action,csrf_token,origin):
        return (self.csrf and bearer_token==TOKEN and csrf_token==CSRF
                and origin==ORIGIN)


def _env(method="GET",path="/api/group-c/v1/projects/"+PROJ+"/runs",
         *,auth=True,origin=None,csrf=None,body=None,ctype="application/json",
         host=HOST,scheme="https",query="",extra=None):
    if body is None:
        raw=b""
    elif isinstance(body,bytes):
        raw=body
    else:
        raw=json.dumps(body).encode("utf-8")
    env={"REQUEST_METHOD":method,"PATH_INFO":path,"HTTP_HOST":host,
         "wsgi.url_scheme":scheme,"QUERY_STRING":query,
         "wsgi.input":io.BytesIO(raw),"CONTENT_TYPE":ctype,
         "CONTENT_LENGTH":str(len(raw))}
    if auth:
        env["HTTP_AUTHORIZATION"]="Bearer "+TOKEN
    if origin is not None:
        env["HTTP_ORIGIN"]=origin
    if csrf is not None:
        env["HTTP_X_CSRF_TOKEN"]=csrf
    if extra:
        env.update(extra)
    return env


def _call(api,env):
    captured=[]
    def start(status,headers):
        captured.append((int(status[:3]),dict(headers)))
    data=b"".join(api(env,start))
    return captured[0][0],json.loads(data.decode("utf-8")),captured[0][1]


class TestHTTPPure(unittest.TestCase):
    def setUp(self):
        self.iam=_TestIAM()
        self.calls=[]
        def forbidden_connection():
            self.calls.append("DB opened")
            raise AssertionError("Rejected request must not open database")
        self.api=GroupCHTTPAPI(
            connect=forbidden_connection,access_verifier=self.iam,
            allowed_hosts=(HOST,),allowed_origins=(ORIGIN,),
        )

    def status(self,env):
        return _call(self.api,env)[0]

    def test_factory_requires_real_ports_and_hosts(self):
        with self.assertRaises(ContractError):
            GroupCHTTPAPI(connect=None,access_verifier=self.iam,
                          allowed_hosts=(HOST,),allowed_origins=(ORIGIN,))
        with self.assertRaises(ContractError):
            GroupCHTTPAPI(connect=lambda:None,access_verifier=object(),
                          allowed_hosts=(HOST,),allowed_origins=(ORIGIN,))
        for host in ("*","BAD.local","https://localhost","evil/a",HOST+"@evil"):
            with self.subTest(host=host),self.assertRaises(ContractError):
                GroupCHTTPAPI(connect=lambda:None,access_verifier=self.iam,
                              allowed_hosts=(host,),allowed_origins=(ORIGIN,))

    def test_missing_bearer_blocks_before_database(self):
        status,data,_=_call(self.api,_env(auth=False))
        self.assertEqual(status,401)
        self.assertEqual(data["error"]["code"],"authentication_required")
        self.assertEqual(self.calls,[])

    def test_external_IAM_denies_cross_project_even_if_bearer_present(self):
        self.iam.scope=OTHER
        self.assertEqual(self.status(_env()),403)
        self.assertEqual(self.calls,[])

    def test_wrong_host_forwarded_host_and_http_rejected(self):
        for env in (
            _env(host="evil.local"),_env(extra={"HTTP_X_FORWARDED_HOST":HOST}),
            _env(extra={"HTTP_X_FORWARDED_PROTO":"https"}),
            _env(scheme="http"),
        ):
            with self.subTest(env=env):
                self.assertIn(self.status(env),(400,403))
        self.assertEqual(self.calls,[])

    def test_get_is_not_preflight_mutation(self):
        self.assertEqual(self.status(_env(method="OPTIONS")),405)
        self.assertEqual(self.status(_env(method="DELETE")),405)
        self.assertEqual(self.status(_env(path="/api/unrelated")),404)
        self.assertEqual(self.calls,[])

    def test_post_must_have_exact_origin_csrf_and_no_cookie(self):
        base="/api/group-c/v1/projects/"+PROJ+"/runs/"+str(uuid4())+"/cancel"
        requests=(
            _env("POST",base,body={}),
            _env("POST",base,origin="https://evil.local",csrf=CSRF,body={}),
            _env("POST",base,origin=ORIGIN,csrf="bad",body={}),
            _env("POST",base,origin=ORIGIN,csrf=CSRF,body={},
                 extra={"HTTP_COOKIE":"session=untrusted"}),
        )
        for env in requests:
            with self.subTest(env=env):
                self.assertEqual(self.status(env),403)
        self.assertEqual(self.calls,[])

    def test_external_csrf_denial_is_fail_closed(self):
        self.iam.csrf=False
        url="/api/group-c/v1/projects/"+PROJ+"/runs/"+str(uuid4())+"/cancel"
        self.assertEqual(self.status(_env("POST",url,body={},origin=ORIGIN,csrf=CSRF)),403)
        self.assertEqual(self.calls,[])

    def test_wrong_project_invalid_identifiers_and_bad_route(self):
        for path in (
            "/api/group-c/v1/projects/"+OTHER.upper()+"/runs",
            "/api/group-c/v1/projects/../../etc/passwd",
            "/api/group-c/v1/projects/"+PROJ+"/unknown",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.status(_env(path=path)),404)
        self.assertEqual(self.calls,[])

    def test_invalid_or_duplicate_pagination_rejected_before_db(self):
        for query in ("limit=0","limit=51","offset=10001","limit=abc",
                      "limit=1&limit=2","unknown=2","limit=1&bad=1",
                      "limit=1&offset=2&limit=3","limit","limit=2%","limit=-1"):
            with self.subTest(query=query):
                status=self.status(_env(query=query))
                self.assertEqual(status,400)
        self.assertEqual(self.calls,[])

    def test_invalid_content_and_huge_json_enforced(self):
        for data,fields,req in (
            (b'{"a":1,"a":2}',{"a"},{"a"}),
            (b'NaN',set(),set()),
            (b'[]',set(),set()),
            (b'{"unexpected":3}',set(),set()),
        ):
            with self.subTest(data=data),self.assertRaises(Exception):
                _body(_env(method="POST",body=data),fields=fields,required=req)
        with self.assertRaises(Exception):
            _body(_env(method="POST",body=b"{}"*20000),
                  fields=set(),required=set())

    def test_nonstandard_query_only_on_list(self):
        path="/api/group-c/v1/projects/"+PROJ+"/scores/18"
        self.assertEqual(self.status(_env(path=path,query="limit=1")),400)

    def test_response_security_headers_schema_and_no_float_decimal(self):
        cap=[]
        answer=_json_response(lambda status,headers:cap.append((status,dict(headers))),
                              status=200,payload={"data":{"score":Decimal("25.000000000000")}})
        self.assertEqual(json.loads(answer[0])["data"]["score"],"25.000000000000")
        self.assertEqual(json.loads(answer[0])["schema_version"],API_VERSION)
        self.assertEqual(cap[0][1]["Cache-Control"],"no-store, max-age=0")
        self.assertEqual(cap[0][1]["X-Content-Type-Options"],"nosniff")
        self.assertEqual(cap[0][1]["X-Frame-Options"],"DENY")

    def test_response_never_serializes_unknown_or_nonfinite_math(self):
        with self.assertRaises(ValueError):
            _jsonable(Decimal("NaN"))
        with self.assertRaises(TypeError):
            _jsonable(object())

    def test_iam_grant_is_immutable_and_must_include_action(self):
        grant=HTTPGrant("external-user",PROJ,"group_c:read","auth-event")
        with self.assertRaises(FrozenInstanceError):
            grant.action="group_c:run_cancel"
        with self.assertRaises(ContractError):
            HTTPGrant("external-user",PROJ,"","auth-event")

    def test_pagination_bounds_and_exact_duplicate_fields(self):
        self.assertEqual(_pagination(_env(query="limit=50&offset=10000")),(50,10000))
        self.assertEqual(_pagination(_env(query="")),(20,0))


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "Explicit disposable PostgreSQL 17 required for HTTP API integration tests",
)
class TestHTTPPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        TestDurableWorkerPG17.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        TestDurableWorkerPG17.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        self.base=TestDurableWorkerPG17()
        self.base.conn=self.conn
        self.base.psycopg=self.psycopg
        self.base.setUp()
        self.project=self.base.project
        self.iam=_TestIAM(scope=self.project)
        import psycopg
        self.api=GroupCHTTPAPI(
            connect=lambda:psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True),
            access_verifier=self.iam,
            allowed_hosts=(HOST,),allowed_origins=(ORIGIN,),
        )
        self.base_url="/api/group-c/v1/projects/"+self.project

    def tearDown(self):
        self.base.tearDown()

    def get(self,path,query=""):
        return _call(self.api,_env(path=self.base_url+path,query=query))

    def post(self,path,data):
        return _call(self.api,_env("POST",self.base_url+path,body=data,
                                   origin=ORIGIN,csrf=CSRF))

    def done(self):
        from test_group_c_durable_worker import _SyntheticC7IAM
        request=self.base.request()
        lease=self.base.claim()
        out=self.base.worker.execute_score(
            self.conn,lease=lease,review_request_id=request.request_id,
            approval_verifier=_SyntheticC7IAM(),
        )
        return out,request

    def test_run_list_detail_project_and_order(self):
        st,p,_=self.get("/runs?bad")
        self.assertEqual(st,404)
        status,data,_=self.get("/runs",query="limit=1&offset=0")
        self.assertEqual(status,200)
        self.assertEqual(len(data["data"]["items"]),1)
        run=str(data["data"]["items"][0]["id"])
        status,one,_=self.get("/runs/"+run)
        self.assertEqual(status,200)
        self.assertEqual(str(one["data"]["id"]),run)

    def test_run_cancel_real_fenced_worker_and_idempotent_replay(self):
        _,run=self.base.ids[0]
        st,body,_=self.post("/runs/"+run+"/cancel",{})
        self.assertEqual(st,200)
        self.assertTrue(body["data"]["cancelled"])
        st,again,_=self.post("/runs/"+run+"/cancel",{})
        self.assertEqual(st,200)
        self.assertFalse(again["data"]["cancelled"])
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_runs WHERE id=%s::uuid",(run,),
        ).fetchone()[0],"cancelled")

    def test_unknown_run_cross_project_and_cancel_denied(self):
        with_project_other="/api/group-c/v1/projects/"+OTHER
        st,_,_=_call(self.api,_env(path=with_project_other+"/runs"))
        self.assertEqual(st,403)
        st,_,_=self.post("/runs/"+str(uuid4())+"/cancel",{})
        self.assertEqual(st,404)

    def test_current_c7_score_exposes_four_original_sql_decimal_components(self):
        result,_=self.done()
        mid=result.score.calculated_metric_id
        st,resp,_=self.get("/scores/"+str(mid))
        self.assertEqual(st,200)
        item=resp["data"]
        self.assertEqual(item["state"],"valid_current")
        self.assertEqual(item["breakout_score"],str(result.score.breakout_score))
        vals=self.conn.execute(
            """SELECT score_creator_component,score_growth_component,
                      score_engagement_component,score_freshness_component
                 FROM calculated_metrics WHERE id=%s""",(mid,),
        ).fetchone()
        for col,val in zip(("score_creator_component","score_growth_component",
                            "score_engagement_component","score_freshness_component"),vals):
            self.assertEqual(item[col],str(val))
        self.assertEqual(item["evidence_sha256"],result.score.evidence_sha256)

    def test_expired_c7_is_never_returned_as_valid_or_scored(self):
        result,_=self.done()
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.base.source,),
        )
        mid=result.score.calculated_metric_id
        st,resp,_=self.get("/scores/"+str(mid))
        self.assertEqual(st,200)
        self.assertEqual(resp["data"]["state"],"unavailable")
        self.assertIsNone(resp["data"]["breakout_score"])
        self.assertIsNone(resp["data"]["evidence_sha256"])
        st,list_result,_=self.get("/scores")
        self.assertEqual(st,200)
        self.assertEqual(len(list_result["data"]["items"]),1)
        self.assertIsNone(list_result["data"]["items"][0]["breakout_score"])

    def test_project_video_preview_only_shows_current_certified_C7(self):
        vid=self.base.target[0]
        st,p,_=self.get("/videos/"+str(vid)+"/preview")
        self.assertEqual(st,200)
        self.assertIsNone(p["data"]["breakout_score"])
        completed,_=self.done()
        st,shown,_=self.get("/videos/"+str(vid)+"/preview")
        self.assertEqual(st,200)
        self.assertEqual(shown["data"]["state"],"valid_current")
        self.assertEqual(shown["data"]["breakout_score"],
                         str(completed.score.breakout_score))
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.base.source,),
        )
        st,denied,_=self.get("/videos/"+str(vid)+"/preview")
        self.assertEqual(st,200)
        self.assertIsNone(denied["data"]["breakout_score"])

    def test_review_list_and_no_cross_project_subject(self):
        req=self.base.request()
        st,data,_=self.get("/reviews")
        self.assertEqual(st,200)
        self.assertIn(req.request_id,[str(r["id"]) for r in data["data"]["items"]])
        st,entry,_=self.get("/reviews/"+req.request_id)
        self.assertEqual(st,200)
        self.assertEqual(str(entry["data"]["id"]),req.request_id)
        other_id=str(uuid4())
        st,_,_=self.get("/reviews/"+other_id)
        self.assertEqual(st,404)

    def test_config_approval_read_provenance_and_cohorts(self):
        req=self.base.request()
        st,data,_=self.get("/configs")
        self.assertEqual(st,200)
        self.assertTrue(any(r["id"]==self.base.config for r in data["data"]["items"]))
        st,one,_=self.get("/configs/"+str(self.base.config))
        self.assertEqual(st,200)
        self.assertEqual(str(one["data"]["review_request_id"]),req.request_id)
        st,peers,_=self.get("/cohorts")
        self.assertEqual(st,200)
        self.assertGreaterEqual(len(peers["data"]["items"]),2)
        first=peers["data"]["items"][0]["id"]
        st,cohort,_=self.get("/cohorts/"+str(first))
        self.assertEqual(st,200)
        self.assertEqual(cohort["data"]["id"],first)

    def test_mutating_decision_requires_external_human_verifier(self):
        req=self.base.fixture.signed()
        st,data,_=self.post("/reviews/"+req.request_id+"/decisions",{
            "decision":"approved","signed_assertion":"synthetic-test-only-assertion",
            "note":"I inspected this material in the separate approval tool",
        })
        self.assertEqual(st,503)
        self.assertEqual(data["error"]["code"],"human_approval_unavailable")

    def test_config_activation_requires_real_external_verifier(self):
        req=self.base.fixture.signed()
        st,data,_=self.post("/configs/"+str(self.base.config)+"/activate",{
            "review_request_id":req.request_id,
            "signed_activation_assertion":"synthetic-test-only-config-activation",
        })
        self.assertEqual(st,503)
        self.assertEqual(data["error"]["code"],"human_approval_unavailable")

    def test_review_create_enforces_real_project_freeze_and_config(self):
        req=self.base.fixture.signed()
        c,p=self.conn.execute(
            "SELECT creator_freeze_id,prior_freeze_id FROM group_c_review_requests "
            "WHERE id=%s",(req.request_id,),
        ).fetchone()
        st,result,_=self.post("/reviews",{
            "config_id":self.base.config,
            "creator_freeze_id":c,"prior_freeze_id":p,
            "minimum_creator_posts":2,"minimum_prior_posts":2,
        })
        self.assertEqual(st,201)
        self.assertEqual(result["data"]["request_id"],req.request_id)

    def test_approval_routes_cannot_access_other_project_signed_request(self):
        req=self.base.fixture.signed()
        self.iam.scope=OTHER
        st,_,_=self.post("/reviews/"+req.request_id+"/decisions",{
            "decision":"approved","signed_assertion":"synthetic-test-only-assertion",
            "note":"I have reviewed and explicitly approved this exact evidence",
        })
        self.assertEqual(st,403)

    def test_paginated_score_list_bounded_exact_one_score(self):
        result,_=self.done()
        st,ans,_=self.get("/scores",query="limit=1&offset=0")
        self.assertEqual(st,200)
        self.assertEqual(len(ans["data"]["items"]),1)
        self.assertEqual(ans["data"]["items"][0]["calculated_metric_id"],
                         result.score.calculated_metric_id)
        st,ans,_=self.get("/scores",query="limit=1&offset=1")
        self.assertEqual(st,200)
        self.assertEqual(ans["data"]["items"],[])

    def test_completed_worker_cannot_be_cancelled(self):
        result,_=self.done()
        st,data,_=self.post("/runs/"+result.lease.run_id+"/cancel",{})
        self.assertEqual(st,409)
        self.assertEqual(data["error"]["code"],"worker_terminal")


if __name__=="__main__":
    unittest.main()
