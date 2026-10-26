"""Bounded, local-only FFprobe/FFmpeg extraction of technical Group A A1 signals.

No network access; never download, transcribe, OCR, classify CTA or invent media.
Source permission and QDP eligibility are checked by repository before AND after
calling this module. The local path must reside in an explicitly configured root.
"""
from __future__ import annotations

from pathlib import Path
import os

from .contracts import GroupAError

VIDEO_EXTENSIONS = frozenset((".mp4",".mov",".mkv",".webm"))
MAX_LOCAL_BYTES = 2 * 1024 * 1024 * 1024


def _allowed_roots(roots=None) -> tuple[Path,...]:
    if roots is None:
        raw=os.environ.get("CM_GROUP_A_MEDIA_ROOTS","")
        roots = raw.split(os.pathsep) if raw else []
    if not roots or any(type(root) is not str or not root.strip() for root in roots):
        raise GroupAError("media_roots_unconfigured",
                          "Configure CM_GROUP_A_MEDIA_ROOTS with authorized local folders",409)
    directories=[]
    for value in roots:
        path=Path(value).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise GroupAError("invalid_media_root","Authorized media root is not a directory",409)
        directories.append(path)
    return tuple(directories)


def authorized_file(raw_path: str, *, roots=None) -> Path:
    if type(raw_path) is not str or not raw_path.strip() or "\x00" in raw_path:
        raise GroupAError("invalid_media_path","Invalid media path",409)
    candidate=Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise GroupAError("invalid_media_path","Media path must be absolute",409)
    try:
        path=candidate.resolve(strict=True)
        approved=_allowed_roots(roots)
        if not any(path.is_relative_to(root) for root in approved):
            raise GroupAError("media_path_not_allowed","Media asset outside configured roots",403)
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            raise GroupAError("media_type_not_supported","Expected a local video file",422)
        size=path.stat().st_size
        if not 0<size<=MAX_LOCAL_BYTES:
            raise GroupAError("media_file_limit","Media file is empty or exceeds the 2 GiB limit",422)
        return path
    except (OSError,RuntimeError) as exc:
        raise GroupAError("media_unavailable","Cannot access authorized media",409) from exc


# GA-MOD-06: preserve the old public extractor API while assigning technical
# measurements to the isolated and stricter extraction module. No D1/QDP
# schema/data changes are needed for callers of extractor.measure().
from .extractors.technical import (  # noqa: E402,F401
    METHOD_VERSION, ProbeInfo, TechnicalEvidence, analyze_technical,
    probe_details, inspect_probe, parse_cuts, _binary, _execute, measure,
)
