"""Canonical D1 -> SRE references, with strict typed BIGINT lineage.

An arbitrary client-supplied reference is NOT proof of rights or D1 provenance.
The DB-backed reader rechecks the live S3 policy/content row on every request.
Collector textual candidate UUIDs are deliberately NOT D1 legacy BIGINT FKs.
"""
from __future__ import annotations

from ..v1_contracts import (
    authorize_content_usage, code, positive, project_id, record, reject,
    sha256_digest, utc_time, validate_content_input,
)

DISCOVERY_REF_VERSION = "sre-discovery-ref/v1"


def validate_discovery_reference(payload):
    record(payload, "discovery_ref", required=(
        "schema_version", "project_id", "video_id", "discovery_id",
        "source_id", "source_policy_version_id", "content_version_id",
        "content_sha256", "completeness", "search_query_id",
        "source_request_id", "expires_at"))
    if payload["schema_version"] != DISCOVERY_REF_VERSION:
        reject("discovery_ref.schema_version", "expected "+DISCOVERY_REF_VERSION,
               "unsupported_schema_version")
    project_id(payload["project_id"])
    for key in ("video_id","discovery_id","source_policy_version_id",
                "content_version_id"):
        positive(payload[key], "discovery_ref."+key)
    code(payload["source_id"], "discovery_ref.source_id")
    sha256_digest(payload["content_sha256"], "discovery_ref.content_sha256")
    if payload["completeness"] not in ("metadata_only","partial","full"):
        reject("discovery_ref.completeness", "unknown D1 completeness")
    for key in ("search_query_id", "source_request_id"):
        if payload[key] is not None:
            # No UUID-as-bigint conversion. These nullable fields are legacy D1 BIGINT.
            positive(payload[key], "discovery_ref."+key)
    if payload["expires_at"] is not None:
        utc_time(payload["expires_at"], "discovery_ref.expires_at")
    return dict(payload)


def reference_from_authorized_content(content):
    """Output of S3 DB reader. A caller cannot use this to authorize raw input."""
    normalized = validate_content_input(content)
    discovery_id = normalized["provenance"]["discovery_id"]
    if discovery_id is None:
        reject("discovery_id", "SRE project association requires exact D1 discovery",
               "missing_provenance")
    result = {
        "schema_version": DISCOVERY_REF_VERSION,
        "project_id": normalized["project_id"],
        "video_id": normalized["video_id"],
        "discovery_id": discovery_id,
        "source_id": normalized["source_id"],
        "source_policy_version_id": normalized["source_policy_version_id"],
        "content_version_id": normalized["content_version_id"],
        "content_sha256": normalized["content_sha256"],
        "completeness": normalized["completeness"],
        "search_query_id": normalized["provenance"]["search_query_id"],
        "source_request_id": normalized["provenance"]["source_request_id"],
        "expires_at": normalized["expires_at"],
    }
    return validate_discovery_reference(result)


def read_verified_discovery(conn, project, content_version_id, *,
                            external_llm=False):
    """DB-authoritative S3 source-policy and project-scoped read, then version pin."""
    from ..intake import read_content
    result = read_content(conn, project, content_version_id, external_llm=external_llm)
    # authorize_content_usage already called by read_content; never trust an
    # external rights dictionary or unverified client source policy claim.
    return reference_from_authorized_content(result)
