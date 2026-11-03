"""Deterministic image-slideshow MP4 renderer for Carousel Studio.

Accepts already-composed PNG slide frames. No provider calls, no image generation.
Requires a local FFmpeg with libx264.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import os
import re
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

MAX_REQUEST_BYTES = 100_000_000
MAX_FRAME_BYTES = 12_000_000
MAX_SLIDES = 40
MAX_DURATION = 600.0
JOB_RE = re.compile(r"[a-f0-9]{32}\Z")
DATA_RE = re.compile(r"data:image/png;base64,([A-Za-z0-9+/]+={0,2})\Z")
NAME_RE = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="carousel-video")
LOCK = threading.RLock()
ACTIVE: set[str] = set()


class VideoError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def root_dir() -> Path:
    override = os.environ.get("CM_CAROUSEL_VIDEO_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".contentmachine")
    return base / "ContentMachineData" / "carousel-videos"


def job_dir(job_id: str) -> Path:
    if not JOB_RE.fullmatch(job_id):
        raise VideoError("invalid_job", "Некорректный ID задачи.", 404)
    return root_dir() / job_id


def job_metadata(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def write_job(job: dict) -> None:
    with LOCK:
        folder = job_dir(job["id"])
        folder.mkdir(parents=True, exist_ok=True)
        staging = folder / "job.json.tmp"
        staging.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(staging, job_metadata(job["id"]))


def read_job(job_id: str) -> dict | None:
    with LOCK:
        path = job_metadata(job_id)
        if not path.is_file():
            return None
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            if job.get("status") in {"queued", "running"} and job_id not in ACTIVE:
                job["status"] = "failed"
                job["updated_at"] = now()
                job["error"] = {"code": "interrupted", "message": "Сборка была прервана. Запустите её снова."}
                write_job(job)
            return job
        except (ValueError, OSError):
            return None


def validate_request(data: object) -> tuple[str, list[tuple[bytes, float]]]:
    if not isinstance(data, dict):
        raise VideoError("invalid_json", "Нужен JSON-объект.")
    name = data.get("carousel_id", "carousel")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise VideoError("invalid_name", "Название карусели: латинские буквы, цифры, _ и -.")
    slides = data.get("slides")
    if not isinstance(slides, list) or not 1 <= len(slides) <= MAX_SLIDES:
        raise VideoError("invalid_slides", "Требуется от 1 до 40 слайдов.")
    frames: list[tuple[bytes, float]] = []
    total = 0.0
    for index, slide in enumerate(slides, 1):
        if not isinstance(slide, dict):
            raise VideoError("invalid_slide", f"Слайд {index}: нужен объект.")
        duration = slide.get("duration_seconds")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise VideoError("invalid_duration", f"Слайд {index}: укажите duration_seconds числом.")
        if not math.isfinite(duration) or not 0.25 <= duration <= 60:
            raise VideoError("invalid_duration", f"Слайд {index}: длительность от 0.25 до 60 секунд.")
        total += duration
        value = slide.get("image")
        if not isinstance(value, str):
            raise VideoError("invalid_image", f"Слайд {index}: нет PNG кадра.")
        match = DATA_RE.fullmatch(value)
        if not match or len(match[1]) > (MAX_FRAME_BYTES * 4 // 3 + 8):
            raise VideoError("invalid_image", f"Слайд {index}: нужен PNG data URL до 12 МБ.")
        try:
            binary = base64.b64decode(match[1], validate=True)
        except (ValueError, binascii.Error):
            raise VideoError("invalid_image", f"Слайд {index}: некорректный base64.") from None
        if len(binary) > MAX_FRAME_BYTES or not binary.startswith(b"\x89PNG\r\n\x1a\n"):
            raise VideoError("invalid_image", f"Слайд {index}: файл не является PNG.")
        # Inspect only the IHDR signature and dimensions; FFmpeg will validate full stream.
        if len(binary) < 33 or binary[12:16] != b"IHDR":
            raise VideoError("invalid_image", f"Слайд {index}: повреждён PNG.")
        width = int.from_bytes(binary[16:20], "big")
        height = int.from_bytes(binary[20:24], "big")
        if not 600 <= width <= 2160 or not 600 <= height <= 2160:
            raise VideoError("invalid_image", f"Слайд {index}: недопустимые размеры PNG.")
        frames.append((binary, round(float(duration), 3)))
    if total > MAX_DURATION:
        raise VideoError("too_long", "Длительность ролика ограничена 10 минутами.")
    return name, frames


def ffmpeg_binary() -> str:
    program = os.environ.get("CM_FFMPEG_PATH", "").strip()
    if program:
        if Path(program).is_file():
            return program
        raise VideoError("ffmpeg_missing", "CM_FFMPEG_PATH указывает на отсутствующий ffmpeg.exe.", 503)
    program = shutil.which("ffmpeg")
    if not program:
        raise VideoError("ffmpeg_missing", "FFmpeg не установлен или не найден в PATH. Установите FFmpeg для экспорта MP4.", 503)
    return program


def render_job(job_id: str, frames: list[tuple[bytes, float]], program: str) -> None:
    folder = job_dir(job_id)
    with LOCK:
        job = read_job(job_id)
        if not job:
            ACTIVE.discard(job_id)
            return
        job["status"] = "running"
        job["updated_at"] = now()
        write_job(job)
    try:
        manifest_lines = ["ffconcat version 1.0"]
        for index, (data, duration) in enumerate(frames):
            filename = f"frame_{index:03}.png"
            (folder / filename).write_bytes(data)
            manifest_lines.append(f"file '{filename}'")
            manifest_lines.append(f"duration {duration:.3f}")
        # Concat demuxer requires the last frame repeated to honor final duration.
        manifest_lines.append(f"file 'frame_{len(frames) - 1:03}.png'")
        (folder / "frames.ffconcat").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
        target = folder / "video.mp4"
        seconds = sum(item[1] for item in frames)
        cmd = [
            program, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-safe", "0", "-f", "concat", "-i", "frames.ffconcat",
            "-t", f"{seconds:.3f}",
            "-vf", "fps=30,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-movflags", "+faststart", "video.mp4",
        ]
        result = subprocess.run(
            cmd, cwd=folder, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=600, check=False,
        )
        if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            # Provider diagnostics may contain paths. Do not reflect raw stderr to the UI.
            raise VideoError("encoding_failed",
                             "FFmpeg не смог кодировать MP4. Проверьте наличие libx264 и корректность PNG.", 500)
        with LOCK:
            job = read_job(job_id)
            job["status"] = "succeeded"
            job["video_bytes"] = target.stat().st_size
            job["updated_at"] = now()
            write_job(job)
    except subprocess.TimeoutExpired:
        failed_job(job_id, "timeout", "FFmpeg превысил лимит времени кодирования.")
    except (VideoError, OSError) as exc:
        if isinstance(exc, VideoError):
            failed_job(job_id, exc.code, str(exc))
        else:
            failed_job(job_id, "disk_error", "Ошибка чтения или записи файлов MP4.")
    finally:
        # Once video is encoded, the temporary PNG frames are no longer needed.
        for frame in folder.glob("frame_*.png"):
            try:
                frame.unlink()
            except OSError:
                pass
        with LOCK:
            ACTIVE.discard(job_id)


def failed_job(job_id: str, code: str, message: str) -> None:
    with LOCK:
        job = read_job(job_id)
        if job:
            job["status"] = "failed"
            job["updated_at"] = now()
            job["error"] = {"code": code, "message": message}
            write_job(job)


def handle_post(handler, route: str) -> bool:
    if route != "/api/carousel-video/render":
        return False
    try:
        try:
            length = int(handler.headers.get("Content-Length", "0"))
        except (ValueError, TypeError):
            raise VideoError("invalid_size", "Некорректный Content-Length.")
        if length < 2 or length > MAX_REQUEST_BYTES:
            raise VideoError("invalid_size", "Загрузка слишком большая (максимум 100 МБ).", 413)
        program = ffmpeg_binary()
        payload = json.loads(handler.rfile.read(length).decode("utf-8"))
        name, frames = validate_request(payload)
        with LOCK:
            if ACTIVE:
                raise VideoError("busy", "Другой видеоролик уже кодируется. Завершите предыдущую сборку.", 429)
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id, "carousel_id": name, "status": "queued",
                "created_at": now(), "updated_at": now(),
                "slides": len(frames), "duration_seconds": round(sum(x[1] for x in frames), 3),
            }
            write_job(job)
            ACTIVE.add(job_id)
        try:
            POOL.submit(render_job, job_id, frames, program)
        except RuntimeError:
            failed_job(job_id, "queue_error", "Не удалось запустить обработчик FFmpeg.")
            with LOCK:
                ACTIVE.discard(job_id)
            raise VideoError("queue_error", "Не удалось начать сборку.", 503)
        handler.json_response(202, {"job": job})
    except VideoError as exc:
        handler.json_response(exc.status, {"error": {"code": exc.code, "message": str(exc)}})
    except (json.JSONDecodeError, UnicodeError):
        handler.json_response(400, {"error": {"code": "invalid_json", "message": "Некорректный JSON."}})
    except OSError:
        handler.json_response(500, {"error": {"code": "disk_error", "message": "Ошибка записи задания на диск."}})
    return True


def handle_get(handler, parsed) -> bool:
    path = parsed.path
    if path == "/api/carousel-video/status":
        handler.json_response(200, {"ffmpeg_available": bool(shutil.which("ffmpeg") or (
            os.environ.get("CM_FFMPEG_PATH") and Path(os.environ["CM_FFMPEG_PATH"]).is_file()))})
        return True
    match = re.fullmatch(r"/api/carousel-video/jobs/([0-9a-f]{32})", path)
    if match:
        job = read_job(match[1])
        handler.json_response(200 if job else 404, {"job": job} if job else {"error": {"message": "Задача не найдена."}})
        return True
    match = re.fullmatch(r"/api/carousel-video/files/([0-9a-f]{32})/video", path)
    if match:
        job = read_job(match[1])
        if not job or job["status"] != "succeeded":
            handler.json_response(404, {"error": {"message": "Видео пока недоступно."}})
            return True
        file = job_dir(match[1]) / "video.mp4"
        if not file.is_file():
            handler.json_response(404, {"error": {"message": "Файл MP4 не найден."}})
            return True
        handler.send_response(200)
        handler.send_header("Content-Type", "video/mp4")
        handler.send_header("Content-Length", str(file.stat().st_size))
        handler.send_header("Cache-Control", "private, no-store")
        handler.send_header("Content-Disposition", 'inline; filename="carousel.mp4"')
        handler.end_headers()
        try:
            with file.open("rb") as stream:
                shutil.copyfileobj(stream, handler.wfile, length=256 * 1024)
        except (BrokenPipeError, ConnectionResetError):
            pass
        return True
    return False
