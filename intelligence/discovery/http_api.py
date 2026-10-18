"""Local-only HTTP adapter for D1. No SQL or provider-specific logic in app.py."""
from __future__ import annotations
import json
from urllib.parse import parse_qs
from . import service
from .importers import ImportErrorPublic

def respond(handler, status, payload):
    handler.json_response(status,payload)

def error(handler, code, message, status=400, retryable=False):
    respond(handler,status,{"ok":False,"error":{"code":code,"message":message,
                                               "retryable":retryable}})

def safe_positive_id(value):
    try:
        if not isinstance(value,str) or len(value)>19 or not value.isdigit():
            raise ValueError()
        value=int(value)
        if value<=0: raise ValueError()
        return value
    except (ValueError,TypeError) as exc:
        raise service.DiscoveryError("Некорректный ID.") from exc

def handle_get(handler, url):
    path=url.path
    if not path.startswith("/api/intelligence/") or path=="/api/intelligence/health":
        return False
    params=parse_qs(url.query)
    try:
        if path=="/api/intelligence/diagnostics":
            value=service.diagnostics()
        elif path=="/api/intelligence/sources":
            value=service.list_sources()
        elif path=="/api/intelligence/search-runs":
            if "id" in params:
                value=service.run_detail(safe_positive_id(params["id"][0]))
            else:
                value=service.list_runs()
        elif path=="/api/intelligence/videos":
            before=params.get("before",[None])[0]
            limit=params.get("limit",["50"])[0]
            value=service.list_videos(
                safe_positive_id(limit),safe_positive_id(before) if before else None)
        elif path.startswith("/api/intelligence/videos/") and path.endswith("/trace"):
            value=service.video_trace(safe_positive_id(path.split("/")[4]))
        else:
            error(handler,"not_found","Маршрут не найден.",404)
            return True
        respond(handler,200,value)
    except service.DiscoveryError as exc:
        error(handler,"not_found" if "не найден" in str(exc) else "invalid_request",
              str(exc),404 if "не найден" in str(exc) else 400)
    except Exception:
        error(handler,"db_unavailable","Discovery временно недоступен. Проверь PostgreSQL.",503,True)
    return True

def handle_post(handler, path):
    if not path.startswith("/api/intelligence/"):
        return False
    try:
        size=int(handler.headers.get("Content-Length","0"))
        if not 2<=size<=2_200_000:
            error(handler,"payload_size","Размер запроса: не более 2 МБ.",413)
            return True
        obj=json.loads(handler.rfile.read(size).decode("utf-8"))
        if not isinstance(obj,dict):
            raise service.DiscoveryError("Ожидается JSON-объект.")
        if path=="/api/intelligence/manual-imports":
            if set(obj)-{"format","text","topic","idempotency_key"}:
                raise service.DiscoveryError("Неизвестные поля импорта.")
            key=obj.get("idempotency_key") or handler.headers.get("Idempotency-Key")
            value=service.create_import(obj.get("text",""),obj.get("format","urls"),
                                        obj.get("topic",""),key)
            service.start_worker()
            respond(handler,202,value)
        elif path=="/api/intelligence/preview":
            # D1 does not contact any remote source or enqueue paid work.
            respond(handler,200,{"ok":True,"available":["manual_import"],
                 "blocked":["youtube","tiktok","instagram","commercial"],
                 "message":"Внешний поиск будет подключён в D2/D3. D1 поддерживает CSV и URL."})
        elif path=="/api/intelligence/human-labels":
            if set(obj)-{"video_id","topic","label","reason"}:
                raise service.DiscoveryError("Неизвестные поля разметки.")
            value=service.label_video(safe_positive_id(str(obj.get("video_id",""))),
                  obj.get("topic",""),obj.get("label",""),obj.get("reason",""))
            respond(handler,201,value)
        elif path.startswith("/api/intelligence/search-runs/") and path.endswith("/cancel"):
            run_id=safe_positive_id(path.split("/")[4])
            value=service.cancel_run(run_id)
            respond(handler,202,value)
        else:
            error(handler,"not_found","Маршрут не найден.",404)
    except (ImportErrorPublic,service.DiscoveryError,ValueError,UnicodeError,json.JSONDecodeError) as exc:
        error(handler,"invalid_input",str(exc)[:180],400)
    except Exception:
        error(handler,"db_unavailable","Discovery временно недоступен. Проверь PostgreSQL.",503,True)
    return True
