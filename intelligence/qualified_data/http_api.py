"""QDP FIX-16: local-only, project-scoped Studio HTTP facade.

Parent app.py owns loopback Host/Origin and X-ContentMachine mutation checks;
never expose handlers as a remote publicly routable service. No provider calls
on GET, no untrusted browser-supplied revocation origin or external jobs.
"""
from __future__ import annotations
import json
import re
from urllib.parse import parse_qs

from . import repository,studio_read
from .contracts import QDPError,positive_id

BASE=re.compile(r"/api/projects/([0-9a-f]{32})/qualified-data(?:/(.*))?\Z")
ID=r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
_RUN=re.compile(r"runs/"+ID+"\Z")
_CANCEL=re.compile(r"runs/"+ID+r"/cancel\Z")
_REFRESH=re.compile(r"runs/"+ID+r"/refresh\Z")
_SUFFIX=re.compile(r"runs/"+ID+r"/(diagnostics|events|manifest)\Z")
_LINEAGE=re.compile(r"runs/"+ID+r"/lineage\Z")


def _reply(handler,status,value):
    handler.json_response(status,value)


def _query(url,allowed):
    args=parse_qs(url.query,keep_blank_values=True,max_num_fields=8)
    if set(args)-set(allowed) or any(len(v)!=1 or not v[0] for v in args.values()):
        raise QDPError("invalid_query","Unsupported or repeated QDP query argument",422)
    return {k:v[0] for k,v in args.items()}


def _int(raw,name,maximum):
    if type(raw) is not str or not raw.isascii() or not raw.isdecimal():
        raise QDPError("invalid_query","Invalid "+name,422)
    value=int(raw)
    if not 1<=value<=maximum:
        raise QDPError("invalid_query","Invalid "+name,422)
    return value


def handle_get(handler,url):
    match=BASE.fullmatch(url.path)
    if not match:
        return False
    project,suffix=match.groups()
    try:
        if suffix=="preview":
            q=_query(url,{"sre_run_id"})
            if set(q)!={"sre_run_id"}:
                raise QDPError("invalid_query","sre_run_id is required")
            result=repository.preview(project,q["sre_run_id"])
        elif suffix=="eligible-sre-runs":
            q=_query(url,{"limit","cursor"})
            result=studio_read.eligible_sre_runs(
                project,limit=_int(q["limit"],"limit",100) if "limit" in q else 25,
                cursor=q.get("cursor"))
        elif suffix=="runs":
            q=_query(url,{"limit","cursor"})
            result=repository.list_runs(
                project,limit=_int(q["limit"],"limit",100) if "limit" in q else 30,
                cursor=q.get("cursor"))
        elif suffix and _RUN.fullmatch(suffix):
            q=_query(url,{"limit","after_video_id"})
            result=repository.get_run(
                project,suffix.split("/")[1],
                limit=_int(q["limit"],"limit",200) if "limit" in q else 50,
                after_video_id=_int(q["after_video_id"],"cursor",9223372036854775807)
                if "after_video_id" in q else None)
        elif suffix and _SUFFIX.fullmatch(suffix):
            qpart=suffix.split("/")
            rid,kind=qpart[1],qpart[2]
            q=_query(url,{"limit","cursor"} if kind=="events" else set())
            if kind=="events":
                result=studio_read.events(
                    project,rid,
                    limit=_int(q["limit"],"limit",100) if "limit" in q else 40,
                    cursor=q.get("cursor"))
            elif kind=="diagnostics":
                result=studio_read.diagnostics(project,rid)
            else:
                result=studio_read.manifest_summary(project,rid)
        elif suffix and _LINEAGE.fullmatch(suffix):
            q=_query(url,{"video_id"})
            if set(q)!={"video_id"}:
                raise QDPError("invalid_query","video_id is required")
            result=studio_read.lineage(
                project,suffix.split("/")[1],_int(q["video_id"],"video_id",9223372036854775807))
        else:
            raise QDPError("not_found","Qualified Data route not found",404)
        _reply(handler,200,result)
    except QDPError as exc:
        _reply(handler,exc.status,{"ok":False,"error":{"code":exc.code,"message":str(exc)}})
    except Exception:
        _reply(handler,503,{"ok":False,"error":{"code":"database_unavailable",
            "message":"Qualified Data database unavailable"}})
    return True


def handle_post(handler,path):
    match=BASE.fullmatch(path)
    if not match:
        return False
    project,suffix=match.groups()
    start=suffix=="runs"
    cancel=bool(suffix and _CANCEL.fullmatch(suffix))
    refresh=bool(suffix and _REFRESH.fullmatch(suffix))
    if not (start or cancel or refresh):
        _reply(handler,404,{"ok":False,"error":{"code":"not_found","message":"QDP route not found"}})
        return True
    try:
        raw_len=handler.headers.get("Content-Length","")
        if not raw_len.isascii() or not raw_len.isdecimal():
            raise QDPError("invalid_payload","Expected Content-Length",400)
        size=int(raw_len)
        if not 2<=size<=4096:
            raise QDPError("invalid_payload","JSON must be 2..4096 bytes",413)
        data=json.loads(handler.rfile.read(size).decode("utf-8"))
        if type(data) is not dict:
            raise QDPError("invalid_payload","Expected JSON object",422)
        if start:
            if set(data)!={"sre_run_id","idempotency_key","config"}:
                raise QDPError("invalid_payload","Expected SRE run, key, config",422)
            result=repository.create_run(project,data["sre_run_id"],
                                         data["idempotency_key"],data["config"])
            from .worker import start_worker
            start_worker()
            _reply(handler,202,result)
        elif refresh:
            if set(data)!={"idempotency_key","config"}:
                raise QDPError("invalid_payload","Expected refresh key and config",422)
            from .invalidation import explicit_refresh
            result=explicit_refresh(project,suffix.split("/")[1],
                                    data["idempotency_key"],config=data["config"])
            from .worker import start_worker
            start_worker()
            _reply(handler,202,result)
        else:
            if data:
                raise QDPError("invalid_payload","Cancel requires empty JSON object",422)
            _reply(handler,200,repository.cancel_run(project,suffix.split("/")[1]))
    except QDPError as exc:
        _reply(handler,exc.status,{"ok":False,"error":{"code":exc.code,"message":str(exc)}})
    except (UnicodeError,ValueError,TypeError,KeyError,json.JSONDecodeError):
        _reply(handler,400,{"ok":False,"error":{"code":"invalid_payload","message":"Malformed request"}})
    except Exception:
        _reply(handler,503,{"ok":False,"error":{"code":"database_unavailable",
            "message":"Qualified Data database unavailable"}})
    return True
