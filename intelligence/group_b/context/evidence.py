"""GB-MOD-06: hash source-first local bytes and verify provenance/time truth.

No crawling, downloads, FFmpeg, OCR, ASR, models or invented observations.
An injected catalog must return authoritative per-proof metadata, never
a caller's guessed path/time. The manifest exports hashes/IDs, not raw text.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
import hashlib
import os
import stat
import re

from ..errors import GroupBError

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = {
    "scene": "scene", "transcript": "transcript_segment",
    "screen_text": "ocr_span", "audio": "audio_event",
    "frame": "sampled_frame", "slide": "slide_ref",
}
_TIMED = frozenset(("scene","transcript_segment","ocr_span","sampled_frame","audio_event"))
_MAX_BYTES = 2 * 1024 * 1024 * 1024
_MAX_EVIDENCE = 2000
_REQUIRED = frozenset((
    "reference_sha256", "kind", "project_id", "video_id",
    "source_id", "media_asset_id", "asset_sha256",
    "content_sha256", "local_path", "timebase",
))
_OPTIONAL = frozenset((
    "start_ms","end_ms","clip_origin_ms","clip_duration_ms",
    "slide_index","frame_ref_sha256",
))


class EvidenceFailure(GroupBError):
    def __init__(self, reason: str):
        super().__init__("evidence_unavailable", "media_evidence: " + reason)
        self.reason = reason


def _fail(reason: str):
    raise EvidenceFailure(reason)


def _hash(value: object) -> bool:
    return type(value) is str and _SHA.fullmatch(value) is not None


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def _nat(value: object) -> bool:
    return type(value) is int and value >= 0


@dataclass(frozen=True, slots=True)
class MediaByteProof:
    asset_id: int
    source_sha256: str
    verified_size: int
    media_kind: str
    slide_index: int
    duration_ms: int | None

    def wire(self) -> dict:
        return {
            "media_asset_id": self.asset_id,
            "source_sha256": self.source_sha256,
            "byte_size": self.verified_size,
            "media_kind": self.media_kind,
            "slide_index": self.slide_index,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True, slots=True)
class VerifiedObservation:
    kind: str
    ref_sha256: str
    content_sha256: str
    asset_sha256: str
    media_asset_id: int
    timebase: str
    start_ms: int | None = None
    end_ms: int | None = None
    slide_index: int | None = None
    frame_ref_sha256: str | None = None

    def wire(self) -> dict:
        return {
            "kind": self.kind, "ref_sha256": self.ref_sha256,
            "content_sha256": self.content_sha256,
            "asset_sha256": self.asset_sha256,
            "media_asset_id": self.media_asset_id,
            "timebase": self.timebase,
            "start_ms": self.start_ms, "end_ms": self.end_ms,
            "slide_index": self.slide_index,
            "frame_ref_sha256": self.frame_ref_sha256,
        }


def _trusted_roots(roots: object) -> tuple[Path,...]:
    if type(roots) not in (list, tuple) or not roots:
        _fail("media_roots_unconfigured")
    normalized = []
    for root in roots:
        if not isinstance(root,(str,Path)) or not str(root):
            _fail("media_root_invalid")
        try:
            resolved = Path(root).expanduser().resolve(strict=True)
            if not resolved.is_dir():
                _fail("media_root_invalid")
        except (OSError,ValueError,RuntimeError):
            _fail("media_root_unavailable")
        normalized.append(resolved)
    return tuple(normalized)


def verify_local_bytes(path: object, expected_sha256: str, roots: object, *,
                       expected_size: int | None = None,
                       media_kind: str | None = None,
                       limit_bytes: int = _MAX_BYTES) -> tuple[str,int]:
    """Bounded hash with path allowlist, symlink rejection and file-change gate.

    Checking bytes here does NOT guarantee they cannot change after return:
    every paid execution must reread rights and media at use time.
    """
    if not _hash(expected_sha256) or (expected_size is not None and
        not _positive(expected_size)) or not _positive(limit_bytes) or limit_bytes>_MAX_BYTES:
        _fail("invalid_media_digest_or_limit")
    if type(path) is not str or not path or "\x00" in path:
        _fail("media_path_invalid")
    if media_kind not in (None,"image","video"):
        _fail("media_kind_invalid")
    roots_ok = _trusted_roots(roots)
    supplied = Path(path).expanduser()
    if not supplied.is_absolute():
        _fail("media_path_invalid")
    try:
        normalized = supplied.resolve(strict=True)
        root = next((x for x in roots_ok if normalized.is_relative_to(x)),None)
        if root is None or normalized == root:
            _fail("media_path_not_allowed")
        # Every symlink component between the configured root and leaf is
        # denied (including leaf); the resolver never follows a foreign link.
        relative = supplied.relative_to(root)
        walked = root
        for component in relative.parts:
            walked = walked / component
            if walked.is_symlink():
                _fail("media_symlink_not_allowed")
        info = supplied.lstat()
        if not stat.S_ISREG(info.st_mode):
            _fail("media_file_not_regular")
        if not 0 < info.st_size <= limit_bytes:
            _fail("media_file_size_invalid")
        if expected_size is not None and info.st_size != expected_size:
            _fail("media_size_mismatch")
        flags = os.O_RDONLY | getattr(os,"O_BINARY",0) | getattr(os,"O_NOFOLLOW",0)
        fd = os.open(supplied,flags)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or
                (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns) !=
                (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)):
                _fail("media_file_changed")
            digest = hashlib.sha256()
            read_count = 0
            signature = b""
            with os.fdopen(fd,"rb") as handle:
                fd = -1
                while True:
                    chunk = handle.read(1024*1024)
                    if not chunk:
                        break
                    if not signature:
                        signature = chunk[:16]
                    read_count += len(chunk)
                    if read_count > limit_bytes:
                        _fail("media_file_size_invalid")
                    digest.update(chunk)
                after = os.fstat(handle.fileno())
            final = supplied.lstat()
            if ((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns) !=
                (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or
                (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns) !=
                (final.st_dev,final.st_ino,final.st_size,final.st_mtime_ns) or
                read_count != before.st_size):
                _fail("media_file_changed")
            if digest.hexdigest()!=expected_sha256:
                _fail("media_sha256_mismatch")
            if media_kind=="video" and not (
                (len(signature)>=12 and signature[4:8]==b"ftyp")
                or signature.startswith(b"\\x1a\\x45\\xdf\\xa3")
            ):
                _fail("video_container_unrecognized")
            if media_kind=="image" and not (
                signature.startswith(b"\\x89PNG\\r\\n\\x1a\\n")
                or signature.startswith(b"\\xff\\xd8\\xff")
                or (signature.startswith(b"RIFF") and signature[8:12]==b"WEBP")
                or signature.startswith((b"GIF87a",b"GIF89a",b"BM"))
            ):
                _fail("image_signature_unrecognized")
            return expected_sha256,read_count
        finally:
            if fd >= 0:
                os.close(fd)
    except EvidenceFailure:
        raise
    except (OSError,ValueError,RuntimeError):
        _fail("media_file_unavailable")


def _validate_time(record: Mapping, media: MediaByteProof, kind: str) -> tuple[str,int|None,int|None]:
    timebase = record["timebase"]
    start,end = record.get("start_ms"),record.get("end_ms")
    origin,length=record.get("clip_origin_ms"),record.get("clip_duration_ms")
    slide=record.get("slide_index")
    if kind == "slide_ref":
        if (timebase!="none" or start is not None or end is not None or
            origin is not None or length is not None or
            not _positive(slide) or slide != media.slide_index):
            _fail("slide_has_fabricated_timestamp_or_order")
        return "none",None,None
    if kind not in _TIMED or timebase not in ("video_absolute","clip_relative"):
        _fail("observation_timebase_invalid")
    if not _nat(start) or not _nat(end):
        _fail("observation_interval_invalid")
    if kind=="sampled_frame":
        if end!=start:
            _fail("frame_must_be_point")
    elif end<=start:
        _fail("observation_interval_invalid")
    if slide is not None and (not _positive(slide) or slide!=media.slide_index):
        _fail("observation_slide_mapping_mismatch")
    if timebase=="video_absolute":
        if origin is not None or length is not None:
            _fail("timebase_mixed")
        if media.duration_ms is None:
            _fail("media_duration_unavailable")
        absolute_start,absolute_end=start,end
    else:
        if not _nat(origin) or not _positive(length) or end>length:
            _fail("clip_bounds_invalid")
        if media.duration_ms is None or origin+length>media.duration_ms:
            _fail("clip_origin_unverified")
        absolute_start,absolute_end=origin+start,origin+end
    if absolute_end>media.duration_ms:
        _fail("observation_outside_media")
    return "video_absolute",absolute_start,absolute_end


def validate_observations(expected_proofs: object, catalog: object,
                          *, project_id: str, video_id: int, source_id: str,
                          media: tuple[MediaByteProof,...], roots: object,
                          max_evidence: int = _MAX_EVIDENCE) -> tuple[VerifiedObservation,...]:
    """Resolve *every* frozen A proof from an authenticated catalog, hash bytes.

    Catalog is injected: resolve_a_proof(kind,ref_sha256) -> authoritative
    project/video/source/media/path record. No best-effort path or copying text.
    """
    if not _positive(max_evidence) or max_evidence>_MAX_EVIDENCE or type(expected_proofs) not in (list,tuple):
        _fail("evidence_set_invalid")
    if len(expected_proofs)>max_evidence:
        _fail("evidence_limit_exceeded")
    if expected_proofs and not callable(getattr(catalog,"resolve_a_proof",None)):
        _fail("evidence_catalog_unavailable")
    allowed={x.asset_id:x for x in media}
    requested=set()
    normalized=[]
    for proof in expected_proofs:
        if type(proof) not in (list,tuple) or len(proof)!=3:
            _fail("a_proof_invalid")
        upstream_kind,ref_sha,asset_sha=proof
        kind=_KINDS.get(upstream_kind)
        if kind is None or not _hash(ref_sha) or not _hash(asset_sha) or (upstream_kind,ref_sha) in requested:
            _fail("a_proof_duplicate_or_invalid")
        requested.add((upstream_kind,ref_sha))
        try:
            record=catalog.resolve_a_proof(upstream_kind,ref_sha)
        except Exception:
            _fail("evidence_record_unavailable")
        if type(record) is not dict or not _REQUIRED.issubset(record) or set(record)-(_REQUIRED|_OPTIONAL):
            _fail("evidence_record_invalid")
        if (record["kind"]!=kind or record["reference_sha256"]!=ref_sha or
            record["project_id"]!=project_id or record["video_id"]!=video_id or
            record["source_id"]!=source_id or
            not _positive(record["media_asset_id"]) or
            record["asset_sha256"]!=asset_sha or not _hash(record["content_sha256"])):
            _fail("evidence_provenance_mismatch")
        current=allowed.get(record["media_asset_id"])
        if current is None or current.source_sha256!=asset_sha:
            _fail("evidence_media_mismatch")
        verify_local_bytes(record["local_path"],record["content_sha256"],roots)
        timebase,start,end=_validate_time(record,current,kind)
        frame_ref=record.get("frame_ref_sha256")
        if kind=="ocr_span":
            if not _hash(frame_ref):
                _fail("ocr_frame_reference_missing")
        elif frame_ref is not None:
            _fail("unexpected_frame_reference")
        normalized.append(VerifiedObservation(
            kind,ref_sha,record["content_sha256"],asset_sha,
            current.asset_id,timebase,start,end,
            current.slide_index if kind=="slide_ref" else record.get("slide_index"),
            frame_ref
        ))
    frame_by_ref={r.ref_sha256:r for r in normalized if r.kind=="sampled_frame"}
    for observation in normalized:
        if observation.kind != "ocr_span":
            continue
        frame=frame_by_ref.get(observation.frame_ref_sha256)
        if (frame is None or frame.media_asset_id!=observation.media_asset_id or
            frame.start_ms is None or observation.start_ms is None or
            not observation.start_ms<=frame.start_ms<=observation.end_ms):
            _fail("ocr_frame_lineage_mismatch")
    return tuple(normalized)
