"""Local settings API for Bright Data, Rus-GPT and OpenRouter.

No secret appears in responses or logs. Credential storage is compatible
with the standalone Carousel Studio branch (Windows DPAPI storage).
This API does not trigger provider calls or other billable work.
"""
from __future__ import annotations

import json

from intelligence import config
from intelligence.provider_credentials import (
    CredentialSettingsError, store, status, token,
)

ENDPOINT = "/api/provider-settings"
MODEL = {
    "openrouter": "google/gemini-3.8-flash",
    "rusgpt": "anthropic/claude-haiku-5.5",
}


def get_status() -> dict:
    saved = status()
    from worker.openrouter_transcribe import get_key
    router = token("openrouter_api_key") or get_key(config.ROOT / "worker")
    rusgpt = token("rusgpt_api_key") or config.setting("CM_RUSGPT_API_KEY", "")
    brightdata = token("brightdata_api_token") or config.setting("CM_BRIGHTDATA_API_TOKEN", "")
    authorized = saved["brightdata_authorized"]
    return {
        "ok": True,
        **saved,
        "active_model": MODEL[saved["preferred_provider"]],
        "openrouter_configured": bool(router),
        "rusgpt_configured": bool(rusgpt),
        "brightdata_configured": bool(brightdata),
        "brightdata_ready": bool(brightdata) and authorized,
        "no_keys_returned": True,
    }


def handle_get(handler, url) -> bool:
    if url.path != ENDPOINT:
        return False
    try:
        handler.json_response(200, get_status())
    except (CredentialSettingsError, OSError, RuntimeError, ValueError):
        handler.json_response(503, {"error": "Не удалось прочитать локальные настройки провайдеров."})
    return True


def handle_post(handler, path) -> bool:
    if path != ENDPOINT:
        return False
    if handler.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
        handler.json_response(415, {"error": "Ожидается application/json."})
        return True
    try:
        size = int(handler.headers.get("Content-Length", "0"))
        if not 2 <= size <= 13000:
            handler.json_response(413, {"error": "Некорректный размер настроек."})
            return True
        payload = json.loads(handler.rfile.read(size).decode("utf-8"))
        store(payload)
        handler.json_response(200, get_status())
    except CredentialSettingsError as exc:
        handler.json_response(400, {"error": str(exc)})
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        handler.json_response(400, {"error": "Некорректный JSON настроек."})
    except (OSError, RuntimeError):
        handler.json_response(503, {"error": "Не удалось сохранить настройки локально."})
    return True
