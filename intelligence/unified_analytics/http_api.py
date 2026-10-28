"""UA-MOD-26 | Strict local HTTP adapter; only app.Handler owns networking.

All UAE routes use app's existing Host/Origin and mutation X-ContentMachine
check, PLUS an independently required per-project high-entropy token.
There is no public graphQL/PIE, raw payload, approval override or score API.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs

from .http_service import HTTP_SCHEMA, UAEHTTPProblem, local_http_service

ROUTE=re.compile(r"^/api/projects/([0-9a-f]{32})/unified-analytics(?:/(.*))?$")
_UUID=r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_INTEGER=re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")
MAX_BODY=40000


def _err(h,code:str,http_status:int)->None:
    h.json_response(http_status,{"ok":False,"code":code,"error":"UAE request rejected"})


def _query(url,allowed:tuple[str,...])->dict:
    try:
        q=parse_qs(url.query,keep_blank_values=True,strict_parsing=True,
                   max_num_fields=8)
    except (ValueError,OverflowError):
        raise UAEHTTPProblem("invalid_query",400)
    if (set(q)-set(allowed) or any(len(values)!=1 for values in q.values())):
        raise UAEHTTPProblem("invalid_query",400)
    return {key:value[0] for key,value in q.items()}


def _positive(raw:str)->int:
    if type(raw) is not str or _INTEGER.fullmatch(raw) is None:
        raise UAEHTTPProblem("invalid_query",400)
    val=int(raw)
    if not 0<val<2**63:
        raise UAEHTTPProblem("invalid_query",400)
    return val


def _access(h,mutation:bool)->None:
    # Recheck, even if routed outside app.Handler from an embedded process.
    if not callable(getattr(h,"authorized",None)) or not h.authorized(
        mutation=mutation
    ):
        raise UAEHTTPProblem("same_origin_forbidden",403)
    if h.headers.get("X-UAE-Api-Version","")!=HTTP_SCHEMA:
        raise UAEHTTPProblem("unsupported_api_version",409)


def _token(h)->str:
    return h.headers.get("X-UAE-Project-Token","")


def _json_body(h)->dict:
    header=h.headers.get("Content-Type","")
    if header.lower() not in ("application/json","application/json; charset=utf-8"):
        raise UAEHTTPProblem("invalid_content_type",415)
    try:n=int(h.headers.get("Content-Length",""))
    except (TypeError,ValueError):raise UAEHTTPProblem("invalid_body_size",400)
    if not 2<=n<=MAX_BODY:
        raise UAEHTTPProblem("invalid_body_size",413)
    raw=h.rfile.read(n)
    if len(raw)!=n:
        raise UAEHTTPProblem("invalid_body_size",400)
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out:
                raise UAEHTTPProblem("duplicate_json_key",400)
            out[k]=v
        return out
    def bad_constant(value):
        raise UAEHTTPProblem("nonfinite_json",400)
    try:
        body=json.loads(raw.decode("utf-8"),object_pairs_hook=unique,
                        parse_constant=bad_constant)
    except (UnicodeError,TypeError,ValueError,RecursionError):
        raise UAEHTTPProblem("invalid_json",400)
    if type(body) is not dict:
        raise UAEHTTPProblem("invalid_body",400)
    return body


def handle_get(h,url)->bool:
    match=ROUTE.fullmatch(url.path)
    if not match:return False
    pid,action=match.groups()
    try:
        _access(h,False)
        token=_token(h)
        if action=="preview":
            args=_query(url,("request_id",))
            result=local_http_service().get(pid,token,"preview",args)
        elif action=="snapshots":
            args=_query(url,("before","limit"))
            for item in args:
                args[item]=_positive(args[item])
            result=local_http_service().get(pid,token,"snapshots",args)
        elif action=="aggregates":
            args=_query(url,())
            result=local_http_service().get(pid,token,"aggregates",args)
        elif action and re.fullmatch("jobs/"+_UUID,action):
            if url.query: raise UAEHTTPProblem("invalid_query",400)
            result=local_http_service().get(
                pid,token,"jobs",{"job_id":action.split("/")[1]}
            )
        elif action and re.fullmatch(r"snapshots/[0-9]{1,19}",action):
            if url.query: raise UAEHTTPProblem("invalid_query",400)
            result=local_http_service().get(
                pid,token,"snapshot",
                {"snapshot_id":_positive(action.split("/")[1])},
            )
        else:
            raise UAEHTTPProblem("not_found",404)
        h.json_response(result.status,result.payload)
    except UAEHTTPProblem as exc:
        _err(h,exc.code,exc.http_status)
    except Exception:
        _err(h,"dependency_unavailable",503)
    return True


def handle_post(h,route:str)->bool:
    match=ROUTE.fullmatch(route)
    if not match:return False
    pid,action=match.groups()
    try:
        _access(h,True)
        token=_token(h)
        if action in ("qualify","qualify-batch"):
            body=_json_body(h)
            result=local_http_service().post(pid,token,action,body)
        elif action and re.fullmatch("jobs/"+_UUID+"/cancel",action):
            body=_json_body(h)
            if body:
                raise UAEHTTPProblem("invalid_body",400)
            result=local_http_service().post(pid,token,"cancel",{
                "job_id":action.split("/")[1]
            })
        else:
            raise UAEHTTPProblem("not_found",404)
        h.json_response(result.status,result.payload)
    except UAEHTTPProblem as exc:
        _err(h,exc.code,exc.http_status)
    except Exception:
        _err(h,"dependency_unavailable",503)
    return True


__all__=("ROUTE","MAX_BODY","handle_get","handle_post")
