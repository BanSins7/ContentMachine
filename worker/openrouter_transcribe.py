"""OpenRouter speech-to-text with word timestamps, per-user local budget.

Only transmits decoded audio from the MP4 to OpenRouter, not Instagram cookies.
No network requests are made at import time. No audio is uploaded when mode=local.
"""
import base64
import io
import json
import os
import threading
import time
import wave
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://openrouter.ai/api/v1/audio/transcriptions"
MODEL = "microsoft/mai-transcribe-2"
ESTIMATED_USD_PER_HOUR = 0.10
BUDGET_USD = 1.00
CHUNK_SECONDS = 80
RATE = 16000
_lock = threading.Lock()


class BudgetReached(Exception):
    pass


def get_key(folder):
    """Read the single local OpenRouter key managed in ContentMachine Settings.

    No environment-variable override: changing the key in Settings must switch
    all platforms on their next transcription request without restarting.
    The secret is never stored in the Git repository or browser storage.
    """
    path = Path(folder) / "openrouter_key.txt"
    try:
        return path.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return ""


def load_usage(folder):
    path = Path(folder) / "openrouter_usage.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"spent_usd": float(data.get("spent_usd", 0)), "requests": int(data.get("requests", 0)), "audio_seconds": float(data.get("audio_seconds", 0)), "estimated": bool(data.get("estimated", False))}
    except (ValueError, OSError, TypeError):
        return {"spent_usd": 0.0, "requests": 0, "audio_seconds": 0.0, "estimated": False}


def save_usage(folder, data):
    path = Path(folder) / "openrouter_usage.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def estimate_cost(duration):
    # 20% conservative margin to keep future requests below the soft local cap.
    return max(0, duration) * ESTIMATED_USD_PER_HOUR / 3600 * 1.2


def budget_check(folder, seconds):
    with _lock:
        data = load_usage(folder)
        if data["spent_usd"] + estimate_cost(seconds) > BUDGET_USD:
            raise BudgetReached("Достигнут локальный лимит ~$1; переключаюсь на бесплатный Whisper.")


def budget_add(folder, seconds, reported_cost):
    with _lock:
        data = load_usage(folder)
        if reported_cost is not None:
            amount = max(0.0, float(reported_cost))
        else:
            amount = max(0, seconds) * ESTIMATED_USD_PER_HOUR / 3600
            data["estimated"] = True
        data["spent_usd"] = round(data["spent_usd"] + amount, 9)
        data["requests"] += 1
        data["audio_seconds"] = round(data["audio_seconds"] + seconds, 2)
        save_usage(folder, data)
    return amount


