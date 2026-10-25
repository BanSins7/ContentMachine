"""B Stage13 B9: publish ONLY human-approved immutable knowledge, then C preflight.

The resulting snapshot never auto-starts Taxonomy Builder, pays providers or
reuses B3/B8 budget consent. C has a separate read-only, cost-unapproved
handoff; its eventual paid run must go through independent C authorization.
"""
from __future__ import annotations

from psycopg.types.json import Jsonb

from intelligence.project_sources.context_builder import sha as canonical_sha
from .contracts import VideoAnalysisPin
from .human_review import FactHumanReviewRepository, _reviewer
from .intake import IntakeError, PostgresAResolver, _project, _uuid
from .repository import KnowledgeInputRepository

SCHEMA = "knowledge-snapshot/v1"
ENGINE = "b9-immutable/1.0.0"
C_MODE = "knowledge_snapshot_v1"
C_ENGINE = "c-knowledge-handoff/1.0.0"
C_ALLOWED_KINDS = frozenset(("function","problem","audience","product_type",
                            "constraint","metric","positioning","external_claim"))


def _fail(reason):
    raise IntakeError(reason)


def build_snapshot(*, project_id, submission_id, submission,
                   context, bundle, quality_report, a_video_refs):
    """Pure append-only snapshot candidate built from replayed B8/A context.

    No citation is promoted from blocked, rejected or original unreviewed facts.
    Exact source trust and A video export pins are retained, not interpreted.
    """
    pid = _project(project_id)
    sid = _uuid(submission_id, "b9_submission_id_invalid")
    if (not all(isinstance(x,dict) for x in (
            submission,context,bundle,quality_report))
            or not isinstance(a_video_refs,list)
            or submission.get("schema_version")!="knowledge-human-review/v1"
            or submission.get("project_id")!=pid
            or submission.get("publication_eligible") is not True
            or submission.get("publication_performed") is not False
            or submission.get("review_status")!="human_submitted"
            or quality_report.get("decision") not in ("ready_for_review","partial_ready")
            or quality_report.get("handoff_allowed") is not True
            or quality_report.get("publishing_allowed") is not False
            or quality_report.get("gate_report_sha256") is not None
            or quality_report.get("run_id")!=submission.get("run_id")
            or quality_report.get("batch_id")!=submission.get("batch_id")
            or context.get("context_id")!=bundle.get("context_id")
            or context.get("context_sha256")!=bundle.get("context_sha256")
            or bundle.get("project_id")!=pid):
        _fail("b9_upstream_contract_invalid")
    approved=submission.get("approved_groups")
    rejected=submission.get("rejected_groups")
    conflicts=submission.get("conflict_resolutions")
    if (not isinstance(approved,list) or not approved
            or not isinstance(rejected,list) or not isinstance(conflicts,list)
            or len(approved)!=submission.get("approved_count")
            or len(rejected)!=submission.get("rejected_count")
            or len(conflicts)!=submission.get("conflict_count")
            or submission.get("gate_id")!=quality_report.get("gate_id",submission.get("gate_id"))
            or submission.get("gate_report_sha256")!=canonical_sha(quality_report)
            or quality_report.get("counts",{}).get("unique_groups")!=len(approved)+len(rejected)):
        _fail("b9_decision_coverage_invalid")
    all_group_ids = [g.get("group_id") for g in approved + rejected]
    if (len(set(all_group_ids))!=len(all_group_ids)
            or not all(isinstance(x,str) and len(x)==64 for x in all_group_ids)):
        _fail("b9_duplicate_review_group")
    rejected_ids={g["group_id"] for g in rejected}
    approved_ids={g["group_id"] for g in approved}
    for c in conflicts:
        if not isinstance(c,dict) or c.get("decision") in (None,"defer"):
            _fail("b9_unresolved_conflicts")
        left,right=c.get("group_a"),c.get("group_b")
        if left not in approved_ids|rejected_ids or right not in approved_ids|rejected_ids:
            _fail("b9_conflict_unknown_group")
        if (c["decision"]=="select_a" and not (left in approved_ids and right in rejected_ids)
                or c["decision"]=="select_b" and not (right in approved_ids and left in rejected_ids)
                or c["decision"]=="reject_both" and not (left in rejected_ids and right in rejected_ids)):
            _fail("b9_conflict_inconsistent")
    facts=[]
    global_proposals=set()
    for group in sorted(approved,key=lambda g:g["group_id"]):
        proposals=group.get("proposal_ids")
        citations=group.get("citations")
        if (group.get("kind") not in C_ALLOWED_KINDS
                or not isinstance(group.get("reviewed_text"),str)
                or not group["reviewed_text"].strip()
                or len(group["reviewed_text"])>800
                or not group.get("affirmed_source_support_by_reviewer")
                or not group.get("affirmed_subject_scope_by_reviewer")
                or not isinstance(group.get("review_event_sha256"),str)
                or len(group["review_event_sha256"])!=64
                or not isinstance(citations,list) or not citations
                or not isinstance(proposals,list) or len(citations)!=len(proposals)):
            _fail("b9_approved_fact_invalid")
        if sorted(set(proposals))!=sorted(proposals):
            _fail("b9_duplicate_proposal")
        citation_by_id={}
        for cite in citations:
            if not isinstance(cite,dict):
                _fail("b9_bad_citation")
            id0=cite.get("proposal_id")
            provenance=cite.get("original_provenance")
            if (not isinstance(id0,str) or len(id0)!=64
                    or id0 in citation_by_id or id0 in global_proposals
                    or not isinstance(provenance,dict)
                    or provenance.get("source_text_match_only") is not True
                    or not isinstance(provenance.get("quote_sha256"),str)
                    or len(provenance["quote_sha256"])!=64
                    or not isinstance(provenance.get("locator_sha256"),str)
                    or len(provenance["locator_sha256"])!=64
                    or not isinstance(provenance.get("trust_label"),str)
                    or not isinstance(cite.get("source_quote"),str)
                    or not cite["source_quote"]
                    or not isinstance(cite.get("b5_receipt_sha256"),str)
                    or len(cite["b5_receipt_sha256"])!=64):
                _fail("b9_citation_provenance_invalid")
            from hashlib import sha256
            if sha256(cite["source_quote"].encode("utf-8")).hexdigest()!=provenance["quote_sha256"]:
                _fail("b9_quote_sha_mismatch")
            if (provenance.get("source_role")!=group.get("source_role")
                    or provenance.get("subject_scope")!=group.get("subject_scope")
                    or provenance.get("subject_id")!=group.get("subject_id")):
                _fail("b9_subject_provenance_mismatch")
            citation_by_id[id0]=cite
            global_proposals.add(id0)
        if set(citation_by_id)!=set(proposals):
            _fail("b9_missing_group_citation")
        ordered=[citation_by_id[p] for p in sorted(proposals)]
        facts.append({
            "fact_id":canonical_sha({
                "schema_version":SCHEMA,"submission_sha256":canonical_sha(submission),
                "group_id":group["group_id"],"review_event_sha256":group["review_event_sha256"],
            }),
            "group_id":group["group_id"],"kind":group["kind"],
            "reviewed_text":group["reviewed_text"],
            "original_model_text":group["original_text"],
            "human_edited":group["human_edited"],
            "review_event_sha256":group["review_event_sha256"],
            "source_role":group["source_role"],
            "subject_scope":group["subject_scope"],
            "subject_id":group["subject_id"],
            "proposal_ids":sorted(proposals),"citations":ordered,
            "review_status":"human_approved",
            "source_quote_grounding_only":True,
        })
    expected_video=bundle.get("video_analysis_refs")
    if not isinstance(expected_video,list) or len(expected_video)>1024:
        _fail("b9_video_bundle_invalid")
    got={(x.get("source_id"),x.get("source_version_id"),x.get("output_id")):x
         for x in a_video_refs if isinstance(x,dict)}
    videos=[]
    if len(got)!=len(a_video_refs):
        _fail("b9_video_original_duplicate")
    for pin in expected_video:
        if not isinstance(pin,dict):
            _fail("b9_video_bundle_invalid")
        key=(pin.get("source_id"),pin.get("source_version_id"),pin.get("output_id"))
        from_a=got.get(key)
        if (not isinstance(from_a,dict) or
                pin.get("schema_version")!="video-intelligence/v1"
                or from_a.get("schema_version")!=pin["schema_version"]
                or from_a.get("output_sha256")!=pin.get("output_sha256")):
            _fail("b9_video_a_pin_mismatch")
        videos.append({
            "source_id":key[0],"source_version_id":key[1],
            "output_id":key[2],"output_sha256":pin["output_sha256"],
            "schema_version":pin["schema_version"],
        })
    videos.sort(key=lambda v:(v["source_id"],v["source_version_id"],v["output_id"]))
    if quality_report.get("counts",{}).get("video_refs_available")!=len(videos) or (
            quality_report["counts"].get("video_refs_missing")!=0):
        _fail("b9_video_gate_mismatch")
    snap={
        "schema_version":SCHEMA,"builder_version":ENGINE,
        "project_id":pid,
        "session_id":submission["session_id"],
        "submission_id":sid,"submission_sha256":canonical_sha(submission),
        "reviewer_id":submission["reviewer_id"],
        "human_review_status":"human_submitted",
        "run_id":submission["run_id"],"batch_id":submission["batch_id"],
        "gate_id":submission["gate_id"],
        "gate_report_sha256":submission["gate_report_sha256"],
        "context_id":context["context_id"],
        "context_sha256":context["context_sha256"],
        "profile_revision":context["profile_revision"],
        "profile_hash":context["profile_hash"],
        "source_bundle_sha256":canonical_sha(bundle),
        "facts":facts,"approved_count":len(facts),
        "rejected_count":len(rejected),"rejected_group_ids":sorted(rejected_ids),
        "conflict_resolutions":conflicts,
        "video_analysis_refs":videos,
        "website_source_truth_independently_verified":False,
        "objective_truth_guaranteed":False,
        "paid_c_authorized":False,"c_run_started":False,
        "mimo_calls":0,"c_provider_calls":0,
    }
    return {"snapshot":snap,"snapshot_sha256":canonical_sha(snap)}


