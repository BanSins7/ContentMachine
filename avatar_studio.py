"""AI Avatar Studio: local, reference-guided GPT Image generation.

No third-party dependencies. The OpenAI key is server-side only; image requests
are initiated explicitly and never retried automatically (to avoid double charges).
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import socket
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

MODEL = "gpt-image-2.5-sunburst"
ENDPOINT = "https://api.openai.com/v1/images/edits"
MAX_REQUEST = 15 * 1024 * 1024
MAX_REFERENCE = 10 * 1024 * 1024
MAX_PROVIDER_RESPONSE = 48 * 1024 * 1024
SIZES = {"1024x1536", "1024x1024", "1536x1024"}
QUALITIES = {"high", "xhigh", "max"}
UUID_RE = re.compile(r"[0-9a-f]{32}\Z")
POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="avatar-studio")
LOCK = threading.RLock()
ACTIVE = set()  # Jobs started in this process; old jobs are interrupted after restart.


class StudioError(Exception):
    def __init__(self, status: int, code: str, message: str, request_id: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id

    def public(self):
        result = {"code": self.code, "message": str(self)}
        if self.request_id:
            result["request_id"] = self.request_id
        return result


def root_dir() -> Path:
    configured = os.environ.get("CM_AVATAR_STUDIO_DIR")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ContentMachineData" / "avatar-studio"
    return Path.home() / ".contentmachine" / "avatar-studio"


def api_key() -> str:
    # No browser field or hard-coded token. Can also use the ignored per-machine file.
    token = os.environ.get("OPENAI_API_KEY", "").strip()
    if token:
        return token
    local = Path(__file__).resolve().parent / "worker" / "openai_api_key.txt"
    try:
        return local.read_text(encoding="utf-8").strip() if local.is_file() else ""
    except OSError:
        return ""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def location(job_id: str) -> Path:
    if not UUID_RE.fullmatch(job_id):
        raise StudioError(400, "invalid_id", "Некорректный идентификатор генерации.")
    return root_dir() / job_id


def load_job(job_id: str) -> dict:
    try:
        data = json.loads((location(job_id) / "job.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        if data.get("status") in ("queued", "running") and job_id not in ACTIVE:
            # A restarted server cannot resume the old HTTP request. Avoid endless spinners.
            data["status"] = "failed"
            data["error"] = {"code": "interrupted",
                             "message": "Сервер был перезапущен во время генерации. "
                                        "Проверьте биллинг OpenAI до повторного запуска."}
            data["updated_at"] = now()
            save_job(data)
        return data
    except (OSError, ValueError):
        raise StudioError(404, "not_found", "Генерация не найдена.")


def save_job(job: dict) -> None:
    folder = location(job["id"])
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / "job.tmp"
    with LOCK:
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(folder / "job.json")


def update_job(job_id: str, **changes) -> None:
    with LOCK:
        job = load_job(job_id)
        job.update(changes)
        job["updated_at"] = now()
        save_job(job)


def public_job(job: dict) -> dict:
    ident = job["id"]
    output = {key: job.get(key) for key in
              ("id", "status", "created_at", "updated_at", "prompt",
               "quality", "size", "model", "error", "usage", "estimated_cost_usd")}
    output["reference_url"] = f"/api/avatar-studio/files/{ident}/reference" if job.get("reference_ext") else None
    output["image_url"] = f"/api/avatar-studio/files/{ident}/result" if job.get("status") == "succeeded" else None
    return output


def validate_reference(data_url: str):
    if not isinstance(data_url, str):
        raise StudioError(400, "reference_missing", "Загрузите одно изображение-референс.")
    match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=\r\n]+)", data_url)
    if not match:
        raise StudioError(400, "invalid_reference", "Референс должен быть PNG, JPG или WebP.")
    if len(match.group(2)) > ((MAX_REFERENCE + 2) // 3) * 4 + 8:
        raise StudioError(413, "reference_too_large", "Размер референса не должен превышать 10 МБ.")
    try:
        data = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError):
        raise StudioError(400, "invalid_reference", "Файл референса повреждён.")
    if not data or len(data) > MAX_REFERENCE:
        raise StudioError(413, "reference_too_large", "Размер референса не должен превышать 10 МБ.")
    expected = match.group(1)
    valid = (data.startswith(b"\x89PNG\r\n\x1a\n") if expected == "png" else
             data.startswith(b"\xff\xd8\xff") if expected == "jpeg" else
             data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    if not valid:
        raise StudioError(400, "invalid_reference", "Формат содержимого файла не соответствует расширению.")
    return data, ("jpg" if expected == "jpeg" else expected), ("jpeg" if expected == "jpeg" else expected)


def parse_payload(body: dict):
    if not isinstance(body, dict):
        raise StudioError(400, "invalid_payload", "Ожидается JSON-объект.")
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not (20 <= len(prompt.strip()) <= 15000):
        raise StudioError(400, "invalid_prompt", "Промпт должен содержать от 20 до 15 000 символов.")
    quality = body.get("quality", "high")
    size = body.get("size", "1024x1536")
    if quality not in QUALITIES:
        raise StudioError(400, "invalid_quality", "Выберите качество High, XHigh или Max.")
    if size not in SIZES:
        raise StudioError(400, "invalid_size", "Выберите поддерживаемое разрешение.")
    reference, ext, mime = validate_reference(body.get("reference"))
    return prompt.strip(), quality, size, reference, ext, mime


def multipart(fields: dict, image: bytes, mime: str) -> tuple[bytes, str]:
    boundary = "cm-" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.extend([
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode("ascii"),
            value.encode("utf-8"), b"\r\n",
        ])
    parts.extend([
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image[]\"; filename=\"reference.{ 'jpg' if mime == 'jpeg' else mime }\"\r\nContent-Type: image/{mime}\r\n\r\n".encode("ascii"),
        image, b"\r\n", f"--{boundary}--\r\n".encode("ascii")
    ])
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def provider_error(status: int, raw: bytes, request_id: str = "") -> StudioError:
    code = "provider_error"
    message = ""
    try:
        payload = json.loads(raw.decode("utf-8"))
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            code = str(error.get("code") or error.get("type") or code)[:80]
            message = str(error.get("message") or "")[:300]
    except (UnicodeError, ValueError):
        pass
    if status == 401:
        message = "OpenAI отклонил API-ключ. Проверьте OPENAI_API_KEY."
    elif status == 403:
        message = "Нет доступа к модели или запрос запрещён. Проверьте доступ в OpenAI API."
    elif status == 429:
        message = ("Недостаточно квоты или достигнут лимит запросов OpenAI. "
                   "Проверьте биллинг и лимиты; повторите запрос вручную.")
    elif status >= 500:
        message = "OpenAI временно недоступен. Повторите запрос позже вручную."
    elif not message:
        message = "OpenAI отклонил запрос. Проверьте референс и промпт."
    return StudioError(status if 400 <= status <= 599 else 502, code, message, request_id)


def call_openai(key: str, prompt: str, quality: str, size: str, reference: bytes, mime: str) -> dict:
    body, content_type = multipart({
        "model": MODEL, "prompt": prompt, "quality": quality, "size": size,
        "output_format": "png", "n": "1",
    }, reference, mime)
    request = Request(
        ENDPOINT, data=body, method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": content_type},
    )
    try:
        with urlopen(request, timeout=180) as response:
            request_id = response.headers.get("x-request-id", "")
            raw = response.read(MAX_PROVIDER_RESPONSE + 1)
            if len(raw) > MAX_PROVIDER_RESPONSE:
                raise StudioError(502, "provider_response_large", "Ответ OpenAI слишком большой.", request_id)
    except HTTPError as exc:
        raw = exc.read(16384)
        raise provider_error(exc.code, raw, exc.headers.get("x-request-id", ""))
    except (TimeoutError, socket.timeout):
        raise StudioError(504, "provider_timeout",
                          "Время ожидания OpenAI истекло. Проверьте историю перед повтором: запрос мог быть оплачен.")
    except URLError:
        raise StudioError(503, "provider_unreachable",
                          "Не удалось подключиться к OpenAI. Проверьте сеть и прокси.")
    try:
        payload = json.loads(raw)
        encoded = payload["data"][0]["b64_json"]
        image = base64.b64decode(encoded, validate=True)
        if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) > 24 * 1024 * 1024:
            raise ValueError("invalid output image")
    except (KeyError, IndexError, TypeError, ValueError, binascii.Error):
        raise StudioError(502, "invalid_provider_response", "OpenAI вернул некорректный результат.", request_id)
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}
    cost = None
    if "output_tokens" in usage and "text_tokens" in details and "image_tokens" in details:
        try:
            cost = round((int(usage["output_tokens"]) * 30 +
                          int(details["image_tokens"]) * 8 +
                          int(details["text_tokens"]) * 5) / 1000000, 6)
        except (ValueError, TypeError):
            pass
    return {"image": image, "usage": usage, "estimated_cost_usd": cost,
            "request_id": request_id}


def run(job_id: str, key: str, reference: bytes, mime: str):
    try:
        update_job(job_id, status="running")
        job = load_job(job_id)
        output = call_openai(key, job["prompt"], job["quality"], job["size"], reference, mime)
        folder = location(job_id)
        temporary = folder / "result.tmp"
        temporary.write_bytes(output["image"])
        temporary.replace(folder / "result.png")
        update_job(job_id, status="succeeded", usage=output["usage"],
                   estimated_cost_usd=output["estimated_cost_usd"],
                   request_id=output["request_id"])
    except StudioError as exc:
        update_job(job_id, status="failed", error=exc.public())
    except Exception:
        # No raw provider traces or credentials in responses.
        try:
            update_job(job_id, status="failed",
                       error={"code": "internal_error", "message": "Ошибка сохранения или обработки результата."})
        except OSError:
            pass
    finally:
        with LOCK:
            ACTIVE.discard(job_id)


def read_json(handler):
    try:
        size = int(handler.headers.get("Content-Length", ""))
    except ValueError:
        raise StudioError(411, "length_required", "Не задан размер запроса.")
    if not (2 <= size <= MAX_REQUEST):
        raise StudioError(413, "request_too_large", "Размер запроса превышает 15 МБ.")
    if "application/json" not in handler.headers.get("Content-Type", "").lower():
        raise StudioError(415, "wrong_content_type", "Ожидается application/json.")
    try:
        return json.loads(handler.rfile.read(size).decode("utf-8"))
    except (ValueError, UnicodeError):
        raise StudioError(400, "invalid_json", "Некорректный JSON.")


def serve_bytes(handler, file: Path, mime: str, download: bool = False):
    try:
        data = file.read_bytes()
    except OSError:
        raise StudioError(404, "file_missing", "Файл не найден.")
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "private, no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    if download:
        handler.send_header("Content-Disposition", 'attachment; filename="avatar.png"')
    handler.end_headers()
    handler.wfile.write(data)


def handle_get(handler, url) -> bool:
    route = url.path
    if not route.startswith("/api/avatar-studio/"):
        return False
    try:
        if route == "/api/avatar-studio/status":
            handler.json_response(200, {"ok": True, "configured": bool(api_key()), "model": MODEL})
        elif route == "/api/avatar-studio/history":
            jobs = []
            root = root_dir()
            if root.is_dir():
                for folder in root.iterdir():
                    if folder.is_dir() and UUID_RE.fullmatch(folder.name):
                        try:
                            jobs.append(public_job(load_job(folder.name)))
                        except StudioError:
                            continue
            jobs.sort(key=lambda job: job.get("created_at") or "", reverse=True)
            handler.json_response(200, {"ok": True, "jobs": jobs[:30]})
        else:
            match = re.fullmatch(r"/api/avatar-studio/jobs/([0-9a-f]{32})", route)
            file_match = re.fullmatch(
                r"/api/avatar-studio/files/([0-9a-f]{32})/(reference|result)", route)
            if match:
                handler.json_response(200, {"ok": True, "job": public_job(load_job(match.group(1)))})
            elif file_match:
                job = load_job(file_match.group(1))
                if file_match.group(2) == "reference":
                    ext = job.get("reference_ext")
                    if ext not in ("png", "jpg", "webp"):
                        raise StudioError(404, "file_missing", "Референс не найден.")
                    serve_bytes(handler, location(job["id"]) / ("reference." + ext),
                                {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[ext])
                else:
                    if job.get("status") != "succeeded":
                        raise StudioError(404, "file_missing", "Изображение ещё не готово.")
                    serve_bytes(handler, location(job["id"]) / "result.png", "image/png")
            else:
                raise StudioError(404, "not_found", "Эндпоинт не найден.")
    except StudioError as exc:
        handler.json_response(exc.status, {"ok": False, "error": exc.public()})
    except OSError:
        handler.json_response(503, {"ok": False, "error": {"code": "storage_error",
                                                         "message": "Локальное хранилище недоступно."}})
    return True


def handle_post(handler, route: str) -> bool:
    if not route.startswith("/api/avatar-studio/"):
        return False
    try:
        if route != "/api/avatar-studio/generate":
            raise StudioError(404, "not_found", "Эндпоинт не найден.")
        prompt, quality, size, reference, ext, mime = parse_payload(read_json(handler))
        key = api_key()
        if not key:
            raise StudioError(503, "key_missing",
                              "OpenAI API не настроен. Задайте OPENAI_API_KEY в окружении сервера "
                              "или сохраните ключ в worker/openai_api_key.txt.")
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "status": "queued", "created_at": now(), "updated_at": now(),
               "prompt": prompt, "quality": quality, "size": size, "model": MODEL,
               "reference_ext": ext, "error": None, "usage": None, "estimated_cost_usd": None}
        folder = location(job_id)
        with LOCK:
            ACTIVE.add(job_id)
        folder.mkdir(parents=True, exist_ok=False)
        (folder / ("reference." + ext)).write_bytes(reference)
        save_job(job)
        try:
            POOL.submit(run, job_id, key, reference, mime)
        except RuntimeError:
            with LOCK:
                ACTIVE.discard(job_id)
            update_job(job_id, status="failed",
                       error={"code": "worker_unavailable", "message": "Генератор сейчас недоступен."})
            raise StudioError(503, "worker_unavailable", "Генератор сейчас недоступен.")
        handler.json_response(202, {"ok": True, "job": public_job(job)})
    except StudioError as exc:
        handler.json_response(exc.status, {"ok": False, "error": exc.public()})
    except OSError:
        handler.json_response(503, {"ok": False, "error": {"code": "storage_error",
                                                         "message": "Не удалось записать файл на диск."}})
    return True
