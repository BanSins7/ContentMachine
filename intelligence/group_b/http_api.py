"""GB-MOD-26 same-origin localhost Group B HTTP adapter, no provider I/O.

All high-trust server actions stay behind an injected operator host. Browser
JSON, localStorage, API headers and the same-origin marker are NEVER
sufficient paid approval or authentic reviewer/QDP/source receipts.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import re
from urllib.parse import parse_qs

from .studio_service import GroupBStudioService,StudioProblem,_identity
from .errors import GroupBError
from .review.contracts import ReviewError

PREFIX=re.compile(
    r"^/api/projects/([0-9a-f]{32})/group-b"
    r"(?:/(.*))?$"
)
RUN=re.compile(r"^runs/([0-9a-f-]{36})(?:/(.*))?$")
_ACTIONS={
    "quote":"quote","approve":"approve",
    "start":"start","cancel":"cancel","review":"review",
}
API_ENABLED=False
# Set a verified service only in the same-process trusted server bootstrap.
# No HTTP route, request field or browser preference can install a host.
_trusted_service=None


def configure_operator_service(service):
    global _trusted_service
    if service is not None and not isinstance(service,GroupBStudioService):
        raise TypeError("trusted GroupBStudioService required")
    _trusted_service=service


def _local_owner(project_id,action,conn):
    """Only for local read. Not authorization for any mutation."""
    if action not in ("group_b.read",):
        return False
    from projects import get_project
    return get_project(project_id) is not None


def _default_service():
    from intelligence import config
    if not config.enabled() or not config.dsn():
        raise StudioProblem("group_b_database_disabled",503)
    import psycopg
    from psycopg.rows import dict_row
    return GroupBStudioService(
        lambda:psycopg.connect(config.dsn(),connect_timeout=3,
                               row_factory=dict_row),
        operator=_local_owner,
        host=None,
    )


def _service():
    return _trusted_service if _trusted_service is not None else _default_service()


def _fail(h,e):
    if isinstance(e,StudioProblem):
        return h.json_response(e.http_status,{"ok":False,"code":e.code})
    if isinstance(e,ReviewError):
        return h.json_response(409,{"ok":False,"code":"review_rejected"})
    if isinstance(e,GroupBError):
        return h.json_response(409,{"ok":False,"code":"group_b_invariant_denied"})
    return h.json_response(503,{"ok":False,"code":"group_b_unavailable"})


def _query(url):
    q=parse_qs(url.query,keep_blank_values=True)
    if set(q)-{"limit","offset","block"} or any(len(x)!=1 for x in q.values()):
        raise StudioProblem("query_invalid",400)
    if any(not re.fullmatch(r"[0-9]{1,6}",q.get(key,[""])[0])
           for key in ("limit","offset") if key in q):
        raise StudioProblem("pagination_invalid",400)
    offset=int(q["offset"][0]) if "offset" in q else 0
    limit=int(q["limit"][0]) if "limit" in q else 20
    block=q.get("block",[None])[0]
    if not 0<=offset<=100000 or not 1<=limit<=50:
        raise StudioProblem("pagination_invalid",400)
    if block is not None and block not in {
        "B1","B2","B3","B4","B5","B6","B7","B8"}:
        raise StudioProblem("block_invalid",400)
    return offset,limit,block


def _body(h):
    if h.headers.get("Content-Type","").split(";",1)[0].strip().lower()!= "application/json":
        raise StudioProblem("application_json_required",415)
    raw=h.headers.get("Content-Length","")
    if not re.fullmatch(r"[1-9][0-9]{0,4}",raw):
        raise StudioProblem("content_length_invalid",400)
    size=int(raw)
    if size>48_000:
        raise StudioProblem("json_body_too_large",413)
    try:
        data=json.loads(h.rfile.read(size).decode("utf-8"))
    except (UnicodeError,ValueError):
        raise StudioProblem("invalid_json",400) from None
    if type(data) is not dict:
        raise StudioProblem("json_object_required",400)
    return data


def handle_get(h,url):
    match=PREFIX.fullmatch(url.path)
    if not match:
        return False
    project_id,route=match.groups()
    try:
        if not h.authorized():
            raise StudioProblem("localhost_same_origin_required",403)
        offset,limit,block=_query(url)
        service=_service()
        if route in ("runs",None):
            if block is not None:
                raise StudioProblem("block_not_supported_for_run_list",400)
            result=service.list_runs(project_id,offset=offset,limit=limit)
        else:
            run=RUN.fullmatch(route)
            if run is None:
                raise StudioProblem("group_b_route_missing",404)
            run_id,child=run.groups()
            _identity(project_id,run_id)
            if child in (None,""):
                if block is not None:
                    raise StudioProblem("block_not_supported_for_detail",400)
                result=service.detail(project_id,run_id,offset=offset,limit=limit)
            elif child=="evidence":
                if block is None:
                    raise StudioProblem("evidence_block_required",400)
                result=service.evidence(project_id,run_id,block=block,
                                        offset=offset,limit=limit)
            else:
                raise StudioProblem("group_b_route_missing",404)
        h.json_response(200,{"ok":True,**result})
    except Exception as e:
        _fail(h,e)
    return True


def handle_post(h,route):
    match=PREFIX.fullmatch(route)
    if not match:
        return False
    project_id,action=match.groups()
    try:
        if not h.authorized(mutation=True):
            raise StudioProblem("localhost_same_origin_required",403)
        data=_body(h)
        service=_service()
        if action=="runs":
            result=service.create(project_id,data)
            status=201
        else:
            m=RUN.fullmatch(action or "")
            if m is None or m.group(2) not in _ACTIONS:
                raise StudioProblem("group_b_route_missing",404)
            run_id,kind=m.groups()
            _identity(project_id,run_id)
            result=getattr(service,_ACTIONS[kind])(project_id,run_id,data)
            status=201 if kind in ("quote","approve","review") else 200
        h.json_response(status,{"ok":True,**result})
    except Exception as e:
        _fail(h,e)
    return True
