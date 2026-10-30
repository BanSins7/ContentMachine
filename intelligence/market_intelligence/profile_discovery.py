"""Public profile publication discovery for user-supplied Market Intelligence seeds.

Uses existing yt-dlp dependency and platform playlist/identity strategies from
the local downloader. The output is VERIFIED URL IDENTITIES ONLY, not media,
statistics or an assertion about all profile posts. No auth/cookie bypass.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit
from intelligence.discovery.normalize import CandidateError, canonical_video_url
from intelligence.project_sources.normalize import ReferenceError, normalize_url

MAX_POSTS = 100


class ListingUnavailable(Exception):
    pass


def canonical_profile(raw: str) -> tuple[str, str]:
    try:
        ref = normalize_url(raw, "competitor_account")
    except ReferenceError as exc:
        raise ListingUnavailable(str(exc)) from exc
    if ref.link_kind != "account" or ref.platform not in ("tiktok", "instagram", "youtube"):
        raise ListingUnavailable("Нужен TikTok, Instagram или YouTube аккаунт.")
    return ref.platform, ref.canonical_url


def _from_entry(platform: str, profile_url: str, entry: dict) -> str | None:
    if not isinstance(entry, dict):
        return None
    candidates = []
    for key in ("webpage_url", "url"):
        value = entry.get(key)
        if isinstance(value, str) and value.startswith("https://"):
            candidates.append(value)
    code = str(entry.get("id") or "")
    if platform == "youtube" and re.fullmatch(r"[A-Za-z0-9_-]{11}", code):
        candidates.append("https://www.youtube.com/shorts/" + code)
    elif platform == "tiktok" and re.fullmatch(r"[0-9]{10,25}", code):
        handle = urlsplit(profile_url).path.strip("/")
        candidates.append("https://www.tiktok.com/" + handle + "/video/" + code)
    elif platform == "instagram":
        shortcode = str(entry.get("display_id") or "")
        if re.fullmatch(r"[A-Za-z0-9_-]{5,80}", shortcode):
            candidates.append("https://www.instagram.com/reel/" + shortcode + "/")
    for candidate in candidates:
        try:
            got, native, canonical = canonical_video_url(candidate)
        except CandidateError:
            continue
        if got != platform or (code and platform != "instagram" and
                              re.fullmatch(r"[A-Za-z0-9_-]{11}|[0-9]{10,25}",code) and native != code):
            continue
        return canonical
    return None


def parse_listing(platform: str, profile_url: str, info: object, limit: int = MAX_POSTS) -> dict:
    """Accept no made-up entries; require a real playlist and canonical IDs."""
    if platform not in ("tiktok", "instagram", "youtube"):
        raise ListingUnavailable("Неизвестная платформа.")
    if not 1 <= limit <= MAX_POSTS:
        raise ListingUnavailable("Неверный лимит публикаций.")
    if not isinstance(info, dict) or not isinstance(info.get("entries"), list):
        raise ListingUnavailable("Платформа не отдала список публикаций профиля.")
    seen, urls = set(), []
    for entry in info["entries"]:
        url = _from_entry(platform, profile_url, entry)
        if url is None or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    if not urls:
        raise ListingUnavailable("Нет подтверждённых ссылок на публикации. Профиль может быть закрыт или ограничен.")
    return {"urls": urls, "verified_count": len(urls),
            "capped": len(urls) >= limit,
            "completeness": "unknown_platform_may_limit_listing"}


def enumerate_profile(profile_url: str, *, limit: int = MAX_POSTS) -> dict:
    """Reuse yt-dlp already used by TikTok/Instagram/YouTube downloaders.

    The call can fail on platform anti-bot/login; surface that failure, don't
    read browser secrets, rotate identities, or fabricate a success.
    """
    if not 1 <= limit <= MAX_POSTS:
        raise ListingUnavailable("Неверный лимит.")
    platform, canonical = canonical_profile(profile_url)
    target = canonical
    if platform == "youtube":
        target = canonical + "/shorts"
    try:
        import yt_dlp
        options = {
            "quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": "in_playlist", "playlistend": limit,
            "socket_timeout": 25, "retries": 1, "ignoreerrors": True,
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(target, download=False)
    except Exception as exc:
        raise ListingUnavailable("Не удалось получить публикации из профиля: " + str(exc)[:240]) from exc
    return {"platform": platform, "profile_url": canonical,
            **parse_listing(platform, canonical, info, limit)}
