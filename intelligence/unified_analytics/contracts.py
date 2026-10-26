"""Immutable local normalized Unified Analytics input boundary.

NOT a QDP/SRE/Group A/B/C provider schema. Future adapters must independently
prove source, project, policy, media and lineage compatibility using real data.
No external provider/DB/network actions are performed by this module.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID

PROJECT_RE = re.compile(r"[0-9a-f]{32}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
CONTENT_KIND_TO_B = {
    "video": "video",
    "slideshow_video": "video",
    "photo_carousel": "carousel",
    "mixed_carousel": "carousel",
}

class AnalyticsBoundaryError(ValueError):
    """Strict fail-closed boundary diagnostic."""
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def content_type_for_kind(kind: str) -> str:
    if type(kind) is not str or kind not in CONTENT_KIND_TO_B:
        raise AnalyticsBoundaryError(
            "unsupported_content_kind",
            "Only confirmed video/carousel kinds have the required Group B contract",
        )
    return CONTENT_KIND_TO_B[kind]


def canonical_hash(payload: dict) -> str:
    """Stable SHA of canonical JSON metadata, not a media hash."""
    if type(payload) is not dict:
        raise AnalyticsBoundaryError("invalid_payload", "Expected JSON object")
    try:
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise AnalyticsBoundaryError("invalid_payload", "Payload is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class UnifiedAnalyticsInputPointer:
    """UAE-owned untrusted normalized join pointer.

    Holds identifiers only. Does not certify that referenced records exist
    or are current. Never infer completed/qualified merely by constructing it.
    """
    project_id: str
    video_id: int
    source_id: str
    source_policy_version_id: int
    content_kind: str
    qdp_run_id: str
    preparation_sha256: str
    group_a_analysis_id: int
    group_b_analysis_id: int
    calculated_metric_id: int
    sre_decision_sha256: str
    schema_version: str = "unified-analytics-input/v1"

    def __post_init__(self) -> None:
        if type(self.project_id) is not str or not PROJECT_RE.fullmatch(self.project_id):
            raise AnalyticsBoundaryError("invalid_project_id", "Expected 32 lower-hex chars")
        for name in (
            "video_id", "source_policy_version_id", "group_a_analysis_id",
            "group_b_analysis_id", "calculated_metric_id",
        ):
            val = getattr(self, name)
            if type(val) is not int or val <= 0:
                raise AnalyticsBoundaryError(
                    "invalid_identifier", f"{name} must be a positive integer"
                )
        if type(self.source_id) is not str or not self.source_id.strip() or len(self.source_id) > 250:
            raise AnalyticsBoundaryError("invalid_source_id", "Expected bounded nonempty source ID")
        content_type_for_kind(self.content_kind)
        if type(self.qdp_run_id) is not str:
            raise AnalyticsBoundaryError("invalid_qdp_run_id", "Expected canonical UUID string")
        try:
            parsed = UUID(self.qdp_run_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise AnalyticsBoundaryError("invalid_qdp_run_id", "Expected UUID") from exc
        if str(parsed) != self.qdp_run_id:
            raise AnalyticsBoundaryError("invalid_qdp_run_id", "Expected canonical lowercase UUID")
        for name in ("preparation_sha256", "sre_decision_sha256"):
            val = getattr(self, name)
            if type(val) is not str or not SHA256_RE.fullmatch(val):
                raise AnalyticsBoundaryError(
                    "invalid_sha256", f"{name} must be 64 lowercase hexadecimal characters"
                )
        if self.schema_version != "unified-analytics-input/v1":
            raise AnalyticsBoundaryError("unsupported_schema_version", "Unknown UAE input schema")
