"""Trust-bootstrap for Marketing local media, independent of process entrypoint.

Source provenance and SHA remain verified by media_guard and the DB-bound
workers. This helper only provides the explicit, narrow local archive roots.
No provider calls, no fallback to arbitrary disk paths, no QDP.
"""
from __future__ import annotations

import os
from pathlib import Path
from intelligence import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def ensure_media_roots() -> list[str]:
    """Include Marketing carousel storage and the verified TikTok MP4 archive.

    Works when an app worker has a different environment from the CLI that
    launched a batch. Existing configured roots remain subject to validation.
    """
    roots = [x for x in config.setting("CM_MARKET_MEDIA_ROOTS", "").split(os.pathsep) if x]
    target_roots = (
        PROJECT_ROOT / "marketing-qa" / "media",
        Path.home() / "Downloads" / "TikTokArchive",
    )
    for target in target_roots:
        if target.is_symlink():
            raise ValueError("media_root_symlink_not_allowed")
        target.mkdir(parents=True, exist_ok=True)
        trusted = str(target.resolve(strict=True))
        if trusted not in roots:
            roots.append(trusted)
    normalized=[]
    for value in roots:
        path=Path(value)
        if not path.is_absolute() or not path.is_dir() or path.is_symlink():
            raise ValueError("media_root_not_trusted")
        normalized.append(str(path.resolve(strict=True)))
    os.environ["CM_MARKET_MEDIA_ROOTS"] = os.pathsep.join(normalized)
    return normalized
