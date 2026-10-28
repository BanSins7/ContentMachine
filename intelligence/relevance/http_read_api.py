"""M38 localhost-only WSGI READ-ONLY facade for the standalone SRE module.

Nothing listens, connects, bills or imports Collector/QDP on import.
The authenticated actor is provided by a separately trusted HOST middleware,
not by a browser-controlled actor/project header. Caller MUST authenticate
the request before passing the resulting identity to actor_resolver.
Explicitly instantiate(enabled=True) and bind 127.0.0.1 only.
M39 separately owns mutation APIs, CSRF, consent and POST side effects.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from .opus_consent import _actor, _authorized
from .opus_dispatch_intent import _committable
from .v1_contracts import SREContractError,canonical_uuid,positive,project_id,reject
from . import read_api

VERSION="sre-localhost-read-api/v1"
ROOT="/api/sre/v1"
PROJECT_RE=re.compile(r"^/projects/([0-9a-f]{32})(?:/(.*))?$")
LOOPBACK=frozenset(("127.0.0.1","::1"))
ALLOWED_HOSTS=re.compile(
    r"(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?\Z",re.I,
)
RUN_RESOURCE=re.compile(r"^runs/([0-9a-f-]{36})(?:/(results|history|review-queue|quotes))?$")
TAX_RESOURCE=re.compile(r"^taxonomy/([0-9a-f-]{36})$")
CONTENT_RESOURCE=re.compile(r"^content/([1-9][0-9]{0,18})/preview$")
RANK_RESOURCE=re.compile(r"^runs/([0-9a-f-]{36})/rankings/([1-9][0-9]{0,18})$")

ROUTES=(
    "health","taxonomy/{publication_id}",
    "content/{content_version_id}/preview","runs",
    "runs/{run_id}","runs/{run_id}/results","runs/{run_id}/history",
    "runs/{run_id}/review-queue","runs/{run_id}/quotes",
    "runs/{run_id}/rankings/{item_id}",
)

def _response(start_response, status, payload, *, head=False, allow=None):
    body=json.dumps(payload,sort_keys=True,separators=(",",":"),
                    ensure_ascii=False,default=str).encode("utf-8")
    headers=[
        ("Content-Type","application/json; charset=utf-8"),
        ("Content-Length",str(len(body))),
        ("Cache-Control","no-store, max-age=0"),
        ("Pragma","no-cache"),
        ("X-Content-Type-Options","nosniff"),
        ("Referrer-Policy","no-referrer"),
        ("Content-Security-Policy","default-src 'none'; frame-ancestors 'none'"),
    ]
    if allow is not None:headers.append(("Allow",allow))
    start_response(status,headers)
    return [b"" if head else body]


def _query(raw):
    if type(raw) is not str or len(raw)>300 or raw.count("&")>2:
        reject("query","bounded limit/after query required")
    try:
        pairs=parse_qs(raw,keep_blank_values=True,strict_parsing=True)
    except (ValueError,UnicodeError):
        reject("query","malformed query")
    if set(pairs)-{"limit","after"} or any(len(v)!=1 for v in pairs.values()):
        reject("query","only one limit and after supported")
    limit=pairs.get("limit",["50"])[0]
    if (not re.fullmatch(r"[1-9][0-9]{0,2}",limit)
        or int(limit)>read_api.MAX_PAGE):
        reject("limit","expected 1..100")
    after=pairs.get("after",[None])[0]
    if after is not None and (not after or len(after)>128):
        reject("after","invalid cursor")
    return int(limit),after


def _safe_request(env):
    if env.get("REMOTE_ADDR") not in LOOPBACK:
        reject("remote","local loopback required","actor_not_authorized")
    host=env.get("HTTP_HOST")
    if host is not None and (type(host) is not str
                             or ALLOWED_HOSTS.fullmatch(host) is None):
        reject("host","non-loopback host denied","actor_not_authorized")
    # Reverse-proxy forwarding must not turn a remotely reachable listener
    # into a local-only API via spoofed address/host headers.
    if any(env.get(key) for key in (
        "HTTP_FORWARDED","HTTP_X_FORWARDED_FOR","HTTP_X_REAL_IP",
        "HTTP_X_FORWARDED_HOST","HTTP_X_FORWARDED_PROTO",
    )):
        reject("forwarded","proxy forwarding is unsupported","actor_not_authorized")
    path=env.get("PATH_INFO","")
    if (type(path) is not str or len(path)>360 or
        "%" in path or "\\" in path or "//" in path or
        "\x00" in path):
        reject("path","noncanonical path")
    return path


class LocalSREReadAPI:
    """Callable WSGI app. Explicit authenticated project access ONLY.

    actor_resolver(environ): host-owned trusted authentication, returns actor
    ONLY after session/OS identity verification. Do not accept arbitrary
    X-Actor, X-Project or browser-supplied claim as principal.
    actor_authorizer(pid,actor,action): independent trusted project permission.
    connect(): returns an idle autocommit=True PostgreSQL connection.
    """

    def __init__(self, *, connect, actor_resolver, actor_authorizer,
                 enabled=False, model_resolver=None, rank_rate_limiter=None):
        if (not callable(connect) or not callable(actor_resolver)
                or not callable(actor_authorizer) or type(enabled) is not bool
                or (model_resolver is not None and not callable(model_resolver))
                or (rank_rate_limiter is not None and not callable(rank_rate_limiter))):
            raise ValueError("trusted host DB, identity, authorization and opt-in required")
        self._connect=connect
        self._identify=actor_resolver
        self._authorize=actor_authorizer
        self._models=model_resolver
        self._rank_limiter=rank_rate_limiter
        self.enabled=enabled

    def __call__(self,environ,start_response):
        is_head=environ.get("REQUEST_METHOD")=="HEAD"
        try:
            if not self.enabled:
                return _response(start_response,"503 Service Unavailable",
                                 {"error":"sre_api_disabled"},head=is_head)
            path=_safe_request(environ)
            if environ.get("REQUEST_METHOD") not in ("GET","HEAD"):
                return _response(start_response,"405 Method Not Allowed",
                                 {"error":"read_only_api"},head=False,allow="GET, HEAD")
            try:
                actor=_actor(self._identify(environ))
            except Exception:
                reject("actor","trusted host identity required","actor_not_authorized")
            if path==ROOT+"/manifest":
                if environ.get("QUERY_STRING"):
                    reject("query","manifest has no query parameters")
                _authorized(self._authorize,None,actor,"read_sre_api_manifest")
                return _response(start_response,"200 OK",{
                    "schema_version":VERSION,"prefix":ROOT,
                    "routes":[ROOT+"/manifest"] + [
                        ROOT+"/projects/{project_id}/"+x for x in ROUTES],
                    "read_only":True,"localhost_only":True,
                    "paid_dispatch_allowed":False,
                    "external_provider_calls":0,
                    "runtime_enabled_is_not_live_qa":True,
                },head=is_head)
            if not path.startswith(ROOT):
                return _response(start_response,"404 Not Found",
                                 {"error":"not_found"},head=is_head)
            match=PROJECT_RE.fullmatch(path[len(ROOT):])
            if match is None:
                return _response(start_response,"404 Not Found",
                                 {"error":"not_found"},head=is_head)
            pid=project_id(match[1])
            resource=match[2] or ""
            endpoint,ident,sub=self._resolve(resource)
            if endpoint is None:
                return _response(start_response,"404 Not Found",
                                 {"error":"not_found"},head=is_head)
            limit,after=_query(environ.get("QUERY_STRING",""))
            if endpoint in ("health","run","content","local_rank") and (after is not None or limit!=50):
                reject("query","endpoint does not support pagination")
            _authorized(self._authorize,pid,actor,"read_sre_api_"+endpoint)
            ranking_configuration=None
            if endpoint=="local_rank":
                if not callable(self._models) or not callable(self._rank_limiter):
                    reject("local_rank","trusted operator model/rate ports unavailable",
                           "rights_denied")
                try:
                    permitted=self._rank_limiter(pid,actor,"local_rank")
                except Exception:
                    permitted=False
                if permitted is not True:
                    reject("local_rank","local ranking quota exceeded",
                           "rank_rate_limited")
                try:
                    ranking_configuration=self._models(pid,actor)
                except Exception:
                    reject("local_rank","trusted ONNX configuration unavailable",
                           "local_model_blocked")
                if (type(ranking_configuration) is not dict or
                    set(ranking_configuration)!=set(("model_directory","model_pins"))):
                    reject("local_rank","invalid operator ONNX configuration",
                           "local_model_blocked")
            with self._connect() as conn:
                _committable(conn)
                with conn.transaction():
                    # Read-only even if the host/driver grants a write capable
                    # SQL user. Any attempt to write fails at PostgreSQL.
                    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    read_api.workspace_exists(conn,pid)
                    response=self._dispatch(
                        conn,pid,endpoint,ident,sub,limit,after,
                        rank_config=ranking_configuration,
                    )
                if endpoint=="local_rank":
                    # Long CPU inference may have raced a source revocation.
                    # This SECOND read-only transaction obtains a FRESH DB
                    # snapshot before releasing any ranking to the caller.
                    with conn.transaction():
                        conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY")
                        read_api.workspace_exists(conn,pid)
                        read_api.revalidate_authorized_local_rank(
                            conn,pid,ident,sub,response)
            return _response(start_response,"200 OK",response,head=is_head)
        except SREContractError as exc:
            denied=exc.code in (
                "actor_not_authorized","rights_denied","rights_expired",
                "missing_provenance","snapshot_mismatch",
            )
            # No raw error paths/details, customer data, API keys or SQL in HTTP.
            rate_limited=exc.code=="rank_rate_limited"
            return _response(start_response,
                    "429 Too Many Requests" if rate_limited else
                    ("403 Forbidden" if denied else "400 Bad Request"),
                    {"error":"rate_limited" if rate_limited else
                     ("access_denied" if denied else "invalid_request")},
                    head=is_head)
        except Exception:
            # Deliberately do not stringify DB/driver/secret exceptions.
            return _response(start_response,"503 Service Unavailable",
                             {"error":"temporarily_unavailable"},head=is_head)

    @staticmethod
    def _resolve(resource):
        if resource=="health":return "health",None,None
        m=RANK_RESOURCE.fullmatch(resource)
        if m:
            try:
                canonical_uuid(m[1],"run_id")
                item=positive(int(m[2]),"item_id",maximum=9223372036854775807)
            except SREContractError:
                return None,None,None
            return "local_rank",m[1],item
        if resource=="runs":return "runs",None,None
        m=RUN_RESOURCE.fullmatch(resource)
        if m:
            try:canonical_uuid(m[1],"run_id")
            except SREContractError:return None,None,None
            return (m[2] or "run").replace("-","_"),m[1],None
        m=TAX_RESOURCE.fullmatch(resource)
        if m:
            try:canonical_uuid(m[1],"publication_id")
            except SREContractError:return None,None,None
            return "taxonomy",m[1],None
        m=CONTENT_RESOURCE.fullmatch(resource)
        if m:return "content",int(m[1]),None
        return None,None,None

    @staticmethod
    def _dispatch(conn,pid,endpoint,ident,sub,limit,after,*,rank_config=None):
        if endpoint=="health":return read_api.read_schema_health(conn,pid)
        if endpoint=="taxonomy":return read_api.read_taxonomy_preview(conn,pid,ident,limit,after)
        if endpoint=="content":return read_api.read_content_preview(conn,pid,ident)
        if endpoint=="runs":return read_api.list_live_runs(conn,pid,limit,after)
        if endpoint=="run":return read_api.read_run_status(conn,pid,ident)
        if endpoint=="results":return read_api.read_verified_results(conn,pid,ident,limit,after)
        if endpoint=="history":return read_api.read_review_history(conn,pid,ident,limit,after)
        if endpoint=="review_queue":return read_api.read_review_queue(conn,pid,ident,limit,after)
        if endpoint=="quotes":return read_api.read_quote_preview(conn,pid,ident,limit,after)
        if endpoint=="local_rank":
            if type(rank_config) is not dict:
                reject("local_rank","operator rank configuration missing","local_model_blocked")
            return read_api.read_authorized_local_rank(
                conn,pid,ident,sub,model_directory=rank_config["model_directory"],
                model_pins=rank_config["model_pins"])
        reject("endpoint","unsupported read route")


def create_loopback_server(app,port):
    """Return stopped WSGI server; caller explicitly invokes serve_forever().

    OS sockets never opened at module import; cannot bind non-loopback.
    """
    if not isinstance(app,LocalSREReadAPI) or not app.enabled:
        raise ValueError("explicit enabled SRE API required")
    if type(port) is not int or not 0<=port<=65535:
        raise ValueError("invalid local port")
    return make_server("127.0.0.1",port,app)


__all__=["VERSION","ROOT","LocalSREReadAPI","create_loopback_server"]
