"""Local-only Market Intelligence HTTP surface, mounted in app.Handler.

All POSTs are protected by Handler.authorized(mutation=True), require JSON and
have no external provider or LLM dispatch. Old RTB endpoints remain unchanged.
"""
from __future__ import annotations
import json
import re
from .contracts import MarketError
from .service import MarketIntelligenceService

PREFIX = re.compile(r"^/api/projects/([0-9a-f]{32})/market-intelligence(?:/(.*))?$")
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def _error(handler, exc):
    if isinstance(exc, MarketError):
        handler.json_response(exc.status, {"ok": False, "error": {
            "code": exc.code, "message": str(exc)}})
    else:
        handler.json_response(503, {"ok": False, "error": {
            "code": "market_intelligence_unavailable",
            "message": "Рыночное исследование недоступно. Проверь PostgreSQL и миграции."}})


def handle_get(handler, url):
    matched = PREFIX.fullmatch(url.path)
    if not matched:
        return False
    project_id, operation = matched.groups()
    if url.query:
        handler.json_response(400, {"ok": False, "error": {"code": "query_disallowed",
             "message": "Параметры адреса здесь не поддерживаются."}})
        return True
    try:
        service = MarketIntelligenceService()
        if operation == "preview":
            result = service.preview(project_id)
        elif operation == "authors":
            result = service.authors_status(project_id)
        elif operation and re.fullmatch("snapshots/" + UUID, operation):
            result = service.get(project_id, operation.split("/")[1])
        elif operation and re.fullmatch("rtb-context/" + UUID, operation):
            result = service.rtb_context(project_id, operation.split("/")[1])
        else:
            handler.json_response(404, {"ok": False, "error": {
                "code": "not_found", "message": "Маршрут не найден."}})
            return True
        handler.json_response(200, result)
    except Exception as exc:
        _error(handler, exc)
    return True


def handle_post(handler, path):
    matched = PREFIX.fullmatch(path)
    if not matched:
        return False
    project_id, operation = matched.groups()
    if operation not in ("seeds", "freeze"):
        handler.json_response(404, {"ok": False, "error": {
            "code": "not_found", "message": "Маршрут не найден."}})
        return True
    try:
        if handler.headers.get("Content-Type", "").split(";")[0].lower().strip() != "application/json":
            raise MarketError("content_type", "Требуется application/json.", 415)
        try:
            size = int(handler.headers.get("Content-Length", "0"))
        except (ValueError, TypeError):
            size = 0
        if not 2 <= size <= 440000:
            raise MarketError("body_size", "Недопустимый размер запроса.", 413)
        try:
            body = json.loads(handler.rfile.read(size).decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise MarketError("invalid_json", "Некорректный JSON.") from exc
        if not isinstance(body, dict):
            raise MarketError("invalid_json", "Нужен JSON-объект.")
        service = MarketIntelligenceService()
        if operation == "seeds":
            if set(body) != {"videos", "authors"}:
                raise MarketError("seed_fields", "Нужны только поля videos и authors.")
            result = service.register(project_id, body["videos"], body["authors"])
        else:
            if body:
                raise MarketError("freeze_fields", "Для фиксации отчёта дополнительных полей не нужно.")
            result = service.freeze(project_id)
        handler.json_response(200, result)
    except Exception as exc:
        _error(handler, exc)
    return True
