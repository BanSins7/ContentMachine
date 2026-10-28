"""GB-MOD-22 atomic, versioned SQL005 B1–B8 persistence.

One transaction: source-pinned runtime run -> versioned B analysis -> domain
children -> block states -> run result pointer. A failed step rolls everything
back. No extra B database and NO writes to any other branch/module.
No provider/GA/QDP/rights approval is manufactured here: the host verifier
is mandatory and MUST freshly validate actual request/receipt/media/rights.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import re

from ..context.manifest import MediaEvidenceManifest
from ..contracts import canonical_hash
from ..errors import GroupBError
from ..validation import BLOCKS,Gate,UnifiedAssessment,validate_bundle
from ..blocks.b3_sampling import B3SamplingPlan,verify_b3_plan_against_manifest
from ..topic_registry import TaxonomySnapshot
from ..blocks.b3_storage import persist_b3_story
from ..blocks.b4_storage import persist_b4_visual
from ..blocks.b6_storage import persist_b6_subtitles
from ..blocks.b7_storage import persist_b7_cta
from ..blocks.b8_storage import persist_b8_topic

VERSION="group-b-atomic/v1"
_SHA=re.compile(r"[0-9a-f]{64}\Z")
VIDEO=("video","slideshow_video")
CAROUSEL=("photo_carousel","mixed_carousel")
CHILDREN={
    "B1":("content_b_hooks","content_b_hook_parts"),
    "B2":("content_b_formats",),
    "B3":("content_b_story_sampling","content_b_story_frames",
          "content_b_story_intervals","content_b_story_slides"),
    "B4":("content_b_visual_content",),
    "B5":("content_b_editing",),
    "B6":("content_b_subtitles",),
    "B7":("content_b_cta_analyses","content_b_ctas"),
    "B8":("content_b_topic_assignments",),
}
ALL_CHILDREN=tuple(t for tables in CHILDREN.values() for t in tables)
HOOK_KEYS=("start_sec","end_sec","slide_index","spoken_text","screen_text",
           "visual_description","mechanisms","is_question","has_number",
           "direct_address","has_negation","is_command","delivery",
           "classification_status","taxonomy_version")
PART_KEYS=("part_index","start_sec","end_sec","slide_index","verbatim_text","mechanisms")
FORMAT_KEYS=("video_primary","video_secondary","carousel_format",
             "classification_status","taxonomy_version")
EDIT_KEYS=("editing_type","cut_count","cuts_per_minute",
           "avg_shot_duration","cuts_first_5_sec")
# The SQL identifiers in this file are trusted code-only static strings;
# all source IDs, text, hashes and labels are bound SQL parameters.


class AtomicBError(GroupBError):
    def __init__(self,reason:str):
        self.reason=reason
        super().__init__("b22_atomic_denied","group_b_atomic: "+reason)


def _deny(reason:str):
    raise AtomicBError(reason)


def _sha(v):
    return type(v) is str and _SHA.fullmatch(v) is not None


def _one(cursor):
    row=cursor.fetchone()
    if row is None:
        return None
    if isinstance(row,Mapping):
        return dict(row)
    return dict(zip((c.name for c in cursor.description),row))


def _dict(obj):
    return obj if type(obj) is dict else None


def _insert(conn,table:str,columns:tuple[str,...],data:dict,analysis_id:int):
    if table not in ALL_CHILDREN or any(not re.fullmatch(r"[a-z][a-z0-9_]*",k)
                                        for k in columns):
        _deny("untrusted_sql_insert_shape")
    if set(data)!=set(columns):
        _deny("block_sql005_columns_not_exact")
    names=",".join(("analysis_id",)+columns)
    vals=",".join(["%s"]*(len(columns)+1))
    conn.execute(f"INSERT INTO {table}({names}) VALUES({vals})",
                 (analysis_id,*(data[key] for key in columns)))


def _fingerprints(candidates:dict,assessment:UnifiedAssessment,
                  plan:B3SamplingPlan|None,registry:TaxonomySnapshot|None):
    result={}
    for block in BLOCKS:
        entry=assessment.block(block)
        candidate=candidates.get(block)
        if entry.state is Gate.ACCEPTED:
            if candidate is None:
                _deny("accepted_block_missing_candidate")
            if block=="B5":
                payload=candidate.wire()
                result[block]=canonical_hash(payload)
            else:
                if not candidate.verify_digest() or not _sha(candidate.proposal_sha256):
                    _deny("candidate_digest_invalid")
                if candidate.proposal_sha256!=entry.proposal_sha256:
                    _deny("unified_assessment_stale_or_forged")
                result[block]=candidate.proposal_sha256
        else:
            if candidate is not None and hasattr(candidate,"proposal_sha256"):
                if not candidate.verify_digest() or candidate.proposal_sha256!=entry.proposal_sha256:
                    _deny("review_candidate_digest_changed")
            result[block]=entry.proposal_sha256
    if assessment.block("B3").state is Gate.ACCEPTED and (
        not isinstance(plan,B3SamplingPlan) or not plan.verify_digest()
    ):
        _deny("b3_sampling_plan_missing")
    if assessment.block("B8").state is Gate.ACCEPTED and (
        not isinstance(registry,TaxonomySnapshot) or not registry.verify_digest()
    ):
        _deny("b8_registry_missing")
    return result


def _pins(manifest:MediaEvidenceManifest,assessment:UnifiedAssessment,
          fingerprints,plan,registry):
    pins={
        "group_b_project_id":manifest.project_id,
        "group_b_run_id":manifest.b_run_id,
        "group_b_media_evidence_manifest_sha256":manifest.manifest_sha256,
        "group_b_qdp_run_id":manifest.qdp_run_id,
        "group_b_ga_result_id":manifest.ga_result_id,
        "group_b_ga_result_sha256":manifest.ga_result_sha256,
        "group_b_source_policy_version_id":manifest.source_policy_version_id,
        "group_b_validation_version":assessment.schema_version,
        "group_b_block_fingerprints":fingerprints,
    }
    if plan is not None:
        pins["group_b_b3_sampling_plan_sha256"]=plan.plan_sha256
    if registry is not None:
        pins["group_b_b8_registry_snapshot_sha256"]=registry.snapshot_sha256
    mapping={
        "B3":"group_b_b3_story_proposal_sha256",
        "B4":"group_b_b4_visual_proposal_sha256",
        "B6":"group_b_b6_subtitle_proposal_sha256",
        "B7":"group_b_b7_cta_proposal_sha256",
        "B8":"group_b_b8_topic_proposal_sha256",
    }
    for block,name in mapping.items():
        if fingerprints[block] is not None:
            pins[name]=fingerprints[block]
    return pins


def _analysis_key(manifest, fingerprints,plan,registry):
    blob={
        "schema":VERSION,"project_id":manifest.project_id,
        "b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,
        "source_id":manifest.source_id,
        "source_policy_version_id":manifest.source_policy_version_id,
        "qdp_run_id":manifest.qdp_run_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "original_manifest_sha256":manifest.manifest_sha256,
        "block_proposals":fingerprints,
        "sampling_plan_sha256":plan.plan_sha256 if plan else None,
        "registry_sha256":registry.snapshot_sha256 if registry else None,
    }
    return VERSION+":"+canonical_hash(blob)


def _status_map(assessment):
    statuses={}
    for block in BLOCKS:
        entry=assessment.block(block)
        if entry.state is Gate.ACCEPTED:
            statuses[block]="complete"
        elif entry.state is Gate.NEEDS_REVIEW:
            statuses[block]="partial"
        else:
            _deny("quarantined_block_cannot_be_saved")
    return statuses


@dataclass(frozen=True,slots=True)
class AtomicBResult:
    analysis_id:int
    analysis_key:str
    state:str
    replayed:bool
    block_statuses:dict[str,str]


def _save_b1(conn,aid,proposal):
    main=proposal.hook_db_fields()
    _insert(conn,"content_b_hooks",HOOK_KEYS,main,aid)
    for part in proposal.parts_db_rows():
        _insert(conn,"content_b_hook_parts",PART_KEYS,part,aid)


def _save_b2(conn,aid,proposal):
    _insert(conn,"content_b_formats",FORMAT_KEYS,proposal.db_fields(),aid)


def _save_b5(conn,aid,metrics):
    _insert(conn,"content_b_editing",EDIT_KEYS,metrics.db_fields(),aid)


def _save_b3_carousel(conn,aid,manifest,plan,proposal,admission_verifier):
    """SQL005 forbids video-only story_sampling on photo/mixed carousels.

    Preserve B3's source/plan/receipt gate, insert verified slide roles only;
    do not manufacture technical video-frame FPS metadata for an image post.
    """
    if (manifest.content_kind not in CAROUSEL or
        not verify_b3_plan_against_manifest(plan,manifest) or
        not proposal.verify_digest() or
        proposal.authenticated_inference is not True or
        proposal.payload.get("classification_status")!="inferred" or
        proposal.payload.get("content_type")!="carousel" or
        proposal.payload.get("sampling_plan_sha256")!=plan.plan_sha256 or
        proposal.payload.get("project_id")!=manifest.project_id or
        proposal.payload.get("b_run_id")!=manifest.b_run_id or
        proposal.payload.get("qdp_run_id")!=manifest.qdp_run_id or
        proposal.payload.get("video_id")!=manifest.video_id or
        proposal.payload.get("ga_result_sha256")!=manifest.ga_result_sha256 or
        proposal.payload.get("media_evidence_manifest_sha256")!=manifest.manifest_sha256 or
        proposal.frame_db_rows() or proposal.interval_db_rows() or
        not proposal.slide_db_rows()):
        _deny("b3_carousel_source_plan_or_slide_roles_unverified")
    verifier=getattr(admission_verifier,"verify_story_persistence",None)
    if not callable(verifier):
        _deny("b3_carousel_fresh_admission_missing")
    try:
        accepted=verifier(aid,manifest,plan,proposal)
    except Exception:
        _deny("b3_carousel_fresh_admission_failed")
    if accepted is not True:
        _deny("b3_carousel_fresh_admission_failed")
    fields=("slide_index","role","before_after_phase",
            "screen_text","visual_description","needs_review")
    original_slides={o.slide_index for o in manifest.observations
                     if o.kind=="slide_ref"}
    for slide in proposal.slide_db_rows():
        if slide["slide_index"] not in original_slides or slide["needs_review"] is True:
            _deny("b3_carousel_unverified_original_slide")
        _insert(conn,"content_b_story_slides",fields,slide,aid)


def _counts(conn,aid):
    return {block:sum(conn.execute(
        f"SELECT count(*) FROM {table} WHERE analysis_id=%s",(aid,)
    ).fetchone()[0] for table in tables) for block,tables in CHILDREN.items()}


def _readback(conn,aid,assessment,candidates,manifest):
    counts=_counts(conn,aid)
    accepted={block for block in BLOCKS
              if assessment.block(block).state is Gate.ACCEPTED}
    for block in BLOCKS:
        if block not in accepted and counts[block]:
            _deny("review_only_block_wrote_unapproved_result")
        if block in accepted and counts[block]==0:
            _deny("accepted_block_missing_sql005_row")
    b1=candidates.get("B1")
    if "B1" in accepted and counts["B1"]!=1+len(b1.parts_db_rows()):
        _deny("b1_parts_readback_mismatch")
    b3=candidates.get("B3")
    if "B3" in accepted:
        expected=(1 if manifest.content_kind in VIDEO else 0)+len(b3.frame_db_rows())+len(b3.interval_db_rows())+len(b3.slide_db_rows())
        if counts["B3"]!=expected:
            _deny("b3_frames_intervals_slides_readback_mismatch")
    b7=candidates.get("B7")
    if "B7" in accepted and counts["B7"]!=1+len(b7.occurrence_db_rows()):
        _deny("b7_occurrence_readback_mismatch")
    view=conn.execute(
        "SELECT analysis_status,has_cta,detected_occurrences FROM content_b_cta_summary WHERE analysis_id=%s",
        (aid,)
    ).fetchone()
    if "B7" in accepted:
        if (view is None or view[0]!="complete" or
            view[1] is not bool(b7.occurrence_db_rows()) or
            view[2]!=len(b7.occurrence_db_rows())):
            _deny("b7_summary_not_sql005_truthful")
    elif view is not None:
        _deny("unapproved_b7_view_materialized")
    # Existing 005 views, never duplicated/redefined.
    if "B3" in accepted:
        got=conn.execute(
            "SELECT story_pattern FROM content_b_story_patterns WHERE analysis_id=%s",
            (aid,)
        ).fetchone()
        if got is None or tuple(got[0] or ())!=b3.story_pattern:
            _deny("b3_story_pattern_not_exact")
    return counts


def _persist(conn,*,manifest,assessment,candidates,plan,registry,
             admission_verifier,fingerprints,states,key,pins,project_id):
    # Locking run serializes all concurrent writers for the same project/run.
    run=_one(conn.execute(
        """SELECT id,project_id,video_id,source_id,content_type,state,
                  source_policy_version_id,a_snapshot_sha256,
                  media_manifest_sha256,result_analysis_id,inbox_id
           FROM group_b_runs WHERE project_id=%s AND id=%s::uuid FOR UPDATE""",
        (project_id,manifest.b_run_id)
    ))
    content_type="video" if manifest.content_kind in VIDEO else "carousel"
    if (run is None or run["video_id"]!=manifest.video_id or
        run["source_id"]!=manifest.source_id or
        run["content_type"]!=content_type or
        run["source_policy_version_id"]!=manifest.source_policy_version_id or
        run["a_snapshot_sha256"]!=manifest.ga_result_sha256 or
        run["media_manifest_sha256"]!=manifest.media_manifest_sha256):
        _deny("run_original_ga_qdp_media_or_source_pins_mismatch")
    # Hold the source policy row lock until commit. This prevents rights
    # revocation from racing in AFTER the fresh host verification and the
    # SQL005 per-child triggers.
    source=_one(conn.execute(
        """SELECT id,status,can_analyze_media FROM sources
           WHERE id=%s FOR SHARE""",(manifest.source_id,)
    ))
    if source is None or source["status"]!="enabled" or source["can_analyze_media"] is not True:
        _deny("publication_media_rights_revoked")
    if manifest.source_policy_version_id is not None:
        policy=_one(conn.execute(
            """SELECT id,source_id,is_active,permissions
               FROM source_policy_versions WHERE id=%s FOR SHARE""",
            (manifest.source_policy_version_id,)
        ))
        if (policy is None or policy["source_id"]!=manifest.source_id or
            policy["is_active"] is not True or
            type(policy["permissions"]) is not dict or
            policy["permissions"].get("can_analyze_media") is not True):
            _deny("source_policy_version_revoked_or_unapproved")
    inbox=_one(conn.execute(
        """SELECT status,qdp_run_id,video_id,source_id,project_id
           FROM group_b_inbox WHERE id=%s FOR SHARE""",
        (run["inbox_id"],)
    ))
    if (inbox is None or inbox["status"]!="accepted" or
        str(inbox["qdp_run_id"])!=manifest.qdp_run_id or
        inbox["video_id"]!=manifest.video_id or
        inbox["source_id"]!=manifest.source_id or
        inbox["project_id"]!=project_id):
        _deny("run_inbox_not_authenticated_or_current")
    verify=getattr(admission_verifier,"verify_bundle_persistence",None)
    if not callable(verify):
        _deny("fresh_bundle_admission_missing")
    try:
        approved=verify(manifest,assessment,candidates,key)
    except Exception:
        _deny("fresh_bundle_admission_failed")
    if approved is not True:
        _deny("fresh_bundle_admission_failed")
    stored=run["result_analysis_id"]
    if stored is not None:
        row=_one(conn.execute(
            "SELECT analysis_key,state,method_details FROM content_b_analyses WHERE id=%s",
            (stored,)
        ))
        if (row is None or row["analysis_key"]!=key or
            type(row["method_details"]) is not dict or
            row["method_details"].get("group_b_atomic_version")!=VERSION or
            row["method_details"].get("group_b_block_fingerprints")!=fingerprints or
            row["state"] not in ("partial","completed")):
            _deny("run_replay_or_competing_version_immutable")
        statuses={
            block:status for block,status in conn.execute(
                """SELECT block,status FROM group_b_block_states
                   WHERE project_id=%s AND run_id=%s::uuid""",
                (project_id,manifest.b_run_id)
            ).fetchall()
        }
        if statuses!=states:
            _deny("saved_run_block_statuses_missing_or_changed")
        return AtomicBResult(stored,key,row["state"],True,dict(statuses))
    if run["state"] not in ("running","partial","needs_review"):
        _deny("run_not_claimed_for_persistence")
    # A unique immutable analysis_key is derived from exact source/version
    # inputs; no upsert that can overwrite another project's analysis.
    detail=dict(pins)
    detail.update({"group_b_atomic_version":VERSION,
                   "group_b_atomic_analysis_key":key})
    aid=conn.execute(
        """INSERT INTO content_b_analyses (
             video_id,source_id,source_policy_version_id,
             analysis_key,analysis_version,content_type,
             method_name,method_version,state,method_details)
           VALUES(%s,%s,%s,%s,%s,%s,'group_b_atomic','1','pending',%s::jsonb)
           RETURNING id""",
        (manifest.video_id,manifest.source_id,manifest.source_policy_version_id,
         key,VERSION,content_type,
         json.dumps(detail,sort_keys=True,ensure_ascii=False,allow_nan=False))
    ).fetchone()[0]
    # Child adapters run inside the same DB transaction/savepoint. Each
    # rechecks its own source/policy/receipt and refuses replay.
    if states["B1"]=="complete":
        _save_b1(conn,aid,candidates["B1"])
    if states["B2"]=="complete":
        _save_b2(conn,aid,candidates["B2"])
    if states["B3"]=="complete":
        if content_type=="video":
            persist_b3_story(conn,analysis_id=aid,manifest=manifest,plan=plan,
                             proposal=candidates["B3"],
                             admission_verifier=admission_verifier)
        else:
            _save_b3_carousel(conn,aid,manifest,plan,candidates["B3"],
                              admission_verifier)
    if states["B4"]=="complete":
        persist_b4_visual(conn,analysis_id=aid,manifest=manifest,
                          proposal=candidates["B4"],
                          admission_verifier=admission_verifier)
    if states["B5"]=="complete":
        if content_type!="video":
            _deny("b5_video_only")
        _save_b5(conn,aid,candidates["B5"])
    if states["B6"]=="complete":
        if content_type!="video":
            _deny("b6_video_only")
        persist_b6_subtitles(conn,analysis_id=aid,manifest=manifest,
                             proposal=candidates["B6"],
                             admission_verifier=admission_verifier)
    if states["B7"]=="complete":
        persist_b7_cta(conn,analysis_id=aid,manifest=manifest,
                       proposal=candidates["B7"],
                       admission_verifier=admission_verifier)
    if states["B8"]=="complete":
        persist_b8_topic(conn,analysis_id=aid,manifest=manifest,
                         registry=registry,proposal=candidates["B8"],
                         admission_verifier=admission_verifier)
    _readback(conn,aid,assessment,candidates,manifest)
    # Not-applicable video-only modules remain partial/unavailable on carousels
    # until a separate trusted N/A completion contract exists.
    completed=assessment.can_complete is True and all(v=="complete" for v in states.values())
    if completed and content_type!="video":
        _deny("carousel_full_completion_requires_explicit_na_contract")
    state="completed" if completed else "partial"
    for block,block_state in states.items():
        cur=conn.execute(
            """UPDATE group_b_block_states SET status=%s,
                      evidence_sha256=%s,error_code=%s,updated_at=now()
               WHERE project_id=%s AND run_id=%s::uuid AND block=%s
                 AND status NOT IN ('complete','failed')
            """,
            (block_state,fingerprints[block],
             None if block_state=="complete" else "review_or_not_applicable",
             project_id,manifest.b_run_id,block)
        )
        if cur.rowcount!=1:
            _deny("run_block_state_missing_or_immutable")
    conn.execute(
        """UPDATE content_b_analyses SET state=%s,
                  needs_review=%s,analyzed_at=now()
           WHERE id=%s AND state IN ('pending','partial')""",
        (state,not completed,aid)
    )
    res=conn.execute(
        """UPDATE group_b_runs SET result_analysis_id=%s,state=%s,
                  updated_at=now() WHERE project_id=%s AND id=%s::uuid
                  AND result_analysis_id IS NULL""",
        (aid,state,project_id,manifest.b_run_id)
    )
    if res.rowcount!=1:
        _deny("run_result_concurrent_update")
    return AtomicBResult(aid,key,state,False,dict(states))


def persist_group_b_bundle(conn,*,manifest:MediaEvidenceManifest,
                           assessment:UnifiedAssessment,candidates:dict,
                           admission_verifier:object,packets:dict,
                           request_proofs:dict,
                           plan:B3SamplingPlan|None=None,
                           registry:TaxonomySnapshot|None=None)->AtomicBResult:
    """Atomic SQL005 writer. Connection must be psycopg3, never autocommit
    individual steps. It owns a transaction/savepoint, but caller MUST commit
    its outer transaction if the connection is already inside one.

    The unified acceptance report is recomputed from packets/receipts before
    the transaction; a forged or stale report is rejected. Fresh
    verify_bundle_persistence is called UNDER THE LOCKED run transaction
    and must independently bind source rights,
    exact QDP/GA/media hashes, live provider receipts/model+prompt versions,
    project authorization, worker lease and all block proof. Never use a
    synthetic test verifier in production.
    """
    if (not isinstance(manifest,MediaEvidenceManifest) or
        not manifest.verify_digest() or
        not isinstance(assessment,UnifiedAssessment) or
        assessment.schema_version!="group-b-unified-validation/v1" or
        assessment.manifest_sha256!=manifest.manifest_sha256 or
        type(candidates) is not dict or any(k not in BLOCKS for k in candidates) or
        type(packets) is not dict or
        any(k not in BLOCKS for k in packets) or
        type(request_proofs) is not dict or
        any(k not in BLOCKS for k in request_proofs) or
        type(assessment.blocks) is not tuple or
        tuple(x.block for x in assessment.blocks)!=BLOCKS or
        not callable(getattr(conn,"transaction",None))):
        _deny("bundle_source_or_contract_invalid")
    if type(manifest.project_id) is not str or not manifest.project_id:
        _deny("project_scope_missing")
    # Recompute unified source/schema and external block receipts using the
    # exact current source, packets and request proofs; an externally forged
    # "accepted" BlockAssessment cannot authorize writes.
    fresh=validate_bundle(
        manifest=manifest,candidates=candidates,packets=packets,
        request_proofs=request_proofs,trusted_attestor=admission_verifier,
    )
    if fresh!=assessment:
        _deny("bundle_assessment_stale_or_forged")
    states=_status_map(fresh)
    fingerprints=_fingerprints(candidates,fresh,plan,registry)
    key=_analysis_key(manifest,fingerprints,plan,registry)
    pins=_pins(manifest,assessment,fingerprints,plan,registry)
    with conn.transaction():
        return _persist(
            conn,manifest=manifest,assessment=assessment,
            candidates=candidates,plan=plan,registry=registry,
            admission_verifier=admission_verifier,fingerprints=fingerprints,
            states=states,key=key,pins=pins,project_id=manifest.project_id,
        )


def read_group_b_result(conn,*,project_id:str,run_id:str):
    """Project/run-scoped, READ ONLY consumer snapshot; no cross-project leak.

    Reads existing SQL005 views rather than duplicating mutable storage.
    """
    if (type(project_id) is not str or not project_id or
        type(run_id) is not str or not run_id):
        _deny("read_project_run_id_invalid")
    result=_one(conn.execute(
        """SELECT a.id,a.video_id,a.source_id,a.analysis_key,
                  a.analysis_version,a.content_type,a.state,a.needs_review,
                  a.analyzed_at
           FROM group_b_runs r JOIN content_b_analyses a
             ON a.id=r.result_analysis_id
           WHERE r.project_id=%s AND r.id=%s::uuid
             AND a.video_id=r.video_id AND a.source_id=r.source_id""",
        (project_id,run_id)
    ))
    if result is None:
        return None
    id_=result["id"]
    result["story_pattern"]=conn.execute(
        "SELECT story_pattern FROM content_b_story_patterns WHERE analysis_id=%s",
        (id_,)
    ).fetchone()[0]
    summary=conn.execute(
        "SELECT analysis_status,has_cta,detected_occurrences FROM content_b_cta_summary WHERE analysis_id=%s",
        (id_,)
    ).fetchone()
    result["cta_summary"]=(
        {"analysis_status":summary[0],"has_cta":summary[1],
         "detected_occurrences":summary[2]} if summary else None
    )
    result["block_states"]={
        block:state for block,state in conn.execute(
            """SELECT block,status FROM group_b_block_states
               WHERE project_id=%s AND run_id=%s::uuid ORDER BY block""",
            (project_id,run_id)
        ).fetchall()
    }
    return result
