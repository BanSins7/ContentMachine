"""Local-only QDP HTTP facade. Parent app.py enforces localhost Host/Origin/CSRF."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs

from . import repository
from .contracts import QDPError

BASE = re.compile(r"/api/projects/([0-9a-f]{32})/qualified-data(?:/(.*))?\Z")
ID = r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"


def _reply(handler, status, value):
    handler.json_response(status, value)


def handle_get(handler, url):
    match = BASE.fullmatch(url.path)
    if not match:
        return False
    project, suffix = match.groups()
    query = parse_qs(url.query)
    try:
        if suffix == "preview":
            if set(query) != {"sre_run_id"} or len(query["sre_run_id"]) != 1:
                raise QDPError("invalid_query", "sre_run_id is required")
            result = repository.preview(project, query["sre_run_id"][0])
        elif suffix == "runs":
            result = repository.list_runs(project)
        elif suffix and re.fullmatch(r"runs/" + ID, suffix):
            result = repository.get_run(project, suffix.split("/")[1])
        else:
            raise QDPError("not_found", "Qualified Data route not found", 404)
        _reply(handler, 200, result)
    except QDPError as exc:
        _reply(handler, exc.status, {"ok":False,"error":{"code":exc.code,"message":str(exc)}})
    except Exception:
        _reply(handler, 503, {"ok":False,"error":{"code":"database_unavailable",
            "message":"Qualified Data database unavailable"}})
    return True


def handle_post(handler, path):
    match = BASE.fullmatch(path)
    if not match:
        return False
    project, suffix = match.groups()
    start = suffix == "runs"
    cancel = bool(suffix and re.fullmatch(r"runs/" + ID + r"/cancel", suffix))
    if not start and not cancel:
        _reply(handler,404,{"ok":False,"error":{"code":"not_found","message":"QDP route not found"}})
        return True
    try:
        size = int(handler.headers.get("Content-Length", "0"))
        if not 2 <= size <= 4096:
            raise QDPError("invalid_payload", "Request must be JSON <= 4096 bytes", 413)
        data = json.loads(handler.rfile.read(size).decode("utf-8"))
        if type(data) is not dict:
            raise QDPError("invalid_payload", "Expected JSON object")
        if start:
            if set(data) != {"sre_run_id","idempotency_key","config"}:
                raise QDPError("invalid_payload", "Expected sre_run_id, idempotency_key and config")
            result = repository.create_run(project, data["sre_run_id"],
                                           data["idempotency_key"], data["config"])
            from .worker import start_worker
            start_worker()
            _reply(handler, 202, result)
        else:
            if data:
                raise QDPError("invalid_payload", "Cancel requires empty JSON object")
            _reply(handler, 200, repository.cancel_run(project,suffix.split("/")[1]))
    except QDPError as exc:
        _reply(handler,exc.status,{"ok":False,"error":{"code":exc.code,"message":str(exc)}})
    except (UnicodeError,ValueError,TypeError,KeyError,json.JSONDecodeError):
        _reply(handler,400,{"ok":False,"error":{"code":"invalid_payload","message":"Malformed request"}})
    except Exception:
        _reply(handler,503,{"ok":False,"error":{"code":"database_unavailable",
            "message":"Qualified Data database unavailable"}})
    return True
