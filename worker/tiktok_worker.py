"""TikTok download + subtitles using the shared ContentMachine worker queue.

Supports public canonical TikTok video/profile URLs only. Never guesses a video
from unrelated network media: yt-dlp's extracted ID must match the requested ID.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# TikTok has intermittently returned a challenge page to non-browser requests.
# A normal browser User-Agent is documented as a workaround by yt-dlp users.
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
EDGE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

import local_worker as w
from openrouter_transcribe import MODEL, get_key, load_usage, transcribe_video

ARCHIVE = Path.home() / "Downloads" / "TikTokArchive"
USER_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._]{1,31}\Z")
VIDEO_RE = re.compile(r"[1-9][0-9]{10,24}\Z")
VIDEO_PATH = re.compile(r"/@([A-Za-z0-9_][A-Za-z0-9._]{1,31})/video/([1-9][0-9]{10,24})/?\Z")
PROFILE_PATH = re.compile(r"/@([A-Za-z0-9_][A-Za-z0-9._]{1,31})/?\Z")


def _error_text(exc):
    return _ANSI.sub("", str(exc))


def _is_challenge(exc):
    message = _error_text(exc).lower()
    return any(x in message for x in (
        "unexpected response from webpage request",
        "unable to extract universal data for rehydration",
        "captcha", "verify you are human", "please wait a few seconds",
    ))


def _friendly_error(exc):
    if _is_challenge(exc):
        return ("TikTok не отдал данные видео (проверка доступа). Мы попробовали "
                "два браузерных профиля. Открой видео в обычном браузере; "
                "если там оно доступно, повтори позже. TikTok может временно "
                "блокировать автоматические загрузки.")
    return _error_text(exc)[:350]


def _browser_opts(base, agent):
    return {**base, "http_headers": {
        **base.get("http_headers", {}),
        "User-Agent": agent,
        "Accept-Language": "en-US,en;q=0.9",
    }}


def _extract_verified(url, opts, verify):
    """Two bounded browser-like attempts. Never retry a video identity mismatch."""
    import yt_dlp
    last_error = None
    for attempt, agent in enumerate((CHROME_UA, EDGE_UA), 1):
        try:
            with yt_dlp.YoutubeDL(_browser_opts({k: v for k, v in opts.items() if k != "_download_verified"}, agent)) as downloader:
                info = downloader.extract_info(url, download=False)
                verify(info)
                # Verification must occur before writing any MP4.
                return downloader.process_ie_result(info, download=True) if opts.get("_download_verified") else info
        except yt_dlp.utils.DownloadError as exc:
            last_error = exc
            if not _is_challenge(exc) or attempt == 2:
                break
            w.safe_log("[TikTok] Access challenge: retrying with a second User-Agent.", flush=True)
    raise ValueError(_friendly_error(last_error)) from last_error


def parse_link(raw: str):
    value = str(raw).strip()
    if not value.startswith(("https://", "http://")):
        value = "https://" + value
    url = urlparse(value)
    if (url.scheme == "https" and url.hostname in ("vt.tiktok.com", "vm.tiktok.com")
            and not url.port and not url.username and not url.password
            and re.fullmatch(r"/[A-Za-z0-9_-]{5,70}/?", url.path)):
        return "short", None, None, f"https://{url.hostname}{url.path}"
    if (url.scheme != "https" or url.hostname not in ("tiktok.com", "www.tiktok.com", "m.tiktok.com")
            or url.port or url.username or url.password):
        raise ValueError("Нужна HTTPS-ссылка на TikTok (@аккаунт или видео).")
    video = VIDEO_PATH.fullmatch(url.path)
    if video:
        user, video_id = video.groups()
        return "video", user, video_id, f"https://www.tiktok.com/@{user}/video/{video_id}"
    profile = PROFILE_PATH.fullmatch(url.path)
    if profile:
        return "profile", profile.group(1), None, f"https://www.tiktok.com/@{profile.group(1)}"
    raise ValueError("Нужна ссылка вида https://www.tiktok.com/@user/video/1234567890123456789 или https://www.tiktok.com/@user")


def _require_video_info(value):
    if not isinstance(value, dict):
        raise ValueError("TikTok не отдал метаданные этого видео.")


def _require_playlist_info(value):
    if not isinstance(value, dict) or "entries" not in value:
        raise ValueError("TikTok не отдал список роликов. Возможно, требуется вход или профиль ограничен.")


def resolve_short(job_id: str, url: str, status_update):
    """Turn a short share link into a URL tied to one confirmed video ID."""
    try:
        data = _extract_verified(url, {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "socket_timeout": 25, "retries": 1,
        }, _require_video_info)
        ident = str(data.get("id") or "")
        if not VIDEO_RE.fullmatch(ident):
            raise ValueError("TikTok не подтвердил ID видео по короткой ссылке.")
        full = str(data.get("webpage_url") or "")
        try:
            kind, user, from_url, canonical = parse_link(full)
        except ValueError:
            author = str(data.get("uploader") or data.get("uploader_id") or "").lstrip("@")
            if not USER_RE.fullmatch(author):
                raise ValueError("Не удалось получить аккаунт автора по короткой ссылке.")
            canonical = f"https://www.tiktok.com/@{author}/video/{ident}"
        else:
            if kind != "video" or from_url != ident:
                raise ValueError("ID TikTok из короткой ссылки не совпадает с метаданными.")
        status_update(job_id, status="complete", count=1, urls=[canonical])
    except Exception as exc:
        status_update(job_id, status="failed", error=_friendly_error(exc))


def library_items():
    rows = []
    if not ARCHIVE.is_dir():
        return rows
    for meta in ARCHIVE.glob("*/*.json"):
        try:
            obj = json.loads(meta.read_text(encoding="utf-8"))
            account = str(obj.get("account", ""))
            code = str(obj.get("shortcode", ""))
            if not USER_RE.fullmatch(account) or not VIDEO_RE.fullmatch(code):
                continue
            movie = meta.with_suffix(".mp4")
            if not movie.is_file():
                continue
            rows.append({
                "account": account, "shortcode": code, "source_url": obj.get("source_url", ""),
                "date": obj.get("saved_at_utc", ""), "status": obj.get("subtitle_status", ""),
                "openrouter_cost_usd": obj.get("openrouter_cost_usd", 0.0),
            })
        except (OSError, ValueError, TypeError):
            pass
    rows.sort(key=lambda item: item["date"], reverse=True)
    return rows


def discover_profile(job_id: str, username: str, status_update):
    """Best-effort listing of public TikTok posts."""
    try:
        info = _extract_verified(f"https://www.tiktok.com/@{username}", {
            "quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
            "playlistend": 500, "socket_timeout": 25, "retries": 1,
            "ignoreerrors": True,
        }, _require_playlist_info)
        found, seen = [], set()
        for entry in info.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            candidate = str(entry.get("id") or "")
            link = str(entry.get("webpage_url") or entry.get("url") or "")
            if not VIDEO_RE.fullmatch(candidate):
                try:
                    kind, _, candidate, _ = parse_link(link)
                    if kind != "video":
                        continue
                except ValueError:
                    continue
            if candidate in seen:
                continue
            if link.startswith(("https://", "http://")):
                try:
                    kind, _, link_id, _ = parse_link(link)
                    if kind == "video" and link_id != candidate:
                        continue
                except ValueError:
                    pass
            seen.add(candidate)
            found.append(f"https://www.tiktok.com/@{username}/video/{candidate}")
            status_update(job_id, count=len(found))
        if not found:
            raise ValueError("Не нашёл подтверждённых видео этого TikTok-профиля. Попробуй отдельную ссылку.")
        status_update(job_id, status="complete", urls=found, count=len(found))
    except Exception as exc:
        status_update(job_id, status="failed", error=_friendly_error(exc))


def download_job(job_id: str, req: dict):
    """Download one verified TikTok post, then make same-stem SRT/TXT/JSON."""
    temporary = []
    try:
        kind, account, vid, source = parse_link(req["source_url"])
        if kind != "video" or account != req.get("account") or vid != req.get("shortcode"):
            raise ValueError("Идентификатор TikTok не совпал с заданием.")
        import yt_dlp
        import imageio_ffmpeg
        folder = ARCHIVE / account
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{vid}.mp4"
        temp_base = str(folder / (vid + ".cm_tiktok.%(ext)s"))
        w.update_job(job_id, status="processing", phase="prepare", progress=None)
        def on_progress(data):
            if data.get("status") == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                n = data.get("downloaded_bytes", 0)
                w.update_job(job_id, phase="download", progress=round(n / total * 100, 1) if total else None)
            elif data.get("status") == "finished":
                w.update_job(job_id, phase="prepare", progress=None)
        opts = {
            "quiet": True, "no_warnings": True, "noplaylist": True, "ignoreerrors": False,
            "retries": 1, "fragment_retries": 1, "socket_timeout": 30,
            "outtmpl": temp_base, "progress_hooks": [on_progress],
            "format": "best[ext=mp4][acodec!=none]/bestvideo*+bestaudio/best",
            "merge_output_format": "mp4", "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        }
        uploader = ""
        def check_identity(info):
            nonlocal uploader
            if not isinstance(info, dict):
                raise ValueError("TikTok не отдал метаданные этого видео.")
            returned = str(info.get("id") or "")
            page = str(info.get("webpage_url") or "")
            if returned != vid:
                raise ValueError("TikTok вернул ID другого видео. Скачивание остановлено.")
            if page:
                try:
                    page_kind, _, page_id, _ = parse_link(page)
                    if page_kind == "video" and page_id != vid:
                        raise ValueError("TikTok вернул адрес другого видео. Скачивание остановлено.")
                except ValueError as exc:
                    if "другого видео" in str(exc):
                        raise
            uploader = str(info.get("uploader_id") or info.get("channel_id") or "").lstrip("@")
        _extract_verified(source, {**opts, "_download_verified": True}, check_identity)
        temporary = list(folder.glob(vid + ".cm_tiktok.*"))
        candidates = [p for p in temporary if p.suffix.lower() == ".mp4" and p.is_file()]
        if len(candidates) != 1:
            raise ValueError("TikTok не создал один полноценный MP4.")
        types = w.inspect_media(candidates[0], "video")
        if "audio" not in types:
            raise ValueError("В скачанном видео нет аудиодорожки для субтитров.")
        candidates[0].replace(dest)
        digest = w.sha256_of_file(dest)
        srt, txt, meta = (dest.with_suffix(ext) for ext in (".srt", ".txt", ".json"))
        old = {}
        if meta.exists():
            try:
                old = json.loads(meta.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        details = {
            "platform": "tiktok", "account": account, "shortcode": vid,
            "source_url": source, "profile_url": f"https://www.tiktok.com/@{account}",
            "video_sha256": digest, "video": f"{account}/{vid}.mp4",
            "subtitles": f"{account}/{vid}.srt", "transcript": f"{account}/{vid}.txt",
            "metadata": f"{account}/{vid}.json", "language": "auto",
            "saved_at_utc": old.get("saved_at_utc") or datetime.now(timezone.utc).isoformat(),
            "subtitle_status": "processing", "subtitle_engine": req.get("engine", "local"),
            "subtitle_model": MODEL if req.get("engine") == "openrouter" else w.MODEL_NAME,
            "openrouter_cost_usd": 0.0, "uploader_id": uploader,
        }
        w.write_json(meta, details)
        if (old.get("video_sha256") == digest and old.get("source_url") == source
                and old.get("subtitle_status") in ("ok", "no_speech") and srt.exists() and txt.exists()):
            details["subtitle_status"] = old["subtitle_status"]
            details["subtitle_engine"] = old.get("subtitle_engine", "")
            details["subtitle_model"] = old.get("subtitle_model", "")
            details["openrouter_cost_usd"] = old.get("openrouter_cost_usd", 0)
        else:
            w.update_job(job_id, phase="transcribe", progress=None)
            cues = None
            if req.get("engine") == "openrouter" and get_key(Path(__file__).parent):
                start_spent = load_usage(Path(__file__).parent)["spent_usd"]
                try:
                    cues, content, cost, _ = transcribe_video(dest, Path(__file__).parent, None)
                    details["subtitle_engine"] = "openrouter"
                    details["subtitle_model"] = MODEL
                    details["openrouter_cost_usd"] = round(cost, 8)
                except Exception as exc:
                    details["openrouter_cost_usd"] = round(max(0, load_usage(Path(__file__).parent)["spent_usd"] - start_spent), 8)
                    details["openrouter_fallback_reason"] = str(exc)[:200]
                    w.safe_log("[TikTok/OpenRouter] fallback:", str(exc)[:160], flush=True)
            if cues is not None:
                srt.write_text("\n".join(f"{i}\n{w.timestamp(a)} --> {w.timestamp(b)}\n{line}\n"
                                         for i, (a,b,line) in enumerate(cues, 1)), encoding="utf-8-sig")
                txt.write_text(content, encoding="utf-8")
                details["subtitle_status"] = "ok" if cues else "no_speech"
            else:
                model = w.get_model()
                segments, result = model.transcribe(str(dest), language=None, beam_size=5, vad_filter=True)
                rows, lines = [], []
                for segment in segments:
                    line = " ".join(segment.text.split())
                    if line and segment.end > segment.start:
                        rows.append(f"{len(rows)+1}\n{w.timestamp(segment.start)} --> {w.timestamp(segment.end)}\n{line}\n")
                        lines.append(line)
                srt.write_text("\n".join(rows), encoding="utf-8-sig")
                txt.write_text("\n".join(lines), encoding="utf-8")
                details["language"] = result.language
                details["subtitle_engine"] = "local_whisper" if req.get("engine") == "local" else "local_whisper_fallback"
                details["subtitle_model"] = w.MODEL_NAME
                details["subtitle_status"] = "ok" if rows else "no_speech"
        w.write_json(meta, details)
        with w.index_lock:
            w.write_index(ARCHIVE)
        w.update_job(job_id, status="complete", phase="complete", progress=100)
        w.safe_log(f"[TikTok] Готово: @{account}/{vid}.mp4 + SRT", flush=True)
    except Exception as exc:
        w.update_job(job_id, status="failed", phase="failed", progress=None, error=_friendly_error(exc))
        w.safe_log("[TikTok] Ошибка:", _friendly_error(exc), flush=True)
        if "meta" in locals() and "details" in locals():
            try:
                details["subtitle_status"] = "error"
                details["error"] = str(exc)[:500]
                w.write_json(meta, details)
                with w.index_lock:
                    w.write_index(ARCHIVE)
            except OSError:
                pass
    finally:
        for file in temporary:
            try:
                file.unlink(missing_ok=True)
            except OSError:
                pass
