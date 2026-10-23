"""Owner-local HTTP boundary for qualified Analytics; no paid or provider calls."""
from __future__ import annotations
import json
import re
from urllib.parse import parse_qs
from .contracts import AnalyticsError, _json_value
from .queue import enqueue_batch, list_jobs, process_one
from .aggregate import aggregate_project
from .service import list_unified, run_qualification

GET = re.compile(r"/api/projects/([0-9a-f]{32})/analytics\Z")
AGGREGATE = re.compile(r"/api/projects/([0-9a-f]{32})/analytics/aggregate\Z")
POST = re.compile(r"/api/projects/([0-9a-f]{32})/analytics/qualify\Z")
BATCH = re.compile(r"/api/projects/([0-9a-f]{32})/analytics/batch\Z")
JOBS = re.compile(r"/api/projects/([0-9a-f]{32})/analytics/jobs\Z")
WORK = re.compile(r"/api/projects/([0-9a-f]{32})/analytics/jobs/work\Z")

def _error(handler, exc):
    if isinstance(exc, AnalyticsError):
        handler.json_response(exc.http_status, {"ok":False,"error":{"code":exc.code,"message":str(exc)}})
    else:
        # No DSN, credentials, raw third-party content or internal trace.
        handler.json_response(503, {"ok":False,"error":{"code":"analytics_unavailable",
                              "message":"Analytics database operation unavailable"}})

def handle_get(handler,url):
    found=GET.fullmatch(url.path)
    job_match=JOBS.fullmatch(url.path)
    aggregate_match=AGGREGATE.fullmatch(url.path)
    if not found and not job_match and not aggregate_match:
        return False
    qs=parse_qs(url.query)
    try:
        if aggregate_match:
            expected={"source_id","config_id","dimension","min_sample"}
            if set(qs)!=expected or any(len(v)!=1 for v in qs.values()):
                raise AnalyticsError("invalid_query","Expected one source_id, config_id, dimension and min_sample")
            if not qs["config_id"][0].isdecimal() or not qs["min_sample"][0].isdecimal():
                raise AnalyticsError("invalid_query","Config and sample must be positive integers")
            result=aggregate_project(aggregate_match.group(1),qs["source_id"][0],
                  int(qs["config_id"][0]),qs["dimension"][0],int(qs["min_sample"][0]))
            handler.json_response(200,{"ok":True,"summary":result})
            return True
        if any(k not in ("limit","include_partial") for k in qs):
            raise AnalyticsError("invalid_query","Unknown query parameter")
        raw_limit=qs.get("limit",["30"])[0]
        if not raw_limit.isdecimal():
            raise AnalyticsError("invalid_limit","Invalid limit")
        include=qs.get("include_partial",["0"])[0]
        if include not in ("0","1"):
            raise AnalyticsError("invalid_filter","Invalid include_partial")
        if job_match:
            if include!="0":
                raise AnalyticsError("invalid_filter","include_partial not valid for jobs")
            jobs=list_jobs(job_match.group(1),limit=int(raw_limit))
            handler.json_response(200,{"ok":True,"jobs":_json_value(jobs)})
        else:
            results=list_unified(found.group(1),limit=int(raw_limit),qualified_only=include=="0")
            handler.json_response(200,{"ok":True,"results":results})
    except Exception as exc:
        _error(handler,exc)
    return True

def handle_post(handler,route):
    found=POST.fullmatch(route)
    batch=BATCH.fullmatch(route)
    work=WORK.fullmatch(route)
    if not found and not batch and not work:
        return False
    try:
        raw_length=handler.headers.get("Content-Length","")
        if not raw_length.isdecimal() or not 2<=int(raw_length)<=4096:
            raise AnalyticsError("invalid_size","Request must be 2..4096 bytes",413)
        body=json.loads(handler.rfile.read(int(raw_length)).decode("utf-8"))
        if type(body) is not dict:
            raise AnalyticsError("invalid_request","Expected JSON object")
        if found:
            if set(body)!={"video_id","source_id","request_key"}:
                raise AnalyticsError("invalid_request","Only video_id, source_id, request_key are allowed")
            result=run_qualification(found.group(1),body["video_id"],body["source_id"],body["request_key"])
            handler.json_response(200,{"ok":True,"snapshot":result})
        elif batch:
            if set(body)!={"video_ids","source_id","batch_key"}:
                raise AnalyticsError("invalid_request","Only video_ids, source_id, batch_key are allowed")
            result=enqueue_batch(batch.group(1),body["source_id"],body["video_ids"],body["batch_key"])
            handler.json_response(202,{"ok":True,"jobs":_json_value(result)})
        else:
            if set(body)!={"max_jobs"} or type(body["max_jobs"]) is not int or not 1<=body["max_jobs"]<=5:
                raise AnalyticsError("invalid_work_limit","max_jobs must be an integer from 1 to 5")
            jobs=[]
            for _ in range(body["max_jobs"]):
                job=process_one(project_id=work.group(1))
                if job is None:
                    break
                jobs.append(_json_value(job))
            handler.json_response(200,{"ok":True,"jobs":jobs,"processed":len(jobs)})
    except (UnicodeError,ValueError) as exc:
        _error(handler,exc if isinstance(exc,AnalyticsError) else
               AnalyticsError("invalid_json","Invalid JSON"))
    except Exception as exc:
        _error(handler,exc)
    return True
