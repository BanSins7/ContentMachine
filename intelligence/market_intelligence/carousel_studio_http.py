"""HTTP endpoints for isolated, localhost-only TikTok Carousel Studio."""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from intelligence.market_intelligence import carousel_studio as studio

RUN_RE=re.compile(r"/api/studio/runs/([0-9a-f]{32})(?:/(.*))?\Z")

def handle_get(handler,url):
    path=url.path
    if not path.startswith("/api/studio/"):
        return False
    if path=="/api/studio/runs":
        handler.json_response(200,{"runs":studio.list_runs()})
        return True
    if path=="/api/studio/readiness":
        from intelligence.market_intelligence import brightdata_provider as bd
        from intelligence.market_intelligence.openrouter_credentials import get_market_openrouter_key
        status=bd.readiness()
        handler.json_response(200,{
            "brightdata":status["ready"],
            "gemini":bool(get_market_openrouter_key()),
            "model":studio.story.MODEL,
            "storage":"локальный SQLite + файлы в marketing-qa/carousel-studio",
            "profile_scope":"Только публикации, возвращённые Bright Data",
            "platform":"tiktok"})
        return True
    match=RUN_RE.fullmatch(path)
    if match:
        ident,subpath=match.groups()
        item=studio.get_run(ident)
        if item is None:
            handler.json_response(404,{"error":"Запуск не найден."})
            return True
        if subpath is None:
            handler.json_response(200,item)
            return True
        if subpath.startswith("download/"):
            fmt=subpath.removeprefix("download/")
            file=studio.download_path(ident,fmt)
            if file is None:
                handler.json_response(404,{"error":"Файл пока недоступен."})
                return True
            content_type={
                "zip":"application/zip",
                "json":"application/json; charset=utf-8",
                "logs":"application/x-ndjson; charset=utf-8",
            }[fmt]
            handler.send_response(200)
            handler.send_header("Content-Type",content_type)
            handler.send_header("Content-Disposition",
                                'attachment; filename="'+file.name+'"')
            handler.send_header("Content-Length",str(file.stat().st_size))
            handler.send_header("Cache-Control","no-store")
            handler.send_header("X-Content-Type-Options","nosniff")
            handler.end_headers()
            try:
                with file.open("rb") as f:
                    for part in iter(lambda:f.read(1024*1024),b""):
                        handler.wfile.write(part)
            except (BrokenPipeError,ConnectionAbortedError):
                pass
            return True
    handler.json_response(404,{"error":"Не найдено."})
    return True

def handle_post(handler,route):
    if not route.startswith("/api/studio/"):
        return False
    if route=="/api/studio/runs":
        try:
            length=int(handler.headers.get("Content-Length","0"))
            if length<2 or length>4096:
                raise ValueError("Размер запроса неверный.")
            body=json.loads(handler.rfile.read(length))
            if type(body) is not dict:
                raise ValueError("Некорректный JSON.")
            job=studio.start(body.get("kind"),body.get("value"))
        except (ValueError,TypeError,KeyError) as exc:
            handler.json_response(400,{"error":str(exc)})
            return True
        handler.json_response(202,job)
        return True
    handler.json_response(404,{"error":"Не найдено."})
    return True
