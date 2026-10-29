"""S3 metadata-only content assembly; no URL fetch or invented hashtags/media.

A transcript or scene can be accepted only in a future audited producer
integration. D1's title and description come from the exact source observation,
never from an unrelated video's global catalog row.
"""
from __future__ import annotations

from .v1_contracts import CONTRACT_VERSIONS, fingerprint, nonempty, validate_content_input
from .source_policy import utc_iso


def metadata_payload(observation):
    result = {"title": None, "description": None, "hashtags": []}
    if observation is not None:
        for key in ("title", "description"):
            value = observation.get(key)
            if value is not None and value.strip():
                result[key] = nonempty(value, f"observation.{key}", limit=30000)
    return result


def build_metadata_envelope(*, project, video, source, policy_id,
                            content_version, rights, observation,
                            discovery_id, search_query_id,
                            source_request_id, expiry):
    data = metadata_payload(observation)
    candidate = {
        "schema_version": CONTRACT_VERSIONS["content"],
        "project_id": project, "video_id": video, "source_id": source,
        "source_policy_version_id": policy_id,
        "content_version_id": content_version,
        "content_sha256": fingerprint(data),
        "completeness": "metadata_only",
        "rights": dict(rights),
        "data": data,
        "provenance": {"discovery_id": discovery_id,
                       "search_query_id": search_query_id,
                       "source_request_id": source_request_id},
        "media_asset_id": None,
        "expires_at": utc_iso(expiry),
    }
    return validate_content_input(candidate)


def intake_fingerprint(*, project, video, discovery_id, source,
                       policy_id, observation_id, content_sha256):
    """Immutable identity per D1 observation and project association.

    Excludes recomputed retention expiry to keep exact retries idempotent.
    A new policy or metadata observation creates a new revision.
    """
    return fingerprint({
        "project_id": project, "video_id": video,
        "discovery_id": discovery_id, "source_id": source,
        "source_policy_version_id": policy_id,
        "metadata_observation_id": observation_id,
        "content_sha256": content_sha256,
    })