def build_c_handoff(snapshot, snapshot_id, snapshot_sha256):
    """Safe C taxonomy-generation input contract; never route to fact_extraction."""
    sid=_uuid(snapshot_id,"b9_snapshot_id_invalid")
    if (not isinstance(snapshot,dict) or snapshot.get("schema_version")!=SCHEMA
            or canonical_sha(snapshot)!=snapshot_sha256
            or snapshot.get("approved_count",0)<1
            or snapshot.get("paid_c_authorized") is not False
            or snapshot.get("c_run_started") is not False):
        _fail("c_snapshot_pin_invalid")
    facts=snapshot["facts"]
    if any(x.get("review_status")!="human_approved" for x in facts):
        _fail("c_unapproved_fact_in_snapshot")
    rows=[{
        "fact_id":x["fact_id"],"kind":x["kind"],
        "text":x["reviewed_text"],"source_role":x["source_role"],
        "subject_scope":x["subject_scope"],"subject_id":x["subject_id"],
        "citation_provenance_sha256":canonical_sha(x["citations"]),
    } for x in facts]
    return {
        "schema_version":"knowledge-c-handoff/v1","input_mode":C_MODE,
        "adapter_version":C_ENGINE,"snapshot_id":sid,
        "snapshot_sha256":snapshot_sha256,
        "project_id":snapshot["project_id"],
        "context_sha256":snapshot["context_sha256"],
        "facts":rows,"fact_count":len(rows),
        "video_analysis_refs":snapshot["video_analysis_refs"],
        "video_to_mimo":False,
        "allowed_start_role":"taxonomy_generation",
        "forbidden_stage":"fact_extraction",
        "allowed_downstream_roles":["taxonomy_generation","taxonomy_critic"],
        "independent_c_quote_required":True,
        "independent_c_consent_required":True,
        "may_reuse_b3_spending_consent":False,
        "execution_status":"not_started",
        "paid_provider_calls":0,
    }


