"""Verified QDP qdp-event/v1 + qdp-preparation/v2 consumer for Group B.

The original B intake and legacy v1 reader are kept untouched in semantics.
Only the installed QDP→B adapter injects this version-negotiated reader.
QDP separately checks the local SRE and media bytes before dispatch.
"""
from __future__ import annotations

from intelligence.group_b.adapters.qdp import (
    QDPHandoff, VerifiedPreparation, _sha, _one_mapping,
)
from intelligence.group_b.contracts import canonical_hash, content_type_for_kind, ContractError
from intelligence.group_b.errors import DependencyUnavailable


def read_qdp_v2(conn, event: QDPHandoff, announced_event_sha256: str) -> VerifiedPreparation:
    """Pin a CURRENT prepared B video to the immutable original QDP event.

    Run inside B inbox transaction, not a stale discovery read. Reject
    replay after policy/media revocation and mismatched QDP outbox digests.
    """
    if not isinstance(event, QDPHandoff):
        raise DependencyUnavailable("qdp", "verified_notice_required")
    _sha(announced_event_sha256,"original_qdp_event_sha256")
    required = (
        "qdp_outbox", "qdp_outbox_deliveries", "qdp_runs",
        "qdp_group_current_preparations", "qdp_invalidations",
        "sources", "source_policy_versions", "media_assets",
        "videos", "source_video_metadata",
    )
    missing = conn.execute(
        """SELECT v.rel FROM unnest(%s::text[]) AS v(rel)
            WHERE to_regclass('public.' || v.rel) IS NULL""",
        (list(required),),
    ).fetchall()
    if missing:
        raise DependencyUnavailable("qdp", "qdp_v2_relation_missing")
    result = _one_mapping(conn.execute(
        """SELECT o.event_id::text AS event_id,
                  o.payload AS event_payload,o.payload_sha256 AS event_sha,
                  o.schema_version AS event_version,o.event_type,
                  d.state AS delivery_state,q.status AS qdp_state,
                  q.cancel_requested,p.state AS preparation_state,
                  p.inputs,p.input_sha256,p.source_id,p.source_policy_version_id,
                  p.metadata_id,p.media_asset_id,
                  s.status AS source_state,s.can_analyze_media,
                  sp.is_active AS policy_active,sp.permissions AS policy_permissions,
                  v.content_kind,m.sha256 AS media_sha256,
                  m.video_id AS media_video_id,m.source_id AS media_source_id,
                  m.media_kind,m.permission AS media_permission,
                  m.expires_at AS media_expires_at,
                  md.is_current AS metadata_current,md.deleted_at,
                  md.expires_at AS metadata_expires_at
           FROM qdp_outbox o
           JOIN qdp_outbox_deliveries d
             ON d.event_id=o.event_id AND d.consumer='group_b'
           JOIN qdp_runs q ON q.id=o.run_id AND q.project_id=o.project_id
           JOIN qdp_group_current_preparations p
             ON p.run_id=o.run_id AND p.project_id=o.project_id
            AND p.video_id=o.video_id AND p.analysis_group='B'
           JOIN sources s ON s.id=p.source_id
           JOIN source_policy_versions sp
             ON sp.id=p.source_policy_version_id AND sp.source_id=s.id
           JOIN videos v ON v.id=p.video_id
           JOIN media_assets m
             ON m.id=p.media_asset_id AND m.video_id=p.video_id
            AND m.source_id=p.source_id
           JOIN source_video_metadata md
             ON md.id=p.metadata_id AND md.video_id=p.video_id
            AND md.source_id=p.source_id
            AND md.source_policy_version_id=p.source_policy_version_id
           WHERE o.event_id=%s::uuid AND o.project_id=%s
             AND o.run_id=%s::uuid AND o.video_id=%s
             AND o.analysis_group='B'
           FOR SHARE OF o,d,q,s,sp,v,m,md""",
        (event.event_id,event.project_id,event.qdp_run_id,event.video_id),
    ))
    if result is None:
        raise DependencyUnavailable("qdp", "current_b_preparation_unavailable")
    expected_event = {
        "schema_version":"qdp-event/v1",
        "event_type":"preparation_ready",
        "project_id":event.project_id,
        "run_id":event.qdp_run_id,
        "video_id":event.video_id,
        "analysis_group":"B",
        "state":"ready",
        "input_sha256":event.input_sha256,
    }
    payload = result["event_payload"]
    if (type(payload) is not dict or
        any(payload.get(k) != v for k,v in expected_event.items()) or
        payload.get("reason_code") is not None or
        result["event_version"] != "qdp-event/v1" or
        result["event_type"] != "preparation_ready" or
        result["event_id"] != event.event_id or
        canonical_hash(payload) != result["event_sha"] or
        result["event_sha"] != announced_event_sha256):
        raise DependencyUnavailable("qdp", "qdp_v2_event_sha_or_identity_mismatch")
    if (result["delivery_state"] not in ("pending","leased","acknowledged") or
        result["qdp_state"] not in ("running","completed","partial") or
        result["cancel_requested"] is not False or
        result["preparation_state"] != "ready"):
        raise DependencyUnavailable("qdp", "qdp_b_delivery_not_current")
    inputs = result["inputs"]
    if (type(inputs) is not dict or inputs.get("result_schema") != "qdp-preparation/v2" or
        inputs.get("group") != "B" or
        result["input_sha256"] != event.input_sha256 or
        canonical_hash(inputs) != event.input_sha256):
        raise DependencyUnavailable("qdp", "qdp_v2_preparation_hash_mismatch")
    kind = result["content_kind"]
    try:
        content_type = content_type_for_kind(kind)
    except ContractError as exc:
        raise DependencyUnavailable("qdp", "unsupported_content_kind") from exc
    if content_type != "video" or inputs.get("media_kind") != "video" or result["media_kind"] != "video":
        raise DependencyUnavailable("qdp", "qdp_b_video_media_required")
    if (result["source_state"] != "enabled" or
        result["can_analyze_media"] is not True or
        result["policy_active"] is not True or
        type(result["policy_permissions"]) is not dict or
        result["policy_permissions"].get("can_analyze_media") is not True or
        result["metadata_current"] is not True or
        result["deleted_at"] is not None or
        result["media_permission"] in (None,"","metadata_only")):
        raise DependencyUnavailable("qdp", "qdp_b_source_rights_revoked")
    for k in ("media_expires_at","metadata_expires_at"):
        expires=result[k]
        if expires is not None and conn.execute(
            "SELECT %s <= now()",(expires,),
        ).fetchone()[0]:
            raise DependencyUnavailable("qdp", "qdp_b_source_expired")
    source,policy,media=result["source_id"],result["source_policy_version_id"],result["media_asset_id"]
    sha=result["media_sha256"]
    if (type(source) is not str or not source or
        type(policy) is not int or policy <= 0 or
        type(media) is not int or media <= 0 or
        type(sha) is not str or len(sha)!=64 or
        type(inputs.get("media_size_bytes")) is not int or
        inputs["media_size_bytes"] <= 0 or
        inputs.get("enrichment_source_id") != source or
        inputs.get("source_policy_version_id") != policy or
        inputs.get("metadata_id") != result["metadata_id"] or
        inputs.get("media_asset_id") != media or
        inputs.get("media_sha256") != sha or
        result["media_video_id"] != event.video_id or
        result["media_source_id"] != source):
        raise DependencyUnavailable("qdp", "qdp_b_media_lineage_inconsistent")
    _sha(sha,"media_sha256")
    # A stale or compromised caller must not bypass the QDP current view.
    # QDP dispatch_once also checks actual SRE and authorized media bytes
    # immediately before calling the receiver, then rights again at ACK.
    return VerifiedPreparation(
        project_id=event.project_id,event_id=event.event_id,
        qdp_run_id=event.qdp_run_id,video_id=event.video_id,
        source_id=source,content_kind=kind,content_type=content_type,
        input_sha256=event.input_sha256,event_sha256=announced_event_sha256,
        source_policy_version_id=policy,media_asset_id=media,media_sha256=sha,
    )
