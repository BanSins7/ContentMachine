"""Standalone Group C normalized pointer and frozen constants (bootstrap only).

This normalized pointer is local to Group C, NOT the QDP provider's event schema.
No raw metric data are fetched or scored by this module at GC-MOD-00.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

_PROJECT = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_KINDS = {
    "video": "video",
    "slideshow_video": "video",
    "photo_carousel": "carousel",
    "mixed_carousel": "carousel",
}

BREAKOUT_WEIGHTS = (
    Decimal("0.45"), Decimal("0.30"),
    Decimal("0.15"), Decimal("0.10"),
)
FRESHNESS_HALF_LIFE_H = Decimal("48")


class ContractError(ValueError):
    """A typed boundary error, never a scored result."""
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def content_type_for_kind(kind: str) -> str:
    if type(kind) is not str or kind not in _ALLOWED_KINDS:
        raise ContractError("unsupported_content_kind", "Unsupported or unknown publication kind")
    return _ALLOWED_KINDS[kind]


def canonical_hash(payload: dict) -> str:
    """Metadata fingerprint, not a cryptographic claim about media bytes."""
    if type(payload) is not dict:
        raise ContractError("invalid_payload", "A JSON object is required")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise ContractError("invalid_payload", "Invalid canonical JSON payload") from exc
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class GroupCInputPointer:
    """Internal QDP-C pointer; adapters must verify actual DB/provider identity."""
    project_id: str
    qdp_run_id: str
    video_id: int
    source_id: str
    content_kind: str
    metric_snapshot_id: int
    preparation_sha256: str
    metric_source_definition: str
    metric_source_version: str
    schema_version: str = "group-c-input/v1"

    def __post_init__(self) -> None:
        if type(self.project_id) is not str or not _PROJECT.fullmatch(self.project_id):
            raise ContractError("invalid_project_id", "Expected 32 lower-case hex")
        if type(self.qdp_run_id) is not str:
            raise ContractError("invalid_qdp_run", "Expected canonical UUID string")
        try:
            parsed = UUID(self.qdp_run_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise ContractError("invalid_qdp_run", "Expected UUID") from exc
        if str(parsed) != self.qdp_run_id:
            raise ContractError("invalid_qdp_run", "Expected canonical lower-case UUID")
        for name in ("video_id", "metric_snapshot_id"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ContractError("invalid_identifier", f"{name} must be positive integer")
        if type(self.source_id) is not str or not self.source_id.strip() or len(self.source_id) > 250:
            raise ContractError("invalid_source_id", "Expected bounded nonblank source ID")
        content_type_for_kind(self.content_kind)
        if type(self.preparation_sha256) is not str or not _SHA.fullmatch(self.preparation_sha256):
            raise ContractError("invalid_sha256", "Expected 64 lower-case hex digest")
        for name in ("metric_source_definition", "metric_source_version"):
            value = getattr(self, name)
            if type(value) is not str or not value.strip() or len(value) > 120:
                raise ContractError("invalid_metric_definition", f"{name} must be nonblank bounded text")
        if self.schema_version != "group-c-input/v1":
            raise ContractError("unsupported_schema_version", "Unknown normalized input schema")
