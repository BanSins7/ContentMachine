"""GC-MOD-26: dependency-free, authenticated Group C WSGI HTTP interface.

This module does NOT mount into app.py, start a listener, authenticate with
untrusted forwarded headers, fetch providers, call an LLM or score C7. A
trusted deployment must inject a real project IAM verifier, CSRF verifier,
human-review/activation verifiers and a dedicated PostgreSQL connection
factory. Default construction without these is NOT permitted.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import parse_qsl
from uuid import UUID

from .analytics_bridge import PostgreSQLAnalyticsBridge
from .config_activation import PostgreSQLConfigActivation
from .contracts import ContractError
from .d1 import _record, _records
from .durable_worker import PostgreSQLGroupCWorker
from .human_review import PostgreSQLHumanReview
from .retention import PostgreSQLScoreRetention

API_VERSION="group-c-http/v1"
_PREFIX="/api/group-c/v1/projects/"
_PROJECT_RE=re.compile(r"[0-9a-f]{32}\Z")
_ID_RE=re.compile(r"[1-9][0-9]{0,18}\Z")
_FIELD_RE=re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_AUTH_RE=re.compile(r"Bearer ([A-Za-z0-9._~+/-]{24,4096})\Z")
_JSON_MAX=32768
_MAX_PAGE=10000
_MAX_LIMIT=50

# The deployment's IAM must validate token signatures, expiration, revocation,
# exact project role and this action; the HTTP route does not trust X-User,
# cookies, X-Forwarded-For/Host/Proto or claims in its own request body.
@dataclass(frozen=True)
class HTTPGrant:
    principal_id: str
    project_id: str
    action: str
    authentication_event_id: str

    def __post_init__(self):
        if (type(self.principal_id) is not str or not self.principal_id.strip()
                or type(self.project_id) is not str
                or _PROJECT_RE.fullmatch(self.project_id) is None
                or type(self.action) is not str or not self.action.strip()
                or type(self.authentication_event_id) is not str
                or not self.authentication_event_id.strip()):
            raise ContractError("http_auth_invalid","Exact verified IAM grant required")


class HTTPAccessVerifier(Protocol):
    def authorize(
        self, *, bearer_token:str, project_id:str,
        action:str, method:str, path:str,
    ) -> HTTPGrant | None: ...
    def verify_csrf(
        self, *, bearer_token:str, project_id:str,
        action:str, csrf_token:str, origin:str,
    ) -> bool: ...


class _HTTPError(Exception):
    def __init__(self,status:int,code:str):
        self.status=status
        self.code=code
        super().__init__(code)


def _jsonable(x:Any)->Any:
    if is_dataclass(x) and not isinstance(x,type):
        return _jsonable(asdict(x))
    if isinstance(x,Decimal):
        if not x.is_finite():
            raise ValueError("Nonfinite Decimal in response")
        return str(x)
    if isinstance(x,(datetime,date)):
        if isinstance(x,datetime) and (x.tzinfo is None or x.utcoffset() is None):
            raise ValueError("Naive timestamp")
        return x.isoformat()
    if isinstance(x,UUID):
        return str(x)
    if isinstance(x,Mapping):
        return {str(k):_jsonable(v) for k,v in x.items()}
    if isinstance(x,(tuple,list)):
        return [_jsonable(v) for v in x]
    if x is None or type(x) in (bool,int,float,str):
        return x
    raise TypeError("Unsupported response type")


def _json_response(start_response:Callable, *, status:int, payload:dict):
    text=json.dumps(_jsonable({"schema_version":API_VERSION,**payload}),
                    ensure_ascii=False,sort_keys=True,separators=(",",":"))
    body=text.encode("utf-8")
    labels={200:"OK",201:"Created",400:"Bad Request",401:"Unauthorized",
            403:"Forbidden",404:"Not Found",405:"Method Not Allowed",
            409:"Conflict",413:"Content Too Large",415:"Unsupported Media Type",
            422:"Unprocessable Entity",429:"Too Many Requests",
            500:"Internal Server Error",503:"Service Unavailable"}
    start_response(f"{status} {labels.get(status,'Error')}",[
        ("Content-Type","application/json; charset=utf-8"),
        ("Content-Length",str(len(body))),
        ("Cache-Control","no-store, max-age=0"),
        ("Pragma","no-cache"),
        ("X-Content-Type-Options","nosniff"),
        ("Referrer-Policy","no-referrer"),
        ("X-Frame-Options","DENY"),
        ("Content-Security-Policy","default-src 'none'; frame-ancestors 'none'"),
        ("Vary","Authorization"),
    ])
    return [body]


def _project(value:Any)->str:
    if type(value) is not str or _PROJECT_RE.fullmatch(value) is None:
        raise _HTTPError(404,"not_found")
    return value


def _uuid(value:Any)->str:
    if type(value) is not str:
        raise _HTTPError(404,"not_found")
    try:
        x=UUID(value)
    except (AttributeError,ValueError,TypeError):
        raise _HTTPError(404,"not_found") from None
    if str(x)!=value:
        raise _HTTPError(404,"not_found")
    return value


def _id(value:Any)->int:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise _HTTPError(404,"not_found")
    n=int(value)
    if n>=2**63:
        raise _HTTPError(404,"not_found")
    return n


def _pagination(environ:Mapping[str,Any])->tuple[int,int]:
    query=environ.get("QUERY_STRING","")
    if type(query) is not str or len(query)>240:
        raise _HTTPError(400,"invalid_query")
    try:
        pairs=parse_qsl(query,keep_blank_values=True,strict_parsing=True)
    except ValueError:
        raise _HTTPError(400,"invalid_query") from None
    if len(pairs)>2 or any(k not in ("limit","offset") for k,_ in pairs):
        raise _HTTPError(400,"invalid_query")
    if len({k for k,_ in pairs})!=len(pairs):
        raise _HTTPError(400,"invalid_query")
    d=dict(pairs)
    for v in d.values():
        if not v.isascii() or not v.isdecimal() or len(v)>5:
            raise _HTTPError(400,"invalid_pagination")
    limit=int(d.get("limit","20"))
    offset=int(d.get("offset","0"))
    if not 1<=limit<=_MAX_LIMIT or not 0<=offset<=_MAX_PAGE:
        raise _HTTPError(400,"invalid_pagination")
    return limit,offset


def _body(environ:Mapping[str,Any],*,fields:set[str],required:set[str])->dict:
    ctype=environ.get("CONTENT_TYPE","")
    if ctype not in ("application/json","application/json; charset=utf-8"):
        raise _HTTPError(415,"json_required")
    length=environ.get("CONTENT_LENGTH","")
    if type(length) is not str or not length.isascii() or not length.isdecimal():
        raise _HTTPError(400,"invalid_content_length")
    if len(length)>6:
        raise _HTTPError(413,"invalid_content_length")
    n=int(length)
    if n<2 or n>_JSON_MAX:
        raise _HTTPError(413 if n>_JSON_MAX else 400,"invalid_content_length")
    stream=environ.get("wsgi.input")
    if stream is None:
        raise _HTTPError(400,"body_unavailable")
    raw=stream.read(n)
    if len(raw)!=n:
        raise _HTTPError(400,"invalid_content_length")
    try:
        def pairs(items):
            out={}
            for k,v in items:
                if k in out:
                    raise ValueError("duplicate JSON key")
                out[k]=v
            return out
        payload=json.loads(raw.decode("utf-8"),object_pairs_hook=pairs,
                           parse_constant=lambda _:(_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (UnicodeError,ValueError):
        raise _HTTPError(400,"invalid_json") from None
    if type(payload) is not dict or not required.issubset(payload) or not set(payload).issubset(fields):
        raise _HTTPError(422,"invalid_request_fields")
    return payload


def _int_field(obj:dict,key:str,required:bool=True)->int|None:
    v=obj.get(key)
    if v is None and not required:
        return None
    if type(v) is not int or not 1<=v<2**63:
        raise _HTTPError(422,"invalid_"+key)
    return v


def _minimum(obj:dict,key:str,required:bool=True)->int|None:
    value=_int_field(obj,key,required)
    if value is not None and value>500:
        raise _HTTPError(422,"invalid_review_minimum")
    return value


def _uuid_field(obj:dict,key:str)->str:
    try:
        return _uuid(obj[key])
    except _HTTPError:
        raise _HTTPError(422,"invalid_"+key) from None


def _reason(exc:ContractError)->tuple[int,str]:
    # Error surface intentionally omits raw ContractError messages; those may
    # reveal source/policy, SQL objects, external IAM or peer provenance.
    code=exc.code if type(exc.code) is str and _FIELD_RE.fullmatch(exc.code) else "group_c_rejected"
    if code.startswith(("invalid_","missing_minimum_")):
        return 422,code
    if code in ("score_not_found","worker_job_missing","project_video_unavailable",
                "missing_review_subject","creator_cohort_not_approved"):
        return 404,"not_found"
    if code in ("human_auth_unavailable","review_not_approved"):
        return 403,"not_authorized"
    if code in ("dependency_unavailable","transaction_contract_unavailable",
                "source_policy_unavailable","source_expired_or_retracted",
                "metric_snapshot_expired","metadata_expired"):
        return 503,"dependency_unavailable"
    return 409,code


class GroupCHTTPAPI:
    """WSGI handler; no HTTP listener, no production IAM implementation."""

    def __init__(
        self, *, connect:Callable[[],Any],
        access_verifier:HTTPAccessVerifier,
        allowed_hosts:tuple[str,...],
        allowed_origins:tuple[str,...],
        human_reviewer_verifier:Any=None,
        config_activation_verifier:Any=None,
        require_https:bool=True,
    ):
        if not callable(connect):
            raise ContractError("http_unavailable","Dedicated PG connection factory required")
        if not callable(getattr(access_verifier,"authorize",None)) or not callable(
                getattr(access_verifier,"verify_csrf",None)):
            raise ContractError("http_auth_unavailable","Real external IAM/CSRF verifier required")
        if (type(allowed_hosts) is not tuple or not allowed_hosts
                or any(type(h) is not str or not h or h.lower()!=h
                       or "/" in h or "@" in h or "*" in h or h.strip()!=h
                       for h in allowed_hosts)
                or len(set(allowed_hosts))!=len(allowed_hosts)):
            raise ContractError("invalid_http_hosts","Explicit canonical Host allowlist required")
        if (type(allowed_origins) is not tuple or not allowed_origins
                or any(type(o) is not str or not o.startswith("https://")
                       or o[8:] not in allowed_hosts for o in allowed_origins)
                or len(set(allowed_origins))!=len(allowed_origins)):
            raise ContractError("invalid_http_origins","Exact HTTPS Origin allowlist required")
        if type(require_https) is not bool:
            raise ContractError("invalid_http_option","HTTPS mode must be boolean")
        self.connect=connect
        self.access_verifier=access_verifier
        self.allowed_hosts=frozenset(allowed_hosts)
        self.allowed_origins=frozenset(allowed_origins)
        self.human_reviewer_verifier=human_reviewer_verifier
        self.config_activation_verifier=config_activation_verifier
        self.require_https=require_https

    def __call__(self,environ,start_response):
        try:
            response=self._handle(environ)
            return _json_response(start_response,status=response[0],payload=response[1])
        except _HTTPError as e:
            return _json_response(start_response,status=e.status,
                                  payload={"error":{"code":e.code}})
        except ContractError as e:
            status,code=_reason(e)
            return _json_response(start_response,status=status,
                                  payload={"error":{"code":code}})
        except Exception:
            # Never expose SQL, stacktrace, assertion or authorization details.
            return _json_response(start_response,status=500,
                                  payload={"error":{"code":"internal_error"}})

    def _handle(self,environ):
        method=environ.get("REQUEST_METHOD","")
        path=environ.get("PATH_INFO","")
        host=environ.get("HTTP_HOST","")
        if (type(path) is not str or len(path)>250 or
                type(host) is not str or host not in self.allowed_hosts or
                environ.get("HTTP_X_FORWARDED_HOST") or
                environ.get("HTTP_X_FORWARDED_PROTO")):
            raise _HTTPError(400,"invalid_request_host")
        if self.require_https and environ.get("wsgi.url_scheme")!="https":
            raise _HTTPError(403,"https_required")
        if method not in ("GET","POST"):
            raise _HTTPError(405,"method_not_allowed")
        if not path.startswith(_PREFIX):
            raise _HTTPError(404,"not_found")
        sub=path[len(_PREFIX):].split("/")
        if not sub or not sub[0]:
            raise _HTTPError(404,"not_found")
        project_id=_project(sub[0])
        tail=sub[1:]
        action=self._route(method,tail)
        if action is None:
            raise _HTTPError(404,"not_found")
        if method=="POST" and environ.get("QUERY_STRING"):
            raise _HTTPError(400,"invalid_query")
        if method=="GET":
            # Reject invalid pagination before *any* DB connection is opened.
            if len(tail)==1 and tail[0] in ("runs","scores","reviews","configs","cohorts"):
                _pagination(environ)
            elif environ.get("QUERY_STRING"):
                raise _HTTPError(400,"invalid_query")
        token=environ.get("HTTP_AUTHORIZATION","")
        if type(token) is not str:
            raise _HTTPError(401,"authentication_required")
        match=_AUTH_RE.fullmatch(token)
        if match is None:
            raise _HTTPError(401,"authentication_required")
        token=match.group(1)
        try:
            grant=self.access_verifier.authorize(
                bearer_token=token,project_id=project_id,action=action,
                method=method,path=path,
            )
        except Exception:
            raise _HTTPError(403,"not_authorized") from None
        if (not isinstance(grant,HTTPGrant)
                or grant.project_id!=project_id or grant.action!=action):
            raise _HTTPError(403,"not_authorized")
        if method=="POST":
            origin=environ.get("HTTP_ORIGIN","")
            csrf=environ.get("HTTP_X_CSRF_TOKEN","")
            if (type(origin) is not str or origin not in self.allowed_origins
                    or type(csrf) is not str or len(csrf)<24 or len(csrf)>4096
                    or environ.get("HTTP_COOKIE")):
                raise _HTTPError(403,"csrf_failed")
            if origin.split("://",1)[1]!=host:
                raise _HTTPError(403,"csrf_failed")
            try:
                checked=self.access_verifier.verify_csrf(
                    bearer_token=token,project_id=project_id,action=action,
                    csrf_token=csrf,origin=origin,
                )
            except Exception:
                raise _HTTPError(403,"csrf_failed") from None
            if checked is not True:
                raise _HTTPError(403,"csrf_failed")
        with self.connect() as conn:
            if getattr(conn,"autocommit",None) is not True:
                raise ContractError("transaction_contract_unavailable",
                                    "HTTP operations require dedicated autocommit connection")
            return self._dispatch(conn,method,project_id,tail,environ)

    @staticmethod
    def _route(method,tail):
        if method=="GET":
            if len(tail)==1 and tail[0] in ("runs","scores","reviews","configs","cohorts"):
                return "group_c:read"
            if len(tail)==2 and tail[0] in ("runs","scores","reviews","configs","cohorts"):
                return "group_c:read"
            if len(tail)==3 and tail[0]=="videos" and tail[2] in ("preview","studio"):
                return "group_c:read"
        if method=="POST":
            if tail==["reviews"]:
                return "group_c:review_create"
            if len(tail)==3 and tail[0]=="reviews" and tail[2]=="decisions":
                return "group_c:review_decide"
            if len(tail)==3 and tail[0]=="configs" and tail[2]=="activate":
                return "group_c:config_activate"
            if len(tail)==3 and tail[0]=="runs" and tail[2]=="cancel":
                return "group_c:run_cancel"
        return None

    def _dispatch(self,conn,method,project_id,tail,environ):
        noun=tail[0]
        if method=="GET" and len(tail)==1:
            return 200,{"data":self._list(conn,project_id,noun,environ)}
        if method=="GET" and len(tail)==2:
            return 200,{"data":self._one(conn,project_id,noun,tail[1])}
        if method=="GET" and noun=="videos" and len(tail)==3:
            if tail[2]=="studio":
                from .studio_read import PostgreSQLGroupCStudio
                return 200,{"data":PostgreSQLGroupCStudio().read(
                    conn,project_id=project_id,video_id=_id(tail[1]))}
            return 200,{"data":self._preview(conn,project_id,_id(tail[1]))}
        if method=="POST" and tail==["reviews"]:
            body=_body(environ,fields={
                "config_id","creator_freeze_id","prior_freeze_id",
                "niche_freeze_id","minimum_creator_posts","minimum_prior_posts",
                "minimum_niche_posts"},required={
                "config_id","creator_freeze_id","prior_freeze_id",
                "minimum_creator_posts","minimum_prior_posts"})
            config=_int_field(body,"config_id")
            creator=_int_field(body,"creator_freeze_id")
            prior=_int_field(body,"prior_freeze_id")
            niche=_int_field(body,"niche_freeze_id",False)
            c_min=_minimum(body,"minimum_creator_posts")
            p_min=_minimum(body,"minimum_prior_posts")
            n_min=_minimum(body,"minimum_niche_posts",False)
            if (niche is None)!=(n_min is None):
                raise _HTTPError(422,"invalid_niche_review")
            receipt=PostgreSQLHumanReview().create_request(
                conn,project_id=project_id,config_id=config,
                creator_freeze_id=creator,prior_freeze_id=prior,
                niche_freeze_id=niche,
                minimum_creator_posts=c_min,minimum_prior_posts=p_min,
                minimum_niche_posts=n_min,
            )
            return 201,{"data":receipt}
        if method=="POST" and noun=="reviews":
            ident=_uuid(tail[1])
            body=_body(environ,fields={"decision","signed_assertion","note"},
                       required={"decision","signed_assertion","note"})
            if body["decision"] not in ("approved","rejected"):
                raise _HTTPError(422,"invalid_review_decision")
            if any(type(body[k]) is not str for k in ("signed_assertion","note")):
                raise _HTTPError(422,"invalid_review_request")
            self._review_project(conn,project_id,ident)
            if self.human_reviewer_verifier is None:
                raise _HTTPError(503,"human_approval_unavailable")
            receipt=PostgreSQLHumanReview().record_decision(
                conn,request_id=ident,decision=body["decision"],
                signed_assertion=body["signed_assertion"],note=body["note"],
                reviewer_verifier=self.human_reviewer_verifier,
            )
            return 200,{"data":receipt}
        if method=="POST" and noun=="configs":
            ident=_id(tail[1])
            body=_body(environ,fields={
                "review_request_id","replace_active_config_id",
                "signed_activation_assertion"},
                required={"review_request_id","signed_activation_assertion"})
            review=_uuid_field(body,"review_request_id")
            prev=_int_field(body,"replace_active_config_id",False)
            if type(body["signed_activation_assertion"]) is not str:
                raise _HTTPError(422,"invalid_activation_signature")
            self._review_project(conn,project_id,review,config_id=ident)
            if self.config_activation_verifier is None:
                raise _HTTPError(503,"human_approval_unavailable")
            receipt=PostgreSQLConfigActivation().activate(
                conn,project_id=project_id,review_request_id=review,
                expected_config_id=ident,replace_active_config_id=prev,
                signed_activation_assertion=body["signed_activation_assertion"],
                approval_verifier=self.config_activation_verifier,
            )
            return 200,{"data":receipt}
        if method=="POST" and noun=="runs":
            run=_uuid(tail[1])
            _body(environ,fields=set(),required=set())
            job=_record(conn.execute(
                """SELECT id FROM group_c_jobs
                    WHERE project_id=%s AND run_id=%s::uuid
                    ORDER BY id DESC LIMIT 1""",(project_id,run),
            ))
            if job is None:
                raise _HTTPError(404,"not_found")
            changed=PostgreSQLGroupCWorker().cancel(
                conn,project_id=project_id,job_id=job["id"],
            )
            return 200,{"data":{
                "run_id":run,"job_id":job["id"],"cancelled":changed,
                "state":"cancelled",
            }}
        raise _HTTPError(404,"not_found")

    @staticmethod
    def _review_project(conn,project_id,request_id,config_id=None):
        row=_record(conn.execute(
            """SELECT id,calculation_config_id
               FROM group_c_review_requests
               WHERE project_id=%s AND id=%s::uuid""",
            (project_id,request_id),
        ))
        if row is None or (
            config_id is not None and row["calculation_config_id"]!=config_id
        ):
            raise _HTTPError(404,"not_found")
        return row

    def _list(self,conn,project,noun,environ):
        limit,offset=_pagination(environ)
        if noun=="runs":
            query="""SELECT id,video_id,state,reason_code,calculated_metric_id,
                            snapshot_id,previous_snapshot_id,calculation_config_id,
                            created_at,updated_at,completed_at
                       FROM group_c_runs WHERE project_id=%s
                       ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s"""
        elif noun=="reviews":
            query="""SELECT r.id,r.target_video_id,r.calculation_config_id,
                            r.creator_freeze_id,r.prior_freeze_id,r.niche_freeze_id,
                            r.subject_sha256,r.created_at,
                            d.decision,d.id AS decision_id
                       FROM group_c_review_requests r
                       LEFT JOIN group_c_review_decisions d ON d.request_id=r.id
                       WHERE r.project_id=%s
                       ORDER BY r.created_at DESC,r.id DESC LIMIT %s OFFSET %s"""
        elif noun=="configs":
            query="""SELECT DISTINCT ON (c.id) c.id,c.calculation_version,
                            c.is_active,e.review_request_id,
                            e.review_decision_id,e.id AS activation_event_id,
                            e.created_at
                       FROM group_c_config_activation_events e
                       JOIN metric_calculation_configs c ON c.id=e.config_id
                       WHERE e.project_id=%s AND e.kind='activated'
                       ORDER BY c.id DESC,e.created_at DESC,e.id DESC
                       LIMIT %s OFFSET %s"""
        elif noun=="cohorts":
            query="""SELECT id,purpose,state,member_count,legacy_cohort_id,
                            source_metric_definition,source_metric_version,
                            evidence_sha256,target_video_id,sealed_at
                       FROM group_c_cohort_freezes
                       WHERE project_id=%s
                       ORDER BY sealed_at DESC,id DESC LIMIT %s OFFSET %s"""
        elif noun=="scores":
            query="""SELECT c.id,c.post_id AS video_id,c.calculation_config_id,
                            c.snapshot_id,c.previous_snapshot_id,c.calculated_at
                       FROM calculated_metrics c
                       JOIN group_c_calculation_receipts r
                         ON r.calculated_metric_id=c.id
                       WHERE r.project_id=%s
                       ORDER BY c.calculated_at DESC,c.id DESC LIMIT %s OFFSET %s"""
        else:
            raise _HTTPError(404,"not_found")
        rows=_records(conn.execute(query,(project,limit,offset)))
        if noun=="scores":
            out=[]
            for row in rows:
                current=PostgreSQLScoreRetention().read_current(
                    conn,project_id=project,calculated_metric_id=row["id"],
                )
                out.append({
                    "calculated_metric_id":row["id"],
                    "video_id":row["video_id"],
                    "state":current.state,
                    "reason_code":current.reason_code,
                    "breakout_score":current.breakout_score,
                })
            rows=out
        return {"items":rows,"limit":limit,"offset":offset,
                "next_offset":offset+len(rows) if len(rows)==limit
                              and offset+len(rows)<=_MAX_PAGE else None}

    def _one(self,conn,project,noun,ident):
        if noun=="scores":
            mid=_id(ident)
            result=PostgreSQLScoreRetention().read_current(
                conn,project_id=project,calculated_metric_id=mid,
            )
            if not result.valid:
                return {
                    "calculated_metric_id":mid,"state":result.state,
                    "reason_code":result.reason_code,
                    "breakout_score":None,"evidence_sha256":None,
                }
            from .analytics_bridge import PostgreSQLAnalyticsBridge
            certified=PostgreSQLAnalyticsBridge().load_persisted(
                conn,project_id=project,calculated_metric_id=mid,
            )
            return {
                "calculated_metric_id":mid,"state":"valid_current",
                "reason_code":None,"breakout_score":certified.breakout_score,
                "evidence_sha256":certified.evidence_sha256,
                "source_policy_version_id":certified.source_policy_version_id,
                "metric_version":certified.metric_version,
                "snapshot_id":certified.snapshot_id,
                "previous_snapshot_id":certified.previous_snapshot_id,
                "published_metadata_id":certified.published_metadata_id,
                "calculation_config_id":certified.calculation_config_id,
                "creator_freeze_id":certified.creator_freeze_id,
                "prior_freeze_id":certified.prior_freeze_id,
                "niche_freeze_id":certified.niche_freeze_id,
                "score_creator_component":certified.score_creator_component,
                "score_growth_component":certified.score_growth_component,
                "score_engagement_component":certified.score_engagement_component,
                "score_freshness_component":certified.score_freshness_component,
            }
        if noun=="runs":
            key=_uuid(ident)
            q="""SELECT id,video_id,state,reason_code,calculated_metric_id,
                        snapshot_id,previous_snapshot_id,calculation_config_id,
                        created_at,updated_at,completed_at
                   FROM group_c_runs WHERE project_id=%s AND id=%s::uuid"""
            args=(project,key)
        elif noun=="reviews":
            key=_uuid(ident)
            q="""SELECT r.id,r.target_video_id,r.calculation_config_id,
                        r.creator_freeze_id,r.prior_freeze_id,r.niche_freeze_id,
                        r.subject_sha256,r.created_at,
                        d.decision,d.id AS decision_id
                   FROM group_c_review_requests r
                   LEFT JOIN group_c_review_decisions d ON d.request_id=r.id
                   WHERE r.project_id=%s AND r.id=%s::uuid"""
            args=(project,key)
        elif noun=="configs":
            key=_id(ident)
            q="""SELECT c.id,c.calculation_version,c.is_active,
                        e.review_request_id,e.review_decision_id,
                        e.id AS activation_event_id,e.created_at
                   FROM group_c_config_activation_events e
                   JOIN metric_calculation_configs c ON c.id=e.config_id
                   WHERE e.project_id=%s AND e.config_id=%s AND e.kind='activated'
                   ORDER BY e.created_at DESC,e.id DESC LIMIT 1"""
            args=(project,key)
        elif noun=="cohorts":
            key=_id(ident)
            q="""SELECT id,purpose,state,member_count,legacy_cohort_id,
                        source_metric_definition,source_metric_version,
                        evidence_sha256,target_video_id,sealed_at
                   FROM group_c_cohort_freezes
                   WHERE project_id=%s AND id=%s"""
            args=(project,key)
        else:
            raise _HTTPError(404,"not_found")
        row=_record(conn.execute(q,args))
        if row is None:
            raise _HTTPError(404,"not_found")
        return row

    def _preview(self,conn,project,video_id):
        # The HTTP layer NEVER assembles uncertified C7 from raw facts. The
        # "preview" is the most recent project's actual run and its certified
        # score, if still eligible under GC24; otherwise an explicit reason.
        link=_record(conn.execute(
            """SELECT project_id,video_id FROM sre_project_videos
               WHERE project_id=%s AND video_id=%s""",(project,video_id),
        ))
        if link is None:
            raise _HTTPError(404,"not_found")
        run=_record(conn.execute(
            """SELECT id,state,reason_code,calculated_metric_id,
                      snapshot_id,previous_snapshot_id,calculation_config_id
               FROM group_c_runs
               WHERE project_id=%s AND video_id=%s
               ORDER BY created_at DESC,id DESC LIMIT 1""",
            (project,video_id),
        ))
        if run is None:
            return {"video_id":video_id,"state":"not_calculated",
                    "breakout_score":None,"reason_code":"no_c_run"}
        out={"video_id":video_id,"run_id":run["id"],
             "state":run["state"],"reason_code":run["reason_code"],
             "snapshot_id":run["snapshot_id"],
             "previous_snapshot_id":run["previous_snapshot_id"],
             "calculation_config_id":run["calculation_config_id"],
             "breakout_score":None}
        if run["calculated_metric_id"] is not None and run["state"]=="completed":
            current=PostgreSQLScoreRetention().read_current(
                conn,project_id=project,
                calculated_metric_id=run["calculated_metric_id"],
            )
            out["state"]=current.state
            out["reason_code"]=current.reason_code
            out["breakout_score"]=current.breakout_score
        return out
