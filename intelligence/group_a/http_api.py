"""Project-scoped Group A local HTTP handlers. Inherits app.py host/origin guard."""
from __future__ import annotations
import json
import re
from urllib.parse import parse_qs
from . import repository
from .contracts import GroupAError

PROJECT=r"[0-9a-f]{32}"
UUID=r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
BASE=re.compile(rf"/api/projects/({PROJECT})/group-a(?:/(.*))?\Z")


def _error(handler,exc):
    handler.json_response(exc.http_status,{"ok":False,"error":{
        "code":exc.code,"message":str(exc)}})


def handle_get(handler,url):
    match=BASE.fullmatch(url.path)
    if not match:
        return False
    project,suffix=match.groups()
    try:
        if suffix=="tasks":
            params=parse_qs(url.query)
            if set(params)!={"qdp_run_id"} or len(params["qdp_run_id"])!=1:
                raise GroupAError("invalid_query","Exact QDP run ID required")
            result=repository.list_tasks(project,params["qdp_run_id"][0])
        elif suffix and re.fullmatch(r"tasks/"+UUID,suffix):
            result=repository.task_details(project,suffix.split("/")[1])
        else:
            raise GroupAError("not_found","Group A route not found",404)
        handler.json_response(200,result)
    except GroupAError as exc:
        _error(handler,exc)
    except Exception:
        handler.json_response(503,{"ok":False,"error":{
            "code":"db_unavailable","message":"Group A unavailable"}})
    return True


def handle_post(handler,path):
    match=BASE.fullmatch(path)
    if not match:
        return False
    project,suffix=match.groups()
    if suffix!="tasks":
        handler.json_response(404,{"ok":False,"error":{
            "code":"not_found","message":"Group A route not found"}})
        return True
    try:
        size=int(handler.headers.get("Content-Length","0"))
        if not 2<=size<=4096:
            raise GroupAError("invalid_body","Group A accepts at most 4096 bytes",413)
        payload=json.loads(handler.rfile.read(size).decode("utf-8"))
        if type(payload) is not dict or set(payload)!={"qdp_run_id","video_id","config"}:
            raise GroupAError("invalid_body","Expected qdp_run_id,video_id,config")
        result=repository.create_task(project,payload["qdp_run_id"],
                                      payload["video_id"],payload["config"])
        from .worker import start_worker
        start_worker()
        handler.json_response(202,result)
    except GroupAError as exc:
        _error(handler,exc)
    except (ValueError,KeyError,TypeError,UnicodeError):
        handler.json_response(400,{"ok":False,"error":{
            "code":"invalid_body","message":"Malformed Group A request"}})
    except Exception:
        handler.json_response(503,{"ok":False,"error":{
            "code":"db_unavailable","message":"Group A unavailable"}})
    return True
