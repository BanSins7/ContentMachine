"""Safe local HTTP surface for the separate self-hosted DTK v5 service."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit
from . import dtk

TASK_PATH = re.compile(r"/api/tools/dtk/tasks/([0-9a-fA-F-]{36})\Z")


def error(handler, exc: dtk.DTKError):
    handler.json_response(exc.http_status, {
        "ok": False, "error": {"code": exc.code, "message": str(exc)}})


def handle_get(handler, url) -> bool:
    if url.path == "/api/tools/dtk/status":
        handler.json_response(200, {"ok": True, **dtk.status()})
        return True
    found = TASK_PATH.fullmatch(url.path)
    if found:
        try:
            handler.json_response(200, {"ok": True, **dtk.task(found.group(1))})
        except dtk.DTKError as exc:
            error(handler, exc)
        return True
    if url.path.startswith("/api/tools/dtk/"):
        handler.json_response(404, {"ok": False, "error": {"code":"not_found","message":"Неизвестный маршрут."}})
        return True
    return False


def handle_post(handler, path) -> bool:
    if path != "/api/tools/dtk/run":
        if path.startswith("/api/tools/dtk/"):
            handler.json_response(404, {"ok": False, "error":{"code":"not_found","message":"Неизвестный маршрут."}})
            return True
        return False
    try:
        size = int(handler.headers.get("Content-Length", "0"))
        if not 2 <= size <= 8000:
            raise dtk.DTKError("invalid_size", "Размер запроса должен быть от 2 до 8000 байт.", 413)
        data = json.loads(handler.rfile.read(size).decode("utf-8"))
        if not isinstance(data, dict) or set(data) != {"action","params"}:
            raise dtk.DTKError("invalid_request", "Нужны action и params.")
        value = dtk.submit(data["action"], data["params"])
        handler.json_response(202, {"ok": True, **value})
    except (UnicodeError, ValueError, TypeError):
        handler.json_response(400, {"ok": False, "error":{"code":"invalid_json","message":"Некорректный JSON."}})
    except dtk.DTKError as exc:
        error(handler, exc)
    return True
