"""GA19 opt-in GA054 immutable result + outbox persistence for Group B.

Writer owns ONLY GA SQL tables and reads canonical A003/A004 and D1 source
rights; no Group B imports/tables/classifiers. A partial result is NEVER
published as eligible to Group B.
"""
from __future__ import annotations

import json

from ..contracts_v1 import canonical_sha256, media_manifest_v1
from .group_b import (
    GroupBHandoffError, prepare_group_b_result, validate_prepared_handoff,
    VIDEO_KINDS, CAROUSEL_KINDS, _scope,
)

ASSEMBLY="group-a-assembly/v1"
EVENT="group_a.result_ready/v1"


def _fence_run(conn,m,pre):
    row=conn.execute(
        """SELECT project_id,qdp_run_id::text,video_id,source_id,
                  source_policy_version_id,qdp_input_sha256,manifest_sha256,
                  content_kind,expected_media_items,stage_version,state,
                  worker_id,lease_until
           FROM ga_runs WHERE id=%s::uuid FOR UPDATE""",
        (pre["ga_run_id"],)
    ).fetchone()
    expected=(
        m["project_id"],m["qdp_run_id"],m["video_id"],m["source_id"],
        m["source_policy_version_id"],m["qdp_input_sha256"],m["manifest_sha256"],
        m["content_kind"],len(m["items"]),ASSEMBLY,
    )
    if row is None or tuple(row[:10])!=expected:
        raise GroupBHandoffError("ga_run_qdp_lineage_mismatch")
    if row[10] not in ("queued","partial","completed"):
        raise GroupBHandoffError("ga_run_not_publishable")
    if row[11] is not None or row[12] is not None:
        raise GroupBHandoffError("ga_run_worker_lease_active")
    if row[10]=="completed" and not pre["ready_for_b"]:
        raise GroupBHandoffError("completed_run_cannot_regress_to_partial")
    return row[10]


def _fence_media(conn,m):
    s=conn.execute(
        """SELECT s.status,s.can_analyze_media,p.is_active,p.permissions
           FROM sources s JOIN source_policy_versions p
             ON p.id=%s AND p.source_id=s.id
           WHERE s.id=%s FOR SHARE OF s,p""",
        (m["source_policy_version_id"],m["source_id"])
    ).fetchone()
    if (s is None or s[0]!="enabled" or s[1] is not True or
        s[2] is not True or not isinstance(s[3],dict) or
        s[3].get("can_analyze_media") is not True):
        raise GroupBHandoffError("source_policy_revoked")
    rows=conn.execute(
        """SELECT r.slide_index,r.media_asset_id,r.media_kind,r.asset_sha256,
                  r.byte_size,r.verified_at,m.video_id,m.source_id,m.media_kind,
                  m.sha256,m.permission,(m.expires_at IS NULL OR m.expires_at>now())
           FROM ga_media_refs r JOIN media_assets m ON m.id=r.media_asset_id
           WHERE r.run_id=%s::uuid ORDER BY r.slide_index
           FOR SHARE OF r,m""",
        (m["ga_run_id"],)
    ).fetchall()
    if len(rows)!=len(m["items"]):
        raise GroupBHandoffError("ga_media_refs_incomplete")
    for row,item in zip(rows,m["items"]):
        if (row[0]!=item["index"] or row[1]!=item["media_asset_id"]
            or row[2]!=item["kind"] or row[3]!=item["sha256"]
            or row[4]!=item["byte_size"]
            or row[6]!=m["video_id"] or row[7]!=m["source_id"]
            or row[8]!=item["kind"] or row[9]!=item["sha256"]
            or row[10]!="analysis_allowed" or row[11] is not True):
            raise GroupBHandoffError("media_ref_or_permission_mismatch")


def _fence_video(conn,m,item,aid,snap):
    row=conn.execute(
        """SELECT video_id,media_asset_id,source_id,source_policy_version_id,
                  state,analysis_version,scene_status,speech_status,
                  screen_text_status,audio_status,has_subtitles,
                  duration_sec,aspect_ratio,spoken_words,speech_duration_sec,
                  cta_start_sec,measurement_methods
           FROM video_feature_a_analyses WHERE id=%s FOR SHARE""",
        (aid,)
    ).fetchone()
    if row is None or row[:6]!=(
        m["video_id"],item["media_asset_id"],m["source_id"],
        m["source_policy_version_id"],snap["analysis_state"],"GA18-v1",
    ):
        raise GroupBHandoffError("canonical_video_analysis_mismatch")
    expected_status=(snap["scene_status"],snap["speech_status"],
                     snap["screen_text_status"],snap["audio_status"],
                     snap["has_subtitles"])
    if tuple(row[6:11])!=expected_status or (
        format(row[11],".3f") if row[11] is not None else None
    )!=snap["duration_sec"] or row[12]!=snap["aspect_ratio"] or (
        row[13]!=snap["spoken_words"]
        or (None if row[14] is None else format(row[14],".3f"))!=snap["speech_duration_sec"]
        or row[15] is not None
    ):
        raise GroupBHandoffError("canonical_video_truth_mismatch")
    methods=row[16]
    if (not isinstance(methods,dict) or
        methods.get("ga18_snapshot_sha256")!=snap["observation_sha256"]
        or methods.get("ga_asset_sha256")!=item["sha256"]
        or methods.get("ga18_manifest_sha256")!=m["manifest_sha256"]):
        raise GroupBHandoffError("canonical_video_snapshot_digest_mismatch")


