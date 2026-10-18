"""Strict URL, date and numeric validation. Never fetch user-provided URLs."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, parse_qs
from .domain import Candidate, ValidationResult

YT_ID = re.compile(r"[a-zA-Z0-9_-]{11}\Z")
TT_ID = re.compile(r"[0-9]{10,25}\Z")
IG_ID = re.compile(r"[a-zA-Z0-9_-]{5,80}\Z")
YT_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"}
TT_HOSTS = {"www.tiktok.com", "tiktok.com", "m.tiktok.com"}
IG_HOSTS = {"www.instagram.com", "instagram.com"}
METRICS = ("views", "likes", "comments", "shares", "saves")

class CandidateError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

def canonical_video_url(raw: str) -> tuple[str, str, str]:
    if not isinstance(raw, str) or len(raw) > 2048:
        raise CandidateError("invalid_url")
    try:
        p = urlsplit(raw.strip())
        host = (p.hostname or "").lower()
        if p.scheme != "https" or p.username is not None or p.password is not None or p.port is not None or p.fragment:
            raise CandidateError("invalid_url")
    except (ValueError, TypeError) as exc:
        raise CandidateError("invalid_url") from exc
    if host in YT_HOSTS:
        if host == "youtu.be":
            code = p.path.strip("/")
        elif p.path == "/watch":
            ids = parse_qs(p.query).get("v", [])
            code = ids[0] if len(ids) == 1 else ""
        else:
            match = re.fullmatch(r"/shorts/([a-zA-Z0-9_-]{11})/?", p.path)
            code = match.group(1) if match else ""
        if not YT_ID.fullmatch(code):
            raise CandidateError("invalid_video_id")
        return "youtube", code, "https://www.youtube.com/watch?v=" + code
    if host in TT_HOSTS:
        match = re.fullmatch(r"/@([a-zA-Z0-9._]{1,40})/video/([0-9]{10,25})/?", p.path)
        if not match:
            raise CandidateError("invalid_video_id")
        return "tiktok", match.group(2), "https://www.tiktok.com/@" + match.group(1) + "/video/" + match.group(2)
    if host in IG_HOSTS:
        match = re.fullmatch(r"/(reel|reels|p)/([a-zA-Z0-9_-]{5,80})/?", p.path)
        if not match or not IG_ID.fullmatch(match.group(2)):
            raise CandidateError("invalid_video_id")
        return "instagram", match.group(2), "https://www.instagram.com/" + ("reel" if match.group(1) != "p" else "p") + "/" + match.group(2) + "/"
    raise CandidateError("unsupported_host")

def datetime_utc(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 80:
        raise CandidateError("invalid_datetime")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError("naive datetime")
    except ValueError as exc:
        raise CandidateError("invalid_datetime") from exc
    dt = dt.astimezone(timezone.utc)
    if dt > datetime.now(timezone.utc) + timedelta(hours=24):
        raise CandidateError("future_publication")
    return dt

def optional_count(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > (2**63 - 1):
        raise CandidateError("invalid_metric")
    return value

def validate_candidate(row: dict) -> ValidationResult:
    if not isinstance(row, dict):
        return ValidationResult("rejected", None, ("invalid_record",))
    try:
        platform, video_id, url = canonical_video_url(row.get("url"))
        claimed_platform = row.get("platform")
        if claimed_platform not in (None, "") and claimed_platform != platform:
            raise CandidateError("platform_mismatch")
        title = row.get("title")
        if title is not None and (not isinstance(title, str) or len(title) > 500):
            raise CandidateError("invalid_title")
        dt = datetime_utc(row.get("published_at"))
        duration = row.get("duration_seconds")
        if duration is None or duration == "":
            duration = None
        else:
            if isinstance(duration, str):
                # CSV strings must be finite decimal numbers, not NaN/Infinity.
                if not re.fullmatch(r"(?:0|[1-9][0-9]{0,4})(?:\.[0-9]{1,3})?", duration):
                    raise CandidateError("invalid_duration")
                duration = float(duration)
            if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                    or not 0 <= duration <= 86400 or duration != duration):
                raise CandidateError("invalid_duration")
        counts = {k: optional_count(row.get(k)) for k in METRICS}
        observed = datetime_utc(row.get("observed_at"))
        if any(v is not None for v in counts.values()) and observed is None:
            raise CandidateError("missing_observation_time")
        candidate = Candidate(platform,video_id,url,title or None,dt,duration,None,
                              *(counts[k] for k in METRICS),observed)
        reasons = []
        if dt is None:
            reasons.append("missing_publication_date")
        if title is None or title == "":
            reasons.append("missing_title")
        return ValidationResult("partial" if reasons else "valid", candidate,tuple(reasons))
    except CandidateError as exc:
        return ValidationResult("rejected", None, (exc.code,))
