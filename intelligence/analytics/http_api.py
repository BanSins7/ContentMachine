"""Owner-local HTTP boundary for qualified Analytics; no paid or provider calls."""
from __future__ import annotations
import json
import re
from urllib.parse import parse_qs
from .contracts import AnalyticsError
from .service import list_unified, run_qualification

GET = re.compile(r"/api/projects/([0-9a-f]{32})/analytics\Z")
POST = re.compile(r"/api/projects/([0-9a-f]{32})/analytics/qualify\Z")

def _error(handler, exc):
    if isinstance(exc, AnalyticsError):
        handler.json_response(exc.http_status, {"ok":False,"error":{"code":exc.code,"message":str(exc)}})
    else:
        # No DSN, credentials, raw third-party content or internal trace.
        handler.json_response(503, {"ok":False,"error":{"code":"analytics_unavailable",
                              "message":"Analytics database operation unavailable"}})

def handle_get(handler,url):
    found=GET.fullmatch(url.path)
    if not found:
        return False
    qs=parse_qs(url.query)
    try:
        if any(k not in ("limit","include_partial") for k in qs):
            raise AnalyticsError("invalid_query","Unknown query parameter")
        raw_limit=qs.get("limit",["30"])[0]
        if not raw_limit.isdecimal():
            raise AnalyticsError("invalid_limit","Invalid limit")
        include=qs.get("include_partial",["0"])[0]
        if include not in ("0","1"):
            raise AnalyticsError("invalid_filter","Invalid include_partial")
        results=list_unified(found.group(1),limit=int(raw_limit),qualified_only=include=="0")
        handler.json_response(200,{"ok":True,"results":results})
    except Exception as exc:
        _error(handler,exc)
    return True

def handle_post(handler,route):
    found=POST.fullmatch(route)
    if not found:
        return False
    try:
        raw_length=handler.headers.get("Content-Length","")
        if not raw_length.isdecimal() or not 2<=int(raw_length)<=4096:
            raise AnalyticsError("invalid_size","Request must be 2..4096 bytes",413)
        body=json.loads(handler.rfile.read(int(raw_length)).decode("utf-8"))
        if type(body) is not dict or set(body)!={"video_id","source_id","request_key"}:
            raise AnalyticsError("invalid_request","Only video_id, source_id, request_key are allowed")
        result=run_qualification(found.group(1),body["video_id"],body["source_id"],body["request_key"])
        handler.json_response(200,{"ok":True,"snapshot":result})
    except (UnicodeError,ValueError) as exc:
        _error(handler,exc if isinstance(exc,AnalyticsError) else
               AnalyticsError("invalid_json","Invalid JSON"))
    except Exception as exc:
        _error(handler,exc)
    return True
