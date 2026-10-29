"""GB-MOD-25 exact source-backed SQL005 B1–B8 -> UAE handoff reader.

No imports or writes to Unified Analytics' separate branch. This is a
read-only, project-scoped, live checked B *candidate*, NOT an UAE
QualificationObservation or a fabricated Group C/SRE/D1 qualification.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json

from ..contracts import canonical_hash
from .freshness import CurrentLineage,FreshnessState
from .invalidation import (
    read_pinned_lineage,assess_current_run,INVALIDATED,
)
from .analytics_contract import (
    VERSION,UAECompatibility,HandoffDecision,HandoffState,
    HandoffError,blocked,validate_snapshot,VIDEO_KINDS,CAROUSEL_KIN,
)

# SQL is code-only static. Never interpolate a project, block or consumer
# supplied column. These are the same SQL005 tables read by legacy _b_payload.
_SINGLE={
 "hook":"""SELECT taxonomy_version,classification_status,mechanisms,delivery,
                   spoken_text,screen_text,visual_description,
                   is_question,has_number,direct_address,has_negation,is_command,
                   start_sec,end_sec,slide_index
            FROM content_b_hooks WHERE analysis_id=%s""",
 "format":"""SELECT taxonomy_version,classification_status,video_primary,
                   video_secondary,carousel_format
              FROM content_b_formats WHERE analysis_id=%s""",
 "visual":"""SELECT ai_generated,person_visible,product_visible,logo_visible
              FROM content_b_visual_content WHERE analysis_id=%s""",
 "editing":"""SELECT editing_type,cut_count,cuts_per_minute,
                    avg_shot_duration,cuts_first_5_sec
               FROM content_b_editing WHERE analysis_id=%s""",
 "subtitles":"""SELECT subtitle_type,position,font_size,font_style,
                     text_color,highlight_color,background_style,display_mode
                 FROM content_b_subtitles WHERE analysis_id=%s""",
 "cta_summary":"""SELECT analysis_status,has_cta,detected_occurrences
                    FROM content_b_cta_summary WHERE analysis_id=%s""",
 "topic":"""SELECT b.category_id,b.topic_id,b.subtopic_id,
                   b.taxonomy_version,b.needs_review,
                   cat.merged_into_id AS category_merge_id,
                   topic.merged_into_id AS topic_merge_id,
                   topic.category_id AS topic_parent_category_id,
                   sub.merged_into_id AS subtopic_merge_id,
                   sub.topic_id AS subtopic_parent_topic_id
            FROM content_b_topic_assignments b
            JOIN content_b_categories cat ON cat.id=b.category_id
            JOIN content_b_topics topic ON topic.id=b.topic_id
            LEFT JOIN content_b_subtopics sub ON sub.id=b.subtopic_id
            WHERE b.analysis_id=%s""",
}
_MULTI={
 "hook_parts":"""SELECT part_index,start_sec,end_sec,slide_index,
                         verbatim_text,mechanisms
                 FROM content_b_hook_parts WHERE analysis_id=%s
                 ORDER BY part_index""",
 "frames":"""SELECT frame_index,time_sec,role,before_after_phase,needs_review
              FROM content_b_story_frames WHERE analysis_id=%s
              ORDER BY frame_index""",
 "intervals":"""SELECT interval_index,start_sec,end_sec,role,before_after_phase,
                       needs_review
                 FROM content_b_story_intervals WHERE analysis_id=%s
                 ORDER BY interval_index""",
 "slides":"""SELECT slide_index,role,before_after_phase,needs_review
              FROM content_b_story_slides WHERE analysis_id=%s
              ORDER BY slide_index""",
 "ctas":"""SELECT occurrence_index,cta_type,text,spoken_text,screen_text,
                  delivery,start_sec,end_sec,slide_index,destination_type,
                  extraction_status
           FROM content_b_ctas WHERE analysis_id=%s ORDER BY occurrence_index""",
}

_RUN_SQL="""SELECT r.id::text AS run_id,r.project_id,r.video_id,r.source_id,
                   r.source_policy_version_id,r.content_type,r.state AS run_state,
                   r.result_analysis_id,v.content_kind AS canonical_content_kind,
                   r.video_a_analysis_id,
                   r.carousel_a_analysis_id,r.a_snapshot_sha256,
                   r.media_manifest_sha256,r.config_sha256,
                   a.id AS analysis_id,a.analysis_key,a.analysis_version,
                   a.taxonomy_version,a.method_name,a.method_version,
                   a.model_name,a.model_version,a.prompt_version,
                   a.video_id AS analysis_video_id,
                   a.source_id AS analysis_source_id,
                   a.source_policy_version_id AS analysis_policy_id,
                   a.video_a_analysis_id AS analysis_video_a_id,
                   a.carousel_a_analysis_id AS analysis_carousel_a_id,
                   a.content_type AS analysis_content_type,
                   a.state AS analysis_state,a.needs_review,
                   a.unavailable_reasons,a.method_details,a.analyzed_at,
                   i.status AS inbox_status,i.producer_schema,i.event_key,
                   i.event_sha256 AS inbox_event_sha256,
                   i.preparation_sha256,i.qdp_run_id::text AS qdp_run_id,
                   rc.event_id::text AS receipt_event_id,
                   rc.event_sha256 AS receipt_event_sha256,
                   rc.preparation_sha256 AS receipt_preparation_sha256,
                   rc.run_id::text AS receipt_run_id,
                   src.status AS source_status,
                   src.can_analyze_media,src.can_store_metadata,
                   src.can_store_metrics,src.can_derive_scores,
                   pol.is_active AS policy_active,
                   pol.source_id AS policy_source_id,
                   pol.permissions AS policy_permissions,
                   pol.reviewed_at AS policy_reviewed_at
            FROM group_b_runs r
            JOIN videos v ON v.id=r.video_id
            JOIN group_b_inbox i ON i.id=r.inbox_id
             AND i.project_id=r.project_id
            LEFT JOIN group_b_inbox_receipts rc ON rc.inbox_id=i.id
             AND rc.project_id=r.project_id AND rc.run_id=r.id
            LEFT JOIN content_b_analyses a ON a.id=r.result_analysis_id
            JOIN sources src ON src.id=r.source_id
            LEFT JOIN source_policy_versions pol
             ON pol.id=r.source_policy_version_id AND pol.source_id=r.source_id
            WHERE r.project_id=%s AND r.id=%s::uuid
            FOR SHARE OF r"""
_BLOCK_SQL="""SELECT block,status,evidence_sha256,error_code
               FROM group_b_block_states
               WHERE project_id=%s AND run_id=%s::uuid ORDER BY block"""
_NA_SQL="""SELECT
             (SELECT count(*) FROM content_b_editing WHERE analysis_id=%s)
                AS editing_rows,
             (SELECT count(*) FROM content_b_subtitles WHERE analysis_id=%s)
                AS subtitles_rows,
             (SELECT count(*) FROM content_b_story_frames WHERE analysis_id=%s)
                AS frames_rows,
             (SELECT count(*) FROM content_b_story_intervals WHERE analysis_id=%s)
                AS intervals_rows,
             (SELECT count(*) FROM content_b_story_sampling WHERE analysis_id=%s)
                AS sampling_rows"""
_RELATIONS=(
    "group_b_runs","group_b_inbox","group_b_inbox_receipts",
    "group_b_block_states","group_b_review_events","group_b_outbox",
    "content_b_analyses","content_b_hooks","content_b_hook_parts",
    "content_b_formats","content_b_story_sampling",
    "content_b_story_frames","content_b_story_intervals",
    "content_b_story_slides","content_b_visual_content",
    "content_b_editing","content_b_subtitles","content_b_cta_analyses",
    "content_b_ctas","content_b_topic_assignments","content_b_categories",
    "content_b_topics","content_b_subtopics","content_b_cta_summary",
    "sources","source_policy_versions","videos",
)
_PROBE_SQL="""SELECT t.name FROM unnest(%s::text[]) AS t(name)
            WHERE to_regclass('public.'||t.name) IS NULL
            ORDER BY t.name"""


def probe_uae_relations(conn):
    """Return unavailable SQL005/055/056 relationships, never infer peer
    code version or that GA54/QDP producers exist. Zero write side effects.
    """
    try:
        items=conn.execute(_PROBE_SQL,(list(_RELATIONS),)).fetchall()
    except Exception:
        return ("group_b_sql_schema_probe_failed",)
    return tuple(x[0] for x in items)


_STORY_SAMPLING_SQL="""SELECT count(*) FROM content_b_story_sampling WHERE analysis_id=%s"""
_B7_SQL="""SELECT analysis_status,taxonomy_version,analyzed_at
             FROM content_b_cta_analyses WHERE analysis_id=%s"""
_REVIEW_SQL="""SELECT count(*) FROM group_b_review_events
               WHERE project_id=%s AND run_id=%s::uuid"""
_INVALID_SQL="""SELECT EXISTS(SELECT 1 FROM group_b_outbox
                WHERE project_id=%s AND run_id=%s::uuid
                AND event_type='group_b.invalidated.v1')"""


def _deny(reason):
    raise HandoffError(reason)


def _many(conn,sql,args):
    cur=conn.execute(sql,args)
    columns=[x.name for x in cur.description]
    return [dict(x) if isinstance(x,Mapping) else dict(zip(columns,x))
            for x in cur.fetchall()]


def _first(conn,sql,args):
    cur=conn.execute(sql,args)
    x=cur.fetchone()
    if x is None:
        return None
    return dict(x) if isinstance(x,Mapping) else dict(zip(
        [d.name for d in cur.description],x
    ))


def _serializable(v):
    if isinstance(v,Decimal):
        if not v.is_finite():
            _deny("uae_nonfinite_sql_numeric")
        return str(v)
    if isinstance(v,datetime):
        if v.utcoffset() is None:
            _deny("uae_naive_timestamp")
        return v.astimezone(timezone.utc).isoformat()
    if type(v) is dict:
        if any(type(k) is not str for k in v):
            _deny("uae_nonstring_payload_key")
        return {k:_serializable(x) for k,x in v.items()}
    if type(v) in (list,tuple):
        return [_serializable(x) for x in v]
    if v is None or type(v) in (str,bool,int):
        return v
    _deny("uae_unsupported_sql_payload_value")


def _components(conn,aid,content_kind):
    video=content_kind in VIDEO_KINDS
    values={}
    for name,sql in _SINGLE.items():
        if not video and name in ("editing","subtitles"):
            continue
        row=_first(conn,sql,(aid,))
        if row is None:
            return None,("missing_sql005_component_"+name,)
        values[name]=row
    for name,sql in _MULTI.items():
        values[name]=_many(conn,sql,(aid,))
    story={name:values.pop(name) for name in ("frames","intervals","slides")}
    values["story"]=story
    extras={
        "hook_parts":values.pop("hook_parts"),
        "ctas":values.pop("ctas"),
    }
    reasons=[]
    if not video:
        counts=_first(conn,_NA_SQL,(aid,)*5)
        if counts is None or any(counts.get(k)!=0 for k in (
            "editing_rows","subtitles_rows","frames_rows","intervals_rows",
            "sampling_rows",
        )):
            reasons.append("carousel_contains_video_only_sql005_rows")
    else:
        if conn.execute(_STORY_SAMPLING_SQL,(aid,)).fetchone()[0]!=1:
            reasons.append("video_story_sampling_not_single_verified_plan")
    b7=_first(conn,_B7_SQL,(aid,))
    if (b7 is None or b7.get("analysis_status")!="complete" or
        b7.get("analyzed_at") is None):
        reasons.append("b7_original_complete_scan_missing")
    elif len(extras["ctas"])!=values["cta_summary"]["detected_occurrences"]:
        reasons.append("b7_occurrences_not_same_as_summary")
    if any(c.get("extraction_status")!="detected" for c in extras["ctas"]):
        reasons.append("b7_occurrence_review_unresolved")
    topic=values["topic"]
    if (topic.get("topic_parent_category_id")!=topic.get("category_id") or
        any(topic.get(k) is not None for k in (
            "category_merge_id","topic_merge_id","subtopic_merge_id")) or
        (topic.get("subtopic_id") is not None and
         topic.get("subtopic_parent_topic_id")!=topic.get("topic_id"))):
        reasons.append("b8_hierarchy_or_current_merge_invalid")
    return values,tuple(reasons),extras,b7


def _checked_rights(row):
    rights=("can_analyze_media","can_store_metadata",
            "can_store_metrics","can_derive_scores")
    permissions=row.get("policy_permissions")
    return (row.get("source_status")=="enabled" and
            row.get("policy_active") is True and
            row.get("policy_reviewed_at") is not None and
            row.get("policy_source_id")==row.get("source_id") and
            type(permissions) is dict and all(
                row.get(right) is True and permissions.get(right) is True
                for right in rights
            ))


def _verified_capability(host,conn,capability):
    verify=getattr(host,"verify_uae_consumer_capability",None)
    if not isinstance(capability,UAECompatibility) or not capability.compatible:
        return False
    if not callable(verify):
        return False
    try:
        return verify(capability,conn) is True
    except Exception:
        return False


def read_uae_handoff(conn,*,project_id,run_id,current:CurrentLineage,
                     compatibility:UAECompatibility,verifier)->HandoffDecision:
    """Read-only exact version under a project-scoped SQL transaction.

    A real producer-side host must verify current QDP/GA/media/rights,
    consent to this UAE consumer, original B component validity and
    independent (non-recycled) quality-gate evidence. DOES NOT call UAE
    evaluate_qualification or create a qualified snapshot.
    """
    if (type(project_id) is not str or not project_id or
        type(run_id) is not str or not run_id or
        not callable(getattr(conn,"transaction",None))):
        _deny("uae_handoff_scope_or_db_invalid")
    with conn.transaction():
        missing=probe_uae_relations(conn)
        if missing:
            return blocked(project_id,run_id,"uae_sql005_runtime_schema_missing")
        pins,history=read_pinned_lineage(
            conn,project_id=project_id,run_id=run_id,
        )
        if not isinstance(current,CurrentLineage) or not current.verify_digest():
            return blocked(project_id,run_id,"current_group_a_qdp_media_missing")
        fresh=assess_current_run(
            conn,project_id=project_id,run_id=run_id,
            current=current,verifier=verifier,
        )
        if fresh.state is not FreshnessState.FRESH or fresh.reasons:
            return blocked(project_id,run_id,*fresh.reasons)
        if not _verified_capability(verifier,conn,compatibility):
            return blocked(project_id,run_id,"uae_consumer_capability_unverified")
        row=_first(conn,_RUN_SQL,(project_id,run_id))
        if row is None:
            return blocked(project_id,run_id,"uae_canonical_run_missing")
        video=pins.content_type=="video"
        a_id=row["video_a_analysis_id"] if video else row["carousel_a_analysis_id"]
        a_other=row["carousel_a_analysis_id"] if video else row["video_a_analysis_id"]
        analysis_a=row["analysis_video_a_id"] if video else row["analysis_carousel_a_id"]
        analysis_other=row["analysis_carousel_a_id"] if video else row["analysis_video_a_id"]
        canonical_kind=row["canonical_content_kind"]
        expected_type=("video" if canonical_kind in VIDEO_KINDS else
                       "carousel" if canonical_kind in CAROUSEL_KINDS else None)
        original_ok=(
            expected_type==pins.content_type and
            row["project_id"]==project_id and
            row["run_id"]==run_id and
            row["run_state"]=="completed" and
            row["analysis_state"]=="completed" and
            row["needs_review"] is False and
            row["analysis_id"]==row["result_analysis_id"] and
            type(row["analysis_id"]) is int and row["analysis_id"]>0 and
            row["video_id"]==pins.video_id==row["analysis_video_id"] and
            row["source_id"]==pins.source_id==row["analysis_source_id"] and
            row["content_type"]==pins.content_type==row["analysis_content_type"] and
            row["source_policy_version_id"]==pins.source_policy_version_id==
                 row["analysis_policy_id"] and
            type(row["source_policy_version_id"]) is int and
            row["source_policy_version_id"]>0 and
            a_id==analysis_a==current.ga_result_id and
            type(a_id) is int and a_id>0 and
            a_other is None and analysis_other is None and
            row["a_snapshot_sha256"]==current.ga_result_sha256 and
            row["media_manifest_sha256"]==current.media_manifest_sha256 and
            row["qdp_run_id"]==current.qdp_run_id and
            row["preparation_sha256"]==current.qdp_input_sha256 and
            row["inbox_status"]=="accepted" and
            row["producer_schema"]=="qdp-handoff/v1" and
            row["receipt_run_id"]==run_id and
            row["receipt_event_id"]==row["event_key"] and
            row["receipt_event_sha256"]==row["inbox_event_sha256"] and
            row["receipt_preparation_sha256"]==row["preparation_sha256"] and
            row["unavailable_reasons"]=={} and
            type(row["analysis_version"]) is str and
            bool(row["analysis_version"]) and
            type(row["taxonomy_version"]) is str and
            bool(row["taxonomy_version"]) and
            type(row["method_version"]) is str and
            bool(row["method_version"]) and
            type(row["method_details"]) is dict and
            row["method_details"].get("group_b_atomic_version")==
                "group-b-atomic/v1" and
            row["method_details"].get("group_b_project_id")==project_id and
            row["method_details"].get("group_b_run_id")==run_id and
            row["method_details"].get("group_b_ga_result_sha256")==
                current.ga_result_sha256 and
            _sha(row["method_details"].get(
                "group_b_media_evidence_manifest_sha256"))
        )
        if not original_ok:
            return blocked(project_id,run_id,
                           "uae_exact_a_b_qdp_receipt_or_version_mismatch")
        if not _checked_rights(row):
            return blocked(project_id,run_id,"uae_policy_or_rights_denied")
        if row["analyzed_at"] is None:
            return blocked(project_id,run_id,"uae_analysis_time_missing")
        blocks=_many(conn,_BLOCK_SQL,(project_id,run_id))
        if len(blocks)!=8 or [x.get("block") for x in blocks]!=[
            "B1","B2","B3","B4","B5","B6","B7","B8"
        ]:
            return blocked(project_id,run_id,"uae_block_matrix_missing")
        mapping={x["block"]:{
            "status":x["status"],
            "evidence_sha256":x["evidence_sha256"],
            "error_code":x["error_code"],
        } for x in blocks}
        if conn.execute(_INVALID_SQL,(project_id,run_id)).fetchone()[0]:
            return blocked(project_id,run_id,"uae_historical_invalidation")
        if conn.execute(_REVIEW_SQL,(project_id,run_id)).fetchone()[0]:
            # The current UAE-GroupBExactReader rejects ANY review event,
            # including gold-only annotations. Never silently claim its
            # concrete v1 wire supports a distinction it does not yet make.
            return blocked(project_id,run_id,"uae_reader_review_event_incompatible")
        collected=_components(conn,row["analysis_id"],
                              canonical_kind)
        if collected[0] is None:
            return blocked(project_id,run_id,*collected[1])
        parts,component_errors,extras,b7=collected
        if component_errors:
            return blocked(project_id,run_id,*component_errors)
        taxonomy=row["taxonomy_version"]
        if any(parts[x].get("taxonomy_version")!=taxonomy for x in
               ("hook","format","topic")) or b7.get("taxonomy_version")!=taxonomy:
            return blocked(project_id,run_id,"uae_sql005_taxonomy_revision_mismatch")
        quality=getattr(verifier,"verify_uae_quality_gate",None)
        source=getattr(verifier,"verify_uae_exact_source",None)
        if not callable(quality) or not callable(source):
            return blocked(project_id,run_id,"uae_host_quality_or_source_port_missing")
        try:
            source_ok=source(project_id,run_id,current,row,parts,extras,conn) is True
            quality_ok=quality(project_id,run_id,current,row,conn) is True
        except Exception:
            source_ok=quality_ok=False
        structural=validate_snapshot(
            project_id=project_id,run_id=run_id,
            analysis_id=row["analysis_id"],
            analysis_version=row["analysis_version"],
            content_kind=canonical_kind,
            block_rows=mapping,parts=parts,
            qualified_freshness=True,signed_source=source_ok,
            quality_passed=quality_ok,compatibility=compatibility,
            expected_a_id=current.ga_result_id,linked_a_id=a_id,
            expected_policy_id=current.source_policy_version_id,
            linked_policy_id=row["source_policy_version_id"],
            expected_qdp_sha=current.qdp_input_sha256,
            received_qdp_sha=row["receipt_preparation_sha256"],
            has_correction=False,has_invalidation=False,
        )
        if not structural.can_offer:
            return structural
        # Never expose raw text to an unknown recipient: source + quality
        # + accepted same-transaction UAE capability have all succeeded.
        features={**parts,
                  "hook_parts":extras["hook_parts"],
                  "ctas":extras["ctas"],
                  "analysis_version":row["analysis_version"],
                  "taxonomy_version":row["taxonomy_version"]}
        data={
            "schema_version":VERSION,
            "consumer_adapter_schema":compatibility.adapter_schema,
            "consumer_reader_wire":compatibility.reader_wire,
            "project_id":project_id,"run_id":run_id,
            "analysis_id":row["analysis_id"],
            "analysis_key":row["analysis_key"],
            "video_id":row["video_id"],"source_id":row["source_id"],
            "source_policy_version_id":row["source_policy_version_id"],
            "content_kind":canonical_kind,
            "qdp_run_id":current.qdp_run_id,
            "qdp_preparation_sha256":current.qdp_input_sha256,
            "qdp_event_sha256":row["inbox_event_sha256"],
            "group_a_analysis_id":a_id,
            "group_a_result_sha256":current.ga_result_sha256,
            "group_a_media_manifest_sha256":current.media_manifest_sha256,
            "group_a_media_manifest_sha256_pinned":row["media_manifest_sha256"],
            "group_b_observation_manifest_sha256":
                row["method_details"]["group_b_media_evidence_manifest_sha256"],
            "b_config_sha256":row["config_sha256"],
            "analysis_version":row["analysis_version"],
            "taxonomy_version":taxonomy,
            "method_name":row["method_name"],
            "model_name":row["model_name"],
            "model_version":row["model_version"],
            "prompt_version":row["prompt_version"],
            "method_version":row["method_version"],
            "block_evidence_sha256s":{
                x["block"]:x["evidence_sha256"] for x in blocks
            },
            "source_current_evidence_sha256":current.evidence_sha256,
            "review_corrections":0,
            "features_b":features,
            "uae_qualification_status":"not_evaluated",
            "group_c_qualification_status":"not_evaluated",
        }
        normalized=_serializable(data)
        digest=canonical_hash(normalized)
        return HandoffDecision(
            HandoffState.CANDIDATE,(),project_id,run_id,
            row["analysis_id"],digest,normalized,False,
        )
