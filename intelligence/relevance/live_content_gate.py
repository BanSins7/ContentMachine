"""M08: current D1 rights, expiry, immutable content and lineage gate.

No network/provider imports or paid calls. This module cannot authorize a
project owner: trusted service entrypoints must authenticate callers. A
previously accepted S1 payload or self-signed hash does NOT grant current
source access. Only an actual PostgreSQL D1/S3 read can authorize use.
"""
from __future__ import annotations

from .v1_contracts import (
    authorize_content_usage, fingerprint, positive, project_id, record,
    reject, sha256_digest, validate_content_input,
)

GATE_VERSION = "sre-live-content-gate/v1"
PHASES = frozenset((
    "planning", "before_offline_compute", "before_external_send",
    "before_publish", "result_read",
))
PIN_REQUIRED = ("video_id", "content_version_id", "content_sha256")
PIN_OPTIONAL = (
    "source_id", "source_policy_version_id", "discovery_id",
    "metadata_observation_id", "completeness", "expires_at",
)


def validate_media_fidelity(content):
    """S1 shape plus M08 evidence completeness: no invented media capability.

    A 'full' video can be silent (transcript absent) or have no named scenes;
    no specific media stream may be fabricated to satisfy this validator.
    Instead a partial/full revision must be *source-backed* by an actual media
    asset and at least one observed nonempty segment. metadata_only supports
    title/description/hashtags exclusively and cannot imply any media.
    """
    obj = validate_content_input(content)
    media = obj["data"]
    segments = any(media.get(name) for name in ("transcript", "scenes"))
    if obj["completeness"] == "metadata_only":
        if segments or obj["media_asset_id"] is not None:
            reject("completeness", "metadata_only cannot contain media evidence",
                   "snapshot_mismatch")
    else:
        if not segments or obj["media_asset_id"] is None:
            reject("completeness",
                   "partial/full needs source-backed measured media evidence",
                   "missing_provenance")
        if obj["rights"]["can_analyze_media"] is not True:
            reject("rights.can_analyze_media", "media analysis right was not granted",
                   "rights_denied")
    return obj


def _validate_pins(pins):
    if pins is None:
        return None
    record(pins, "pinned_content", required=PIN_REQUIRED, optional=PIN_OPTIONAL)
    for key in ("video_id", "content_version_id", "source_policy_version_id",
                "discovery_id", "metadata_observation_id"):
        if key in pins and pins[key] is not None:
            positive(pins[key], "pinned_content." + key)
    sha256_digest(pins["content_sha256"], "pinned_content.content_sha256")
    return dict(pins)


def verify_pinned_content(content, provenance, *, pins=None):
    """Pure equality check; NEVER replaces the DB-backed read or creates rights."""
    obj = validate_media_fidelity(content)
    from .ports.d1_provenance import validate_d1_provenance
    proof = validate_d1_provenance(provenance)
    # A signed provenance digest is not evidence of DB authority. The external
    # D1 reader verifies actual rows; this helper ensures transport coherence.
    matches = (
        obj["project_id"] == proof["project_id"]
        and obj["video_id"] == proof["video_id"]
        and obj["content_version_id"] == proof["content_version_id"]
        and obj["source_id"] == proof["source_id"]
        and obj["source_policy_version_id"] == proof["source_policy_version_id"]
        and obj["content_sha256"] == proof["content_sha256"]
        and obj["completeness"] == proof["completeness"]
        and obj["expires_at"] == proof["expires_at"]
        and obj["provenance"]["discovery_id"] == proof["discovery_id"]
        and obj["provenance"]["search_query_id"] == proof["search_query_id"]
        and obj["provenance"]["source_request_id"] == proof["source_request_id"]
    )
    if not matches:
        reject("content_provenance", "S3 content and D1 source lineage differ",
               "snapshot_mismatch")
    expected = _validate_pins(pins)
    if expected is not None:
        actual = {
            "video_id": proof["video_id"],
            "content_version_id": proof["content_version_id"],
            "content_sha256": proof["content_sha256"],
            "source_id": proof["source_id"],
            "source_policy_version_id": proof["source_policy_version_id"],
            "discovery_id": proof["discovery_id"],
            "metadata_observation_id": proof["metadata_observation_id"],
            "completeness": proof["completeness"],
            "expires_at": proof["expires_at"],
        }
        for field, value in expected.items():
            if actual[field] != value:
                reject("pinned_content." + field, "pinned input version or source changed",
                       "snapshot_mismatch")
    return obj


def read_live_content_gate(conn, project, content_version_id, *,
                           phase="planning", pins=None, external_llm=False):
    """Authoritative D1/S3 gate in caller's existing consistent DB transaction.

    For outbound provider data, ALWAYS use 'before_external_send'; this
    selects external_llm=True regardless of caller input. For an outbound
    analytics result, use 'before_publish'. No client-supplied content rights
    or project ownership claims are accepted as proof.
    """
    if phase not in PHASES or type(phase) is not str:
        reject("phase", "unsupported use phase", "unsupported_schema_version")
    if type(external_llm) is not bool:
        reject("external_llm", "expected explicit boolean")
    pid = project_id(project)
    cid = positive(content_version_id, "content_version_id")
    from .ports.d1_provenance import read_d1_provenance
    from .intake import read_content

    # The source-specific, project-scoped D1 reader calls read_content and
    # rechecks the source policy, observation and SHA before returning its
    # authoritative lineage. Read actual content afterward from the SAME
    # transaction, rather than accepting any client-supplied payload.
    send_external = external_llm or phase == "before_external_send"
    proof = read_d1_provenance(conn, pid, cid, external_llm=send_external)
    content = read_content(conn, pid, cid, external_llm=send_external)
    verified = verify_pinned_content(content, proof, pins=pins)
    # Separate pure assertion also catches forged offline envelopes; it does
    # not grant rights, whose source is solely the live D1 policy DB read.
    authorize_content_usage(verified, external_llm=send_external)
    return {
        "schema_version": GATE_VERSION,
        "project_id": pid,
        "phase": phase,
        "content": verified,
        "provenance": proof,
        "content_sha256": verified["content_sha256"],
        "version_pin_sha256": fingerprint({
            "project_id": pid, "video_id":verified["video_id"],
            "content_version_id":cid,
            "content_sha256":verified["content_sha256"],
            "source_id":verified["source_id"],
            "source_policy_version_id":verified["source_policy_version_id"],
            "discovery_id":proof["discovery_id"],
            "metadata_observation_id":proof["metadata_observation_id"],
        }),
    }


def load_live_content_gate(project, content_version_id, *, phase="planning",
                           pins=None, external_llm=False, connect=None):
    """One consistent read-only transaction; no network or SQL writes."""
    from intelligence.taxonomy_builder.pipeline_repository import _connect

    pid = project_id(project)
    cid = positive(content_version_id, "content_version_id")
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return read_live_content_gate(
                conn, pid, cid, phase=phase, pins=pins, external_llm=external_llm,
            )


__all__ = (
    "GATE_VERSION", "PHASES", "validate_media_fidelity",
    "verify_pinned_content", "read_live_content_gate", "load_live_content_gate",
)