def _fence_carousel(conn,m,pre,obs):
    row=conn.execute(
        """SELECT video_id,source_id,source_policy_version_id,content_kind,
                  state,slide_status,text_status,measurement_methods
           FROM carousel_feature_a_analyses WHERE id=%s FOR SHARE""",
        (pre["carousel_analysis_id"],)
    ).fetchone()
    if (row is None or tuple(row[:4])!=(
        m["video_id"],m["source_id"],m["source_policy_version_id"],
        m["content_kind"]
    ) or row[4]!=obs["analysis_state"] or
        row[5]!=obs["slide_status"] or row[6]!=obs["text_status"] or
        not isinstance(row[7],dict) or
        row[7].get("manifest_sha256")!=m["manifest_sha256"] or
        row[7].get("observation_sha256")!=obs["observation_sha256"]):
        raise GroupBHandoffError("canonical_carousel_truth_mismatch")
    items=conn.execute(
        """SELECT slide_index,item_type,media_asset_id,video_a_analysis_id
           FROM carousel_feature_a_items WHERE analysis_id=%s
           ORDER BY slide_index FOR SHARE""",
        (pre["carousel_analysis_id"],)
    ).fetchall()
    if len(items)!=len(m["items"]):
        raise GroupBHandoffError("canonical_carousel_slide_count_mismatch")
    for current,origin in zip(items,m["items"]):
        expected=(
            origin["index"],origin["kind"],origin["media_asset_id"],
            pre["video_analysis_ids"].get(origin["media_asset_id"])
        )
        if tuple(current)!=expected:
            raise GroupBHandoffError("canonical_carousel_slide_binding_mismatch")


def _persist_result(conn,m,pre):
    snap=pre["snapshot"]
    video_id=snap["video_a_analysis_id"]
    carousel_id=snap["carousel_a_analysis_id"]
    new=conn.execute(
        """INSERT INTO ga_results
           (run_id,project_id,video_id,source_id,state,video_a_analysis_id,
            carousel_a_analysis_id,result_schema,result_sha256,evidence,
            snapshot,method_version)
           VALUES (%s::uuid,%s,%s,%s,%s,%s,%s,'group-a-to-b/v1',
                   %s,%s::jsonb,%s::jsonb,%s)
           ON CONFLICT (run_id,result_sha256) DO NOTHING RETURNING id""",
        (pre["ga_run_id"],m["project_id"],m["video_id"],m["source_id"],
         snap["analysis_state"],video_id,carousel_id,
         pre["result_sha256"],json.dumps(snap["evidence"]),
         json.dumps(snap),snap["method_version"])
    ).fetchone()
    if new is not None:
        return new[0]
    previous=conn.execute(
        """SELECT id,state,video_a_analysis_id,carousel_a_analysis_id,
                  result_schema,result_sha256,evidence,snapshot,method_version
           FROM ga_results WHERE run_id=%s::uuid AND result_sha256=%s
           FOR UPDATE""",
        (pre["ga_run_id"],pre["result_sha256"])
    ).fetchone()
    if previous is None or previous[1:]!=(
        snap["analysis_state"],video_id,carousel_id,"group-a-to-b/v1",
        pre["result_sha256"],snap["evidence"],snap,snap["method_version"]
    ):
        raise GroupBHandoffError("ga_result_idempotency_conflict")
    return previous[0]


def _enqueue_if_complete(conn,m,pre,result_id):
    if not pre["ready_for_b"]:
        return None
    event={
        "schema_version":EVENT,
        "ga_run_id":pre["ga_run_id"],"ga_result_id":result_id,
        "result_sha256":pre["result_sha256"],"project_id":m["project_id"],
        "qdp_run_id":m["qdp_run_id"],"video_id":m["video_id"],
        "source_id":m["source_id"],"source_policy_version_id":m["source_policy_version_id"],
        "manifest_sha256":m["manifest_sha256"],
        "content_kind":m["content_kind"],
        "video_a_analysis_id":pre["snapshot"]["video_a_analysis_id"],
        "carousel_a_analysis_id":pre["snapshot"]["carousel_a_analysis_id"],
    }
    digest=canonical_sha256(event)
    new=conn.execute(
        """INSERT INTO ga_outbox
           (run_id,project_id,result_id,payload_sha256,payload,state)
           VALUES (%s::uuid,%s,%s,%s,%s::jsonb,'pending')
           ON CONFLICT (run_id,event_type,payload_sha256) DO NOTHING
           RETURNING id""",
        (pre["ga_run_id"],m["project_id"],result_id,digest,json.dumps(event))
    ).fetchone()
    if new is not None:
        return new[0]
    old=conn.execute(
        """SELECT id,result_id,payload FROM ga_outbox
           WHERE run_id=%s::uuid AND event_type=%s AND payload_sha256=%s
           FOR UPDATE""",(pre["ga_run_id"],EVENT,digest)
    ).fetchone()
    if old is None or old[1]!=result_id or old[2]!=event:
        raise GroupBHandoffError("ga_outbox_idempotency_conflict")
    return old[0]


