"""GB-MOD-23 append-only human review over immutable SQL005 B results.

All writes go to existing 055 group_b_review_events, never SQL005 automatic
B1-B8 tables. Runtime run row lock serializes review revisions. A reviewer
string or hash is NOT authenticated reviewer identity; trusted host MUST
authorize exact person, source bytes, role, and all media citations.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID
import json

from .contracts import (
    ReviewDecision,ReviewError,VERSION,FIELDS,TARGETS,verify_decision,
    validate_effective_row,make_decision,
)
from ..contracts import canonical_hash
from ..topic_registry import read_topic_registry
from ..persistence.repository import read_group_b_result

# SQL names exclusively trusted in source; never interpolate user strings.
TABLES={
 "B1":{"hook":("content_b_hooks",None)},
 "B2":{"format":("content_b_formats",None)},
 "B3":{"frame":("content_b_story_frames","frame_index"),
       "interval":("content_b_story_intervals","interval_index"),
       "slide":("content_b_story_slides","slide_index")},
 "B4":{"visual":("content_b_visual_content",None)},
 "B5":{"editing":("content_b_editing",None)},
 "B6":{"subtitle":("content_b_subtitles",None)},
 "B7":{"occurrence":("content_b_ctas","occurrence_index")},
 "B8":{"topic":("content_b_topic_assignments",None)},
}
_TARGETABLE=tuple(t for m in TABLES.values() for t,_ in m.values())


def _deny(reason):
    raise ReviewError(reason)


def _one(cur):
    row=cur.fetchone()
    if row is None:
        return None
    if isinstance(row,Mapping):
        return dict(row)
    return dict(zip((c.name for c in cur.description),row))


def _row(conn,analysis_id,block,target,index):
    table,index_column=TABLES[block][target]
    query=f"SELECT * FROM {table} WHERE analysis_id=%s"
    args=(analysis_id,)
    if index_column:
        query+=f" AND {index_column}=%s"
        args+=(index,)
    return _one(conn.execute(query,args))


def _latest_events(conn,project_id,run_id):
    events=conn.execute(
        """SELECT revision,reviewer_id,decision_sha256,decision
           FROM group_b_review_events
           WHERE project_id=%s AND run_id=%s::uuid
           ORDER BY revision""",(project_id,run_id)
    ).fetchall()
    previous=None
    result=[]
    for number,raw in enumerate(events,1):
        revision,reviewer,sha,payload=raw
        candidate=ReviewDecision(payload,sha)
        if (revision!=number or not verify_decision(candidate) or
            payload["previous_revision"]!=number-1 or
            payload["previous_sha256"]!=previous or
            type(reviewer) is not str or not reviewer):
            _deny("review_history_chain_invalid")
        result.append((number,reviewer,candidate))
        previous=sha
    return tuple(result)


def _load_run(conn,project_id,run_id,*,lock):
    query="""SELECT r.id,r.state AS run_state,r.result_analysis_id,
                     r.video_id,r.source_id,r.source_policy_version_id,
                     a.analysis_key,a.state AS analysis_state,
                     a.method_details,a.needs_review
              FROM group_b_runs r
              JOIN content_b_analyses a ON a.id=r.result_analysis_id
              WHERE r.project_id=%s AND r.id=%s::uuid
                AND a.video_id=r.video_id AND a.source_id=r.source_id"""
    if lock:
        query+=" FOR UPDATE OF r"
    else:
        # Keep review writer's run lock blocked until the effective read
        # has assembled a single consistent revision chain.
        query+=" FOR SHARE OF r"
    info=_one(conn.execute(query,(project_id,run_id)))
    if (info is None or
        info["run_state"] not in ("partial","completed","needs_review") or
        info["analysis_state"] not in ("partial","completed") or
        type(info["method_details"]) is not dict or
        info["method_details"].get("group_b_project_id")!=project_id or
        info["method_details"].get("group_b_run_id")!=run_id):
        _deny("review_run_analysis_not_authoritative")
    source=_one(conn.execute(
        "SELECT status,can_analyze_media FROM sources WHERE id=%s FOR SHARE",
        (info["source_id"],)
    ))
    if (source is None or source["status"]!="enabled" or
        source["can_analyze_media"] is not True):
        _deny("review_source_rights_missing_or_revoked")
    if info["source_policy_version_id"] is not None:
        policy=_one(conn.execute(
            """SELECT is_active,permissions,source_id
               FROM source_policy_versions WHERE id=%s FOR SHARE""",
            (info["source_policy_version_id"],)
        ))
        if (policy is None or policy["is_active"] is not True or
            policy["source_id"]!=info["source_id"] or
            type(policy["permissions"]) is not dict or
            policy["permissions"].get("can_analyze_media") is not True):
            _deny("review_source_policy_revoked")
    return info


def _same_target(p,decision):
    return (p["block"]==decision["block"] and
            p["target"]==decision["target"] and
            p["index"]==decision["index"])


def _effective_row(conn,analysis_id,decision,events):
    original=_row(conn,analysis_id,decision["block"],decision["target"],decision["index"])
    if original is None:
        _deny("review_target_sql005_row_missing")
    result=dict(original)
    if decision["block"]=="B2":
        parent=conn.execute(
            "SELECT content_type FROM content_b_analyses WHERE id=%s",
            (analysis_id,)
        ).fetchone()
        if parent is None:
            _deny("review_b2_parent_missing")
        result["_content_type"]=parent[0]
    for _,_,event in events:
        p=event.payload
        if p["kind"]=="correction" and _same_target(p,decision):
            result[p["field"]]=p["value"]
    return original,result


@dataclass(frozen=True,slots=True)
class ReviewAppendResult:
    revision:int
    decision_sha256:str
    replayed:bool


def append_review(conn,*,project_id:str,run_id:str,reviewer_id:str,
                  expected_revision:int,decision:ReviewDecision,
                  admission_verifier:object)->ReviewAppendResult:
    """Atomic compare-and-append. Caller owns any surrounding transaction.

    `verify_human_review_admission` checks authenticated reviewer role,
    original evidence refs, true full-source observations (especially B5/B6
    'none'), required secondary approver, current rights and source revocation.
    No standalone reviewer UI credential is trusted here.
    """
    if (type(project_id) is not str or not project_id or
        type(run_id) is not str or not run_id or
        type(reviewer_id) is not str or not 1<=len(reviewer_id)<=160 or
        type(expected_revision) is not int or expected_revision<0 or
        not verify_decision(decision) or
        decision.payload["previous_revision"]!=expected_revision or
        not callable(getattr(conn,"transaction",None))):
        _deny("review_request_invalid")
    verify=getattr(admission_verifier,"verify_human_review_admission",None)
    if not callable(verify):
        _deny("review_trusted_reviewer_admission_missing")
    with conn.transaction():
        run=_load_run(conn,project_id,run_id,lock=True)
        p=decision.payload
        if (p["analysis_id"]!=run["result_analysis_id"] or
            p["analysis_key"]!=run["analysis_key"] or
            p["source_evidence_manifest_sha256"]!=run["method_details"].get(
                "group_b_media_evidence_manifest_sha256")):
            _deny("review_source_version_or_analysis_mismatch")
        events=_latest_events(conn,project_id,run_id)
        current=len(events)
        if current!=expected_revision:
            if (current>=expected_revision+1 and expected_revision>=0):
                prior=events[expected_revision]
                if (prior[1]==reviewer_id and
                    prior[2].decision_sha256==decision.decision_sha256):
                    return ReviewAppendResult(
                        expected_revision+1,decision.decision_sha256,True
                    )
            _deny("review_revision_conflict")
        expected_prior=events[-1][2].decision_sha256 if events else None
        if p["previous_sha256"]!=expected_prior:
            _deny("review_previous_sha_forged")
        base_row,effective=_effective_row(
            conn,run["result_analysis_id"],p,events
        )
        effective[p["field"]]=p["value"]
        if p["kind"]=="correction":
            if p["block"]=="B8":
                # Same lock discipline as B8's original SQL005 writer:
                # prevent category/topic merge or reparenting TOCTOU.
                conn.execute(
                    "LOCK TABLE content_b_categories, content_b_topics, "
                    "content_b_subtopics IN SHARE MODE"
                )
                registry=read_topic_registry(conn)
            else:
                registry=None
            validate_effective_row(p["block"],effective,registry)
        else:
            # For gold, the label must be a valid domain label, but gold
            # must NEVER change the effective content_b_* view.
            unique=(p["gold_version"],p["sample_key"],p["block"],
                    p["target"],p["index"],p["field"])
            for _,reviewer,event in events:
                old=event.payload
                if (old["kind"]=="gold_annotation" and reviewer==reviewer_id
                    and (old["gold_version"],old["sample_key"],old["block"],
                         old["target"],old["index"],old["field"])==unique):
                    _deny("gold_reviewer_duplicate_in_same_frozen_version")
        try:
            authorized=verify(
                project_id,run_id,reviewer_id,decision,base_row,
                effective,tuple(x[2] for x in events)
            )
        except Exception:
            _deny("review_independent_admission_exception")
        if authorized is not True:
            _deny("review_independent_admission_denied")
        conn.execute(
            """INSERT INTO group_b_review_events(
                 project_id,run_id,revision,reviewer_id,decision_sha256,decision)
               VALUES(%s,%s::uuid,%s,%s,%s,%s::jsonb)""",
            (project_id,run_id,current+1,reviewer_id,
             decision.decision_sha256,
             json.dumps(p,sort_keys=True,ensure_ascii=False,allow_nan=False)),
        )
        if p["kind"]=="correction":
            # Both review revision and source invalidation/outbox must either
            # COMMIT together or ROLLBACK. No SQL005 row is rewritten.
            from ..integration.invalidation import invalidate_human_correction
            invalidate_human_correction(
                conn,project_id=project_id,run_id=run_id,
                decision_sha256=decision.decision_sha256,
            )
        return ReviewAppendResult(current+1,decision.decision_sha256,False)


def read_effective_labels(conn,*,project_id:str,run_id:str,admission_verifier):
    """Verified, project-scoped overlay; the baseline SQL005 stays immutable.

    A corrected run is NOT automatically qualified downstream. An explicit
    next-version refresh and Analytics invalidation are required by GB24/25.
    """
    if (type(project_id) is not str or not project_id or
        type(run_id) is not str or not run_id):
        _deny("review_read_scope_invalid")
    verify=getattr(admission_verifier,"verify_human_review_effective",None)
    if not callable(verify):
        _deny("review_effective_current_authorization_missing")
    with conn.transaction():
        info=_load_run(conn,project_id,run_id,lock=False)
        events=_latest_events(conn,project_id,run_id)
        sha=events[-1][2].decision_sha256 if events else None
        try:
            granted=verify(project_id,run_id,info["result_analysis_id"],sha)
        except Exception:
            _deny("review_effective_authorization_failed")
        if granted is not True:
            _deny("review_effective_authorization_failed")
        effective={}
        revisions={}
        for revision,reviewer,event in events:
            p=event.payload
            if (p["analysis_id"]!=info["result_analysis_id"] or
                p["analysis_key"]!=info["analysis_key"] or
                p["source_evidence_manifest_sha256"]!=info["method_details"].get(
                    "group_b_media_evidence_manifest_sha256")):
                _deny("review_effective_history_wrong_source")
            if p["kind"]!="correction":
                continue
            key=(p["block"],p["target"],p["index"])
            if key not in effective:
                _,effective[key]=_effective_row(
                    conn,info["result_analysis_id"],p,()
                )
            effective[key][p["field"]]=p["value"]
            revisions[key]=revision
        if any(k[0]=="B8" for k in effective):
            conn.execute(
                "LOCK TABLE content_b_categories, content_b_topics, "
                "content_b_subtopics IN SHARE MODE"
            )
            registry=read_topic_registry(conn)
        else:
            registry=None
        for (block,_,_),row in effective.items():
            validate_effective_row(block,row,registry)
        corrections=sum(e.payload["kind"]=="correction" for _,_,e in events)
        return {
            "analysis_id":info["result_analysis_id"],
            "analysis_key":info["analysis_key"],
            "review_revision":len(events),
            "review_sha256":sha,
            "effective_rows":effective,
            "last_revisions":revisions,
            "human_corrected":corrections>0,
            # A review does not magically repair signed source provenance.
            "qualified_for_downstream":False if corrections else (
                info["analysis_state"]=="completed" and
                info["run_state"]=="completed" and
                info["needs_review"] is False
            ),
        }
