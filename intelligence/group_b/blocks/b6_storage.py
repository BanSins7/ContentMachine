"""GB-MOD-18: opt-in one-shot SQL005 B6 insert under a trusted host gate.

Never creates an analysis, calls a paid model, decides video rights or stamps
full B1-B8 completion. Caller owns atomic transaction and fresh source checks.
"""
from __future__ import annotations
from collections.abc import Mapping

from .b6_subtitles import B6SubtitleError, B6SubtitleProposal
from ..context.manifest import MediaEvidenceManifest

COLUMNS = (
    "subtitle_type", "position", "font_size", "font_style", "text_color",
    "highlight_color", "background_style", "display_mode",
)


def _deny(code):
    raise B6SubtitleError(code)


def _row(cursor):
    v = cursor.fetchone()
    if v is None:
        return None
    if isinstance(v, Mapping):
        return dict(v)
    return dict(zip((x.name for x in cursor.description), v))


def persist_b6_subtitles(conn, *, analysis_id: int, manifest: MediaEvidenceManifest,
                         proposal: B6SubtitleProposal, admission_verifier: object):
    """Only if trusted host reverified whole-source scan and current rights.

    Host verify_subtitle_persistence must validate the detector's original
    signed scan, exact A/QDP/media SHA and current source policy, plus safety
    of a pending/partial parent. Merely a model/fixture saying 'none' fails.
    """
    if (type(analysis_id) is not int or analysis_id <= 0 or
        not isinstance(manifest, MediaEvidenceManifest) or
        not manifest.verify_digest() or
        not isinstance(proposal, B6SubtitleProposal) or
        not proposal.can_persist or
        manifest.content_kind not in ("video", "slideshow_video")):
        _deny("b6_proposal_not_persistable")
    p = proposal.payload
    expected = {
        "project_id": manifest.project_id, "b_run_id": manifest.b_run_id,
        "video_id": manifest.video_id, "qdp_run_id": manifest.qdp_run_id,
        "ga_result_id": manifest.ga_result_id,
        "ga_result_sha256": manifest.ga_result_sha256,
        "source_policy_version_id": manifest.source_policy_version_id,
        "source_evidence_manifest_sha256": manifest.manifest_sha256,
        "content_kind": manifest.content_kind,
    }
    if any(p.get(k) != v for k,v in expected.items()):
        _deny("b6_source_lineage_mismatch")
    verify = getattr(admission_verifier, "verify_subtitle_persistence", None)
    if not callable(verify):
        _deny("trusted_b6_admission_missing")
    try:
        allowed = verify(analysis_id, manifest, proposal)
    except Exception:
        _deny("trusted_b6_admission_failed")
    if allowed is not True:
        _deny("trusted_b6_admission_failed")
    parent = _row(conn.execute(
        """SELECT id,video_id,source_id,content_type,state,
                  source_policy_version_id,method_details
             FROM content_b_analyses WHERE id=%s FOR UPDATE""",
        (analysis_id,),
    ))
    if parent is None or parent["state"] not in ("pending", "partial"):
        _deny("b6_parent_missing_or_immutable")
    if (parent["video_id"] != manifest.video_id or
        parent["source_id"] != manifest.source_id or
        parent["source_policy_version_id"] != manifest.source_policy_version_id or
        parent["content_type"] != "video"):
        _deny("b6_parent_media_identity_changed")
    details = parent["method_details"]
    if type(details) is not dict or any(details.get(k) != v for k,v in {
        "group_b_project_id": manifest.project_id,
        "group_b_run_id": manifest.b_run_id,
        "group_b_media_evidence_manifest_sha256": manifest.manifest_sha256,
        "group_b_b6_subtitle_proposal_sha256": proposal.proposal_sha256,
    }.items()):
        _deny("b6_parent_pins_changed")
    if conn.execute(
        "SELECT EXISTS(SELECT 1 FROM content_b_subtitles WHERE analysis_id=%s)",
        (analysis_id,),
    ).fetchone()[0]:
        _deny("b6_subtitle_row_already_present")
    data = proposal.db_fields()
    conn.execute(
        """INSERT INTO content_b_subtitles
           (analysis_id,subtitle_type,position,font_size,font_style,
            text_color,highlight_color,background_style,display_mode)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (analysis_id, *(data[k] for k in COLUMNS)),
    )
    conn.execute(
        """UPDATE content_b_analyses SET state='partial'
           WHERE id=%s AND state IN ('pending','partial')""",
        (analysis_id,),
    )
    saved = conn.execute(
        """SELECT subtitle_type,position,font_size,font_style,text_color,
                  highlight_color,background_style,display_mode
           FROM content_b_subtitles WHERE analysis_id=%s""",
        (analysis_id,),
    ).fetchone()
    if saved is None or tuple(saved) != tuple(data[k] for k in COLUMNS):
        _deny("b6_sql005_readback_mismatch")
    return data
