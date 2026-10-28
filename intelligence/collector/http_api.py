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
_RUN_READ_PATH=re.compile(r"/api/intelligence/collector/runs/([1-9][0-9]{0,17})/(results|trace)\Z")
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


def _bounded_param(params, key, *, default=20):
    raw=params.get(key)
    if raw is None:
        return default
    if len(raw)!=1 or not raw[0].isdigit() or raw[0].startswith("0"):
        raise CollectorError("invalid_query_parameter",
                             "A positive numeric query parameter is required",400)
    value=int(raw[0])
    if value<1 or value>50:
        raise CollectorError("invalid_query_parameter",
                             "Page limit must be 1..50",400)
    return value


def _single_param(params,key):
    raw=params.get(key)
    if raw is None:
        return None
    if len(raw)!=1 or not raw[0] or len(raw[0])>80:
        raise CollectorError("invalid_query_parameter",
                             "Malformed or repeated query parameter",400)
    return raw[0]


def _validate_query_keys(params,allowed):
    if set(params)-set(allowed):
        raise CollectorError("invalid_query_parameter",
                             "Unknown read-only query parameter",400)


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
    if path==_PREFIX+"sp-query-sets":
        params=parse_qs(url.query,keep_blank_values=True)
        def action():
            _validate_query_keys(params,{"project_id","limit"})
            from .sp_handoff import list_sp_query_sets
            return list_sp_query_sets(_project_id(params),limit=_bounded_param(params,"limit"))
        result=_boundary(handler,action)
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
    if path==_PREFIX+"runs":
        params=parse_qs(url.query,keep_blank_values=True)
        def action():
            from .results_api import list_runs
            _validate_query_keys(params,{"project_id","limit","before_run_id"})
            before=_single_param(params,"before_run_id")
            if before is not None and (
                not before.isdigit() or before.startswith("0")
            ):
                raise CollectorError("invalid_cursor","Invalid run cursor",400)
            return list_runs(
                _project_id(params),limit=_bounded_param(params,"limit"),
                before_run_id=int(before) if before else None)
        result=_boundary(handler,action)
        if result is not None:
            handler.json_response(200,{"ok":True,**result})
        return True
    read_match=_RUN_READ_PATH.fullmatch(path)
    if read_match:
        params=parse_qs(url.query,keep_blank_values=True)
        def action():
            pid=_project_id(params)
            runid=int(read_match.group(1))
            if read_match.group(2)=="results":
                from .results_api import list_run_results
                _validate_query_keys(params,{"project_id","limit","after"})
                return list_run_results(
                    pid,runid,limit=_bounded_param(params,"limit"),
                    after=_single_param(params,"after"))
            from .results_api import get_run_trace
            _validate_query_keys(params,{"project_id"})
            return get_run_trace(pid,runid)
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
