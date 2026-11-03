"""HTTP endpoints for isolated, localhost-only TikTok Carousel Studio."""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from intelligence.market_intelligence import carousel_studio as studio
from intelligence.market_intelligence import carousel_studio_accounts as accounts
from intelligence.market_intelligence import studio_credentials as secrets

RUN_RE=re.compile(r"/api/studio/runs/([0-9a-f]{32})(?:/(.*))?\Z")
ACCOUNT_RE=re.compile(r"/api/studio/accounts/([0-9a-f]{32})(?:/(.*))?\Z")

def handle_get(handler,url):
    path=url.path
    if not path.startswith("/api/studio/"):
        return False
    if path=="/api/studio/accounts":
        handler.json_response(200,{"accounts":accounts.catalog()})
        return True
    profile=ACCOUNT_RE.fullmatch(path)
    if profile:
        ident,action=profile.groups()
        report=accounts.master(ident)
        if report is None:
            handler.json_response(404,{"error":"Аккаунт ещё не сохранён в Studio."})
            return True
        if action is None:
            latest=report["latest_profile_snapshot"]
            handler.json_response(200,{
                "account":report["account"],
                "cumulative":report["cumulative"],
                "runs":report["runs"],
                "latest_profile_snapshot":({
                    key:latest[key] for key in ("run_id","created_at","snapshot_id","post_count")
                } if latest else None),
                "group_analytics":report["group_analytics"],
            })
            return True
        if action in ("download/json","download/groups"):
            fmt=action.removeprefix("download/")
            file=accounts.file_path(ident,fmt)
            if file is None:
                handler.json_response(404,{"error":"Общий отчёт ещё не сформирован."})
                return True
            handler.send_response(200)
            handler.send_header("Content-Type","application/json; charset=utf-8")
            handler.send_header("Content-Disposition",'attachment; filename="'+file.name+'"')
            handler.send_header("Content-Length",str(file.stat().st_size))
            handler.send_header("Cache-Control","no-store")
            handler.send_header("X-Content-Type-Options","nosniff")
            handler.end_headers()
            try:
                with file.open("rb") as stream:
                    for part in iter(lambda:stream.read(1024*1024),b""):
                        handler.wfile.write(part)
            except (BrokenPipeError,ConnectionAbortedError):
                pass
            return True
        handler.json_response(404,{"error":"Неизвестный файл аккаунта."})
        return True
    if path=="/api/studio/runs":
        handler.json_response(200,{"runs":studio.list_runs()})
        return True
    if path=="/api/studio/settings":
        from intelligence.market_intelligence import brightdata_provider as bd
        from intelligence.market_intelligence.openrouter_credentials import get_market_openrouter_key
        state=bd.readiness()
        handler.json_response(200,{
            **secrets.status(),
            "openrouter_configured":bool(get_market_openrouter_key()),
            "rusgpt_configured":bool(secrets.token("rusgpt_api_key")),
            "active_model":studio.MODEL_BY_PROVIDER[secrets.provider()],
            "brightdata_configured":state["configured"],
            "brightdata_ready":state["ready"],
            "brightdata_rights_verified":state["rights_verified"],
            "no_keys_returned":True,
        })
        return True
    if path=="/api/studio/readiness":
        from intelligence.market_intelligence import brightdata_provider as bd
        from intelligence.market_intelligence.openrouter_credentials import get_market_openrouter_key
        status=bd.readiness()
        handler.json_response(200,{
            "brightdata":status["ready"],
            "gemini":bool(secrets.token("rusgpt_api_key")) if secrets.provider()=="rusgpt"
                     else bool(get_market_openrouter_key()),
            "provider":secrets.provider(),
            "model":studio.MODEL_BY_PROVIDER[secrets.provider()],
            "max_carousels_supported":True,
            "resume_and_dedup_supported":True,
            "account_master_supported":True,
            "safe_resume_revision":2,
            "retry_failed_supported":True,
            "server_worktree_root":str(studio.ROOT.parents[1].resolve()),
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
        if subpath=="groups":
            groups=studio.read_group_analytics(ident)
            if groups is None:
                handler.json_response(404,{"error":"Аналитика групп ещё не сформирована."})
            else:
                handler.json_response(200,groups)
            return True
        still=re.fullmatch(r"items/([0-9]{10,25})/slide/([0-9]{1,2})",subpath)
        if still:
            native,index=still.groups()
            src=studio.item_slide_path(ident,native,int(index))
            if src is None:
                handler.json_response(404,{"error":"Слайд недоступен."})
                return True
            mimetype={".jpg":"image/jpeg",".jpeg":"image/jpeg",
                      ".png":"image/png",".webp":"image/webp"}[src.suffix.lower()]
            handler.send_response(200)
            handler.send_header("Content-Type",mimetype)
            handler.send_header("Content-Length",str(src.stat().st_size))
            handler.send_header("Cache-Control","private, max-age=3600")
            handler.send_header("X-Content-Type-Options","nosniff")
            handler.end_headers()
            try:
                with src.open("rb") as stream:
                    for part in iter(lambda:stream.read(1024*1024),b""):
                        handler.wfile.write(part)
            except (BrokenPipeError,ConnectionAbortedError):
                pass
            return True
        media=re.fullmatch(r"items/([0-9]{10,25})/download/(metrics|analysis|manifest)",subpath)
        if media:
            native,fmt=media.groups()
            file=studio.item_download_path(ident,native,fmt)
            if file is None:
                handler.json_response(404,{"error":"Файл пока недоступен."})
                return True
            content_type="application/json; charset=utf-8"
        elif subpath.startswith("download/") and subpath.removeprefix("download/") in studio.DOWNLOADS:
            fmt=subpath.removeprefix("download/")
            file=studio.download_path(ident,fmt)
            if file is None:
                handler.json_response(404,{"error":"Файл пока недоступен."})
                return True
            content_type={
                "zip":"application/zip",
                "json":"application/json; charset=utf-8",
                "groups":"application/json; charset=utf-8",
                "logs":"application/x-ndjson; charset=utf-8",
            }[fmt]
        else:
            handler.json_response(404,{"error":"Файл не найден."})
            return True
        if file is not None:
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
    if route=="/api/studio/settings":
        if handler.headers.get("Content-Type","").split(";",1)[0].lower()!="application/json":
            handler.json_response(415,{"error":"Ожидается JSON."})
            return True
        try:
            length=int(handler.headers.get("Content-Length","0"))
            if not 2<=length<=9500:
                raise ValueError("Некорректная длина настроек.")
            payload=json.loads(handler.rfile.read(length))
            secrets.store(payload)
        except secrets.CredentialSettingsError as exc:
            # Only allowlisted static messages, never uploaded key values.
            handler.json_response(400,{"error":str(exc)})
            return True
        except (ValueError,TypeError,UnicodeError):
            handler.json_response(400,{"error":"Невозможно прочитать JSON настроек. Значения не сохранены."})
            return True
        except (RuntimeError,OSError):
            handler.json_response(503,{"error":"Windows DPAPI не смог сохранить настройки. Старые ключи сохранены."})
            return True
        from intelligence.market_intelligence import brightdata_provider as bd
        from intelligence.market_intelligence.openrouter_credentials import get_market_openrouter_key
        status=bd.readiness()
        handler.json_response(200,{
            "saved":True,
            **secrets.status(),
            "openrouter_configured":bool(get_market_openrouter_key()),
            "rusgpt_configured":bool(secrets.token("rusgpt_api_key")),
            "active_model":studio.MODEL_BY_PROVIDER[secrets.provider()],
            "brightdata_configured":status["configured"],
            "brightdata_ready":status["ready"],
            "brightdata_rights_verified":status["rights_verified"],
            "no_keys_returned":True,
        })
        return True
    match=RUN_RE.fullmatch(route)
    if match and match.group(2)=="retry-failed":
        if handler.headers.get("X-ContentMachine")!="1":
            handler.json_response(400,{"error":"Требуется явный запуск из Studio."})
            return True
        if handler.headers.get("Content-Type","").split(";",1)[0].lower()!="application/json":
            handler.json_response(415,{"error":"Ожидается JSON."})
            return True
        try:
            length=int(handler.headers.get("Content-Length","0"))
            if not 2<=length<=250:
                raise ValueError("Некорректный размер подтверждения.")
            body=json.loads(handler.rfile.read(length))
            if (type(body) is not dict or
                set(body)!={"confirm_paid_retries"} or
                type(body["confirm_paid_retries"]) is not bool):
                raise ValueError("Подтвердите повторную оплату LLM.")
            job=studio.retry_failed(match.group(1),
                                    confirm_paid_retries=body["confirm_paid_retries"])
        except (ValueError,TypeError,KeyError) as exc:
            handler.json_response(400,{"error":str(exc)})
            return True
        handler.json_response(202,job)
        return True
    if match and match.group(2)=="resume":
        if handler.headers.get("X-ContentMachine")!="1":
            handler.json_response(400,{"error":"Требуется явный запуск из Studio."})
            return True
        try:
            job=studio.resume(match.group(1))
        except (ValueError,TypeError,KeyError) as exc:
            handler.json_response(400,{"error":str(exc)})
            return True
        handler.json_response(202,job)
        return True
    if route=="/api/studio/runs":
        try:
            length=int(handler.headers.get("Content-Length","0"))
            if length<2 or length>4096:
                raise ValueError("Размер запроса неверный.")
            body=json.loads(handler.rfile.read(length))
            if type(body) is not dict:
                raise ValueError("Некорректный JSON.")
            job=studio.start(body.get("kind"),body.get("value"),
                             max_carousels=body.get("max_carousels",30))
        except (ValueError,TypeError,KeyError) as exc:
            handler.json_response(400,{"error":str(exc)})
            return True
        handler.json_response(202,job)
        return True
    handler.json_response(404,{"error":"Не найдено."})
    return True
