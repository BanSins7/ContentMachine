"""Local same-origin Collector Phase II HTTP adapter. NO external requests.

App's existing Handler.authorized(mutation=True) must run before POST routing;
this module does not replace Host/Origin/X-ContentMachine protection.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from .contracts import CollectorError, PROJECT_RE
from .planner import build_preview
from .registry import list_sources
from .repository import trusted_preview, commit_run, get_run, cancel_run, _validated_commit_request

_PREFIX="/api/intelligence/collector/"
_MANIFEST_PATH=re.compile(r"/api/intelligence/collector/manifests/([0-9a-fA-F-]{36})")
_RUN_PATH=re.compile(r"/api/intelligence/collector/runs/([1-9][0-9]{0,17})(/(?:cancel|release))?\Z")
_MAX_BODY=256*1024


def _error(handler, code, detail, status=400):
    handler.json_response(status,{"ok":False,"error":{"code":code,"message":detail[:250]}})


def _boundary(handler, operation):
    try:
        return operation()
    except CollectorError as exc:
        _error(handler,exc.code,str(exc),exc.http_status)
    except PipelineError as exc:
        # Do not leak DB, credentials or partial input in errors.
        _error(handler,"collector_dependency_error",str(exc),exc.http_status)
    except Exception:
        _error(handler,"db_unavailable",
               "Collector backend unavailable; check PostgreSQL/migrations.",503)


def _project_id(params):
    raw=params.get("project_id",[])
    if len(raw)!=1 or PROJECT_RE.fullmatch(raw[0]) is None:
        raise CollectorError("project_scope_required","Valid project_id query parameter required")
    return raw[0]


def handle_get(handler,url) -> bool:
    path=url.path
    if not path.startswith(_PREFIX):
        return False
    if path==_PREFIX+"sources":
        handler.json_response(200,{"ok":True,"sources":list_sources(),
                                    "live_provider_calls":False})
        return True
    if path==_PREFIX+"sources/dtk_tiktok/diagnostics":
        # Explicit user-triggered local auth/me only, never a search or DB mutation.
        from .adapters.dtk_tiktok import diagnose_local_connection
        result=_boundary(handler,diagnose_local_connection)
        if result is not None:
            handler.json_response(200,{"ok":True,**result})
        return True
    if path==_PREFIX+"manifests":
        params=parse_qs(url.query)
        def action():
            from .manifest_access import list_published_manifests
            return list_published_manifests(_project_id(params))
        result=_boundary(handler,action)
        if result is not None:
            handler.json_response(200,{"ok":True,**result})
        return True
    manifest_match=_MANIFEST_PATH.fullmatch(path)
    if manifest_match:
        params=parse_qs(url.query)
        def action():
            from .manifest_access import get_published_manifest
            return get_published_manifest(_project_id(params),manifest_match.group(1))
        result=_boundary(handler,action)
        if result is not None:
            handler.json_response(200,{"ok":True,**result})
        return True
    match=_RUN_PATH.fullmatch(path)
    if match and not match.group(2):
        params=parse_qs(url.query)
        def action():
            pid=_project_id(params)
            return get_run(pid,int(match.group(1)))
        result=_boundary(handler,action)
        if result is not None:
            handler.json_response(200,{"ok":True,**result})
        return True
    _error(handler,"not_found","Unknown collector route",404)
    return True


def _read_request(handler):
    value=handler.headers.get("Content-Length","")
    try:
        size=int(value)
    except (ValueError,TypeError):
        raise CollectorError("invalid_payload_size","Content-Length required",413) from None
    if not 2<=size<=_MAX_BODY:
        raise CollectorError("invalid_payload_size","Maximum JSON payload is 256 KiB",413)
    try:
        raw=json.loads(handler.rfile.read(size).decode("utf-8"))
    except (UnicodeError,ValueError,json.JSONDecodeError):
        raise CollectorError("invalid_json","Invalid JSON body",400) from None
    if type(raw) is not dict:
        raise CollectorError("invalid_json","JSON object required")
    return raw


def handle_post(handler,path) -> bool:
    if not path.startswith(_PREFIX):
        return False
    match=_RUN_PATH.fullmatch(path)
    def action():
        body=_read_request(handler)
        if path==_PREFIX+"preview":
            if set(body)=={"query_set","source_id","limits"}:
                return 200, build_preview(body["query_set"],
                                          source_id=body["source_id"],
                                          limits=body["limits"])
            if set(body)=={"query_set_id","source_id","limits"}:
                return 200, trusted_preview(body["query_set_id"],body["source_id"],
                                            limits=body["limits"])
            raise CollectorError("invalid_preview",
                                 "Use query_set or query_set_id with source_id and limits")
        if path==_PREFIX+"manifest-preview":
            from .manifest_access import preview_published_shard
            return 200, preview_published_shard(body)
        if path==_PREFIX+"manifest-runs":
            from .manifest_access import commit_published_shard
            return 201, commit_published_shard(body)
        if path==_PREFIX+"runs":
            _validated_commit_request(body)
            return 201, commit_run(body)
        if match and match.group(2)=="/release":
            from .execution import authorize_execution
            if body.get("run_id") != int(match.group(1)):
                raise CollectorError("run_scope_mismatch","Route and body run IDs differ",409)
            return 200, authorize_execution(body)
        if match and match.group(2)=="/cancel":
            if set(body)!={"project_id"}:
                raise CollectorError("project_scope_required","Only project_id is accepted")
            pid=body["project_id"]
            if type(pid) is not str or PROJECT_RE.fullmatch(pid) is None:
                raise CollectorError("project_scope_required","Invalid project_id")
            return 200, cancel_run(pid,int(match.group(1)))
        raise CollectorError("not_found","Unknown collector route",404)
    result=_boundary(handler,action)
    if result is not None:
        status,payload=result
        handler.json_response(status,{"ok":True,**payload})
    return True
