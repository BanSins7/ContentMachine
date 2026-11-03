"""GB-MOD-16 opt-in one-shot SQL005 B3 story storage, never mint B analysis.

An already created, project-authorized, SHA-pinned *pending/partial* B analysis
is required. The caller owns a single database transaction and supplies a
trusted point-of-use admission verifier (current QDP/GA/media/rights + remote
model receipt). No live verifier is shipped; absent gate always denies.

A failed statement/view mismatch must roll back the *entire* transaction.
Never call this function through an untrusted HTTP endpoint.
"""
from __future__ import annotations

import json
from collections.abc import Mapping

from .b3_sampling import B3SamplingPlan,verify_b3_plan_against_manifest
from .b3_story import B3StoryProposal,B3StoryError
from ..context.manifest import MediaEvidenceManifest


def _deny(reason):
    raise B3StoryError(reason)


def _one(cursor):
    row=cursor.fetchone()
    if row is None:
        return None
    if isinstance(row,Mapping):
        return dict(row)
    return dict(zip((x.name for x in cursor.description),row))


def persist_b3_story(conn, *,analysis_id:int,manifest:MediaEvidenceManifest,
                     plan:B3SamplingPlan,proposal:B3StoryProposal,
                     admission_verifier:object) -> tuple[str,...]:
    """Write exactly SQL005 sampling/frames/intervals/slides, once.

    Must run within a caller-owned transaction; no side effects on import or
    row creation. The analysis has to be pre-provisioned by the trusted host
    with its exact B run/proposal/source pin in method_details.
    """
    if (type(analysis_id) is not int or analysis_id<=0 or
        not isinstance(proposal,B3StoryProposal) or
        not proposal.verify_digest() or proposal.authenticated_inference is not True or
        proposal.payload.get("verification_state")!=
           "authenticated_receipt_and_exact_visual_source" or
        not verify_b3_plan_against_manifest(plan,manifest)):
        _deny("unverified_story_or_sampling_proposal")
    p=proposal.payload
    expected={
        "project_id":manifest.project_id,"b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,"qdp_run_id":manifest.qdp_run_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "media_evidence_manifest_sha256":manifest.manifest_sha256,
        "sampling_plan_sha256":plan.plan_sha256,
        "content_type":"video" if manifest.content_kind in ("video","slideshow_video")
                       else "carousel",
    }
    if any(p.get(key)!=value for key,value in expected.items()):
        _deny("story_source_identity_mismatch")
    verifier=getattr(admission_verifier,"verify_story_persistence",None)
    if not callable(verifier):
        _deny("trusted_story_admission_missing")
    try:
        authenticated=verifier(analysis_id,manifest,plan,proposal)
    except Exception:
        _deny("trusted_story_admission_failed")
    if authenticated is not True:
        _deny("trusted_story_admission_failed")
    row=_one(conn.execute(
        """SELECT id,video_id,source_id,content_type,state,
                  source_policy_version_id,method_details
             FROM content_b_analyses WHERE id=%s FOR UPDATE""",
        (analysis_id,)
    ))
    if row is None or row["state"] not in ("pending","partial"):
        _deny("analysis_not_mutable_or_missing")
    if (row["video_id"]!=manifest.video_id or
        row["source_id"]!=manifest.source_id or
        row["source_policy_version_id"]!=manifest.source_policy_version_id or
        row["content_type"]!=p["content_type"]):
        _deny("analysis_identity_or_policy_mismatch")
    pins=row["method_details"]
    if type(pins) is not dict or any(pins.get(key)!=value for key,value in {
        "group_b_project_id":manifest.project_id,
        "group_b_run_id":manifest.b_run_id,
        "group_b_media_evidence_manifest_sha256":manifest.manifest_sha256,
        "group_b_b3_sampling_plan_sha256":plan.plan_sha256,
        "group_b_b3_story_proposal_sha256":proposal.proposal_sha256,
    }.items()):
        _deny("analysis_b3_pins_missing_or_changed")
    # One-shot, no delete/rewrite of a prior version. A revised result
    # requires a NEW analysis_key, not mutation of existing B3 history.
    if conn.execute(
        """SELECT EXISTS (SELECT 1 FROM content_b_story_sampling
                         WHERE analysis_id=%s)""",(analysis_id,)
    ).fetchone()[0]:
        _deny("story_already_persisted_create_new_analysis")
    fields=plan.sampling_db_fields()
    notes=dict(fields["sampling_notes"])
    notes.update({
        "story_proposal_sha256":proposal.proposal_sha256,
        "story_classification_status":p["classification_status"],
        "story_review_reasons":p["reason_codes"],
        "provider_response_id":p["provider_response_id"],
        "model_request_sha256":p["model_request_sha256"],
    })
    conn.execute(
        """INSERT INTO content_b_story_sampling(
             analysis_id,first_5_sec_fps,remaining_fps,
             max_sampled_frames_target,extra_at_scene_cuts,
             extra_at_text_changes,sampling_notes)
           VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)""",
        (analysis_id,fields["first_5_sec_fps"],fields["remaining_fps"],
         fields["max_sampled_frames_target"],fields["extra_at_scene_cuts"],
         fields["extra_at_text_changes"],
         json.dumps(notes,ensure_ascii=False,sort_keys=True,allow_nan=False))
    )
    for r in proposal.frame_db_rows():
        conn.execute(
            """INSERT INTO content_b_story_frames(
                  analysis_id,frame_index,time_sec,sampled_frame_ref,role,
                  before_after_phase,needs_review)
               VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (analysis_id,r["frame_index"],r["time_sec"],r["sampled_frame_ref"],
             r["role"],r["before_after_phase"],r["needs_review"])
        )
    for r in proposal.interval_db_rows():
        conn.execute(
            """INSERT INTO content_b_story_intervals(
                 analysis_id,interval_index,start_sec,end_sec,role,
                 before_after_phase,boundary_quality,needs_review)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
            (analysis_id,r["interval_index"],r["start_sec"],r["end_sec"],
             r["role"],r["before_after_phase"],r["boundary_quality"],
             r["needs_review"])
        )
    for r in proposal.slide_db_rows():
        conn.execute(
            """INSERT INTO content_b_story_slides(
                 analysis_id,slide_index,role,before_after_phase,
                 screen_text,visual_description,needs_review)
               VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (analysis_id,r["slide_index"],r["role"],r["before_after_phase"],
             r["screen_text"],r["visual_description"],r["needs_review"])
        )
    conn.execute(
        """UPDATE content_b_analyses SET state='partial',
                  needs_review=needs_review OR %s
           WHERE id=%s AND state IN ('pending','partial')""",
        (proposal.needs_review,analysis_id)
    )
    view=conn.execute(
        """SELECT story_pattern FROM content_b_story_patterns
           WHERE analysis_id=%s""",(analysis_id,)
    ).fetchone()
    got=tuple(view[0] or ()) if view else None
    if got!=proposal.story_pattern:
        _deny("story_pattern_view_mismatch")
    return got
