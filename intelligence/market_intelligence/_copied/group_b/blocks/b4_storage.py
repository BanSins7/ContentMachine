"""GB-MOD-17 opt-in, one-shot storage of exactly SQL005 four B4 columns.

No analysis creation; a trusted caller transaction, fresh QDP/GA/media/source
rights and actual provider delivery receipt admission are mandatory.
A NULL ai_generated is valid, but NOT NULL visible fields cannot be guessed.
"""
from __future__ import annotations
from collections.abc import Mapping

from .b4_visual import B4VisualProposal, B4VisualError
from ..context.manifest import MediaEvidenceManifest


def _deny(code):
    raise B4VisualError(code)


def _row(cursor):
    item=cursor.fetchone()
    if item is None:
        return None
    if isinstance(item,Mapping):
        return dict(item)
    return dict(zip((x.name for x in cursor.description),item))


def persist_b4_visual(conn,*,analysis_id:int,manifest:MediaEvidenceManifest,
                      proposal:B4VisualProposal,
                      admission_verifier:object):
    """Caller must enclose in transaction; refuses replay, changed pins or rights.

    Host MUST check exact signed model request/response/attached images and
    fresh QDP/A/media-byte/policy authorization. This adapter supplies no
    user-facing or automatic approval implementation.
    """
    if (type(analysis_id) is not int or analysis_id<=0 or
        not isinstance(manifest,MediaEvidenceManifest) or
        not manifest.verify_digest() or
        not isinstance(proposal,B4VisualProposal) or
        not proposal.can_persist):
        _deny("visual_proposal_not_persistable")
    p=proposal.payload
    if any(p.get(k)!=v for k,v in {
        "project_id":manifest.project_id,
        "b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,
        "qdp_run_id":manifest.qdp_run_id,
        "ga_result_id":manifest.ga_result_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "source_policy_version_id":manifest.source_policy_version_id,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "content_kind":manifest.content_kind,
    }.items()):
        _deny("visual_proposal_source_lineage_mismatch")
    verify=getattr(admission_verifier,"verify_visual_persistence",None)
    if not callable(verify):
        _deny("trusted_visual_admission_missing")
    try:
        accepted=verify(analysis_id,manifest,proposal)
    except Exception:
        _deny("trusted_visual_admission_failed")
    if accepted is not True:
        _deny("trusted_visual_admission_failed")
    parent=_row(conn.execute(
        """SELECT id,video_id,source_id,content_type,state,
                  source_policy_version_id,method_details
             FROM content_b_analyses WHERE id=%s FOR UPDATE""",
        (analysis_id,)
    ))
    if parent is None or parent["state"] not in ("pending","partial"):
        _deny("analysis_missing_or_immutable")
    if (parent["video_id"]!=manifest.video_id or
        parent["source_id"]!=manifest.source_id or
        parent["source_policy_version_id"]!=manifest.source_policy_version_id or
        parent["content_type"]!=(
            "video" if manifest.content_kind in ("video","slideshow_video")
            else "carousel"
        )):
        _deny("analysis_source_or_content_kind_changed")
    details=parent["method_details"]
    if type(details) is not dict or any(details.get(k)!=v for k,v in {
        "group_b_project_id":manifest.project_id,
        "group_b_run_id":manifest.b_run_id,
        "group_b_media_evidence_manifest_sha256":manifest.manifest_sha256,
        "group_b_b4_visual_proposal_sha256":proposal.proposal_sha256,
    }.items()):
        _deny("visual_analysis_pins_not_current")
    if conn.execute(
        "SELECT EXISTS(SELECT 1 FROM content_b_visual_content WHERE analysis_id=%s)",
        (analysis_id,)
    ).fetchone()[0]:
        _deny("visual_row_already_saved_create_new_analysis")
    data=proposal.db_fields()
    conn.execute(
        """INSERT INTO content_b_visual_content (
             analysis_id,ai_generated,person_visible,product_visible,logo_visible)
           VALUES (%s,%s,%s,%s,%s)""",
        (analysis_id,data["ai_generated"],data["person_visible"],
         data["product_visible"],data["logo_visible"])
    )
    conn.execute(
        """UPDATE content_b_analyses
           SET state='partial'
           WHERE id=%s AND state IN ('pending','partial')""",
        (analysis_id,)
    )
    actual=conn.execute(
        """SELECT ai_generated,person_visible,product_visible,logo_visible
           FROM content_b_visual_content WHERE analysis_id=%s""",
        (analysis_id,)
    ).fetchone()
    if actual is None or tuple(actual)!=(
        data["ai_generated"],data["person_visible"],
        data["product_visible"],data["logo_visible"]
    ):
        _deny("sql005_visual_readback_mismatch")
    return data
