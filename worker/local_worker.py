#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""127.0.0.1-only service used by the Content Zavod Chrome extension.

The extension downloads the MP4 using Chrome, so no Instagram passwords,
cookies, or Instaloader logins are required by this service. This server only
opens MP4 files inside an InstagramArchive/<account>/ directory and writes
matching SRT/TXT/JSON files and an index.csv.
"""

import csv
import hashlib
import json
import os
import sys
import subprocess
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from openrouter_transcribe import BUDGET_USD, MODEL as OPENROUTER_MODEL, get_key, load_usage, transcribe_video, BudgetReached

HOST = "127.0.0.1"
PORT = 8765
MODEL_NAME = os.environ.get("CZ_WHISPER_MODEL", "small")
LANGUAGE = os.environ.get("CZ_LANGUAGE", "ru").strip() or None
ACCOUNT_RE = re.compile(r"[A-Za-z0-9._]{1,30}\Z")
CODE_RE = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")

jobs = {}
job_lock = threading.Lock()
model_lock = threading.Lock()
index_lock = threading.Lock()
model = None
pool = ThreadPoolExecutor(max_workers=1)


def safe_log(*parts, flush=False):
    """Print safely on legacy Windows console encodings (e.g. CP1252).

    Preserve readable Unicode on terminals that support it; escape unsupported
    characters instead of allowing logging to fail a media-processing job.
    """
    codec = getattr(sys.stdout, "encoding", None) or "utf-8"
    message = " ".join(str(part) for part in parts)
    message = message.encode(codec, errors="backslashreplace").decode(codec)
    print(message, flush=flush)


def update_job(job_id, **values):
    with job_lock:
        jobs[job_id].update(values)


def timestamp(seconds):
    ms = max(0, round(float(seconds) * 1000))
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def write_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def write_index(root):
    fields = ["account", "source_url", "shortcode", "video", "subtitles", "transcript", "metadata", "language", "subtitle_status", "saved_at_utc", "subtitle_engine", "subtitle_model", "openrouter_cost_usd"]
    rows = []
    for meta in root.glob("*/*.json"):
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            if "shortcode" in info and "video" in info:
                rows.append({k: info.get(k, "") for k in fields})
        except (ValueError, OSError):
            pass
    rows.sort(key=lambda x: (x["account"], x["shortcode"]))
    tmp = root / "index.csv.tmp"
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(root / "index.csv")


def validate_job(req):
    account = str(req.get("account") or "")
    shortcode = str(req.get("shortcode") or "")
    if not ACCOUNT_RE.fullmatch(account) or not CODE_RE.fullmatch(shortcode):
        raise ValueError("Неверный аккаунт или shortcode.")
    source = str(req.get("source_url") or "")
    parsed = urlparse(source)
    if parsed.scheme != "https" or parsed.hostname not in ("www.instagram.com", "instagram.com"):
        raise ValueError("Нужна корректная ссылка на публикацию Instagram.")
    source_code = re.fullmatch(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,})/?", parsed.path)
    if not source_code or source_code.group(1) != shortcode:
        raise ValueError("Shortcode файла не совпадает со ссылкой Instagram. Задание отклонено.")
    if req.get("verified_shortcode") != shortcode:
        raise ValueError("Расширение не подтвердило принадлежность MP4 этой публикации. Отменено.")
    if req.get("capture_method") not in ("og-verified", "schema-verified", "post-bound", "single-verified-player", "yt-dlp-exact"):
        raise ValueError("Неизвестный способ подтверждения источника MP4.")
    video = Path(str(req.get("video_path") or "")).expanduser().resolve()
    if video.suffix.lower() != ".mp4":
        raise ValueError("Обработчик принимает MP4-файлы.")
    if video.parent.name != account or video.parent.parent.name != "InstagramArchive" or video.stem != shortcode:
        raise ValueError("Видео должно находиться в InstagramArchive/<аккаунт>/<shortcode>.mp4.")
    if not video.is_file() or video.stat().st_size == 0:
        raise ValueError("Видео не найдено на диске или пустое.")
    if video.stat().st_size > 2_000_000_000:
        raise ValueError("Ограничение размера файла: 2 ГБ.")
    return video, account, shortcode, source



def inspect_media(path, expected):
    """Reject incomplete DASH media before making paid transcription calls."""
    path = Path(path)
    with path.open('rb') as handle:
        start = handle.read(32)
    if b'ftyp' not in start[:24]:
        if b'moof' in start[:24] or b'styp' in start[:24]:
            raise ValueError('Скачан DASH-фрагмент, а не целый MP4. Повторите загрузку обновлённым расширением.')
        raise ValueError('Файл не является корректным MP4 (нет начального заголовка ftyp).')
    try:
        import av
        with av.open(str(path)) as container:
            kinds = {stream.type for stream in container.streams}
            if expected not in kinds:
                raise ValueError(f'В {path.name} отсутствует дорожка {expected}.')
            present = set()
            for i, packet in enumerate(container.demux()):
                if packet.size > 0 and packet.stream.type in ('video', 'audio'):
                    present.add(packet.stream.type)
                if present == (kinds & {'video', 'audio'}) or i >= 600:
                    break
            if expected not in present:
                raise ValueError(f'В {path.name} нет медиаданных ({expected}).')
            return kinds
    except ValueError:
        raise
    except Exception as ex:
        raise ValueError(f'Файл {path.name} не читается как MP4: {str(ex)[:130]}') from ex


def prepare_video(video, audio):
    video_types = inspect_media(video, 'video')
    if 'audio' in video_types:
        if audio:
            try:
                audio.unlink(missing_ok=True)
            except OSError:
                pass
        return
    if not audio:
        raise ValueError('Видео скачано без звука (Instagram DASH). Нет звуковой дорожки для создания субтитров.')
    inspect_media(audio, 'audio')
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise ValueError('Для объединения видео и звука установите: python -m pip install imageio-ffmpeg') from exc
    merged = video.with_name(video.stem + '.cz_merge.mp4')
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
               '-i', str(video), '-i', str(audio),
               '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', '-movflags', '+faststart', str(merged)]
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if run.returncode != 0:
            raise ValueError('Не удалось объединить видео и звук: ' + (run.stderr.strip()[-250:] or 'ffmpeg error'))
        kinds = inspect_media(merged, 'video')
        if 'audio' not in kinds:
            raise ValueError('После сборки MP4 всё ещё отсутствует звук.')
        merged.replace(video)
        audio.unlink(missing_ok=True)
    finally:
        merged.unlink(missing_ok=True)


def validate_audio_path(req, video):
    value = str(req.get('audio_path') or '').strip()
    if not value:
        return None
    audio = Path(value).expanduser().resolve()
    if audio != video.with_name(video.stem + '.audio.mp4') or not audio.is_file():
        raise ValueError('Звуковая дорожка должна находиться рядом с MP4 и иметь суффикс .audio.mp4.')
    if audio.stat().st_size > 500_000_000:
        raise ValueError('Слишком большой аудиофайл.')
    return audio

def get_model():
    global model
    with model_lock:
        if model is None:
            # Model is downloaded automatically on the first transcription.
            safe_log(f"[Whisper] Загружаю модель: {MODEL_NAME}; первый запуск может быть дольше…", flush=True)
            from faster_whisper import WhisperModel
            model = WhisperModel(MODEL_NAME, device="auto", compute_type="int8")
        return model


def sha256_of_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_job(job_id, request):
    update_job(job_id, status="processing", phase="prepare", progress=None)
    try:
        video, account, code, source = validate_job(request)
        audio = validate_audio_path(request, video)
        # Never send an invalid/fragmented/silent file to a paid model.
        prepare_video(video, audio)
        video_hash = sha256_of_file(video)
        root = video.parent.parent
        srt, txt, meta = (video.with_suffix(ext) for ext in (".srt", ".txt", ".json"))
        previously = {}
        if meta.exists():
            try:
                previously = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        info = {
            "account": account,
            "profile_url": f"https://www.instagram.com/{account}/" if account != "instagram" else "",
            "shortcode": code,
            "source_url": source,
            "video_sha256": video_hash,
            "verified_shortcode": code,
            "video": str(video.relative_to(root)).replace("\\", "/"),
            "subtitles": str(srt.relative_to(root)).replace("\\", "/"),
            "transcript": str(txt.relative_to(root)).replace("\\", "/"),
            "metadata": str(meta.relative_to(root)).replace("\\", "/"),
            "description": str(request.get("description") or "")[:4000],
            "capture_method": str(request.get("capture_method") or "")[:100],
            "language": LANGUAGE or "auto",
            "saved_at_utc": previously.get("saved_at_utc") or datetime.now(timezone.utc).isoformat(),
            "subtitle_status": "processing",
            "subtitle_engine": str(request.get("engine") or "openrouter"),
            "subtitle_model": OPENROUTER_MODEL if request.get("engine") != "local" else MODEL_NAME,
            "openrouter_cost_usd": 0.0,
        }
        write_json(meta, info)
        # Subtitles must NEVER be reused after a new MP4 replaced an old one.
        # Earlier versions could download an unrelated video under this name.
        if (srt.exists() and txt.exists()
                and previously.get("video_sha256") == video_hash
                and previously.get("source_url") == source
                and previously.get("subtitle_status") in ("ok", "no_speech")):
            info["subtitle_status"] = previously["subtitle_status"]
            info["language"] = previously.get("language") or info["language"]
            info["subtitle_engine"] = previously.get("subtitle_engine") or info["subtitle_engine"]
            info["subtitle_model"] = previously.get("subtitle_model") or info["subtitle_model"]
            info["openrouter_cost_usd"] = previously.get("openrouter_cost_usd", 0.0)
        else:
            safe_log(f"[Обработка] @{account} / {code}", flush=True)
            update_job(job_id, phase="transcribe", progress=None)
            choice = str(request.get("engine") or "openrouter")
            if choice not in ("openrouter", "local"):
                choice = "openrouter"
            remote_cues = None
            if choice == "openrouter":
                prior_spent = load_usage(Path(__file__).parent)["spent_usd"]
                try:
                    remote_cues, remote_text, cost, count = transcribe_video(video, Path(__file__).parent, LANGUAGE)
                    info["subtitle_engine"] = "openrouter"
                    info["subtitle_model"] = OPENROUTER_MODEL
                    info["openrouter_cost_usd"] = round(cost, 8)
                    safe_log(f"[OpenRouter] {count} частей, стоимость ~${cost:.5f}", flush=True)
                except Exception as err:
                    info["openrouter_cost_usd"] = round(max(0.0, load_usage(Path(__file__).parent)["spent_usd"] - prior_spent), 8)
                    # An error after some successful chunks may already have incurred costs;
                    # the persistent usage file tracks completed requests.
                    safe_log(f"[OpenRouter] {str(err)[:190]}; переход на бесплатный Whisper.", flush=True)
                    info["openrouter_fallback_reason"] = str(err)[:200]
            if remote_cues is not None:
                subtitles = [f"{i}\n{timestamp(a)} --> {timestamp(b)}\n{t}\n" for i, (a,b,t) in enumerate(remote_cues, 1)]
                srt.write_text("\n".join(subtitles), encoding="utf-8-sig")
                txt.write_text(remote_text, encoding="utf-8")
                info["subtitle_status"] = "ok" if subtitles else "no_speech"
            else:
                engine = get_model()
                segments, result = engine.transcribe(str(video), language=LANGUAGE, beam_size=5, vad_filter=True)
                cues, utterances = [], []
                for seg in segments:
                    text = " ".join(seg.text.split())
                    if text and seg.end > seg.start:
                        cues.append(f"{len(cues)+1}\n{timestamp(seg.start)} --> {timestamp(seg.end)}\n{text}\n")
                        utterances.append(text)
                srt.write_text("\n".join(cues), encoding="utf-8-sig")
                txt.write_text("\n".join(utterances), encoding="utf-8")
                info["language"] = result.language
                info["subtitle_engine"] = "local_whisper" if choice == "local" else "local_whisper_fallback"
                info["subtitle_model"] = MODEL_NAME
                info["subtitle_status"] = "ok" if cues else "no_speech"
        write_json(meta, info)
        with index_lock:
            write_index(root)
        update_job(job_id, status="complete", phase="complete", progress=100, result=info["subtitle_status"])
        safe_log(f"[Готово] {srt.name} / {txt.name} / index.csv ({info['subtitle_status']})", flush=True)
    except Exception as exc:
        update_job(job_id, status="failed", phase="failed", progress=None, error=str(exc)[:500])
        safe_log(f"[Ошибка] {job_id}: {exc}", flush=True)
        # Preserve a failure record when possible.
        try:
            if "meta" in locals() and "info" in locals():
                info["subtitle_status"] = "error"
                info["error"] = str(exc)[:500]
                write_json(meta, info)
                with index_lock:
                    write_index(root)
        except Exception:
            pass


def validate_exact_request(req):
    account = str(req.get("account") or "")
    shortcode = str(req.get("shortcode") or "")
    source = str(req.get("source_url") or "")
    if not ACCOUNT_RE.fullmatch(account) or not CODE_RE.fullmatch(shortcode):
        raise ValueError("Некорректный аккаунт или shortcode.")
    parsed = urlparse(source)
    if parsed.scheme != "https" or parsed.hostname not in ("www.instagram.com", "instagram.com"):
        raise ValueError("Требуется ссылка Instagram.")
    match = re.fullmatch(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,})/?", parsed.path)
    if not match or match.group(1) != shortcode:
        raise ValueError("Shortcode не совпадает со ссылкой.")
    if req.get("engine", "openrouter") not in ("openrouter", "local"):
        raise ValueError("Некорректная модель субтитров.")
    return account, shortcode, source


def exact_download_job(job_id, request):
    """yt-dlp fallback. Verify extracted post identity BEFORE downloading/transcribing.

    Does not read Chrome cookies, passwords or the user's browser session.
    Instagram may deny this request; that is reported, never bypassed by a
    random media URL from a neighboring Reel.
    """
    temp_files = []
    try:
        account, shortcode, source = validate_exact_request(request)
        import yt_dlp
        import imageio_ffmpeg
        root = Path.home() / "Downloads" / "InstagramArchive" / account
        root.mkdir(parents=True, exist_ok=True)
        outfile = root / (shortcode + ".mp4")
        template = str(root / (shortcode + ".cz_exact.%(ext)s"))
        def report_download(progress):
            if progress.get("status") == "downloading":
                total = progress.get("total_bytes") or progress.get("total_bytes_estimate")
                done = progress.get("downloaded_bytes", 0)
                amount = min(100.0, round(done / total * 100, 1)) if total else None
                update_job(job_id, phase="download", progress=amount)
            elif progress.get("status") == "finished":
                update_job(job_id, phase="prepare", progress=None)
        settings = {
            "progress_hooks": [report_download],
            "outtmpl": template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "retries": 1,
            "fragment_retries": 1,
            "socket_timeout": 20,
            "format": "best[ext=mp4][acodec!=none]/bestvideo*+bestaudio/best",
            "merge_output_format": "mp4",
            "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        }
        safe_log(f"[Точная загрузка] {shortcode}: запрашиваю метаданные Instagram…", flush=True)
        update_job(job_id, status="processing", phase="prepare", progress=None)
        with yt_dlp.YoutubeDL(settings) as downloader:
            info = downloader.extract_info(source, download=False)
            if not isinstance(info, dict):
                raise ValueError("Instagram не отдал метаданные нужного видео.")
            extracted = info.get("display_id") or info.get("shortcode") or (info.get("id") if isinstance(info.get("id"), str) and not info["id"].isdigit() else None)
            page = str(info.get("webpage_url") or "")
            code_from_page = None
            if page:
                match = re.fullmatch(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,})/?", urlparse(page).path)
                code_from_page = match.group(1) if match else None
            if extracted and extracted != shortcode:
                raise ValueError("Загрузчик вернул shortcode ДРУГОЙ публикации — отклонено.")
            if code_from_page and code_from_page != shortcode:
                raise ValueError("Загрузчик вернул URL ДРУГОЙ публикации — отклонено.")
            if not (extracted == shortcode or code_from_page == shortcode):
                raise ValueError("Не удалось подтвердить shortcode через метаданные загрузчика.")
            downloader.process_ie_result(info, download=True)
        temp_files = list(root.glob(shortcode + ".cz_exact.*"))
        candidates = [x for x in temp_files if x.suffix.lower() == ".mp4" and x.is_file()]
        if len(candidates) != 1:
            raise ValueError("Загрузчик не создал один полный MP4. Проверьте ограничения Instagram.")
        prepared = candidates[0]
        kinds = inspect_media(prepared, "video")
        if "audio" not in kinds:
            raise ValueError("Точное видео скачалось без звука; не отправляю его на платную транскрибацию.")
        prepared.replace(outfile)
        verified_request = dict(request)
        verified_request.update(video_path=str(outfile),verified_shortcode=shortcode,capture_method="yt-dlp-exact",audio_path="")
        safe_log(f"[Точная загрузка] Подтверждён shortcode {shortcode}; MP4 со звуком получен.",flush=True)
        process_job(job_id, verified_request)
    except Exception as exc:
        update_job(job_id,status="failed",error=str(exc)[:500])
        safe_log(f"[Точная загрузка] Ошибка: {str(exc)[:250]}",flush=True)
    finally:
        for item in temp_files:
            try: item.unlink(missing_ok=True)
            except OSError: pass


class Handler(BaseHTTPRequestHandler):
    server_version = "ContentZavodLocal/1.0"

    def log_message(self, fmt, *args):
        # Keep login URLs, querystrings, and any arbitrary user input out of logs.
        if self.path in ("/health", "/status", "/process", "/download-exact"):
            return

    def _allowed_origin(self):
        origin = self.headers.get("Origin", "")
        return not origin or bool(re.fullmatch(r"chrome-extension://[a-p]{32}", origin))

    def _send(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin", "")
        if origin and self._allowed_origin():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-CZ-Extension")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self._allowed_origin():
            self._send(403, {"error": "Origin is not a Chrome extension."})
        else:
            self._send(200, {"ok": True})

    def do_GET(self):
        if not self._allowed_origin():
            self._send(403, {"error": "Origin is not a Chrome extension."})
            return
        if self.path in ("/health", "/status"):
            with job_lock:
                latest = list(jobs.values())[-300:]
                summary = {"queued": 0, "processing": 0, "complete": 0, "failed": 0}
                for j in latest:
                    summary[j["status"]] += 1
                snapshot = {j["job_id"]: {"status":j["status"],"error":j.get("error", ""),"result":j.get("result", "")} for j in latest}
            usage = load_usage(Path(__file__).parent)
            payload = {"ok": True, "model": OPENROUTER_MODEL if get_key(Path(__file__).parent) else MODEL_NAME,
                       "default_engine": "openrouter" if get_key(Path(__file__).parent) else "local",
                       "budget_usd": BUDGET_USD, "spent_usd": usage["spent_usd"], "jobs": summary}
            if self.path == "/status":
                payload["job_statuses"] = snapshot
            self._send(200, payload)
        else:
            self._send(404, {"error": "Not found"})

    def do_POST(self):
        if not self._allowed_origin() or self.headers.get("X-CZ-Extension") != "1":
            self._send(403, {"error": "Only the installed Chrome extension can submit a job."})
            return
        if self.path == "/download-exact":
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 2 or size > 20000:
                    raise ValueError("Invalid request size.")
                req = json.loads(self.rfile.read(size).decode("utf-8"))
                account, code, source = validate_exact_request(req)
                job_id = uuid.uuid4().hex
                with job_lock:
                    jobs[job_id] = {"job_id":job_id,"video_path":str(Path.home()/"Downloads"/"InstagramArchive"/account/(code+".mp4")),"status":"queued"}
                pool.submit(exact_download_job, job_id, req)
                self._send(202, {"job_id":job_id})
            except (ValueError, TypeError, UnicodeError) as exc:
                self._send(400, {"error":str(exc)})
            return
        if self.path != "/process":
            self._send(404, {"error": "Not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 2 or size > 20000:
                raise ValueError("Invalid request size.")
            req = json.loads(self.rfile.read(size).decode("utf-8"))
            if req.get("engine", "openrouter") not in ("openrouter", "local"):
                raise ValueError("Выберите OpenRouter или локальный Whisper.")
            video, account, code, source = validate_job(req)
            validate_audio_path(req, video)
        except (ValueError, OSError, UnicodeError, TypeError) as exc:
            self._send(400, {"error": str(exc)})
            return
        with job_lock:
            for old in reversed(list(jobs.values())):
                if old["video_path"] == str(video) and old["status"] in ("queued", "processing"):
                    self._send(202, {"job_id": old["job_id"], "already_queued": True})
                    return
            job_id = uuid.uuid4().hex
            jobs[job_id] = {"job_id": job_id, "video_path": str(video), "status":"queued"}
        pool.submit(process_job, job_id, req)
        self._send(202, {"job_id": job_id})


def main():
    safe_log("=" * 60)
    safe_log("Content Zavod / Instagram / Локальные субтитры")
    safe_log(f"Сервер: http://{HOST}:{PORT} (только этот компьютер)")
    enabled = bool(get_key(Path(__file__).parent))
    safe_log(f"Режим: {'OpenRouter ' + OPENROUTER_MODEL if enabled else 'Whisper ' + MODEL_NAME} | Язык: {LANGUAGE or 'авто'}")
    safe_log(f"OpenRouter: лимит учёта ${BUDGET_USD:.2f}, потрачено по отчётам ${load_usage(Path(__file__).parent)['spent_usd']:.5f}")
    safe_log("При ошибке платной модели используется бесплатный локальный Whisper.")
    safe_log("Не закрывайте окно, пока расширение обрабатывает видео.")
    safe_log("Резервный режим: yt-dlp (без передачи cookies Chrome).")
    safe_log("=" * 60, flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        safe_log("\nОстановка сервера.")
    finally:
        server.server_close()
        pool.shutdown(wait=False, cancel_futures=True)

if __name__ == "__main__":
    main()
