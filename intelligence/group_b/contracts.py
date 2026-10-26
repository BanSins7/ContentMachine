"""Minimal Group B-owned, dependency-free input boundary (bootstrap v0).

This is NOT the QDP producer contract. Concrete versioned adapter translators
and authoritative DB checks are introduced in GB-MOD-04/05.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID

_PROJECT = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = {
    "video": "video",
    "slideshow_video": "video",
    "photo_carousel": "carousel",
    "mixed_carousel": "carousel",
}

class ContractError(ValueError):
    """Typed bootstrap boundary failure; never silently infer missing evidence."""
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code

def content_type_for_kind(kind: str) -> str:
    if type(kind) is not str or kind not in _KINDS:
        raise ContractError("unsupported_content_kind", "Only approved B video/carousel types are supported")
    return _KINDS[kind]

def canonical_hash(payload: dict) -> str:
    if type(payload) is not dict:
        raise ContractError("invalid_payload", "Canonical payload must be an object")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise ContractError("invalid_payload", "Payload is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()

@dataclass(frozen=True)
class GroupBInputPointer:
    """Local normalized pointer, NOT evidence that QDP/Group A is connected."""
    project_id: str
    qdp_run_id: str
    video_id: int
    source_id: str
    content_kind: str
    group_a_analysis_id: int
    qdp_input_sha256: str
    group_a_snapshot_sha256: str
    media_manifest_sha256: str
    schema_version: str = "group-b-input/v1"

    def __post_init__(self):
        if type(self.project_id) is not str or not _PROJECT.fullmatch(self.project_id):
            raise ContractError("invalid_project_id", "Expected 32 lower-hex project ID")
        if type(self.qdp_run_id) is not str:
            raise ContractError("invalid_qdp_run", "Expected canonical UUID string")
        try:
            parsed = UUID(self.qdp_run_id)
        except (ValueError, AttributeError) as exc:
            raise ContractError("invalid_qdp_run", "Expected UUID") from exc
        if str(parsed) != self.qdp_run_id:
            raise ContractError("invalid_qdp_run", "UUID must be canonical lower-case")
        if type(self.video_id) is not int or self.video_id <= 0:
            raise ContractError("invalid_video_id", "Canonical D1 video ID must be positive integer")
        if type(self.group_a_analysis_id) is not int or self.group_a_analysis_id <= 0:
            raise ContractError("invalid_a_analysis_id", "Group A analysis ID must be positive integer")
        if type(self.source_id) is not str or not self.source_id.strip() or len(self.source_id) > 250:
            raise ContractError("invalid_source_id", "Source ID must be bounded nonblank text")
        content_type_for_kind(self.content_kind)
        for name in ("qdp_input_sha256", "group_a_snapshot_sha256", "media_manifest_sha256"):
            val = getattr(self, name)
            if type(val) is not str or not _SHA.fullmatch(val):
                raise ContractError("invalid_sha256", f"{name} must be 64 lower-hex characters")
        if self.schema_version != "group-b-input/v1":
            raise ContractError("unsupported_schema_version", "Unsupported local input schema")
