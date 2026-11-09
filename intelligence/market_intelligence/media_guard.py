"""Own MI local-media guard adapted from the authorized-file principles of GA.

No HTTP, no archive traversal, no symlinks, never analyze arbitrary supplied URLs.
Only a DB-verified video asset hash within explicitly configured directories.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path

MAX_BYTES = 2 * 1024**3
EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv"}

def authorized_media(path: Path, roots) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise ValueError("invalid_media_path")
    if not isinstance(roots, (list,tuple)) or not roots:
        raise ValueError("media_roots_unconfigured")
    if any(not isinstance(r,str) or not r for r in roots):
        raise ValueError("invalid_media_root")
    if path.suffix.lower() not in EXTENSIONS:
        raise ValueError("unsupported_media_format")
    approved = []
    for r in roots:
        root=Path(r).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("invalid_media_root")
        approved.append(root)
    resolved=path.resolve(strict=True)
    if not any(resolved.is_relative_to(root) for root in approved):
        raise ValueError("media_outside_allowed_roots")
    if not resolved.is_file() or not 0<resolved.stat().st_size<=MAX_BYTES:
        raise ValueError("invalid_media_file")
    return resolved

def verified_media(raw_path: str, expected_sha256: str, *, roots=None) -> Path:
    if not isinstance(expected_sha256,str) or len(expected_sha256)!=64 or not all(c in "0123456789abcdef" for c in expected_sha256):
        raise ValueError("invalid_media_hash")
    if roots is None:
        raw=os.environ.get("CM_MARKET_MEDIA_ROOTS","")
        roots=[p for p in raw.split(os.pathsep) if p]
    path=authorized_media(Path(raw_path),roots)
    initial=path.stat()
    digest=hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk=fh.read(1024*1024)
            if not chunk: break
            digest.update(chunk)
    final=path.stat()
    if (digest.hexdigest()!=expected_sha256 or (initial.st_dev,initial.st_ino,initial.st_size,
        initial.st_mtime_ns,initial.st_ctime_ns)!=(final.st_dev,final.st_ino,
        final.st_size,final.st_mtime_ns,final.st_ctime_ns)):
        raise ValueError("media_hash_or_identity_changed")
    return path