class KnowledgeSnapshotRepository:
    """Explicit publication after B8 submission; immutable historical replay."""

    def __init__(self,connection_factory=None, *, verify_local_files=True):
        self.review=FactHumanReviewRepository(
            connection_factory,verify_local_files=verify_local_files)
        self.connection_factory=self.review.connection_factory
        self.verify_local_files=verify_local_files

    def _compute(self,pid,session_id,reviewer_id):
        audit=self.review.materialize_submission(pid,session_id,reviewer_id)
        sub=audit["submission"]
        gate=self.review.gate.materialize(pid,sub["run_id"])
        if (gate["quality_gate_id"]!=sub["gate_id"]
                or gate["report_sha256"]!=sub["gate_report_sha256"]
                or gate["report"]["handoff_allowed"] is not True
                or sub["publication_eligible"] is not True
                or sub["approved_count"]<1):
            _fail("b9_review_gate_not_publishable")
        req=self.review.gate.requests.materialize(pid,sub["batch_id"])
        source=KnowledgeInputRepository(
            self.connection_factory,verify_local_files=self.verify_local_files).get(
                pid,req["blueprint"]["bundle_id"])
        bundle=source["bundle"]
        resolver=PostgresAResolver(
            self.connection_factory,require_current=False,
            verify_local_files=self.verify_local_files)
        context=resolver.resolve_context(pid,bundle["context_id"])
        if (gate["report"]["source_bundle_sha256"]!=source["bundle_sha256"]
                or gate["report"]["run_id"]!=sub["run_id"]
                or context["context_sha256"]!=bundle["context_sha256"]):
            _fail("b9_source_context_mismatch")
        validated=build_snapshot(
            project_id=pid,submission_id=audit["submission_id"],
            submission=sub,context=context,bundle=bundle,
            quality_report=gate["report"],
            a_video_refs=[x for x in context["context"]["analysis_refs"]
                          if x["schema_version"]=="video-intelligence/v1"],
        )
        return validated,sub,gate,bundle

    def publish(self,project_id,session_id,reviewer_id):
        """Explicit B9 publish, not a job triggered automatically by B8."""
        pid=_project(project_id)
        sid=_uuid(session_id,"b9_session_id_invalid")
        actor=_reviewer(reviewer_id)
        calc,sub,gate,bundle=self._compute(pid,sid,actor)
        snap=calc["snapshot"]
        with self.connection_factory() as conn:
            # Serialized replays even on concurrent B9 publication attempts.
            row=conn.execute(
                """SELECT id FROM knowledge_fact_review_sessions
                   WHERE project_id=%s AND id=%s AND status='submitted' FOR UPDATE""",
                (pid,sid),
            ).fetchone()
            if row is None:
                _fail("b9_session_not_submitted")
            prior=conn.execute(
                """SELECT id,snapshot_sha256,snapshot
                   FROM knowledge_approved_snapshots
                   WHERE project_id=%s AND session_id=%s""",
                (pid,sid),
            ).fetchone()
            if prior:
                if prior[1]!=calc["snapshot_sha256"] or prior[2]!=snap:
                    _fail("b9_immutable_snapshot_replay_mismatch")
                return {"snapshot_id":str(prior[0]),"created":False,
                        "snapshot_sha256":prior[1],"approved_count":snap["approved_count"],
                        "provider_calls":0}
            ident=conn.execute(
                """INSERT INTO knowledge_approved_snapshots(
                   project_id,session_id,submission_id,gate_id,context_id,run_id,
                   submission_sha256,context_sha256,snapshot_sha256,
                   approved_count,video_ref_count,snapshot)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (pid,sid,calc["snapshot"]["submission_id"],
                 sub["gate_id"],bundle["context_id"],sub["run_id"],
                 calc["snapshot"]["submission_sha256"],bundle["context_sha256"],
                 calc["snapshot_sha256"],snap["approved_count"],
                 len(snap["video_analysis_refs"]),Jsonb(snap)),
            ).fetchone()[0]
        return {"snapshot_id":str(ident),"created":True,
                "snapshot_sha256":calc["snapshot_sha256"],
                "approved_count":snap["approved_count"],"provider_calls":0}

    def materialize(self,project_id,session_id,reviewer_id):
        """Trust only original A→B8 replay and byte-exact persisted snapshot."""
        pid=_project(project_id);sid=_uuid(session_id,"b9_session_id_invalid")
        actor=_reviewer(reviewer_id)
        calc,_,_,_=self._compute(pid,sid,actor)
        with self.connection_factory() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row=conn.execute(
                """SELECT id,snapshot_sha256,snapshot,approved_count,video_ref_count
                   FROM knowledge_approved_snapshots
                   WHERE project_id=%s AND session_id=%s""",
                (pid,sid),
            ).fetchone()
            if row is None:
                _fail("b9_snapshot_not_found")
            if (row[1]!=calc["snapshot_sha256"] or row[2]!=calc["snapshot"]
                    or row[3]!=calc["snapshot"]["approved_count"]
                    or row[4]!=len(calc["snapshot"]["video_analysis_refs"])):
                _fail("b9_snapshot_integrity")
        return {"snapshot_id":str(row[0]),**calc,"provider_calls":0}

    def prepare_c_handoff(self,project_id,session_id,reviewer_id):
        """Commit non-paid immutable C input. Does NOT create quote/consent/run."""
        pid=_project(project_id);sid=_uuid(session_id,"b9_session_id_invalid")
        snap=self.materialize(pid,sid,reviewer_id)
        payload=build_c_handoff(snap["snapshot"],snap["snapshot_id"],
                                 snap["snapshot_sha256"])
        digest=canonical_sha(payload)
        with self.connection_factory() as conn:
            conn.execute(
                """SELECT id FROM knowledge_approved_snapshots
                   WHERE project_id=%s AND id=%s FOR UPDATE""",
                (pid,snap["snapshot_id"]),
            )
            saved=conn.execute(
                """SELECT id,handoff_sha256,handoff FROM knowledge_c_handoffs
                   WHERE project_id=%s AND snapshot_id=%s""",
                (pid,snap["snapshot_id"]),
            ).fetchone()
            if saved:
                if saved[1]!=digest or saved[2]!=payload:
                    _fail("c_handoff_replay_integrity")
                return {"handoff_id":str(saved[0]),"created":False,
                        "handoff_sha256":digest,"provider_calls":0}
            row=conn.execute(
                """INSERT INTO knowledge_c_handoffs(
                    project_id,snapshot_id,snapshot_sha256,handoff_sha256,handoff)
                   VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                (pid,snap["snapshot_id"],snap["snapshot_sha256"],digest,Jsonb(payload)),
            ).fetchone()[0]
        return {"handoff_id":str(row),"created":True,
                "handoff_sha256":digest,"provider_calls":0}

    def materialize_c_handoff(self,project_id,session_id,reviewer_id):
        pid=_project(project_id);sid=_uuid(session_id,"b9_session_id_invalid")
        snap=self.materialize(pid,sid,reviewer_id)
        expected=build_c_handoff(snap["snapshot"],snap["snapshot_id"],
                                 snap["snapshot_sha256"])
        digest=canonical_sha(expected)
        with self.connection_factory() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row=conn.execute(
                """SELECT id,handoff_sha256,handoff FROM knowledge_c_handoffs
                   WHERE project_id=%s AND snapshot_id=%s""",
                (pid,snap["snapshot_id"]),
            ).fetchone()
            if row is None or row[1]!=digest or row[2]!=expected:
                _fail("c_handoff_not_found_or_changed")
        return {"handoff_id":str(row[0]),"handoff_sha256":digest,
                "handoff":expected,"provider_calls":0}
