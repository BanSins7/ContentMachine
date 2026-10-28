"""UA-MOD-26 | Project-scoped local UAE HTTP backend, fail closed.

NO identity from URL/localhost alone. Requires a project-specific,
high-entropy 32-byte token whose SHA256 is provisioned out of band.
Host/Origin and mutation CSRF are enforced by app.Handler BEFORE dispatch.
This layer independently checks project token on every operation and
project_registry membership, and never exposes UA19-qualified data using
old persisted status alone. No public PIE/signed row export.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID

from .contracts import AnalyticsBoundaryError, PROJECT_RE, SHA256_RE
from .job_queue import QualificationJobQueue, QueueSpec, MAX_BATCH

HTTP_SCHEMA="uae-local-http/v1"
PROJECT_TOKENS_ENV="CM_UAE_HTTP_TOKENS_SHA256"
_TOKEN=re.compile(r"[0-9a-f]{64}\Z")
_UUID=re.compile(r"[0-9a-f-]{36}\Z")
KEY_LIMIT=100
PAGE_LIMIT=50
LIST_SQL="""
SELECT id,video_id,request_id::text AS request_id,
       snapshot_version,status,created_at
FROM uae_unified_snapshots
WHERE project_id=%s AND status IN ('partial','blocked')
 AND (%s::bigint IS NULL OR id<%s::bigint)
 ORDER BY id DESC LIMIT %s
"""
DETAIL_SQL="""
SELECT id,project_id,video_id,request_id::text AS request_id,
 snapshot_version,status,gate_schema,input_schema,created_at
FROM uae_unified_snapshots
WHERE project_id=%s AND id=%s
 AND status IN ('partial','blocked')
"""
REQUEST_SQL="""
SELECT id::text AS request_id,project_id,video_id,
 state,request_sha256,source_id,source_policy_version_id
FROM uae_qualification_requests
WHERE project_id=%s AND id=%s::uuid
"""
JOB_SQL="""
SELECT id::text AS job_id,project_id,request_id::text AS request_id,
 state,attempts,max_attempts,snapshot_id,created_at,updated_at,
 last_error_code
