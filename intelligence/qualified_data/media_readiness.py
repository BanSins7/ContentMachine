"""FIX-09: read-only, source-pinned, locally verifiable media readiness.

Media acquisition belongs to Collector/Media Acquisition. QDP never downloads,
decodes, transcodes or creates a media_asset. 'ready' is permission/path/hash
and container-header readiness, NOT an assertion that FFmpeg can decode it.
Group A and B must revalidate current rights/local assets at execution time.

Carousel/image formats require independent ordered media manifests; a single
media_assets row cannot prove slide order or type. Never use a video file as
carousel evidence, or put raw local paths into QDP output JSON.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

MAX_LOCAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_ASSET_CANDIDATES = 16
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
VIDEO_EXTENSIONS = frozenset((".mp4", ".mov", ".mkv", ".webm"))
VIDEO_KINDS = frozenset(("video", "slideshow_video"))
CAROUSEL_KINDS = frozenset(("photo_carousel", "mixed_carousel"))
SUPPORTED_KINDS = VIDEO_KINDS | CAROUSEL_KINDS | {"image_post"}


def _groups(a_reason=None, b_reason=None, *, asset=None):
    return {
        "A": {"state": "blocked" if a_reason else "ready",
              "reason_code": a_reason},
        "B": {"state": "blocked" if b_reason else "ready",
              "reason_code": b_reason},
        "asset": asset if a_reason is None or b_reason is None else None,
    }


def _configured_roots(roots):
    if roots is None:
        raw = os.environ.get("CM_QDP_MEDIA_ROOTS") or os.environ.get("CM_GROUP_A_MEDIA_ROOTS", "")
        roots = raw.split(os.pathsep) if raw else ()
    if not isinstance(roots, (list, tuple)) or not roots:
        return None
    approved = []
    for entry in roots:
        if type(entry) is not str or not entry.strip():
            return None
        try:
            folder = Path(entry).expanduser().resolve(strict=True)
            if not folder.is_dir():
                return None
            approved.append(folder)
        except (OSError, ValueError, RuntimeError):
            return None
    return tuple(approved)


def _local_media(candidate: Mapping, roots) -> tuple[dict | None, str | None]:
    raw = candidate.get("local_path")
    if type(raw) is not str or not raw.strip() or "\x00" in raw:
        return None, "media_path_invalid"
    supplied = Path(raw).expanduser()
    if not supplied.is_absolute():
        return None, "media_path_invalid"
    if supplied.suffix.lower() not in VIDEO_EXTENSIONS:
        return None, "media_type_unsupported"
    if roots is None:
        return None, "media_roots_unconfigured"
    try:
        resolved = supplied.resolve(strict=True)
        if not any(resolved.is_relative_to(folder) for folder in roots):
            return None, "media_path_not_allowed"
        if not resolved.is_file():
            return None, "media_file_unavailable"
        info = resolved.stat()
        if not 0 < info.st_size <= MAX_LOCAL_BYTES:
            return None, "media_file_size_invalid"
        digest = candidate.get("sha256")
        if type(digest) is not str or not HEX64.fullmatch(digest):
            return None, "media_sha256_unverified"
        # Small signature check avoids treating a text file renamed .mp4 as
        # genuine video. Actual codec/streams are Group A's FFprobe responsibility.
        with resolved.open("rb") as inp:
            magic = inp.read(16)
            suffix = supplied.suffix.lower()
            if suffix in (".mp4", ".mov"):
                recognized = len(magic) >= 12 and magic[4:8] == b"ftyp"
            else:
                recognized = magic.startswith(b"\x1a\x45\xdf\xa3")
            if not recognized:
                return None, "media_container_invalid"
            inp.seek(0)
            hasher = hashlib.sha256()
            while True:
                chunk = inp.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        if hasher.hexdigest() != digest:
            return None, "media_hash_mismatch"
        # Verify file didn't change during hashing. No file is opened for
        # external execution here; A/B still need their own check.
        after = resolved.stat()
        if (after.st_size != info.st_size or after.st_mtime_ns != info.st_mtime_ns or
                after.st_ino != info.st_ino or after.st_dev != info.st_dev):
            return None, "media_file_changed"
    except (OSError, ValueError, RuntimeError):
        return None, "media_file_unavailable"
    return {"id": candidate["id"], "sha256": digest, "media_kind": "video",
            "size_bytes": info.st_size}, None


def verify_media_candidates(rows: Sequence[Mapping], metadata: Mapping, *,
                            roots=None, now=None) -> dict:
    """Return per-A/B eligibility and one verified media pointer, no raw path.

    When metadata rights are absent, do not inspect private filesystem paths.
    Select only the newest current-source candidate; never silently fall back
    from a revoked/invalid replacement or to another D1 source.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Media verification timestamp must include timezone")
    if not metadata.get("can_analyze_media") or (
            metadata.get("permissions") or {}).get("can_analyze_media") is not True:
        return _groups("media_rights_missing", "media_rights_missing")
    kind = metadata.get("content_kind")
    if kind not in SUPPORTED_KINDS:
        return _groups("content_kind_unverified", "content_kind_unverified")
    if kind in CAROUSEL_KINDS:
        return _groups("video_a_carousel_unsupported", "media_manifest_required")
    if kind == "image_post":
        return _groups("video_a_image_unsupported", "group_b_content_kind_unsupported")
    if not isinstance(rows, (list, tuple)) or len(rows) > MAX_ASSET_CANDIDATES:
        raise ValueError("Media candidate set must be bounded")
    if not rows:
        return _groups("media_asset_missing", "media_asset_missing")
    candidate = rows[0]
    if not isinstance(candidate, Mapping) or (
            type(candidate.get("id")) is not int or candidate["id"] <= 0):
        return _groups("media_asset_invalid", "media_asset_invalid")
    if candidate.get("video_id") != metadata["video_id"] or (
            candidate.get("source_id") != metadata["source_id"]):
        return _groups("media_source_mismatch", "media_source_mismatch")
    if candidate.get("permission") in (None, "", "metadata_only"):
        return _groups("media_permission_missing", "media_permission_missing")
    expiry = candidate.get("expires_at")
    if expiry is not None and (
            not isinstance(expiry, datetime) or expiry.tzinfo is None or
            expiry.utcoffset() is None or expiry <= now):
        return _groups("media_asset_expired", "media_asset_expired")
    if candidate.get("media_kind") != "video":
        return _groups("media_kind_incompatible", "media_kind_incompatible")
    allowed = _configured_roots(roots)
    checked, problem = _local_media(candidate, allowed)
    if problem is not None:
        return _groups(problem, problem)
    return _groups(asset=checked)


def read_verified_media(conn, metadata, *, roots=None):
    """No network/media acquisition; a single bounded D1 identity lookup."""
    if not metadata.get("can_analyze_media") or (
            metadata.get("permissions") or {}).get("can_analyze_media") is not True:
        return verify_media_candidates([], metadata, roots=roots)
    # No single media asset can authorize carousel slide order. Avoid
    # unnecessary D1 reads and ensure the Group B manifest isn't invented.
    if metadata.get("content_kind") not in VIDEO_KINDS:
        return verify_media_candidates([], metadata, roots=roots)
    rows = conn.execute(
        """SELECT id,video_id,source_id,media_kind,local_path,sha256,
                  permission,expires_at,created_at
             FROM media_assets
            WHERE video_id=%s AND source_id=%s
            ORDER BY created_at DESC,id DESC LIMIT 16""",
        (metadata["video_id"], metadata["source_id"])).fetchall()
    return verify_media_candidates(rows, metadata, roots=roots)
