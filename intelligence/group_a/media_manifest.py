"""GA-MOD-05 byte-verifying, local-only media manifest resolver.

No downloads, model calls or guessed slide order/duration. Media verification
is a file-descriptor operation, not trust in a DB SHA or filename. Callers
must reopen via the checked handle at processing time; a stored manifest alone
is NOT evidence that bytes remain on disk.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import stat
from typing import Iterator

from .contracts_v1 import (
    ContractViolation, canonical_sha256, ensure_same_lineage,
    media_manifest_v1, qdp_handoff_v1,
)

MAX_FILE_BYTES = 2 * 1024**3
CHUNK_BYTES = 1024**2
EXTENSIONS = {
    "video": frozenset((".mp4", ".mov", ".mkv", ".webm", ".m4v")),
    "image": frozenset((".jpeg", ".jpg", ".png", ".webp", ".gif",
                        ".heic", ".heif", ".avif")),
}
ISOBMFF_VIDEO = frozenset((b"isom",b"iso2",b"iso4",b"iso5",b"iso6",
                           b"mp41",b"mp42",b"avc1",b"qt  ",b"M4V ",b"dash"))
ISOBMFF_IMAGE = frozenset((b"heic",b"heix",b"hevc",b"hevx",b"mif1",
                           b"msf1",b"avif",b"avis"))


class MediaVerificationError(ValueError):
    """Stable sanitized error code; never include local paths in exception."""

    def __init__(self, code: str):
        self.code = code
        super().__init__("Group A media verification failed: " + code)


def _roots(roots):
    if type(roots) not in (list, tuple) or not roots:
        raise MediaVerificationError("roots_unconfigured")
    canonical = []
    for raw in roots:
        if type(raw) is not str or not raw or "\x00" in raw:
            raise MediaVerificationError("invalid_root")
        path = Path(raw).expanduser()
        if not path.is_absolute() or ".." in path.parts:
            raise MediaVerificationError("invalid_root")
        try:
            if path.is_symlink() or not path.is_dir():
                raise MediaVerificationError("invalid_root")
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise MediaVerificationError("invalid_root") from None
        canonical.append(resolved)
    return tuple(canonical)


def _within(raw_path: str, roots: tuple[Path, ...]):
    if type(raw_path) is not str or "\x00" in raw_path or not raw_path:
        raise MediaVerificationError("invalid_path")
    path = Path(raw_path)
    if not path.is_absolute() or ".." in path.parts:
        raise MediaVerificationError("invalid_path")
    # Explicitly forbid any symlink component, even one pointing back inside
    # an allowed root. No storage URI, http(s) URL or network share accepted.
    if os.name == "nt" and str(path).startswith("\\\\"):
        raise MediaVerificationError("invalid_path")
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise MediaVerificationError("media_missing") from None
    for root in roots:
        if real.is_relative_to(root) and path.is_relative_to(root):
            relative = path.relative_to(root)
            if not relative.parts:
                break
            cursor = root
            try:
                for part in relative.parts:
                    cursor = cursor / part
                    if cursor.is_symlink():
                        raise MediaVerificationError("symlink_not_allowed")
            except OSError:
                raise MediaVerificationError("media_missing") from None
            return root, relative, real
    raise MediaVerificationError("media_path_not_allowed")


@contextmanager
def _open_file(path: str, roots) -> Iterator[tuple[object, Path]]:
    approved = _roots(roots)
    root, relative, real = _within(path, approved)
    # Linux/macOS: use dirfd + O_NOFOLLOW for every component to prevent
    # symlink swaps between path validation and open().
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    dirs = []
    descriptor = None
    try:
        if nofollow and os.supports_dir_fd and os.open in os.supports_dir_fd:
            directory = os.open(root, flags | getattr(os,"O_DIRECTORY",0) | nofollow)
            dirs.append(directory)
            for component in relative.parts[:-1]:
                directory = os.open(component,
                    flags | getattr(os,"O_DIRECTORY",0) | nofollow,
                    dir_fd=directory)
                dirs.append(directory)
            descriptor = os.open(relative.parts[-1],
                                 flags | nofollow, dir_fd=directory)
        else:
            # Windows fallback: reject symlinks before and after opening and
            # pin the open file's identity; never treat a raw path as verified.
            descriptor = os.open(real, flags)
            _within(path, approved)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise MediaVerificationError("not_regular_file")
        # Detect a rename/replace between path resolution and fd acquisition.
        nameinfo = os.stat(real, follow_symlinks=False)
        if (info.st_dev,info.st_ino) != (nameinfo.st_dev,nameinfo.st_ino):
            raise MediaVerificationError("media_changed_during_open")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = None
            yield stream, real
    except MediaVerificationError:
        raise
    except (OSError, PermissionError):
        raise MediaVerificationError("media_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for fd in reversed(dirs):
            os.close(fd)


def _check_magic(header: bytes, suffix: str, kind: str):
    if suffix not in EXTENSIONS[kind]:
        raise MediaVerificationError("extension_kind_mismatch")
    if suffix == ".png":
        valid = header.startswith(b"\x89PNG\r\n\x1a\n")
    elif suffix in (".jpg", ".jpeg"):
        valid = header.startswith(b"\xff\xd8\xff")
    elif suffix == ".gif":
        valid = header.startswith((b"GIF87a",b"GIF89a"))
    elif suffix == ".webp":
        valid = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    elif suffix in (".heic",".heif",".avif"):
        valid = (len(header)>=12 and header[4:8] == b"ftyp"
                 and header[8:12] in ISOBMFF_IMAGE)
    elif suffix in (".mp4",".mov",".m4v"):
        valid = (len(header)>=12 and header[4:8] == b"ftyp"
                 and header[8:12] in ISOBMFF_VIDEO)
    elif suffix in (".mkv",".webm"):
        valid = header.startswith(b"\x1a\x45\xdf\xa3")
    else:
        valid = False
    if not valid:
        raise MediaVerificationError("magic_kind_mismatch")


@dataclass(frozen=True, slots=True)
class VerifiedFile:
    path: str
    media_kind: str
    sha256: str
    byte_size: int
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int

    def unchanged(self, info):
        return (info.st_dev==self.device and info.st_ino==self.inode
                and info.st_size==self.byte_size
                and info.st_mtime_ns==self.mtime_ns
                and info.st_ctime_ns==self.ctime_ns)


def verify_file(path, *, media_kind, sha256, roots, expected_bytes=None) -> VerifiedFile:
    """Verify current bytes, magic, permission to read, and exact DB SHA.

    On changed files/replacements validation fails; no DB-only proof accepted.
    """
    if media_kind not in EXTENSIONS:
        raise MediaVerificationError("invalid_media_kind")
    if type(sha256) is not str or len(sha256)!=64 or any(
        c not in "0123456789abcdef" for c in sha256
    ):
        raise MediaVerificationError("invalid_sha256")
    if expected_bytes is not None and (type(expected_bytes) is not int
                                       or not 1<=expected_bytes<=MAX_FILE_BYTES):
        raise MediaVerificationError("invalid_expected_size")
    with _open_file(path, roots) as (f, real):
        first = os.fstat(f.fileno())
        if not 1<=first.st_size<=MAX_FILE_BYTES:
            raise MediaVerificationError("media_size_limit")
        if expected_bytes is not None and first.st_size!=expected_bytes:
            raise MediaVerificationError("size_mismatch")
        header = f.read(32)
        _check_magic(header, real.suffix.lower(), media_kind)
        digest = hashlib.sha256()
        digest.update(header)
        total = len(header)
        while True:
            data = f.read(CHUNK_BYTES)
            if not data:
                break
            digest.update(data)
            total += len(data)
            if total>MAX_FILE_BYTES:
                raise MediaVerificationError("media_size_limit")
        end = os.fstat(f.fileno())
        if (end.st_dev,end.st_ino,end.st_size,end.st_mtime_ns,end.st_ctime_ns) != (
             first.st_dev,first.st_ino,first.st_size,first.st_mtime_ns,first.st_ctime_ns
        ) or total != first.st_size:
            raise MediaVerificationError("media_changed_during_read")
        if digest.hexdigest()!=sha256:
            raise MediaVerificationError("sha256_mismatch")
        return VerifiedFile(str(real),media_kind,sha256,total,
                            end.st_dev,end.st_ino,end.st_mtime_ns,end.st_ctime_ns)


@contextmanager
def open_verified(fingerprint: VerifiedFile, *, roots):
    """Yield the *opened descriptor* only after revalidating unchanged bytes.

    Consumers must use this descriptor directly, not the untrusted original
    pathname. A result must be invalidated if verification fails at any time.
    """
    if not isinstance(fingerprint, VerifiedFile):
        raise TypeError("VerifiedFile required")
    with _open_file(fingerprint.path, roots) as (stream, real):
        current=os.fstat(stream.fileno())
        if not fingerprint.unchanged(current):
            raise MediaVerificationError("media_changed_since_verification")
        _check_magic(stream.read(32), real.suffix.lower(), fingerprint.media_kind)
        stream.seek(0)
        digest=hashlib.sha256()
        count=0
        while True:
            buf=stream.read(CHUNK_BYTES)
            if not buf:
                break
            count+=len(buf)
            if count>MAX_FILE_BYTES:
                raise MediaVerificationError("media_size_limit")
            digest.update(buf)
        if count!=fingerprint.byte_size or digest.hexdigest()!=fingerprint.sha256:
            raise MediaVerificationError("media_changed_since_verification")
        stream.seek(0)
        yield stream
        if not fingerprint.unchanged(os.fstat(stream.fileno())):
            raise MediaVerificationError("media_changed_during_processing")


def build_manifest(pointer, *, source_id, source_policy_version_id,
                   content_kind, ordered_assets, roots, verified_at=None):
    """Deterministic for fixed verified_at and ordered authoritative asset list.

    Assets must be exact public D1 rows: media_asset_id, video_id, source_id,
    media_kind, local_path, sha256, byte_size(optional), duration_sec(optional).
    All ordered rows are verified before any manifest is returned.
    """
    p=qdp_handoff_v1(pointer)
    if type(ordered_assets) is not list or not ordered_assets:
        raise MediaVerificationError("missing_ordered_assets")
    if type(source_id) is not str or not source_id:
        raise MediaVerificationError("invalid_source")
    if type(source_policy_version_id) is not int or source_policy_version_id<=0:
        raise MediaVerificationError("invalid_source_policy")
    if verified_at is None:
        verified_at=datetime.now(timezone.utc)
    if not isinstance(verified_at,datetime) or verified_at.utcoffset() is None:
        raise MediaVerificationError("invalid_verified_at")
    timestamp=verified_at.astimezone(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00","Z")
    items=[]
    fingerprints={}
    for index, asset in enumerate(ordered_assets,1):
        if type(asset) is not dict or set(asset)-{
            "media_asset_id","video_id","source_id","media_kind","local_path",
            "sha256","byte_size","duration_sec"
        } or not {"media_asset_id","video_id","source_id","media_kind",
                   "local_path","sha256"} <= set(asset):
            raise MediaVerificationError("invalid_asset_row")
        if type(asset["media_asset_id"]) is not int or asset["media_asset_id"]<=0:
            raise MediaVerificationError("invalid_asset_id")
        if type(asset["video_id"]) is not int or asset["video_id"]!=p["video_id"]:
            raise MediaVerificationError("foreign_video_asset")
        if asset["source_id"]!=source_id:
            raise MediaVerificationError("foreign_source_asset")
        if type(asset["media_kind"]) is not str or asset["media_kind"] not in EXTENSIONS:
            raise MediaVerificationError("invalid_media_kind")
        file=verify_file(asset["local_path"],media_kind=asset["media_kind"],
                         sha256=asset["sha256"],roots=roots,
                         expected_bytes=asset.get("byte_size"))
        ref=f"ga-local:a{asset['media_asset_id']}-{file.sha256[:16]}"
        if ref in fingerprints:
            raise MediaVerificationError("duplicate_media_asset")
        fingerprints[ref]=file
        item={
            "index":index,"kind":file.media_kind,
            "media_asset_id":asset["media_asset_id"],
            "sha256":file.sha256,"byte_size":file.byte_size,
            "verified_local_path_ref":ref,
        }
        if "duration_sec" in asset:
            if file.media_kind!="video":
                raise MediaVerificationError("image_duration_not_allowed")
            item["duration_sec"]=asset["duration_sec"]
        items.append(item)
    data={
        "schema_version":"ga-media-manifest/v1",
        "project_id":p["project_id"],"qdp_run_id":p["qdp_run_id"],
        "video_id":p["video_id"],"qdp_input_sha256":p["input_sha256"],
        "content_kind":content_kind,"source_id":source_id,
        "source_policy_version_id":source_policy_version_id,
        "verified_at":timestamp,"items":items,
    }
    manifest={**data,"manifest_sha256":canonical_sha256(data)}
    media_manifest_v1(manifest)
    ensure_same_lineage(p,manifest)
    return manifest, fingerprints
