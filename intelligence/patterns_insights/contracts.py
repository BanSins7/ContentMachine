"""Strict PIE-owned boundary for a normalized *pointer*, not an UAE result.

The type validates shape, not the existence, rights, quality, or statistical
validity of referenced external observations. Those checks follow in PIE-MOD.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re

_PROJECT = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class PIEBoundaryError(ValueError):
    """Typed refusal, never a claim about live qualified data."""
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def canonical_hash(payload: dict) -> str:
    """Hash canonical JSON metadata, not physical media or source content."""
    if type(payload) is not dict:
        raise PIEBoundaryError("invalid_payload", "Expected JSON object")
    try:
        encoded = json.dumps(
            payload, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PIEBoundaryError("invalid_payload", "Invalid JSON metadata") from exc
    return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PIEDatasetPointer:
    """PIE normalized dataset pointer, NOT proof of UAE capability."""
    project_id: str
    source_id: str
    source_policy_version_id: int
    uae_engine_version: str
    uae_dataset_sha256: str
    uae_manifest_sha256: str
    calculation_config_id: int
    source_metric_definition: str
    source_metric_version: str
    as_of: str
    schema_version: str = "pie-dataset-pointer/v1"

    def __post_init__(self) -> None:
        if type(self.project_id) is not str or not _PROJECT.fullmatch(self.project_id):
            raise PIEBoundaryError("invalid_project", "Expected 32 lower-case hex characters")
        if type(self.source_id) is not str or not self.source_id.strip() or len(self.source_id) > 250:
            raise PIEBoundaryError("invalid_source", "Expected bounded nonblank source ID")
        for attr in ("source_policy_version_id", "calculation_config_id"):
            value = getattr(self, attr)
            if type(value) is not int or value <= 0:
                raise PIEBoundaryError("invalid_identifier", f"{attr} must be positive integer")
        for attr in ("uae_engine_version", "source_metric_definition", "source_metric_version"):
            value = getattr(self, attr)
            if type(value) is not str or not value.strip() or len(value) > 120:
                raise PIEBoundaryError("invalid_version", f"{attr} must be bounded nonblank text")
        for attr in ("uae_dataset_sha256", "uae_manifest_sha256"):
            value = getattr(self, attr)
            if type(value) is not str or not _HASH.fullmatch(value):
                raise PIEBoundaryError("invalid_sha256", f"{attr} must be 64 lower-case hex")
        if type(self.as_of) is not str or not self.as_of.strip():
            raise PIEBoundaryError("invalid_as_of", "Expected timezone-aware ISO 8601 timestamp")
        try:
            ts = datetime.fromisoformat(self.as_of.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise PIEBoundaryError("invalid_as_of", "Expected valid ISO 8601 timestamp") from exc
        if ts.tzinfo is None or ts.utcoffset() is None:
            raise PIEBoundaryError("invalid_as_of", "Timezone offset required")
        if self.schema_version != "pie-dataset-pointer/v1":
            raise PIEBoundaryError("unsupported_schema", "Unsupported PIE input schema")
