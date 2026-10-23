"""Local, bounded, opt-in REST connector for Evil0ctal DTK v5.

The tool submits one explicit upstream read operation per user request.
It does NOT install DTK, scrape itself, download files, import data into D1,
or start any background monitoring. Credentials never reach the browser.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

from intelligence import config

TOOL_ID = "dtk_tiktok"
MAX_RESPONSE_BYTES = 2_000_000
TASK_RE = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z")
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,130}\Z")
ACTIONS = {
    "search_posts": ("/search/posts", ("keyword",)),
    "search_users": ("/search/users", ("keyword",)),
    "video": ("/video", ("aweme_id",)),
    "related_posts": ("/video/related", ("aweme_id",)),
    "comments": ("/video/comments", ("aweme_id",)),
    "comment_replies": ("/video/comments/replies", ("aweme_id", "comment_id")),
    "user": ("/user", ("sec_user_id",)),
    "user_posts": ("/user/posts", ("sec_user_id",)),
    "hashtag": ("/hashtag", ("name",)),
    "hashtag_posts": ("/hashtag/posts", ("hashtag_id",)),
    "music": ("/music", ("music_id",)),
    "music_posts": ("/music/posts", ("music_id",)),
}


class DTKError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def settings() -> tuple[str, str]:
    enabled = config.setting("CM_DTK_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
    if not enabled:
        raise DTKError("disabled", "DTK отключён. Включи CM_DTK_ENABLED=1 в локальной .env.", 503)
    base = config.setting("CM_DTK_BASE_URL", "http://127.0.0.1:8000").strip()
    p = urlsplit(base)
    # Exact IPv4 loopback prevents external-server SSRF and DNS rebinding.
    if p.scheme != "http" or p.hostname != "127.0.0.1" or p.username or p.password or p.path not in ("", "/") or p.query or p.fragment:
        raise DTKError("invalid_config", "DTK доступен только по http://127.0.0.1:PORT.", 503)
    try:
        if p.port is None or not 1 <= p.port <= 65535:
            raise ValueError("port")
    except ValueError:
        raise DTKError("invalid_config", "Некорректный локальный порт DTK.", 503) from None
    key = config.setting("CM_DTK_API_KEY").strip()
    if not key:
        raise DTKError("missing_key", "Не задан CM_DTK_API_KEY для локального DTK.", 503)
    if not 12 <= len(key) <= 512 or any(ord(ch) < 33 or ord(ch) > 126 for ch in key):
        raise DTKError("invalid_config", "Неверный формат ключа DTK.", 503)
    return base.rstrip("/"), key


def validate_action(action: str, values: dict) -> tuple[str, dict[str, str]]:
    if action not in ACTIONS:
        raise DTKError("unsupported_operation", "Действие не поддерживается адаптером.")
    if not isinstance(values, dict):
        raise DTKError("invalid_params", "Ожидаются параметры-объект.")
    endpoint, required = ACTIONS[action]
    allowed = set(required) | ({"cursor"} if action in
               {"search_posts", "search_users", "user_posts", "comments",
                "comment_replies", "hashtag_posts", "music_posts"} else set())
    if set(values) - allowed:
        raise DTKError("invalid_params", "Неизвестные параметры инструмента.")
    output = {}
    for name in required:
        value = values.get(name)
        if not isinstance(value, str):
            raise DTKError("invalid_params", "Ожидается текстовое значение: " + name)
        value = value.strip()
        if name == "keyword" or name == "name":
            if not 1 <= len(value) <= 120 or any(ord(ch) < 32 for ch in value):
                raise DTKError("invalid_params", "Запрос должен содержать 1–120 символов.")
        elif not SAFE_ID.fullmatch(value):
            raise DTKError("invalid_params", "Некорректный ID: " + name)
        output[name] = value
    if "cursor" in values:
        cursor = values["cursor"]
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 320 or any(ord(ch) < 33 for ch in cursor):
            raise DTKError("invalid_params", "Некорректный cursor.")
        output["cursor"] = cursor
    return "/api/v1/tiktok" + endpoint, output


def request_upstream(path: str, params: dict[str, str] | None = None) -> dict:
    base, key = settings()
    # path originates solely from ACTIONS or two fixed status routes.
    if not path.startswith("/api/v1/") or "?" in path or "#" in path:
        raise DTKError("invalid_path", "Внутренняя ошибка маршрута.", 500)
    url = base + path + (("?" + urlencode(params)) if params else "")
    req = Request(url, method="GET", headers={
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
        "User-Agent": "ContentMachine-DTK-Connector/1"
    })
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=9) as response:
            if response.status != 200 and response.status != 202:
                raise DTKError("upstream_http", "DTK вернул неожиданный статус.", 502)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise DTKError("large_response", "Ответ DTK превышает допустимый размер.", 502)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise DTKError("upstream_auth", "DTK отклонил ключ или права tiktok:read.", 503) from None
        if exc.code in (301, 302, 303, 307, 308):
            raise DTKError("upstream_redirect", "Переадресация DTK запрещена.", 502) from None
        raise DTKError("upstream_http", "DTK вернул HTTP " + str(exc.code) + ".", 502) from None
    except (URLError, TimeoutError, OSError):
        raise DTKError("unavailable", "Локальный сервис DTK недоступен.", 503) from None
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise DTKError("invalid_response", "DTK вернул некорректный JSON.", 502) from None
    if not isinstance(payload, dict) or not isinstance(payload.get("success"), bool):
        raise DTKError("invalid_response", "Неверный формат ответа DTK.", 502)
    if not payload["success"]:
        failure = payload.get("error") or {}
        reason = failure.get("code") if isinstance(failure, dict) else None
        if not isinstance(reason, str) or not re.fullmatch(r"[A-Z0-9_]{2,50}", reason):
            reason = "PROVIDER_ERROR"
        raise DTKError("upstream_" + reason.lower(), "Операция DTK отклонена: " + reason + ".", 502)
    return payload


def status() -> dict:
    state = {"tool_id": TOOL_ID, "platform": "tiktok",
             "operations": list(ACTIONS), "configured": False,
             "available": False, "read_scope": False,
             "supports_keyword_search": False,
             "automatic_ingestion": False, "media_download": False}
    try:
        settings()
        state["configured"] = True
        auth = request_upstream("/api/v1/auth/me")
        data = auth.get("data") or {}
        scopes = data.get("scopes") or []
        if not isinstance(scopes, list):
            scopes = []
        state["read_scope"] = "tiktok:read" in scopes or "admin" in scopes
        state["available"] = True
        state["supports_keyword_search"] = state["read_scope"]
        if not state["read_scope"]:
            state["warning"] = "API-ключ не имеет scope tiktok:read."
    except DTKError as exc:
        state["warning"] = str(exc)
        state["error_code"] = exc.code
    return state


def submit(action: str, params: dict) -> dict:
    path, cleaned = validate_action(action, params)
    payload = request_upstream(path, cleaned)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise DTKError("invalid_response", "Нет данных о задании DTK.", 502)
    task_id = data.get("task_id")
    if not isinstance(task_id, str) or not TASK_RE.fullmatch(task_id):
        raise DTKError("unexpected_task", "DTK не вернул корректный task_id.", 502)
    return {"tool_id": TOOL_ID, "action": action, "task_id": task_id,
            "state": data.get("state", "queued"),
            "provider_request_id": (payload.get("meta") or {}).get("request_id")}


def task(task_id: str) -> dict:
    if not isinstance(task_id, str) or not TASK_RE.fullmatch(task_id):
        raise DTKError("invalid_task_id", "Некорректный task_id.")
    payload = request_upstream("/api/v1/tasks/" + task_id.lower())
    data = payload.get("data")
    if (not isinstance(data, dict) or not isinstance(data.get("task_id", task_id), str)
            or data.get("task_id", task_id).lower() != task_id.lower()):
        raise DTKError("invalid_response", "Неверный ответ на запрос задания.", 502)
    state = data.get("state")
    if state not in {"queued", "running", "done", "failed", "cancelled", "canceled"}:
        raise DTKError("invalid_response", "Неизвестное состояние задания.", 502)
    # Results are for diagnostics/review only: never write them to D1 as manual data.
    return {"tool_id": TOOL_ID, "task_id": task_id.lower(), "state": state,
            "data": data.get("data") if state == "done" else None,
            "error": data.get("error") if state == "failed" else None,
            "result_meta": data.get("result_meta") if state == "done" else None}