def persist_group_b_handoff(conn,manifest,prepared,*,video_snapshots=(),
                            carousel_observation=None,verify_current,
                            verify_origin,verify_coverage=None,
                            extra_evidence=(),verify_evidence=None):
    """Commit v1 snapshot+outbox after locking pre-existing GA run/media/A003.

    Never creates a GA run or QDP receipt; these must exist via GA04 intake.
    Completed results become *pending outbox* (NOT delivered/ACKed).
    Partial results stay audit-only with NO Group B outbox event.
    """
    if not callable(getattr(conn,"transaction",None)):
        raise GroupBHandoffError("transactional_connection_required")
    m=media_manifest_v1(manifest)
    pre=validate_prepared_handoff(m,prepared)
    rebuilt=prepare_group_b_result(
        m,receipt={
            "ga_run_id":pre["ga_run_id"],
            "video_analysis_ids":pre["video_analysis_ids"],
            "carousel_analysis_id":pre["carousel_analysis_id"],
        },video_snapshots=video_snapshots,
        carousel_observation=carousel_observation,
        verify_current=verify_current,verify_origin=verify_origin,
        verify_coverage=verify_coverage,extra_evidence=extra_evidence,
        verify_evidence=verify_evidence,
    )
    if rebuilt!=pre:
        raise GroupBHandoffError("prepared_result_changed_after_validation")
    videos={s["media_asset_id"]:s for s in video_snapshots}
    with conn.transaction():
        run_state=_fence_run(conn,m,pre)
        _fence_media(conn,{**m,"ga_run_id":pre["ga_run_id"]})
        for item in m["items"]:
            if verify_current(_scope(m,item)) is not True:
                raise GroupBHandoffError("current_media_rights_revoked_in_transaction")
            if item["kind"]=="video":
                _fence_video(
                    conn,m,item,pre["video_analysis_ids"][item["media_asset_id"]],
                    videos[item["media_asset_id"]]
                )
        if m["content_kind"] in CAROUSEL_KINDS:
            _fence_carousel(conn,m,pre,carousel_observation)
        if run_state=="completed":
            # GA completion is terminal and cannot be replaced by any other
            # v1 result under the same GA run; allow exact-idempotent replay.
            previous=conn.execute(
                """SELECT result_sha256 FROM ga_results
                   WHERE run_id=%s::uuid AND state='completed' FOR SHARE""",
                (pre["ga_run_id"],)
            ).fetchall()
            if not previous or any(x[0]!=pre["result_sha256"] for x in previous):
                raise GroupBHandoffError("completed_run_result_conflict")
        rid=_persist_result(conn,m,pre)
        if run_state!="completed":
            state="completed" if pre["ready_for_b"] else "partial"
            conn.execute(
                """UPDATE ga_runs SET state=%s,updated_at=now(),
                      finished_at=CASE WHEN %s='completed' THEN now()
                                       ELSE finished_at END
                   WHERE id=%s::uuid""",
                (state,state,pre["ga_run_id"])
            )
        outbox_id=_enqueue_if_complete(conn,m,pre,rid)
        # Recheck point of use after the irreversible-looking insert but
        # BEFORE transaction commit; all new rows roll back on revocation.
        confirmed=prepare_group_b_result(
            m,receipt={
                "ga_run_id":pre["ga_run_id"],
                "video_analysis_ids":pre["video_analysis_ids"],
                "carousel_analysis_id":pre["carousel_analysis_id"],
            },video_snapshots=video_snapshots,carousel_observation=carousel_observation,
            verify_current=verify_current,verify_origin=verify_origin,
            verify_coverage=verify_coverage,extra_evidence=extra_evidence,
            verify_evidence=verify_evidence
        )
        if confirmed!=pre:
            raise GroupBHandoffError("handoff_revoked_before_commit")
    return {
        "schema_version":"ga19-durable-receipt/v1",
        "ga_run_id":pre["ga_run_id"],"ga_result_id":rid,
        "result_sha256":pre["result_sha256"],
        "video_a_analysis_id":pre["snapshot"]["video_a_analysis_id"],
        "carousel_a_analysis_id":pre["snapshot"]["carousel_a_analysis_id"],
        "state":pre["snapshot"]["analysis_state"],
        "delivery_state":"pending_outbox" if outbox_id is not None else "blocked_partial",
        "outbox_id":outbox_id,
    }