FROM uae_qualification_jobs WHERE project_id=%s AND id=%s::uuid
"""
PROJECT_SQL="SELECT id FROM project_registry WHERE id=%s"
NO_PUBLISHED_AGGREGATES="aggregate_requires_live_qualified_pins"


class UAEHTTPProblem(Exception):
    def __init__(self,code:str,http_status:int):
        self.code=code
        self.http_status=http_status
        super().__init__(code)


def _fail(code:str,status:int=400)->None:
    raise UAEHTTPProblem(code,status)


def _project(value:Any)->bool:
    return type(value) is str and PROJECT_RE.fullmatch(value) is not None


def _uuid(value:Any)->bool:
    if type(value) is not str or _UUID.fullmatch(value) is None:
        return False
    try:return str(UUID(value))==value
    except (ValueError,TypeError,AttributeError):return False


def _number(value:Any,low:int,high:int)->int:
    if type(value) is not int or not low<=value<=high:
        _fail("invalid_argument")
    return value


def _json_datetime(value:Any)->Any:
    if type(value) is datetime:return value.isoformat()
    return value


def _one(conn:Any,sql:str,args:tuple)->dict | None:
    rows=conn.execute(sql,args).fetchall()
    if type(rows) is not list or len(rows)>1 or any(
        not isinstance(row,Mapping) for row in rows
    ):
        _fail("storage_shape_invalid",503)
    return dict(rows[0]) if rows else None


def _rows(conn:Any,sql:str,args:tuple)->list[dict]:
    rows=conn.execute(sql,args).fetchall()
    if type(rows) is not list or any(not isinstance(x,Mapping) for x in rows):
        _fail("storage_shape_invalid",503)
    return [dict(x) for x in rows]


def _safe(row:dict,fields:tuple[str,...])->dict:
    return {key:_json_datetime(row[key]) for key in fields}


def configured_token_hashes()->dict[str,str]:
    """Read at request time for rotation, not on import. No secrets echoed."""
    raw=os.environ.get(PROJECT_TOKENS_ENV,"")
    if len(raw)>20000 or not raw:
        return {}
    try:
        value=json.loads(raw)
    except (TypeError,ValueError):
        return {}
    if (type(value) is not dict or not 1<=len(value)<=KEY_LIMIT
        or any(not _project(k) or type(v) is not str
               or _TOKEN.fullmatch(v) is None for k,v in value.items())):
        return {}
    return value


def authorized_project(project_id:str,token:Any,
                       token_hashes:Mapping[str,str])->bool:
    """Explicit per-project constant-time secret match. Fail closed by default."""
    if (not _project(project_id)
        or type(token) is not str or _TOKEN.fullmatch(token) is None
        or type(token_hashes) is not dict):
        return False
    expected=token_hashes.get(project_id)
    if type(expected) is not str or _TOKEN.fullmatch(expected) is None:
        return False
    return hmac.compare_digest(
        hashlib.sha256(bytes.fromhex(token)).hexdigest(),
        expected,
    )


@dataclass(frozen=True,slots=True)
class HTTPResult:
    status:int
    payload:dict

    def __post_init__(self)->None:
        if (type(self.status) is not int
            or self.status not in (200,201,202)
            or type(self.payload) is not dict
            or self.payload.get("schema_version")!=HTTP_SCHEMA):
            _fail("response_invalid",500)


def _queue_call(action:Any)->Any:
    """Translate known guarded queue refusals into stable public-safe codes."""
    try:
        return action()
    except AnalyticsBoundaryError as exc:
        code=getattr(exc,"code",None)
        if code=="queue_authoritative_request_unavailable":
            _fail("qualification_not_ready",409)
        if code=="queue_identity_collision":
            _fail("idempotency_conflict",409)
        if code in ("queue_spec_required","queue_batch_invalid",
                    "queue_spec_invalid","queue_cancel_invalid"):
            _fail("invalid_body",400)
        _fail("queue_unavailable",503)


class UAEHTTPService:
    """Injected DB opener and authorization mapping for easily audited tests.

    No unqualified user rows accepted. All SQL includes the project ID.
    No read endpoint calls UA19's internal reader without genuine pins.
    """

    def __init__(self,connect:Any,token_hashes:dict[str,str]):
        if (not callable(connect) or type(token_hashes) is not dict):
            _fail("http_configuration_invalid",503)
        self.connect=connect
        self.token_hashes=token_hashes

    def _open(self,project_id:str,token:Any):
        if not authorized_project(project_id,token,self.token_hashes):
            _fail("project_forbidden",403)
        return self.connect()

    @staticmethod
    def _exists(db:Any,project_id:str)->None:
        if _one(db,PROJECT_SQL,(project_id,)) is None:
            _fail("project_not_found",404)

    @staticmethod
    def _result(data:dict,status:int=200)->HTTPResult:
        return HTTPResult(status,{"ok":True,"schema_version":HTTP_SCHEMA,**data})

    def get(self,project_id:str,token:Any,action:str,
            args:dict)->HTTPResult:
        if not _project(project_id) or type(args) is not dict:
            _fail("invalid_request")
        with self._open(project_id,token) as db:
            self._exists(db,project_id)
            if action=="aggregates":
                # Status 'qualified' alone is not a current-rights grant.
                # No self-selecting public UA19 pin resolver exists yet.
                if args:_fail("invalid_query")
                _fail(NO_PUBLISHED_AGGREGATES,409)
            if action=="preview":
                if set(args)!={"request_id"} or not _uuid(args["request_id"]):
                    _fail("invalid_query")
                req=_one(db,REQUEST_SQL,(project_id,args["request_id"]))
                if req is None:_fail("not_found",404)
                return self._result({
                    "request":_safe(req,(
                        "request_id","video_id","state","source_id",
                        "source_policy_version_id",
                    )),
                    "qualification":"not_verified",
                    "preview_only":True,
                })
            if action=="jobs":
                if set(args)!={"job_id"} or not _uuid(args["job_id"]):
                    _fail("invalid_query")
                j=_one(db,JOB_SQL,(project_id,args["job_id"]))
                if j is None:_fail("not_found",404)
                return self._result({"job":_safe(j,(
                    "job_id","request_id","state","attempts",
                    "max_attempts","snapshot_id","created_at",
                    "updated_at","last_error_code",
                )),"qualified":False})
            if action=="snapshots":
                if set(args)-{"before","limit"}:
                    _fail("invalid_query")
                before=args.get("before")
                limit=args.get("limit",20)
                if before is not None:
                    _number(before,1,2**63-1)
                _number(limit,1,PAGE_LIMIT)
                entries=_rows(db,LIST_SQL,(
                    project_id,before,before,limit+1
                ))
                page=entries[:limit]
                next_cursor=page[-1]["id"] if len(entries)>limit else None
                return self._result({
                    "snapshots":[_safe(x,(
                        "id","video_id","request_id","snapshot_version",
                        "status","created_at",
                    )) for x in page],
                    "next_before":next_cursor,
                    "qualified_excluded":True,
                })
            if action=="snapshot":
                if set(args)!={"snapshot_id"}:
                    _fail("invalid_query")
                _number(args["snapshot_id"],1,2**63-1)
                row=_one(db,DETAIL_SQL,(
                    project_id,args["snapshot_id"]
                ))
                if row is None:_fail("not_found",404)
                return self._result({"snapshot":_safe(row,(
                    "id","video_id","request_id","snapshot_version",
                    "status","gate_schema","input_schema","created_at",
                )),"qualified":False,"raw_payload_excluded":True})
            _fail("not_found",404)

    def post(self,project_id:str,token:Any,action:str,
             body:dict)->HTTPResult:
        if not _project(project_id) or type(body) is not dict:
            _fail("invalid_body")
        with self._open(project_id,token) as db:
            self._exists(db,project_id)
            if action=="qualify":
                if set(body)!={"request_id","request_sha256",
                               "idempotency_key","batch_key"}:
                    _fail("invalid_body")
                item=self._enqueue_spec(project_id,body)
                result=_queue_call(lambda:QualificationJobQueue(db).enqueue(item))
                return self._result({
                    "job_id":result.job_id,"result":result.state,
                    "qualified":False,
                },202 if result.state=="created" else 200)
            if action=="qualify-batch":
                if set(body)!={"items"} or type(body["items"]) is not list:
                    _fail("invalid_body")
                items=body["items"]
                if not 1<=len(items)<=MAX_BATCH:
                    _fail("invalid_body")
                specs=tuple(self._enqueue_spec(project_id,x) for x in items)
                if len({(s.request_id,s.batch_key) for s in specs})!=len(specs):
                    _fail("duplicate_batch_member")
                results=_queue_call(lambda:QualificationJobQueue(db).enqueue_batch(specs))
                return self._result({
                    "jobs":[{"job_id":r.job_id,"result":r.state} for r in results],
                    "qualified":False,
                },202)
            if action=="cancel":
                if set(body)!={"job_id"} or not _uuid(body["job_id"]):
                    _fail("invalid_body")
                j=_one(db,JOB_SQL,(project_id,body["job_id"]))
                if j is None:_fail("not_found",404)
                if j["state"]=="cancelled":
                    return self._result({"cancelled":True,"replayed":True})
                if j["state"] in ("succeeded","dead"):
                    _fail("job_terminal",409)
                success=_queue_call(lambda:QualificationJobQueue(db).cancel(
                    project_id,body["job_id"],
                ))
                if not success:_fail("job_state_changed",409)
                return self._result({"cancelled":True,"replayed":False})
            _fail("not_found",404)

    @staticmethod
    def _enqueue_spec(pid:str,body:Any)->QueueSpec:
        if type(body) is not dict or set(body)!={
            "request_id","request_sha256","idempotency_key","batch_key"
        }:
            _fail("invalid_body")
        try:
            return QueueSpec(project_id=pid,**body)
        except (AnalyticsBoundaryError,TypeError,ValueError):
            _fail("invalid_body")


def local_db_connection():
    """Legacy local app DB credentials are not a project authorization."""
    from intelligence import config
    if not config.enabled() or not config.dsn():
        _fail("database_unavailable",503)
    import psycopg
    from psycopg.rows import dict_row
    try:
        return psycopg.connect(
            config.dsn(),connect_timeout=config.connect_timeout(),
            autocommit=True,row_factory=dict_row,
        )
    except psycopg.Error as exc:
        raise UAEHTTPProblem("database_unavailable",503) from exc


def local_http_service()->UAEHTTPService:
    return UAEHTTPService(local_db_connection,configured_token_hashes())


__all__=(
    "HTTP_SCHEMA","PROJECT_TOKENS_ENV","UAEHTTPProblem","HTTPResult",
    "UAEHTTPService","authorized_project","configured_token_hashes",
    "local_db_connection","local_http_service",
)
