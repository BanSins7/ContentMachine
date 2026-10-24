"""Stage 02: offline-only URL canonicalization and legacy reference extraction.

This module NEVER resolves redirects, DNS, scrapes pages, or calls an LLM. Its
purpose is stable identity and separate account/video/short-link classification.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

KINDS = frozenset({"product_website", "competitor_website", "competitor_account",
                   "promoter_account", "user_video_reference", "research_link", "other"})
LEGACY_FIELDS = {
    "product_url": "product_website",
    "competitors": "competitor_website",
    "competitor_socials": "competitor_account",
    "promoter_accounts": "promoter_account",
    "video_references": "user_video_reference",
}
TRACKING = frozenset({"fbclid", "gclid", "dclid", "msclkid", "igshid", "mc_cid", "mc_eid"})
URL_RE = re.compile(r"https?://[^\s<>\"']+|(?<![\w@])(?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>\"']*)?", re.I)
YT_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
IG_ID = re.compile(r"[A-Za-z0-9_-]{5,80}\Z")
TT_ID = re.compile(r"\d{6,25}\Z")
SAFE_USER = re.compile(r"[\w.\-]{1,100}\Z", re.UNICODE)


class ReferenceError(ValueError):
    """User-facing normalization error; never contains remote response bodies."""


@dataclass(frozen=True)
class Reference:
    original_url: str
    canonical_url: str
    platform: str | None
    native_id: str | None
    link_kind: str
    identity_key: str
    ingestion_mode: str
    source_type: str


def _host(hostname: str | None) -> str:
    if not hostname:
        raise ReferenceError("URL должен содержать публичный домен.")
    try:
        host = hostname.strip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ReferenceError("Неподдерживаемое имя домена.") from exc
    if len(host) > 253 or not host or any(len(x) > 63 for x in host.split(".")):
        raise ReferenceError("Слишком длинный домен.")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        # Registry accepts public-domain URLs only; the actual fetcher also
        # needs DNS/redirect/IP protection (stage 05).
        raise ReferenceError("Для источника нужен публичный домен, не IP-адрес.")
    if ("." not in host or host.endswith((".local", ".internal", ".localhost", ".test", ".invalid"))
            or host in ("localhost", "metadata.google.internal")
            or any(not x or x.startswith("-") or x.endswith("-") for x in host.split("."))):
        raise ReferenceError("Локальные и некорректные домены не допускаются.")
    return host


def _trim_url(value: str) -> str:
    value = value.strip().strip("<>\"'")
    # punctuation around an URL in a questionnaire is not part of the address
    value = value.rstrip(".,;!?)\u00bb\u201d")
    if value.lower().startswith("www."):
        value = "https://" + value
    if not re.match(r"^https?://", value, re.I) and re.match(r"^[\w-]+(?:\.[\w-]+)+(?:/|$)", value):
        value = "https://" + value
    if not re.match(r"^https?://", value, re.I):
        raise ReferenceError("Укажи полную ссылку http(s):// или домен.")
    return value


def normalize_url(url: str, source_type: str) -> Reference:
    if not isinstance(url, str) or len(url) > 4096 or source_type not in KINDS:
        raise ReferenceError("Некорректная ссылка или тип источника.")
    given = _trim_url(url)
    try:
        p = urlsplit(given)
        port = p.port
    except ValueError as exc:
        raise ReferenceError("Некорректный URL.") from exc
    if p.scheme.lower() not in ("http", "https") or p.username or p.password or port not in (None, 80, 443):
        raise ReferenceError("Допускаются только публичные HTTP(S)-ссылки без авторизации и необычных портов.")
    host = _host(p.hostname)
    if any(ord(c) < 32 for c in given):
        raise ReferenceError("Недопустимые символы в URL.")
    if len(given) > 2048:
        raise ReferenceError("Ссылка слишком длинная.")
    # Only canonicalize syntactically. Never use a network fetch to guess IDs.
    path = re.sub(r"/{2,}", "/", p.path or "/")
    parts = [x for x in path.strip("/").split("/") if x]
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
    platform, native_id, link_kind = None, None, "website"
    if host in ("youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"):
        platform = "youtube"
        if host == "youtu.be" and parts and YT_ID.fullmatch(parts[0]):
            native_id, link_kind = parts[0], "video"
        elif parts and parts[0].lower() in ("shorts", "watch", "live", "embed"):
            if parts[0].lower() == "watch":
                vids = [v for k, v in query if k == "v"]
                native_id = vids[0] if len(vids) == 1 and YT_ID.fullmatch(vids[0]) else None
            elif len(parts) == 2 and YT_ID.fullmatch(parts[1]):
                native_id = parts[1]
            if native_id:
                link_kind = "video"
            else:
                raise ReferenceError("Не удалось определить YouTube video ID.")
        elif parts and (parts[0].startswith("@") or parts[0] in ("channel", "user", "c")):
            link_kind = "account"
            native_id = "/".join(parts[:2]) if parts[0] in ("channel", "user", "c") else parts[0]
        elif len(parts) == 0:
            link_kind = "website"
        else:
            raise ReferenceError("Неподдерживаемый вид ссылки на YouTube.")
        if link_kind == "video":
            host, path, query = "www.youtube.com", f"/watch", [("v", native_id)]
        elif link_kind == "account":
            host, path, query = "www.youtube.com", "/" + native_id, []
    elif host in ("tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"):
        platform = "tiktok"
        if host in ("vm.tiktok.com", "vt.tiktok.com") or (parts and parts[0] in ("t", "share")):
            link_kind = "shortlink"
            if not parts:
                raise ReferenceError("Короткая TikTok-ссылка без идентификатора.")
        elif len(parts) >= 3 and parts[0].startswith("@") and parts[1] == "video" and TT_ID.fullmatch(parts[2]):
            native_id, link_kind = parts[2], "video"
        elif len(parts) == 1 and parts[0].startswith("@") and SAFE_USER.fullmatch(parts[0][1:]):
            native_id, link_kind = parts[0], "account"
        else:
            raise ReferenceError("Неподдерживаемая TikTok-ссылка. Укажи аккаунт, видео или короткую ссылку.")
        if link_kind == "video":
            host, path, query = "www.tiktok.com", f"/{parts[0]}/video/{native_id}", []
        elif link_kind == "account":
            host, path, query = "www.tiktok.com", "/" + native_id, []
    elif host in ("instagram.com", "www.instagram.com", "m.instagram.com"):
        platform = "instagram"
        if len(parts) == 2 and parts[0] in ("reel", "reels", "p", "tv") and IG_ID.fullmatch(parts[1]):
            native_id, link_kind = parts[1], "video"
            path, query, host = f"/reel/{native_id}", [], "www.instagram.com"
        elif len(parts) == 1 and SAFE_USER.fullmatch(parts[0]) and parts[0] not in ("explore", "accounts", "reels", "direct"):
            native_id, link_kind = parts[0].lower(), "account"
            path, query, host = f"/{native_id}", [], "www.instagram.com"
        elif not parts:
            pass
        else:
            raise ReferenceError("Неподдерживаемая ссылка Instagram. Нужны профиль или пост/Reel.")
    elif host in ("x.com", "twitter.com", "www.facebook.com", "facebook.com", "threads.net",
                  "www.threads.net", "linkedin.com", "www.linkedin.com", "pinterest.com",
                  "www.pinterest.com", "t.me", "vk.com", "twitch.tv", "www.twitch.tv"):
        platform = {"x.com": "x", "twitter.com": "x", "facebook.com": "facebook",
                    "threads.net": "threads", "linkedin.com": "linkedin", "pinterest.com": "pinterest",
                    "t.me": "telegram", "vk.com": "vk", "twitch.tv": "twitch"}.get(host.removeprefix("www."))
        if parts and parts[0] not in ("watch", "reel", "reels", "status", "posts", "login", "search"):
            link_kind = "account"
            native_id = "/".join(parts[:2]) if parts[0] in ("company", "in", "user") and len(parts)>1 else parts[0]
            host = {"x":"x.com", "facebook":"www.facebook.com", "threads":"www.threads.net",
                    "linkedin":"www.linkedin.com", "pinterest":"www.pinterest.com",
                    "telegram":"t.me", "vk":"vk.com", "twitch":"www.twitch.tv"}[platform]
            path,query = "/" + native_id, []
    if source_type == "user_video_reference" and link_kind not in ("video", "shortlink"):
        raise ReferenceError("Для референса укажи конкретное видео, а не профиль.")
    if source_type in ("competitor_account", "promoter_account") and link_kind != "account":
        raise ReferenceError("Здесь нужна ссылка на аккаунт, не на видео или сайт.")
    if source_type in ("product_website", "competitor_website") and link_kind == "video":
        raise ReferenceError("В поле сайта нельзя добавлять отдельное видео.")
    if not path.startswith("/"):
        path = "/" + path
    if path != "/":
        path = path.rstrip("/")
    # Preserve non-tracking parameters on arbitrary websites; don't discard IDs.
    qs = urlencode(sorted(query), doseq=True)
    canonical = urlunsplit(("https", host, quote(path, safe="/%:@_-+.~"), qs, ""))
    if len(canonical) > 2048:
        raise ReferenceError("Ссылка слишком длинная.")
    if link_kind == "video":
        identity = f"{platform}:video:{native_id}"
    elif link_kind == "account":
        identity = f"{platform}:account:{native_id}"
    else:
        identity = "url:" + canonical
    return Reference(url, canonical, platform, native_id, link_kind, identity,
                     "web_ai" if link_kind == "website" else "video_reference" if link_kind in ("video", "shortlink") else "metadata_only", source_type)


def parse_legacy(profile: dict) -> tuple[list[dict], list[dict]]:
    """Return proposals and per-line warnings. Never rewrite profile fields."""
    results, warnings = [], []
    for field, kind in LEGACY_FIELDS.items():
        text = profile.get(field, "")
        if not isinstance(text, str) or not text.strip():
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            matches = list(URL_RE.finditer(line))
            if not matches:
                warnings.append({"field": field, "line": line_number, "reason": "no_url"})
                continue
            for match in matches:
                raw_url = match.group(0).rstrip(".,;!?)")
                label = (line[:match.start()].strip(" -:—\t") or None)
                comment = (line[match.end():].strip(" -:—\t") or None)
                try:
                    parsed = normalize_url(raw_url, kind)
                except ReferenceError as exc:
                    warnings.append({"field": field, "line": line_number, "reason": str(exc)})
                    continue
                legacy_key = hashlib.sha256((field + "\0" + parsed.identity_key).encode("utf-8")).hexdigest()
                results.append({"source_type": kind, "url": raw_url,
                                "origin_field": field, "original_input": line,
                                "label": label, "user_notes": comment,
                                "legacy_import_key": legacy_key})
    return results, warnings
