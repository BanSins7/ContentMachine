"""GB-MOD-19: opt-in atomic SQL005 B7 CTA analysis and occurrences.

Separate 0..N rows from analysis completeness: SQL005 content_b_cta_summary
returns NULL unless a fully attested, reviewed B7 scan is COMPLETE.
Requires caller transaction, precreated B parent and trusted fresh admission.
"""
from __future__ import annotations

from collections.abc import Mapping

from .b7_cta import B7CTAProposal,B7CTAError
from ..context.manifest import MediaEvidenceManifest


def _deny(code):
    raise B7CTAError(code)


def _row(cursor):
    r=cursor.fetchone()
    if r is None:
        return None
    if isinstance(r,Mapping):
        return dict(r)
    return dict(zip((item.name for item in cursor.description),r))


_FIELDS=(
    "occurrence_index","cta_type","text","spoken_text","screen_text",
    "delivery","start_sec","end_sec","slide_index",
    "destination_type","destination_service","destination_url",
    "destination_handle","destination_app_name","destination_keyword",
    "destination_other_desc","extraction_status",
)


def persist_b7_cta(conn,*,analysis_id:int,manifest:MediaEvidenceManifest,
                   proposal:B7CTAProposal,admission_verifier:object):
    """Append exactly 1 B7 analysis + 0..N rows under the caller transaction.

    verify_cta_persistence must freshly authenticate provider receipt and
    original full scan (if complete), rights, media byte hash, exact QDP/A
    versions and the recorded proposal. The default is always DENY.
    """
    if (type(analysis_id) is not int or analysis_id<=0 or
        not isinstance(manifest,MediaEvidenceManifest) or
        not manifest.verify_digest() or
        not isinstance(proposal,B7CTAProposal) or
        not proposal.can_persist):
        _deny("cta_proposal_not_persistable")
    p=proposal.payload
    expected={
        "project_id":manifest.project_id,
        "b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,
        "qdp_run_id":manifest.qdp_run_id,
        "ga_result_id":manifest.ga_result_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "source_policy_version_id":manifest.source_policy_version_id,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "content_kind":manifest.content_kind,
    }
    if any(p.get(k)!=v for k,v in expected.items()):
        _deny("cta_source_pins_not_current")
    trusted=getattr(admission_verifier,"verify_cta_persistence",None)
    if not callable(trusted):
        _deny("cta_fresh_admission_missing")
    try:
        decision=trusted(analysis_id,manifest,proposal)
    except Exception:
        _deny("cta_fresh_admission_failed")
    if decision is not True:
        _deny("cta_fresh_admission_failed")
    parent=_row(conn.execute(
        """SELECT id,video_id,source_id,content_type,state,
                  source_policy_version_id,method_details
           FROM content_b_analyses WHERE id=%s FOR UPDATE""",
        (analysis_id,)
    ))
    if parent is None or parent["state"] not in ("pending","partial"):
        _deny("cta_parent_missing_or_immutable")
    if (parent["video_id"]!=manifest.video_id or
        parent["source_id"]!=manifest.source_id or
        parent["source_policy_version_id"]!=manifest.source_policy_version_id or
        parent["content_type"]!=(
            "video" if manifest.content_kind in ("video","slideshow_video")
            else "carousel"
        )):
        _deny("cta_parent_wrong_source_or_type")
    details=parent["method_details"]
    expected_details={
        "group_b_project_id":manifest.project_id,
        "group_b_run_id":manifest.b_run_id,
        "group_b_media_evidence_manifest_sha256":manifest.manifest_sha256,
        "group_b_b7_cta_proposal_sha256":proposal.proposal_sha256,
    }
    if type(details) is not dict or any(details.get(k)!=v for k,v in expected_details.items()):
        _deny("cta_parent_proposal_sha_unpinned")
    if conn.execute(
        "SELECT EXISTS(SELECT 1 FROM content_b_cta_analyses WHERE analysis_id=%s)",
        (analysis_id,)
    ).fetchone()[0]:
        _deny("cta_replay_cannot_overwrite")
    header=proposal.analysis_db_fields()
    rows=proposal.occurrence_db_rows()
    conn.execute(
        """INSERT INTO content_b_cta_analyses(
             analysis_id,analysis_status,taxonomy_version,analyzed_at,
             model_version)
           VALUES(%s,%s,%s,now(),%s)""",
        (analysis_id,header["analysis_status"],
         header["taxonomy_version"],header["model_version"]),
    )
    for row in rows:
        conn.execute(
            """INSERT INTO content_b_ctas(
              analysis_id,occurrence_index,cta_type,text,spoken_text,screen_text,
              delivery,start_sec,end_sec,slide_index,destination_type,
              destination_service,destination_url,destination_handle,
              destination_app_name,destination_keyword,destination_other_desc,
              extraction_status)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (analysis_id,*(row[k] for k in _FIELDS)),
        )
    conn.execute(
        """UPDATE content_b_analyses SET state='partial'
           WHERE id=%s AND state IN ('pending','partial')""",
        (analysis_id,),
    )
    got=_row(conn.execute(
        """SELECT analysis_status,has_cta,detected_occurrences
           FROM content_b_cta_summary WHERE analysis_id=%s""",
        (analysis_id,),
    ))
    if (got is None or got["analysis_status"]!=header["analysis_status"] or
        got["detected_occurrences"]!=len(rows) or
        got["has_cta"] is not (
            bool(rows) if header["analysis_status"]=="complete" else None
        )):
        _deny("cta_sql005_summary_mismatch")
    actual=conn.execute(
        """SELECT occurrence_index,cta_type,text,spoken_text,screen_text,
                  delivery,start_sec,end_sec,slide_index,destination_type,
                  destination_service,destination_url,destination_handle,
                  destination_app_name,destination_keyword,
                  destination_other_desc,extraction_status
           FROM content_b_ctas WHERE analysis_id=%s
           ORDER BY occurrence_index""",(analysis_id,)
    ).fetchall()
    if len(actual)!=len(rows):
        _deny("cta_sql005_row_count_mismatch")
    from decimal import Decimal
    for existing,wanted in zip(actual,rows):
        for i,key in enumerate(_FIELDS):
            observed=existing[i]
            val=wanted[key]
            if key in ("start_sec","end_sec") and val is not None:
                if Decimal(str(observed))!=Decimal(str(val)):
                    _deny("cta_sql005_row_readback_mismatch")
            elif key=="delivery":
                if tuple(observed)!=tuple(val):
                    _deny("cta_sql005_delivery_readback_mismatch")
            elif observed!=val:
                _deny("cta_sql005_row_readback_mismatch")
    return {"analysis_status":header["analysis_status"],
            "has_cta":got["has_cta"],"detected_occurrences":len(rows)}
