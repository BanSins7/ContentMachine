"""ContentMachine YouTube Shorts adapter.

Integrated from Content Zavod Shorts v0.1.10: exact URL parsing, /shorts
channel discovery, explicit browser authorization, yt-dlp FFmpeg pipeline,
MP4/SRT/TXT/JSON output and partial subtitle failure recovery.
Uses shared worker OpenRouter billing / Whisper so no duplicate API key
or separate localhost server is necessary.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import local_worker as w
from openrouter_transcribe import MODEL, get_key, load_usage, transcribe_video

ARCHIVE = Path.home() / "Downloads" / "YouTubeArchive"
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
CHANNEL = re.compile(r"@[A-Za-z0-9._-]{2,100}\Z")
UC_ID = re.compile(r"[A-Za-z0-9_-]{3,120}\Z")
HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
         "youtu.be", "www.youtu.be"}
COOKIE_BROWSERS = {"none", "chrome", "edge", "firefox", "brave"}
CAP = 500


def parse_link(raw: str):
    """Return ('video' or 'channel', canonical YouTube URL, 11-character video ID or None)."""
    raw = str(raw).strip()
    if raw.startswith("@"):
        raw = "https://www.youtube.com/" + raw
    elif raw.startswith(("youtube.com/", "www.youtube.com/", "youtu.be/")):
        raw = "https://" + raw
    url = urlparse(raw)
    if (url.scheme != "https" or url.hostname not in HOSTS
            or url.port or url.username or url.password):
        raise ValueError("Нужна HTTPS-ссылка на YouTube Shorts или канал.")
    parts = [x for x in url.path.split("/") if x]
    short_id = None
    if url.hostname in ("youtu.be", "www.youtu.be") and len(parts) == 1:
        short_id = parts[0]
    elif len(parts) == 2 and parts[0] in ("shorts", "live", "embed"):
        short_id = parts[1]
    elif parts and parts[0] == "watch":
        short_id = parse_qs(url.query).get("v", [""])[0]
    if short_id is not None:
        if not VIDEO_ID.fullmatch(short_id):
            raise ValueError("Неверный ID YouTube-видео.")
        return "video", f"https://www.youtube.com/shorts/{short_id}", short_id
    if url.hostname in ("youtu.be", "www.youtu.be"):
        raise ValueError("Нужна ссылка на YouTube-видео.")
    if parts and CHANNEL.fullmatch(parts[0]):
        if len(parts) not in (1, 2) or (len(parts) == 2 and parts[1] != "shorts"):
            raise ValueError("Нужна ссылка на канал или его раздел Shorts.")
        return "channel", f"https://www.youtube.com/{parts[0]}/shorts", None
    if len(parts) in (2, 3) and parts[0] in ("channel", "user", "c"):
        if not UC_ID.fullmatch(parts[1]) or (len(parts) == 3 and parts[2] != "shorts"):
            raise ValueError("Некорректная ссылка на YouTube-канал.")
        return "channel", f"https://www.youtube.com/{parts[0]}/{parts[1]}/shorts", None
    raise ValueError("Используй youtube.com/shorts/ID или youtube.com/@channel.")


def cookie_opts(browser="none"):
    if browser not in COOKIE_BROWSERS:
        raise ValueError("Браузер YouTube: none, chrome, edge, firefox или brave.")
    return {"cookiesfrombrowser": (browser,)} if browser != "none" else {}


def error_hint(exc: Exception, browser="none"):
    msg = str(exc)
    low = msg.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        if browser == "none":
            return "YouTube запросил вход. Выбери браузер с активной сессией YouTube и повтори."
        return "YouTube не подтвердил доступ через браузер. Проверь вход в YouTube; можно попробовать Firefox."
    if "cookie" in low and any(x in low for x in ("decrypt", "copy", "permission", "access")):
        return "Не удалось прочитать cookies браузера. Попробуй Firefox или закрой Chrome и повтори."
    return msg[:350]


def formats():
    """FFmpeg from the existing ContentMachine installation, no new setup."""
    import imageio_ffmpeg
    return {
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
    }


def _entry_url(entry):
    if not isinstance(entry, dict):
        return None
    vid = str(entry.get("id") or "")
    if VIDEO_ID.fullmatch(vid):
        return f"https://www.youtube.com/shorts/{vid}"
    for field in ("webpage_url", "url"):
        try:
            kind, url, _ = parse_link(str(entry.get(field) or ""))
            if kind == "video":
                return url
        except ValueError:
            pass
    return None


def discover_channel(job_id, channel_url, update_status, browser="none", limit=0):
    """Enumerate video IDs from the channel /shorts listing; does not download."""
    try:
        import yt_dlp
        kind, canonical, _ = parse_link(channel_url)
        if kind != "channel":
            raise ValueError("Для поиска нужен YouTube-канал.")
        options = {
            "quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": "in_playlist", "ignoreerrors": True,
            "playlistend": min(CAP, limit) if limit else CAP, "socket_timeout": 25, "retries": 1,
            **cookie_opts(browser)
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            result = ydl.extract_info(canonical, download=False)
        if not isinstance(result, dict) or "entries" not in result:
            raise ValueError("YouTube не отдал список Shorts этого канала.")
        found, seen = [], set()
        for entry in result.get("entries") or []:
            link = _entry_url(entry)
            if not link or link in seen:
                continue
            seen.add(link)
            found.append(link)
            update_status(job_id, count=len(found))
            if limit and len(found) >= limit:
                break
        if not found:
            raise ValueError("Не удалось найти Shorts. Проверь адрес канала и доступ YouTube.")
        update_status(job_id, status="complete", count=len(found), urls=found)
    except Exception as exc:
        update_status(job_id, status="failed", error=error_hint(exc, browser))


def library_items():
    rows = []
    for meta in ARCHIVE.glob("*/*/metadata.json"):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            video = meta.with_name("video.mp4")
            vid = str(data.get("id") or "")
            if not VIDEO_ID.fullmatch(vid) or meta.parent.name != vid or not video.is_file():
                continue
            rows.append({
                "id": vid, "source_url": data.get("source_url", ""),
                "date": data.get("downloaded_at", ""),
                "status": data.get("subtitle_source", ""),
                "openrouter_cost_usd": float(data.get("openrouter_cost_usd") or 0),
            })
        except (OSError, ValueError, TypeError):
            pass
    return rows


def _cancelled(job_id):
    with w.job_lock:
        return bool(w.jobs.get(job_id, {}).get("cancel_requested"))


class CancelledByUser(Exception):
    pass


def _check_cancel(job_id):
    if _cancelled(job_id):
        raise CancelledByUser("Остановлено пользователем.")


def _write_subtitles(video, folder, mode, source_url, video_hash, previous, meta,
                     language="auto", job_id=None):
    """Keep MP4 after ASR failure; language/mode changes reprocess existing MP4."""
    srt, txt = folder / "subtitles.srt", folder / "transcript.txt"
    mode = mode if mode in ("openrouter", "local", "off") else "openrouter"
    if language not in ("auto", "ru", "en"):
        raise ValueError("Язык речи должен быть auto, ru или en.")
    hint = None if language == "auto" else language
    if job_id:
        _check_cancel(job_id)
    already = (previous.get("video_sha256") == video_hash
               and previous.get("source_url") == source_url
               and previous.get("subtitle_requested") == mode
               and previous.get("subtitle_language_requested") == language
               and previous.get("subtitle_source") in ("openrouter_mai", "whisper", "no_speech", "off")
               and srt.exists() and txt.exists())
    if already:
        meta.update({
            "subtitle_source": previous.get("subtitle_source"),
            "subtitle_language": previous.get("subtitle_language", "auto"),
            "openrouter_cost_usd": float(previous.get("openrouter_cost_usd") or 0),
            "subtitle_requested": mode,
            "subtitle_language_requested": language,
        })
        return False
    meta["subtitle_requested"] = mode
    meta["subtitle_language_requested"] = language
    meta["subtitle_source"] = "asr_failed"
    meta["subtitle_language"] = language
    meta["openrouter_cost_usd"] = 0.0
    meta.pop("asr_error", None)
    if mode == "off":
        srt.write_text("", encoding="utf-8")
        txt.write_text("", encoding="utf-8")
        meta["subtitle_source"] = "off"
        return True
    remote = None
    if mode == "openrouter" and get_key(Path(w.__file__).parent):
        start_spent = load_usage(Path(w.__file__).parent)["spent_usd"]
        try:
            remote, text, cost, _ = transcribe_video(video, Path(w.__file__).parent, hint)
            meta["openrouter_cost_usd"] = round(cost, 8)
        except Exception as exc:
            meta["openrouter_cost_usd"] = round(max(
                0, load_usage(Path(w.__file__).parent)["spent_usd"] - start_spent), 8)
            meta["asr_error"] = str(exc)[:230]
    if job_id:
        _check_cancel(job_id)
    if remote is not None:
        cues = [f"{i}\n{w.timestamp(a)} --> {w.timestamp(b)}\n{line}\n"
                for i, (a, b, line) in enumerate(remote, 1)]
        srt.write_text("\n".join(cues), encoding="utf-8-sig")
        txt.write_text(text, encoding="utf-8")
        meta["subtitle_source"] = "openrouter_mai" if cues else "no_speech"
        return True
    try:
        model = w.get_model()
        segments, info = model.transcribe(str(video), language=hint, beam_size=5, vad_filter=True)
        cues, words = [], []
        for seg in segments:
            if job_id:
                _check_cancel(job_id)
            value = " ".join(seg.text.split())
            if value and seg.end > seg.start:
                cues.append(f"{len(cues)+1}\n{w.timestamp(seg.start)} --> {w.timestamp(seg.end)}\n{value}\n")
                words.append(value)
        srt.write_text("\n".join(cues), encoding="utf-8-sig")
        txt.write_text("\n".join(words), encoding="utf-8")
        meta["subtitle_source"] = "whisper" if cues else "no_speech"
        meta["subtitle_language"] = info.language
    except CancelledByUser:
        raise
    except Exception as exc:
        meta["asr_error"] = str(exc)[:230]
        srt.write_text("", encoding="utf-8")
        txt.write_text("", encoding="utf-8")
    return True


def download_job(job_id, req):
    """Exact Short -> YouTubeArchive/single/ID/{video.mp4,subtitles.srt,transcript.txt,metadata.json}."""
    temporary = []
    browser = req.get("cookie_browser", "none")
    language = req.get("language", "auto")
    mode = req.get("engine", "openrouter")
    try:
        _check_cancel(job_id)
        import yt_dlp
        kind, canonical, vid = parse_link(req.get("source_url", ""))
        if kind != "video" or vid != req.get("video_id"):
            raise ValueError("ID YouTube-видео не совпадает с заданием.")
        cookie_opts(browser)
        folder = ARCHIVE / "single" / vid
        folder.mkdir(parents=True, exist_ok=True)
        movie = folder / "video.mp4"
        metadata = folder / "metadata.json"
        old = {}
        if metadata.exists():
            try: old = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, ValueError): pass
        if movie.exists() and old.get("source_url") == canonical and old.get("id") == vid:
            w.inspect_media(movie, "video")
            digest = w.sha256_of_file(movie)
            w.update_job(job_id, status="processing", phase="transcribe", progress=None)
            changed = _write_subtitles(movie, folder, mode, canonical, digest, old, old, language, job_id)
            old["video_sha256"] = digest
            metadata.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
            _check_cancel(job_id)
            if old.get("subtitle_source") == "asr_failed":
                w.update_job(job_id, status="failed", phase="failed",
                             error="MP4 сохранён, но субтитры не создались: "+str(old.get("asr_error") or "")[:200])
            else:
                w.update_job(job_id, status="complete", phase="complete", progress=100,
                             result="reprocessed" if changed else "skipped")
            return

        def hook(data):
            _check_cancel(job_id)
            if data.get("status") == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                downloaded = data.get("downloaded_bytes", 0)
                w.update_job(job_id, phase="download",
                             progress=round(downloaded / total * 100, 1) if total else None)
            elif data.get("status") == "finished":
                w.update_job(job_id, phase="prepare", progress=None)

        options = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "retries": 2, "fragment_retries": 2, "socket_timeout": 25,
            "outtmpl": str(folder / "source.%(ext)s"),
            "progress_hooks": [hook],
            **formats(), **cookie_opts(browser),
        }
        w.update_job(job_id, status="processing", phase="prepare", progress=None)
        with yt_dlp.YoutubeDL(options) as ydl:
            _check_cancel(job_id)
            info = ydl.extract_info(canonical, download=False)
            if not isinstance(info, dict) or str(info.get("id") or "") != vid:
                raise ValueError("YouTube вернул другое видео. Загрузка отменена.")
            _check_cancel(job_id)
            actual = ydl.process_ie_result(info, download=True)
        _check_cancel(job_id)
        if not isinstance(actual, dict) or str(actual.get("id") or vid) != vid:
            raise ValueError("YouTube не подтвердил ID скачанного Short.")
        candidates = list(folder.glob("source*.mp4"))
        if len(candidates) != 1:
            raise ValueError("Загрузчик не создал один MP4. Проверь FFmpeg и доступ к ролику.")
        temporary = candidates
        media = candidates[0]
        tracks = w.inspect_media(media, "video")
        if "audio" not in tracks:
            raise ValueError("Скачанный Short без звука.")
        media.replace(movie)
        info = {
            "id": vid, "source_url": canonical, "channel": actual.get("channel", ""),
            "channel_id": actual.get("channel_id", ""), "uploader": actual.get("uploader", ""),
            "title": actual.get("title", ""), "duration": actual.get("duration"),
            "description": str(actual.get("description") or "")[:4000],
            "view_count": actual.get("view_count"), "like_count": actual.get("like_count"),
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "video_sha256": w.sha256_of_file(movie),
            "subtitle_source": "processing", "subtitle_language": "auto",
            "model": MODEL, "openrouter_cost_usd": 0.0,
        }
        metadata.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        w.update_job(job_id, phase="transcribe", progress=None)
        _write_subtitles(movie, folder, mode, canonical, info["video_sha256"], old, info, language, job_id)
        metadata.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        _check_cancel(job_id)
        if info.get("subtitle_source") == "asr_failed":
            w.update_job(job_id, status="failed", phase="failed",
                         error="MP4 сохранён, но субтитры не создались: "+str(info.get("asr_error") or "")[:200])
        else:
            w.update_job(job_id, status="complete", phase="complete", progress=100)
    except CancelledByUser:
        w.update_job(job_id, status="cancelled", phase="cancelled", progress=None, result="cancelled")
    except Exception as exc:
        # yt-dlp may wrap progress-hook cancellation in DownloadError.
        if _cancelled(job_id):
            w.update_job(job_id, status="cancelled", phase="cancelled",
                         progress=None, result="cancelled")
        else:
            w.update_job(job_id, status="failed", phase="failed", progress=None,
                         error=error_hint(exc, browser))
    finally:
        for file in temporary:
            try: file.unlink(missing_ok=True)
            except OSError: pass
