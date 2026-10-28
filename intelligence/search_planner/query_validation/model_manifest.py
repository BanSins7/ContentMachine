"""SP-06: operator-verified, completely offline SentenceTransformer asset manifest.

This pin is a LOCAL byte-level integrity attestation, not proof that a remote
model's revision is genuine or multilingual. The operator must separately
verify the upstream repo/revision and licenses before preparing local files.
Neither manifest construction nor verification downloads anything.
"""
from __future__ import annotations

from hashlib import sha256 as hash256
from pathlib import Path
import json
import re

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, canonical, sha256

MANIFEST_FILENAME = "cm_embedding_manifest.json"
SCHEMA_VERSION = "search-planner-embedding-model/v1"
REVISION = re.compile(r"[0-9a-f]{40}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
MAX_MANIFEST_BYTES = 512_000
MAX_FILES = 2000
MAX_RELATIVE_PATH = 260


def _directory(root):
    path = Path(root).expanduser().absolute()
    if not path.is_dir() or path.is_symlink():
        raise PipelineError("Embedding model must be an existing local non-symlink directory", 422)
    return path


def _files(path):
    """Explicitly reject symlinks, nested symlinks, untracked files or dirs."""
    found = {}
    for item in path.rglob("*"):
        if item.is_symlink():
            raise PipelineError("Embedding weights must not contain symlinks", 409)
        if item.is_dir():
            continue
        if not item.is_file():
            raise PipelineError("Unexpected embedding model asset", 409)
        name = item.relative_to(path).as_posix()
        if name == MANIFEST_FILENAME:
            continue
        if (len(name) > MAX_RELATIVE_PATH or
                name.startswith(".git/") or
                len(found) >= MAX_FILES):
            raise PipelineError("Embedding manifest asset limits exceeded", 409)
        found[name] = item
    if not found or "modules.json" not in found:
        raise PipelineError("Provisioned SentenceTransformer modules.json is required", 409)
    return found


def _file_sha(path):
    dig = hash256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            dig.update(block)
    return dig.hexdigest()


def build_model_manifest(model_path, *, model_id, revision, dimensions):
    """Operator offline preparation only; returns JSON, never writes files."""
    path = _directory(model_path)
    if type(model_id) is not str or not MODEL_ID.fullmatch(model_id):
        raise PipelineError("Expected a pinned upstream SentenceTransformer model id", 422)
    if type(revision) is not str or not REVISION.fullmatch(revision):
        raise PipelineError("Explicit 40-character upstream git revision required", 422)
    if type(dimensions) is not int or not 1 <= dimensions <= 4096:
        raise PipelineError("Invalid pinned embedding dimensions", 422)
    assets = _files(path)
    return {
        "schema_version": SCHEMA_VERSION,
        "model_id": model_id,
        "revision": revision,
        "dimensions": dimensions,
        "files": {name: _file_sha(file) for name, file in sorted(assets.items())},
    }


def verify_model_manifest(model_path):
    """Rehash ALL weights and configs before loading or using production QV."""
    path = _directory(model_path)
    filename = path / MANIFEST_FILENAME
    if not filename.is_file() or filename.is_symlink() or filename.stat().st_size > MAX_MANIFEST_BYTES:
        raise PipelineError("Pinned local embedding model manifest missing or oversized", 409)
    try:
        manifest = json.loads(filename.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError) as exc:
        raise PipelineError("Unreadable embedding model manifest", 409) from exc
    if type(manifest) is not dict or set(manifest) != {
        "schema_version", "model_id", "revision", "dimensions", "files",
    }:
        raise PipelineError("Invalid embedding model manifest structure", 409)
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise PipelineError("Unsupported embedding manifest version", 409)
    if (type(manifest["model_id"]) is not str or
            MODEL_ID.fullmatch(manifest["model_id"]) is None or
            type(manifest["revision"]) is not str or
            REVISION.fullmatch(manifest["revision"]) is None or
            type(manifest["dimensions"]) is not int or
            not 1 <= manifest["dimensions"] <= 4096):
        raise PipelineError("Invalid embedding model provenance pins", 409)
    declared = manifest["files"]
    if (type(declared) is not dict or not 1 <= len(declared) <= MAX_FILES or
            any(type(k) is not str or type(v) is not str or
                not HEX.fullmatch(v) for k, v in declared.items())):
        raise PipelineError("Invalid embedding artifact SHA set", 409)
    found = _files(path)
    if set(found) != set(declared):
        raise PipelineError("Unexpected or missing embedding artifact", 409)
    for name, file in sorted(found.items()):
        if _file_sha(file) != declared[name]:
            raise PipelineError("Embedding artifact checksum mismatch: " + name, 409)
    return {
        "manifest": manifest,
        "manifest_sha256": sha256(manifest),
        "model_key": "st-local-sha256:" + sha256(manifest),
    }
