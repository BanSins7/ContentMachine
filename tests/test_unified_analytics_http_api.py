"""UA26 pure HTTP service + transport negative/golden regression.

Positive service tests use an explicitly injected project-token/DB mock,
not real qualified snapshots or a production project identity.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import UUID
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics import http_service as svc
from intelligence.unified_analytics import http_api as api
from test_unified_analytics_descriptive_aggregation import PROJECT, OTHER, Cursor

TOKEN="aa"*32
TOKEN_2="bb"*32
HASHES={PROJECT:sha256(bytes.fromhex(TOKEN)).hexdigest()}
REQUEST=str(UUID(int=13))
JOB=str(UUID(int=14))
NOW=datetime(2026,10,28,6,0,tzinfo=timezone.utc)
REQ_SHA="c"*64


class FakeHTTPDB:
    def __init__(self):
        self.queries=[]
        self.writes=[]
        self.exists=True
        self.requests={REQUEST:{
            "request_id":REQUEST,"project_id":PROJECT,"video_id":1,
            "state":"registered","request_sha256":REQ_SHA,
            "source_id":"test.source","source_policy_version_id":7,
        }}
        self.jobs={JOB:{
            "job_id":JOB,"project_id":PROJECT,"request_id":REQUEST,
            "state":"pending","attempts":0,"max_attempts":4,
            "snapshot_id":None,"created_at":NOW,"updated_at":NOW,
            "last_error_code":None,
        }}
        self.snapshots={
            9:{"id":9,"project_id":PROJECT,"video_id":1,
               "request_id":REQUEST,"snapshot_version":1,"status":"partial",
               "gate_schema":"uae-qualification/v1",
               "input_schema":"unified-analytics-input/v1",
               "created_at":NOW},
            8:{"id":8,"project_id":PROJECT,"video_id":2,
               "request_id":REQUEST,"snapshot_version":1,"status":"blocked",
               "gate_schema":"uae-qualification/v1",
               "input_schema":"unified-analytics-input/v1",
               "created_at":NOW},
            7:{"id":7,"project_id":PROJECT,"video_id":3,
               "request_id":REQUEST,"snapshot_version":1,"status":"qualified",
               "gate_schema":"uae-qualification/v1",
               "input_schema":"unified-analytics-input/v1",
               "created_at":NOW},
        }
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql,args=()):
        self.queries.append((sql,args))
        if sql==svc.PROJECT_SQL:
            return Cursor([{"id":PROJECT}] if self.exists and args[0]==PROJECT else [])
        if sql==svc.REQUEST_SQL:
            row=self.requests.get(args[1])
            return Cursor([row] if row and row["project_id"]==args[0] else [])
        if sql==svc.JOB_SQL:
            row=self.jobs.get(args[1])
            return Cursor([row] if row and row["project_id"]==args[0] else [])
        if sql==svc.LIST_SQL:
            pid,before1,before2,limit=args
            assert before1==before2
            found=[x for x in self.snapshots.values()
                   if x["project_id"]==pid
                   and x["status"] in ("partial","blocked")
                   and (before1 is None or x["id"]<before1)]
            return Cursor(sorted(found,key=lambda x:-x["id"])[:limit])
        if sql==svc.DETAIL_SQL:
            pid,k=args
            row=self.snapshots.get(k)
            return Cursor([row] if row and row["project_id"]==pid
                          and row["status"] in ("partial","blocked") else [])
        raise AssertionError("Unexpected SQL: "+sql[:80])


class QueueMock:
    def __init__(self,db):self.db=db
    def enqueue(self,spec):
        self.db.writes.append(("enqueue",spec))
        return SimpleNamespace(job_id=JOB,state="created")
    def enqueue_batch(self,specs):
        self.db.writes.append(("batch",specs))
        return tuple(SimpleNamespace(
            job_id=str(UUID(int=200+i)),state="created"
        ) for i,_ in enumerate(specs))
    def cancel(self,pid,job_id):
        self.db.writes.append(("cancel",pid,job_id))
        self.db.jobs[job_id]["state"]="cancelled"
        return True


def request_body(key="preamble",batch="request.batch.01"):
    return {"request_id":REQUEST,"request_sha256":REQ_SHA,
            "idempotency_key":"qa26."+key+".001","batch_key":batch}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.db=FakeHTTPDB()
        self.opens=0
        def opener():
            self.opens+=1
            return self.db
        self.service=svc.UAEHTTPService(opener,HASHES)

    def test_token_exact_project_check_and_hash(self):
        self.assertTrue(svc.authorized_project(PROJECT,TOKEN,HASHES))
        for bad in (TOKEN_2,"a"*64,"",True,None):
            self.assertFalse(svc.authorized_project(PROJECT,bad,HASHES))
        self.assertFalse(svc.authorized_project(OTHER,TOKEN,HASHES))
        self.assertFalse(svc.authorized_project(PROJECT,TOKEN,{}))

    def test_default_token_map_deny_all(self):
        with patch.dict("os.environ",{},clear=True):
            self.assertEqual(svc.configured_token_hashes(),{})
        self.assertFalse(svc.authorized_project(PROJECT,TOKEN,{}))

    def test_env_rejects_malformed_or_weak_hash(self):
        for item in ('{"x":"weak"}',"[]", "not-json",
                     json.dumps({PROJECT:"a"*5})):
            with patch.dict("os.environ",{svc.PROJECT_TOKENS_ENV:item}):
                self.assertEqual(svc.configured_token_hashes(),{})

    def test_token_wrong_project_403_pre_io(self):
        with self.assertRaises(svc.UAEHTTPProblem) as ctx:
            self.service.get(OTHER,TOKEN,"preview",{"request_id":REQUEST})
        self.assertEqual(ctx.exception.http_status,403)
        self.assertEqual(self.opens,0)

    def test_unauthorized_mutation_403_no_queue_write(self):
        with self.assertRaises(svc.UAEHTTPProblem) as ctx:
            self.service.post(PROJECT,TOKEN_2,"qualify",request_body())
        self.assertEqual(ctx.exception.http_status,403)
        self.assertEqual(self.opens,0)
        self.assertEqual(self.db.writes,[])

    def test_unknown_project_404_with_explicit_token(self):
        self.db.exists=False
        with self.assertRaises(svc.UAEHTTPProblem) as ctx:
            self.service.get(PROJECT,TOKEN,"preview",{"request_id":REQUEST})
        self.assertEqual(ctx.exception.http_status,404)

    def test_preview_existing_readonly_not_qualification(self):
        out=self.service.get(PROJECT,TOKEN,"preview",{"request_id":REQUEST})
        self.assertEqual(out.status,200)
        self.assertEqual(out.payload["qualification"],"not_verified")
        self.assertEqual(out.payload["request"]["request_id"],REQUEST)
        self.assertNotIn("request_sha256",out.payload["request"])
        self.assertEqual(self.db.writes,[])

    def test_preview_unknown_request_404(self):
        with self.assertRaises(svc.UAEHTTPProblem) as ctx:
            self.service.get(PROJECT,TOKEN,"preview",{"request_id":str(UUID(int=99))})
        self.assertEqual(ctx.exception.http_status,404)

    def test_preview_foreign_project_object_404(self):
        self.db.requests[REQUEST]["project_id"]=OTHER
        with self.assertRaises(svc.UAEHTTPProblem) as ctx:
            self.service.get(PROJECT,TOKEN,"preview",{"request_id":REQUEST})
        self.assertEqual(ctx.exception.http_status,404)

    def test_job_get_scoped_no_lease_token(self):
        out=self.service.get(PROJECT,TOKEN,"jobs",{"job_id":JOB})
        self.assertEqual(out.payload["job"]["state"],"pending")
        self.assertFalse(out.payload["qualified"])
        self.assertNotIn("lease_token",out.payload["job"])
        self.assertEqual(self.db.writes,[])

    def test_job_foreign_project_404(self):
        self.db.jobs[JOB]["project_id"]=OTHER
        with self.assertRaises(svc.UAEHTTPProblem) as ctx:
            self.service.get(PROJECT,TOKEN,"jobs",{"job_id":JOB})
        self.assertEqual(ctx.exception.http_status,404)

    def test_snapshots_sorted_keyset_exclude_qualified(self):
        out=self.service.get(PROJECT,TOKEN,"snapshots",{"limit":1})
        self.assertEqual(out.payload["snapshots"][0]["id"],9)
        self.assertEqual(out.payload["next_before"],9)
        next_page=self.service.get(PROJECT,TOKEN,"snapshots",{
            "limit":1,"before":9,
        })
        self.assertEqual(next_page.payload["snapshots"][0]["id"],8)
        self.assertIsNone(next_page.payload["next_before"])
        self.assertTrue(out.payload["qualified_excluded"])
        self.assertEqual(self.db.writes,[])

    def test_qualified_detail_not_a_read_grant(self):
        with self.assertRaises(svc.UAEHTTPProblem) as ctx:
            self.service.get(PROJECT,TOKEN,"snapshot",{"snapshot_id":7})
        self.assertEqual(ctx.exception.http_status,404)
        self.assertEqual(self.db.writes,[])

    def test_detail_partial_contains_no_raw_evidence(self):
        out=self.service.get(PROJECT,TOKEN,"snapshot",{"snapshot_id":9})
        self.assertEqual(out.payload["snapshot"]["status"],"partial")
        self.assertTrue(out.payload["raw_payload_excluded"])
        self.assertFalse(out.payload["qualified"])
        self.assertNotIn("readiness",out.payload["snapshot"])
        self.assertNotIn("blockers",out.payload["snapshot"])

    def test_aggregate_is_protected_unavailable_409(self):
        with self.assertRaises(svc.UAEHTTPProblem) as ctx:
            self.service.get(PROJECT,TOKEN,"aggregates",{})
        self.assertEqual(ctx.exception.http_status,409)
        self.assertEqual(ctx.exception.code,svc.NO_PUBLISHED_AGGREGATES)
        self.assertEqual(self.db.writes,[])

    def test_aggregate_crossproject_403_not_409(self):
        with self.assertRaises(svc.UAEHTTPProblem) as ctx:
            self.service.get(OTHER,TOKEN,"aggregates",{})
        self.assertEqual(ctx.exception.http_status,403)

    def test_bad_pagination_prevents_unbounded_query(self):
        for bad in (0,51,-1,True,"10"):
            with self.assertRaises(svc.UAEHTTPProblem):
                self.service.get(PROJECT,TOKEN,"snapshots",{"limit":bad})
        self.assertEqual([s for s,_ in self.db.queries if s==svc.LIST_SQL],[])

    def test_query_whitelist_rejects_unknown_arg(self):
        for action,args in (("snapshots",{"limit":2,"score":"raw"}),
                            ("preview",{"request_id":REQUEST,"raw":1}),
                            ("aggregates",{"all":"1"})):
            with self.assertRaises(svc.UAEHTTPProblem):
                self.service.get(PROJECT,TOKEN,action,args)

    def test_qualify_one_calls_actual_queue_with_explicit_project(self):
        with patch.object(svc,"QualificationJobQueue",QueueMock):
            out=self.service.post(PROJECT,TOKEN,"qualify",request_body())
        self.assertEqual(out.status,202)
        self.assertFalse(out.payload["qualified"])
        self.assertEqual(out.payload["job_id"],JOB)
        op,spec=self.db.writes[0]
        self.assertEqual(op,"enqueue")
        self.assertEqual(spec.project_id,PROJECT)
        self.assertEqual(spec.request_sha256,REQ_SHA)

    def test_queue_not_ready_returns_stable_409(self):
        class NotReady(QueueMock):
            def enqueue(self,spec):
                raise AnalyticsBoundaryError(
                    "queue_authoritative_request_unavailable","QDP unavailable"
                )
        with patch.object(svc,"QualificationJobQueue",NotReady):
            with self.assertRaises(svc.UAEHTTPProblem) as ctx:
                self.service.post(PROJECT,TOKEN,"qualify",request_body())
        self.assertEqual(ctx.exception.http_status,409)
        self.assertEqual(ctx.exception.code,"qualification_not_ready")
        self.assertEqual(self.db.writes,[])

    def test_same_idempotency_key_with_different_input_conflicts_409(self):
        class Conflict(QueueMock):
            def enqueue(self,spec):
                raise AnalyticsBoundaryError("queue_identity_collision","collision")
        with patch.object(svc,"QualificationJobQueue",Conflict):
            with self.assertRaises(svc.UAEHTTPProblem) as ctx:
                self.service.post(PROJECT,TOKEN,"qualify",request_body())
        self.assertEqual(ctx.exception.http_status,409)
        self.assertEqual(ctx.exception.code,"idempotency_conflict")
        self.assertEqual(self.db.writes,[])

    def test_unknown_queue_error_not_exposed(self):
        class Unknown(QueueMock):
            def enqueue(self,spec):
                raise AnalyticsBoundaryError("internal_credential_detail","sensitive")
        with patch.object(svc,"QualificationJobQueue",Unknown):
            with self.assertRaises(svc.UAEHTTPProblem) as ctx:
                self.service.post(PROJECT,TOKEN,"qualify",request_body())
        self.assertEqual(ctx.exception.http_status,503)
        self.assertEqual(ctx.exception.code,"queue_unavailable")

    def test_qualify_batch_atomic_spec_and_max(self):
        with patch.object(svc,"QualificationJobQueue",QueueMock):
            out=self.service.post(PROJECT,TOKEN,"qualify-batch",{
                "items":[request_body("1","batch.01"),request_body("2","batch.02")]
            })
        self.assertEqual(out.status,202)
        self.assertEqual(len(out.payload["jobs"]),2)
        self.assertEqual(len(self.db.writes),1)
        self.assertEqual(self.db.writes[0][0],"batch")

    def test_duplicate_batch_member_rejects_no_writes(self):
        with patch.object(svc,"QualificationJobQueue",QueueMock):
            with self.assertRaises(svc.UAEHTTPProblem) as ctx:
                self.service.post(PROJECT,TOKEN,"qualify-batch",{
                    "items":[request_body(),request_body()]
                })
        self.assertEqual(ctx.exception.code,"duplicate_batch_member")
        self.assertFalse(self.db.writes)

    def test_qualify_rejects_unknown_field_before_queue(self):
        obj=request_body();obj["is_qualified"]=True
        with patch.object(svc,"QualificationJobQueue",QueueMock):
            with self.assertRaises(svc.UAEHTTPProblem):
                self.service.post(PROJECT,TOKEN,"qualify",obj)
        self.assertFalse(self.db.writes)

    def test_qualify_batch_101_rejected(self):
        with self.assertRaises(svc.UAEHTTPProblem):
            self.service.post(PROJECT,TOKEN,"qualify-batch",{
                "items":[request_body(str(i),f"batch.{i}") for i in range(101)]
            })
        self.assertFalse(self.db.writes)

    def test_cancel_pending_and_idempotent_replay(self):
        with patch.object(svc,"QualificationJobQueue",QueueMock):
            one=self.service.post(PROJECT,TOKEN,"cancel",{"job_id":JOB})
            two=self.service.post(PROJECT,TOKEN,"cancel",{"job_id":JOB})
        self.assertTrue(one.payload["cancelled"])
        self.assertFalse(one.payload["replayed"])
        self.assertTrue(two.payload["replayed"])
        self.assertEqual(len(self.db.writes),1)

    def test_cancel_job_foreign_project_404_no_write(self):
        self.db.jobs[JOB]["project_id"]=OTHER
        with patch.object(svc,"QualificationJobQueue",QueueMock):
            with self.assertRaises(svc.UAEHTTPProblem) as ctx:
                self.service.post(PROJECT,TOKEN,"cancel",{"job_id":JOB})
        self.assertEqual(ctx.exception.http_status,404)
        self.assertFalse(self.db.writes)

    def test_cancel_terminal_denied(self):
        self.db.jobs[JOB]["state"]="succeeded"
        with patch.object(svc,"QualificationJobQueue",QueueMock):
            with self.assertRaises(svc.UAEHTTPProblem) as ctx:
                self.service.post(PROJECT,TOKEN,"cancel",{"job_id":JOB})
        self.assertEqual(ctx.exception.http_status,409)
        self.assertFalse(self.db.writes)

    def test_queue_replay_responses_are_no_qualification(self):
        class ReplayQueue(QueueMock):
            def enqueue(self,s):
                return SimpleNamespace(job_id=JOB,state="replayed")
        with patch.object(svc,"QualificationJobQueue",ReplayQueue):
            out=self.service.post(PROJECT,TOKEN,"qualify",request_body())
        self.assertEqual(out.status,200)
        self.assertEqual(out.payload["result"],"replayed")
        self.assertFalse(out.payload["qualified"])

    def test_no_open_without_authorized_token_even_if_project_exists(self):
        with self.assertRaises(svc.UAEHTTPProblem):
            self.service.get(PROJECT,TOKEN_2,"snapshots",{})
        self.assertEqual(self.opens,0)


class HandlerFake:
    def __init__(self,method="GET",body=b"{}",**headers):
        self.method=method
        self.headers={
            "X-UAE-Project-Token":TOKEN,
            "X-UAE-Api-Version":svc.HTTP_SCHEMA,
            "Content-Type":"application/json",
            "Content-Length":str(len(body)),
            **headers
        }
        self.rfile=BytesIO(body)
        self.responses=[]
        self.local=True
        self.mutation_allowed=True
    def authorized(self,mutation=False):
        return self.local and (not mutation or self.mutation_allowed)
    def json_response(self,code,body):
        self.responses.append((code,body))


class HandlerTests(unittest.TestCase):
    @contextmanager
    def injected(self):
        db=FakeHTTPDB()
        with patch.object(api,"local_http_service",
                          lambda:svc.UAEHTTPService(lambda:db,HASHES)):
            yield db

    def test_get_preview_http_status_and_schema(self):
        h=HandlerFake()
        with self.injected():
            self.assertTrue(api.handle_get(h,urlparse(
                f"/api/projects/{PROJECT}/unified-analytics/preview?request_id={REQUEST}"
            )))
        code,result=h.responses[0]
        self.assertEqual(code,200)
        self.assertEqual(result["schema_version"],svc.HTTP_SCHEMA)

    def test_get_unknown_project_403(self):
        h=HandlerFake()
        with self.injected():
            api.handle_get(h,urlparse(
                f"/api/projects/{OTHER}/unified-analytics/preview?request_id={REQUEST}"
            ))
        self.assertEqual(h.responses[0][0],403)
        self.assertEqual(h.responses[0][1]["code"],"project_forbidden")

    def test_http_unsupported_or_missing_api_version_409_before_db(self):
        url=urlparse(f"/api/projects/{PROJECT}/unified-analytics/snapshots")
        for version in ("uae-local-http/v0","",None):
            h=HandlerFake()
            if version is None:
                h.headers.pop("X-UAE-Api-Version")
            else:
                h.headers["X-UAE-Api-Version"]=version
            with self.injected() as db:
                api.handle_get(h,url)
            self.assertEqual(h.responses[0][0],409)
            self.assertEqual(h.responses[0][1]["code"],
                             "unsupported_api_version")
            self.assertEqual(db.queries,[])

    def test_get_mutation_header_not_needed_but_host_required(self):
        h=HandlerFake();h.local=False
        with self.injected():
            api.handle_get(h,urlparse(
                f"/api/projects/{PROJECT}/unified-analytics/snapshots"
            ))
        self.assertEqual(h.responses[0][0],403)

    def test_post_requires_host_origin_csrf(self):
        h=HandlerFake("POST");h.mutation_allowed=False
        with self.injected() as db:
            api.handle_post(h,f"/api/projects/{PROJECT}/unified-analytics/qualify")
        self.assertEqual(h.responses[0][0],403)
        self.assertEqual(db.queries,[])

    def test_http_post_qualify_queue(self):
        body=json.dumps(request_body()).encode()
        h=HandlerFake("POST",body)
        with self.injected() as db,patch.object(
            svc,"QualificationJobQueue",QueueMock
        ):
            api.handle_post(h,f"/api/projects/{PROJECT}/unified-analytics/qualify")
        self.assertEqual(h.responses[0][0],202)
        self.assertEqual(len(db.writes),1)

    def test_http_reject_duplicate_body_keys_no_queue(self):
        h=HandlerFake("POST",b'{"request_id":"x","request_id":"y"}')
        with self.injected() as db:
            api.handle_post(h,f"/api/projects/{PROJECT}/unified-analytics/qualify")
        self.assertEqual(h.responses[0],(400,{
            "ok":False,"code":"duplicate_json_key","error":"UAE request rejected"
        }))
        self.assertEqual(db.queries,[])

    def test_http_reject_bad_json_content_type(self):
        h=HandlerFake("POST",b"{}",**{"Content-Type":"text/plain"})
        with self.injected():
            api.handle_post(h,f"/api/projects/{PROJECT}/unified-analytics/qualify")
        self.assertEqual(h.responses[0][0],415)

    def test_http_duplicate_query_parameter_400(self):
        h=HandlerFake()
        with self.injected():
            api.handle_get(h,urlparse(
                f"/api/projects/{PROJECT}/unified-analytics/snapshots?limit=2&limit=50"
            ))
        self.assertEqual(h.responses[0][0],400)
        self.assertEqual(h.responses[0][1]["code"],"invalid_query")

    def test_http_qualified_snapshot_detail_not_found(self):
        h=HandlerFake()
        with self.injected():
            api.handle_get(h,urlparse(
                f"/api/projects/{PROJECT}/unified-analytics/snapshots/7"
            ))
        self.assertEqual(h.responses[0][0],404)

    def test_http_aggregate_409_no_fake_approval(self):
        h=HandlerFake()
        with self.injected():
            api.handle_get(h,urlparse(
                f"/api/projects/{PROJECT}/unified-analytics/aggregates"
            ))
        self.assertEqual(h.responses[0][0],409)

    def test_http_route_no_match_has_no_response(self):
        h=HandlerFake()
        with self.injected() as db:
            self.assertFalse(api.handle_get(h,urlparse(
                f"/api/projects/{PROJECT}/sources"
            )))
        self.assertEqual(h.responses,[])
        self.assertEqual(db.queries,[])

    def test_http_post_cancel_extra_json_rejected(self):
        h=HandlerFake("POST",b'{"force":true}')
        with self.injected() as db:
            api.handle_post(h,f"/api/projects/{PROJECT}/unified-analytics/jobs/{JOB}/cancel")
        self.assertEqual(h.responses[0][0],400)
        self.assertEqual(db.queries,[])

    def test_http_invalid_route_404_no_db(self):
        h=HandlerFake()
        with self.injected() as db:
            api.handle_get(h,urlparse(
                f"/api/projects/{PROJECT}/unified-analytics/unsigned-export"
            ))
        self.assertEqual(h.responses[0][0],404)
        self.assertEqual(db.queries,[])

    def test_http_invalid_paginated_id_400(self):
        h=HandlerFake()
        with self.injected() as db:
            api.handle_get(h,urlparse(
                f"/api/projects/{PROJECT}/unified-analytics/snapshots?limit=0"
            ))
        self.assertEqual(h.responses[0][0],400)
        self.assertEqual(db.queries,[])


if __name__=="__main__":
    unittest.main()