def wav_chunks(mp4_path, seconds=CHUNK_SECONDS):
    """Decode audio without ffmpeg.exe (PyAV comes with faster-whisper).

    Emits (offset_seconds, duration_seconds, PCM WAV bytes). Memory bounded
    to one 80-second 16 kHz mono audio chunk (~2.6 MB).
    """
    import av
    audio = av.open(str(mp4_path))
    try:
        streams = [s for s in audio.streams if s.type == "audio"]
        if not streams:
            return
        stream = streams[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        max_samples = round(seconds * RATE)
        data = bytearray()
        samples_seen = 0
        sample_in_chunk = 0

        def as_wav(payload):
            buff = io.BytesIO()
            with wave.open(buff, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(RATE)
                wf.writeframes(payload)
            return buff.getvalue()

        for packet in audio.demux(stream):
            for frame in packet.decode():
                for out in resampler.resample(frame):
                    pcm = out.to_ndarray().tobytes()
                    pos = 0
                    while pos < len(pcm):
                        available = max_samples - sample_in_chunk
                        take = min(available * 2, len(pcm) - pos)
                        data.extend(pcm[pos:pos+take])
                        pos += take
                        sample_in_chunk += take // 2
                        samples_seen += take // 2
                        if sample_in_chunk >= max_samples:
                            start = (samples_seen - sample_in_chunk) / RATE
                            yield start, sample_in_chunk / RATE, as_wav(data)
                            data.clear()
                            sample_in_chunk = 0
        for out in resampler.resample(None):
            pcm = out.to_ndarray().tobytes()
            pos = 0
            while pos < len(pcm):
                take = min((max_samples - sample_in_chunk)*2, len(pcm)-pos)
                data.extend(pcm[pos:pos+take])
                pos += take
                sample_in_chunk += take//2
                samples_seen += take//2
                if sample_in_chunk >= max_samples:
                    start = (samples_seen - sample_in_chunk)/RATE
                    yield start, sample_in_chunk/RATE, as_wav(data)
                    data.clear()
                    sample_in_chunk = 0
        if sample_in_chunk:
            start = (samples_seen - sample_in_chunk)/RATE
            yield start, sample_in_chunk/RATE, as_wav(data)
    finally:
        audio.close()


def request_transcription(wav_audio, key, language="ru"):
    payload = {
        "model": MODEL,
        "input_audio": {"data": base64.b64encode(wav_audio).decode("ascii"), "format": "wav"},
        "response_format": "verbose_json",
        "timestamp_granularities": ["word"],
    }
    if language:
        payload["language"] = language
    req = Request(API, data=json.dumps(payload).encode("utf-8"), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "Content Zavod Instagram Subtitles",
    })
    try:
        with urlopen(req, timeout=110) as res:
            result = json.loads(res.read().decode("utf-8"))
    except HTTPError as e:
        # Do not echo response headers or uploaded audio or the secret.
        try:
            body = json.loads(e.read(3000).decode("utf-8", errors="replace"))
            msg = body.get("error") or body.get("message") or ""
            if isinstance(msg, dict):
                msg = msg.get("message", "")
            msg = str(msg)[:220]
        except Exception:
            msg = ""
        raise RuntimeError(f"OpenRouter HTTP {e.code}: {msg}".strip()) from None
    except (URLError, TimeoutError) as e:
        raise RuntimeError(f"OpenRouter: ошибка соединения ({type(e).__name__}).") from None
    if not isinstance(result, dict) or not isinstance(result.get("text", ""), str):
        raise RuntimeError("OpenRouter вернул ответ без текста.")
    return result


def normalize_segments(data, offset=0.0):
    """Create readable 1-4 second captions using real word/segment timestamps."""
    words = data.get("words") or []
    result = []
    if words:
        current, start, end = [], None, None
        def flush():
            nonlocal current, start, end
            if current and start is not None and end is not None and end > start:
                result.append((start + offset, end + offset, " ".join(current)))
            current, start, end = [], None, None
        for w in words:
            if not isinstance(w, dict):
                continue
            txt = str(w.get("word") or w.get("text") or "").strip()
            try:
                a, b = float(w.get("start")), float(w.get("end"))
            except (ValueError, TypeError):
                continue
            if not txt or b <= a:
                continue
            if current and (b - start > 4 or sum(map(len, current)) + len(txt) > 63):
                flush()
            if start is None:
                start = a
            end = b
            current.append(txt)
            if txt.endswith((".", "!", "?", "…")) and b - start > 1.2:
                flush()
        flush()
    if not result:
        for seg in data.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            txt = str(seg.get("text") or "").strip()
            try:
                a, b = float(seg.get("start")), float(seg.get("end"))
            except (ValueError, TypeError):
                continue
            if txt and b > a:
                result.append((offset+a, offset+b, txt))
    return result


def transcribe_video(mp4_path, worker_folder, language="ru"):
    key = get_key(worker_folder)
    if not key or not key.startswith("sk-or-"):
        raise RuntimeError("OpenRouter API-ключ не найден. Проверьте worker/openrouter_key.txt.")
    cues, texts, spent_here, chunks = [], [], 0.0, 0
    for offset, duration, wav in wav_chunks(mp4_path):
        budget_check(worker_folder, duration)
        response = request_transcription(wav, key, language)
        usage = response.get("usage") or {}
        cost = usage.get("cost") if isinstance(usage, dict) else None
        spent_here += budget_add(worker_folder, duration, cost)
        chunks += 1
        text = str(response.get("text") or "").strip()
        if text:
            texts.append(text)
        cues.extend(normalize_segments(response, offset))
        if text and not cues:
            # Better no invented timecodes: ask fallback whisper when provider omits timings.
            raise RuntimeError("OpenRouter не вернул таймкоды для SRT; использую локальный Whisper.")
    if texts and not cues:
        raise RuntimeError("OpenRouter не вернул таймкоды для SRT; использую локальный Whisper.")
    return cues, "\n".join(texts), spent_here, chunks
