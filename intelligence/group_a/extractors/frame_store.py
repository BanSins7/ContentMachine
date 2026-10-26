"""GA-MOD-07 atomic, content-addressed local frame evidence storage.

PNG files + complete JSON manifest live on a dedicated local filesystem,
never in PostgreSQL. Writes use a same-directory staging directory, fsync,
atomic rename and cleanup on failure. Existing immutable batches are
verified against both manifest hash and every PNG digest before reuse.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from ..contracts_v1 import canonical_sha256
from .frames import FRAME_SCHEMA, FrameError, MAX_PNG_BYTES, _perceptual

PROJECT_RE = re.compile(r"^[a-f0-9]{32}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
NAME_RE = re.compile(r"^f[0-9]{3}-[0-9a-f]{64}\.png$")
MANIFEST_FILE = "manifest.json"


def _ensure_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise FrameError("unsafe_store_path")
    if path.exists():
        if not path.is_dir():
            raise FrameError("unsafe_store_path")
    else:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if path.is_symlink() or not path.is_dir():
            raise FrameError("unsafe_store_path")


def _write_fsync(path, data):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,"O_NOFOLLOW",0)
    with os.fdopen(os.open(path, flags, 0o600),"wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())


def _root(root):
    if type(root) is not str or not root or "\x00" in root:
        raise FrameError("invalid_store_root")
    path = Path(root)
    if not path.is_absolute() or ".." in path.parts or path.is_symlink():
        raise FrameError("invalid_store_root")
    _ensure_dir(path)
    return path.resolve(strict=True)


def _validate(manifest, blobs):
    if type(manifest) is not dict or type(blobs) is not dict:
        raise FrameError("invalid_manifest")
    required = {"schema_version","method_version","source_media_sha256",
                "source_byte_size","source_duration_sec","source_stream_index",
                "source_timebase","config","coverage","frames","manifest_sha256"}
    if set(manifest) != required or manifest["schema_version"] != FRAME_SCHEMA:
        raise FrameError("invalid_manifest")
    m_sha=manifest["manifest_sha256"]
    if type(m_sha) is not str or not SHA_RE.fullmatch(m_sha):
        raise FrameError("invalid_manifest")
    if canonical_sha256({k:v for k,v in manifest.items() if k!="manifest_sha256"}) != m_sha:
        raise FrameError("manifest_digest_mismatch")
    frames=manifest["frames"]
    if type(frames) is not list or not 1<=len(frames)<=48:
        raise FrameError("invalid_manifest")
    expected=set()
    for f in frames:
        if type(f) is not dict:
            raise FrameError("invalid_frame_entry")
        sha=f.get("png_sha256")
        idx=f.get("sample_index")
        if type(sha) is not str or not SHA_RE.fullmatch(sha) or type(idx) is not int or not 1<=idx<=240:
            raise FrameError("invalid_frame_entry")
        name=f"f{idx:03d}-{sha}.png"
        if not NAME_RE.fullmatch(name) or f.get("frame_ref")!=f"ga-frame:f{idx:03d}-{sha}":
            raise FrameError("invalid_frame_ref")
        if name in expected:
            raise FrameError("duplicate_frame_ref")
        expected.add(name)
        data=blobs.get(name)
        if (type(data) is not bytes or not 0<len(data)<=MAX_PNG_BYTES
            or sha256(data).hexdigest()!=sha or len(data)!=f.get("png_byte_size")):
            raise FrameError("frame_digest_mismatch")
        _,_,w,h=_perceptual(data)
        if w!=f.get("width") or h!=f.get("height"):
            raise FrameError("frame_dimensions_mismatch")
    if set(blobs)!=expected:
        raise FrameError("unexpected_frame_data")
    return m_sha


class FrameStore:
    def __init__(self, root: str):
        self.root=_root(root)

    def _batch(self, project_id, run_id, digest):
        if (type(project_id) is not str or not PROJECT_RE.fullmatch(project_id)
            or type(run_id) is not str or not UUID_RE.fullmatch(run_id)
            or type(digest) is not str or not SHA_RE.fullmatch(digest)):
            raise FrameError("invalid_store_identity")
        project=self.root/project_id
        _ensure_dir(project)
        run=project/run_id
        _ensure_dir(run)
        return run/digest

    def _verify_existing(self, location, manifest, blobs):
        if location.is_symlink() or not location.is_dir():
            raise FrameError("unsafe_store_path")
        m=location/MANIFEST_FILE
        if m.is_symlink() or not m.is_file():
            raise FrameError("immutable_store_conflict")
        try:
            saved=json.loads(m.read_text("utf-8"))
            if saved != manifest:
                raise FrameError("immutable_store_conflict")
            for name,data in blobs.items():
                p=location/name
                if p.is_symlink() or not p.is_file() or p.stat().st_size!=len(data):
                    raise FrameError("immutable_store_conflict")
                if sha256(p.read_bytes()).digest() != sha256(data).digest():
                    raise FrameError("immutable_store_conflict")
            if set(x.name for x in location.iterdir())!=set(blobs)|{MANIFEST_FILE}:
                raise FrameError("immutable_store_conflict")
        except FrameError:
            raise
        except (OSError,ValueError,UnicodeError) as exc:
            raise FrameError("immutable_store_conflict") from exc

    def commit(self, project_id, run_id, manifest, blobs):
        digest=_validate(manifest,blobs)
        target=self._batch(project_id,run_id,digest)
        ref=f"ga-frame-batch:{project_id}/{run_id}/{digest}"
        if target.exists() or target.is_symlink():
            self._verify_existing(target,manifest,blobs)
            return ref
        staging=Path(tempfile.mkdtemp(prefix=".frames-pending-",dir=target.parent))
        try:
            for name,buf in sorted(blobs.items()):
                _write_fsync(staging/name,buf)
            _write_fsync(staging/MANIFEST_FILE,
                         (json.dumps(manifest,sort_keys=True,separators=(",",":"),
                                     ensure_ascii=False)+"\n").encode("utf-8"))
            fd=os.open(staging,os.O_RDONLY | getattr(os,"O_DIRECTORY",0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.rename(staging,target)
            except OSError:
                if not target.exists():
                    raise
                self._verify_existing(target,manifest,blobs)
            parent_fd=os.open(target.parent,os.O_RDONLY | getattr(os,"O_DIRECTORY",0))
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            return ref
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def read_manifest(self, project_id, run_id, digest):
        location=self._batch(project_id,run_id,digest)
        if location.is_symlink():
            raise FrameError("unsafe_store_path")
        path=location/MANIFEST_FILE
        if path.is_symlink() or not path.is_file():
            raise FrameError("frame_batch_missing")
        try:
            manifest=json.loads(path.read_text("utf-8"))
            if manifest.get("manifest_sha256") != digest:
                raise FrameError("manifest_digest_mismatch")
            if canonical_sha256({k:v for k,v in manifest.items() if k!="manifest_sha256"})!=digest:
                raise FrameError("manifest_digest_mismatch")
            return manifest
        except (OSError,UnicodeError,ValueError) as exc:
            raise FrameError("frame_batch_corrupt") from exc

    def read_png(self, project_id, run_id, digest, frame_ref):
        if (type(frame_ref) is not str or not frame_ref.startswith("ga-frame:")
            or not NAME_RE.fullmatch(frame_ref[9:]+".png")):
            raise FrameError("invalid_frame_ref")
        manifest=self.read_manifest(project_id,run_id,digest)
        entry=next((x for x in manifest["frames"] if x["frame_ref"]==frame_ref),None)
        if entry is None:
            raise FrameError("unknown_frame_ref")
        location=self._batch(project_id,run_id,digest)
        p=location/(frame_ref[9:]+".png")
        if p.is_symlink() or not p.is_file():
            raise FrameError("frame_batch_corrupt")
        data=p.read_bytes()
        if len(data)!=entry["png_byte_size"] or sha256(data).hexdigest()!=entry["png_sha256"]:
            raise FrameError("frame_digest_mismatch")
        return data
