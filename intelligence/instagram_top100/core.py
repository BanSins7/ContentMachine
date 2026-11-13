"""Pure, offline-testable selection of an Instagram account's most viewed Reels.

Never rank by likes when view counts are missing, never infer a post date,
never silently treat a /p/ photo as a Reel, and never claim full coverage.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from urllib.parse import urlsplit

HANDLE = re.compile(r"[A-Za-z0-9._]{1,30}\Z")
CODE = re.compile(r"[A-Za-z0-9_-]{5,80}\Z")
POST_PATH = re.compile(r"/(reel|reels|p)/([A-Za-z0-9_-]{5,80})/?\Z")
COUNT_MAX = 2**63 - 1
DATE_FIELDS = ("date_posted", "taken_at", "published_at", "post_date", "create_time", "timestamp")
VIEW_FIELDS = ("views", "video_view_count", "video_play_count", "play_count", "view_count")


def username(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Имя аккаунта должно быть строкой.")
    value = value.strip()
    if value.startswith("https://"):
        parsed = urlsplit(value)
        if (parsed.hostname not in ("www.instagram.com", "instagram.com")
            or parsed.port or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
            raise ValueError("Неподдерживаемый URL Instagram.")
        value = parsed.path.strip("/")
    value = value.lstrip("@").rstrip("/")
    if (not HANDLE.fullmatch(value) or value.startswith(".")
        or value.endswith(".") or ".." in value
        or value.casefold() in ("reel", "reels", "p", "explore", "stories")):
        raise ValueError("Некорректный Instagram username: " + value[:40])
    return value.casefold()


def usernames(value: str | list[str]) -> list[str]:
    parts = re.split(r"[\s,;]+", value.strip()) if isinstance(value, str) else value
    if not isinstance(parts, list) or not parts:
        raise ValueError("Вставь хотя бы один Instagram username.")
    result = list(dict.fromkeys(username(x) for x in parts if isinstance(x, str) and x.strip()))
    if not result or len(result) > 5000:
        raise ValueError("Допустимо от 1 до 5000 уникальных аккаунтов за один запуск.")
    return result


def parse_date(raw: object) -> datetime | None:
    if type(raw) in (float, int) and 0 <= raw <= 10**13:
        try:
            return datetime.fromtimestamp(raw / 1000 if raw > 10**11 else raw, timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%m-%d-%Y", "%Y/%m/%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def counter(raw: object) -> int | None:
    if type(raw) is int and 0 <= raw < COUNT_MAX:
        return raw
    if isinstance(raw, str) and re.fullmatch(r"[0-9]{1,18}", raw):
        value = int(raw)
        return value if value < COUNT_MAX else None
    return None


def _profile_handles(raw: dict) -> set[str]:
    identities = []
    for key in ("user_posted", "username", "author_username", "owner", "user", "account", "author"):
        value = raw.get(key)
        if isinstance(value, dict):
            value = value.get("username") or value.get("user_name") or value.get("account")
        if isinstance(value, str):
            identities.append(value)
    collaborators = raw.get("coauthor_producers") or raw.get("collaborators")
    if isinstance(collaborators, list):
        for value in collaborators:
            if isinstance(value, dict):
                value = value.get("username") or value.get("user_name")
            if isinstance(value, str):
                identities.append(value)
    handles = set()
    for ident in identities:
        try:
            handles.add(username(ident))
        except ValueError:
            # Some providers put public account URLs under the same field.
            continue
    return handles


def _post_identity(raw: dict) -> tuple[str, str] | None:
    for key in ("url", "post_url", "link", "reel_url", "video_url"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.startswith("https://"):
            continue
        parsed = urlsplit(value)
        if parsed.hostname not in ("www.instagram.com", "instagram.com") or parsed.port:
            continue
        match = POST_PATH.fullmatch(parsed.path)
        if not match:
            continue
        kind, code = match.groups()
        if not CODE.fullmatch(code):
            continue
        signals = [str(raw.get(k) or "").casefold() for k in ("post_type", "content_type", "type", "media_type")]
        proven_video = (kind in ("reel", "reels") or raw.get("is_video") is True
                        or any(x in ("reel", "reels", "video", "clip") for x in signals))
        if not proven_video:
            continue
        return code, "https://www.instagram.com/" + ("reel" if kind in ("reel", "reels") else "p") + "/" + code + "/"
    return None


def select_reels(handle: str, records: list[dict], since: date, until: date,
                 *, limit: int = 100) -> dict:
    """Return grounded top-N strictly within verified author, dates and view counts."""
    handle = username(handle)
    if not isinstance(records, list) or not 1 <= limit <= 100 or until < since:
        raise ValueError("Некорректная выборка или диапазон дат.")
    start = datetime.combine(since, time.min, timezone.utc)
    finish = datetime.combine(until, time.max, timezone.utc)
    rejected = {"invalid_record": 0, "not_reel": 0, "unverified_author": 0,
                "missing_date": 0, "outside_period": 0, "missing_views": 0,
                "conflicting_duplicate": 0}
    seen: dict[str, dict | None] = {}
    for row in records:
        if not isinstance(row, dict) or row.get("error") or row.get("error_code"):
            rejected["invalid_record"] += 1
            continue
        identity = _post_identity(row)
        if not identity:
            rejected["not_reel"] += 1
            continue
        code, url = identity
        if handle not in _profile_handles(row):
            rejected["unverified_author"] += 1
            continue
        published = next((parsed for field in DATE_FIELDS
                          if (parsed := parse_date(row.get(field))) is not None), None)
        if published is None:
            rejected["missing_date"] += 1
            continue
        if not start <= published <= finish:
            rejected["outside_period"] += 1
            continue
        views = next((number for field in VIEW_FIELDS
                      if (number := counter(row.get(field))) is not None), None)
        if views is None:
            rejected["missing_views"] += 1
            continue
        post = {"shortcode": code, "url": url, "views": views,
                "published_at": published.isoformat()}
        if code in seen:
            # Two different observations in a single provider snapshot are ambiguous.
            if seen[code] != post:
                seen[code] = None
                rejected["conflicting_duplicate"] += 1
            continue
        seen[code] = post
    ranked = sorted((x for x in seen.values() if x is not None),
                    key=lambda x: (-x["views"], x["published_at"], x["shortcode"]))
    return {"username": handle, "period_start": since.isoformat(),
            "period_end": until.isoformat(), "provider_records": len(records),
            "verified_ranked": len(ranked), "selected": min(limit, len(ranked)),
            "ranking_scope": "provider_returned_records_only",
            "coverage_complete": False, "rejected": rejected, "posts": ranked[:limit]}
