"""B Stage11 Quality Gate: explainable readiness for B8 Human Knowledge Review.

Pure deterministic reconciliation of fully verified B1/B2/B3/B4/B5/B6 lineage.
No LLM, embeddings, external fetch, spending, auto-approval, or publishing.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation

from psycopg.types.json import Jsonb

from intelligence.project_sources.context_builder import sha as canonical_sha
from .deduplication import FactDeduplicationRepository
from .fact_execution import FactExecutionRepository
from .intake import IntakeError, _project, _uuid

VERSION = "knowledge-quality-gate/v1"
ENGINE = "b7-deterministic/1.0.0"
NON_INDEPENDENT_TRUST = "model_reported_unverified"


def _fail(code):
    raise IntakeError(code)


def _count(number):
    return type(number) is int and number >= 0


def _dollar(number):
    try:
        value = Decimal(str(number))
    except (InvalidOperation, TypeError, ValueError):
        _fail("b7_bad_cost")
    if not value.is_finite() or value < 0:
        _fail("b7_bad_cost")
    return value


def evaluate_quality(*, run_id, batch_id, blueprint, source_packs, bundle,
                     execution, normalization, grounding, dedup, multi_plan=None):
    """Pure B7: corrupt prior stages fail closed, domain shortcomings are findings.

    Treat handoff to human review separately from *any* approval of claims.
    The input is a fully replayed set of server-owned B1--B6 contracts, not
    user-supplied source text or an untrusted UI summary.
    """
    rid = _uuid(run_id, "b7_run_id_invalid")
    bid = _uuid(batch_id, "b7_batch_id_invalid")
    if not all(isinstance(x,dict) for x in (
        blueprint,bundle,execution,normalization,grounding,dedup
    )) or not isinstance(source_packs,list):
        _fail("b7_input_contract_invalid")
    packs = blueprint.get("parts")
    attempts = execution.get("attempts")
    norm_report, b5_report, b6_report = (
        normalization.get("report"), grounding.get("report"), dedup.get("report"))
    if not all(isinstance(x,dict) for x in (norm_report,b5_report,b6_report)):
        _fail("b7_missing_stage_report")
    members,groups,conflicts = (
        dedup.get("members"),dedup.get("groups"),dedup.get("conflicts"))
    if not all(isinstance(x,list) for x in (members,groups,conflicts)):
        _fail("b7_missing_stage_members")
    if (blueprint.get("schema_version") != "facts-v3-request-batch/v1"
            or blueprint.get("bundle_id") is None
            or blueprint.get("request_count") != len(packs or [])
            or not packs or len(packs) > 256
            or len(source_packs) != len(packs)
            or not isinstance(attempts,list) or len(attempts) != len(packs)
            or execution.get("run_id") != rid or execution.get("batch_id") != bid
            or execution.get("status") != "ready_for_normalization"
            or execution.get("next_pack_index") != len(packs)
            or _dollar(execution.get("reserved_usd")) != 0
            or execution.get("approved") is not False):
        _fail("b7_incomplete_or_untrusted_execution")
    if blueprint.get("pack_kind") not in ("full","multi"):
        _fail("b7_pack_kind_invalid")
    if (bundle.get("schema_version") != "knowledge-input-bundle/v1"
            or bundle.get("project_id") is None
            or bundle.get("text_coverage_complete") not in (True,False)
            or not isinstance(bundle.get("fragments"),list)
            or not isinstance(bundle.get("excluded_sources"),list)
            or not isinstance(bundle.get("uncovered_required"),list)
            or blueprint["bundle_id"] is None
            or len({f["fragment_id"] for f in bundle["fragments"]})
               != len(bundle["fragments"])):
        _fail("b7_bundle_contract_invalid")
    bundle_sha = canonical_sha(bundle)
    for stage, sha, tag in (
        (norm_report, normalization.get("report_sha256"),"b4"),
        (b5_report, grounding.get("report_sha256"),"b5"),
        (b6_report, dedup.get("report_sha256"),"b6"),
    ):
        if (stage.get("run_id") != rid or stage.get("batch_id") != bid
                or not isinstance(sha,str) or canonical_sha(stage) != sha):
            _fail("b7_"+tag+"_report_integrity")
    if (b5_report.get("normalization_sha256") != normalization["report_sha256"]
            or b6_report.get("b4_report_sha256") != normalization["report_sha256"]
            or b6_report.get("b5_report_sha256") != grounding["report_sha256"]):
        _fail("b7_stage_sha_chain_mismatch")
    if (not _count(norm_report.get("proposal_count"))
            or not _count(norm_report.get("quarantined_count"))
            or not _count(norm_report.get("normalized_count"))
            or norm_report["proposal_count"] !=
            norm_report["normalized_count"] + norm_report["quarantined_count"]
            or b5_report.get("total_count") != norm_report["proposal_count"]
            or b5_report.get("matched_count") != b6_report.get("eligible_count")
            or b5_report.get("rejected_count") + b5_report.get("skipped_count")
               != b6_report.get("blocked_count")
            or b5_report.get("skipped_count") != norm_report["quarantined_count"]
            or b6_report.get("proposal_count") != len(members)
            or b6_report.get("group_count") != len(groups)
            or b6_report.get("conflict_candidate_count") != len(conflicts)
            or b6_report.get("eligible_count") + b6_report.get("blocked_count") != len(members)
            or b6_report.get("eligible_count") - b6_report.get("group_count")
               != b6_report.get("duplicate_count")):
        _fail("b7_stage_count_reconciliation")

    # The complete source inventory must still appear in exactly one Full pack
    # or be byte-exactly covered by the verified Multi-Pack planner.
    if not bundle["fragments"] or not isinstance(bundle.get("coverage"),dict):
        _fail("b7_source_inventory_missing")
    if (bundle["coverage"].get("fragments_indexed") != len(bundle["fragments"])
            or bundle["coverage"].get("uncovered_required") != len(bundle["uncovered_required"])
            or bundle["text_coverage_complete"] != (not bundle["uncovered_required"])):
        _fail("b7_source_inventory_count_mismatch")
    source_ids = {f["fragment_id"] for f in bundle["fragments"]}
    if blueprint["pack_kind"] == "full":
        if len(packs) != 1 or multi_plan is not None:
            _fail("b7_full_pack_mismatch")
    else:
        if not isinstance(multi_plan,dict) or (
            multi_plan.get("schema_version") != "evidence-multi-pack-plan/v1"
            or multi_plan.get("coverage",{}).get("every_fragment_exactly_once") is not True
            or multi_plan["coverage"].get("uncovered_required") != 0
            or multi_plan["coverage"].get("duplicate_characters") != 0
            or multi_plan["coverage"].get("fragment_count") != len(source_ids)
            or multi_plan["coverage"].get("covered_chars") !=
               sum(f["char_count"] for f in bundle["fragments"])
            or len(multi_plan.get("packs",[])) != len(packs)):
            _fail("b7_multi_pack_coverage_invalid")

    seen_chunks = set()
    coverage_ids = []
    audit_packs = []
    norm_pack = norm_report.get("packs")
    if not isinstance(norm_pack,list) or len(norm_pack)!=len(packs):
        _fail("b7_normalization_pack_count")
    sum_paid = Decimal(0)
    sum_candidates = 0
    limit_reached = []
    for idx,(part, source_pair, attempt, nreport) in enumerate(
        zip(packs,source_packs,attempts,norm_pack)
    ):
        if (not isinstance(part,dict) or not isinstance(source_pair,tuple)
                or len(source_pair)!=2 or not isinstance(source_pair[0],dict)
                or not isinstance(attempt,dict) or not isinstance(nreport,dict)):
            _fail("b7_pack_shape_invalid")
        manifest,_ = source_pair
        rows = manifest.get("entries") if blueprint["pack_kind"]=="full" else manifest.get("spans")
        if not isinstance(rows,list) or not rows:
            _fail("b7_pack_entries_invalid")
        if manifest.get("bundle_sha256") != bundle_sha:
            _fail("b7_bundle_pack_sha_mismatch")
        pack_chunks = []
        for entry in rows:
            if not isinstance(entry,dict):
                _fail("b7_source_chunk_invalid")
            source = entry.get("fragment_id")
            chunk = source if blueprint["pack_kind"]=="full" else entry.get("chunk_id")
            if (source not in source_ids or not isinstance(chunk,str)
                    or chunk in seen_chunks):
                _fail("b7_duplicate_or_unknown_chunk")
            seen_chunks.add(chunk)
            coverage_ids.append(source)
            pack_chunks.append(chunk)
        if (part.get("pack_index") != idx or
                part.get("pack_payload_sha256") != manifest.get("payload_sha256") or
                part.get("pack_manifest_sha256") != canonical_sha(manifest) or
                part.get("source_chunk_count") != len(pack_chunks) or
                attempt.get("pack_index") != idx or
                attempt.get("status") != "completed" or
                type(attempt.get("candidate_count")) is not int or
                not 0 <= attempt["candidate_count"] <= 80 or
                attempt.get("candidate_limit_reached") is not
                (attempt["candidate_count"] == 80) or
                attempt.get("usage") is None or
                attempt.get("charged_usd") is None or
                type(nreport.get("candidate_count")) is not int or
                nreport["candidate_count"] != attempt["candidate_count"] or
                nreport.get("pack_index") != idx or
                nreport.get("candidate_limit_reached") is not
                attempt["candidate_limit_reached"] or
                nreport.get("pending_grounding_count") +
                nreport.get("quarantined_count") != attempt["candidate_count"]):
            _fail("b7_pack_attempt_integrity")
        sum_paid += _dollar(attempt["charged_usd"])
        sum_candidates += attempt["candidate_count"]
        if attempt["candidate_limit_reached"]:
            limit_reached.append(idx)
        audit_packs.append({
            "pack_index":idx, "request_sha256":part["request_sha256"],
            "source_chunk_count":len(pack_chunks),
            "candidate_count":attempt["candidate_count"],
            "quarantined_count":nreport["quarantined_count"],
            "candidate_limit_reached":attempt["candidate_limit_reached"],
            "charged_usd":str(_dollar(attempt["charged_usd"])),
        })
    if (blueprint["pack_kind"]=="full" and
            (set(coverage_ids) != source_ids or len(coverage_ids)!=len(source_ids))):
        _fail("b7_full_pack_coverage_invalid")
    if blueprint["pack_kind"]=="multi" and set(coverage_ids)!=source_ids:
        _fail("b7_multi_pack_fragment_missing")
    if (sum_candidates != norm_report["proposal_count"]
            or sum_paid != _dollar(execution.get("spent_usd"))
            or _dollar(execution.get("authorized_usd")) < sum_paid):
        # Provider overcharge is not silently ignored; it requires a financial
        # reconciliation, even if a manually mutated execution says "ready".
        _fail("b7_paid_accounting_mismatch")

    member_by_group=Counter()
    blocked_status=Counter()
    independent_trust=set()
    ambiguous_subjects=0
    for m in members:
        if not isinstance(m,dict) or m.get("status") not in (
            "grouped","blocked_b5","skipped_b4"
        ):
            _fail("b7_member_status_invalid")
        if m["status"]=="grouped":
            prov=m.get("provenance")
            if not isinstance(prov,dict) or prov.get("source_text_match_only") is not True:
                _fail("b7_missing_member_provenance")
            if (not isinstance(prov.get("quote_sha256"),str)
                    or not isinstance(prov.get("locator_sha256"),str)):
                _fail("b7_missing_source_pin")
            if m.get("group_id") is None:
                _fail("b7_missing_member_group")
            member_by_group[m["group_id"]]+=1
            if prov.get("trust_label")==NON_INDEPENDENT_TRUST:
                independent_trust.add(m["group_id"])
            if (prov.get("subject_scope")=="other"
                    or (prov.get("subject_scope")=="competitor"
                        and not prov.get("subject_id"))):
                ambiguous_subjects += 1
        else:
            if m.get("provenance") is not None or m.get("group_id") is not None:
                _fail("b7_blocked_member_promoted")
            blocked_status[m["status"]]+=1
    if (sum(member_by_group.values()) != b6_report["eligible_count"] or
            blocked_status["skipped_b4"]!=norm_report["quarantined_count"] or
            blocked_status["blocked_b5"]!=b5_report["rejected_count"] or
            set(member_by_group) != {g["group_id"] for g in groups} or
            any(member_by_group[g["group_id"]]!=g["member_count"] for g in groups)):
        _fail("b7_group_membership_integrity")
    group_ids=set(member_by_group)
    if any(c["group_a"] not in group_ids or c["group_b"] not in group_ids
           or c.get("review_state")!="needs_review" for c in conflicts):
        _fail("b7_conflict_unresolved_identity")

    allow_partial = (source_packs[0][0].get("allow_partial") if
                     blueprint["pack_kind"]=="full" else multi_plan.get("allow_partial"))
    if type(allow_partial) is not bool:
        _fail("b7_partial_ack_invalid")
    findings = []
    def emit(severity,code,count,detail):
        if not isinstance(count,int) or count < 1:
            _fail("b7_findings_count_invalid")
        findings.append({
            "severity":severity,"code":code,"count":count,"detail":detail,
        })

    if bundle["readiness"] not in ("ready","approved_partial"):
        emit("blocker","source_bundle_not_ready",1,"Original approved B1 text inventory is not ready")
    if bundle["uncovered_required"]:
        emit("blocker","required_source_uncovered",len(bundle["uncovered_required"]),
             "B1 required source fragments remain uncovered")
    if bundle["readiness"]=="approved_partial":
        if not allow_partial:
            emit("blocker","partial_context_not_acknowledged",1,"Partial A context lacks explicit B2 acknowledgement")
        emit("warning","partial_approved_context",len(bundle["excluded_sources"]),
             "Some A sources were excluded by the original approved context")
    if len(groups)==0:
        emit("blocker","no_grounded_fact_groups",1,"No source-quote-matched claim is available for human review")
    if limit_reached:
        emit("blocker","candidate_limit_reached",len(limit_reached),
             "MiMo reached its 80-candidate per-pack cap; extraction may be incomplete")
    if norm_report["quarantined_count"]:
        emit("warning","b4_quarantine",norm_report["quarantined_count"],
             "Invalid candidate drafts remain excluded and require coverage review")
    if b5_report["rejected_count"]:
        emit("warning","b5_rejections",b5_report["rejected_count"],
             "Independent A-source quote checks rejected some claims")
    if conflicts:
        emit("warning","b6_possible_conflicts",len(conflicts),
             "Lexical conflict candidates need explicit human adjudication")
    if independent_trust:
        emit("warning","model_reported_web_source",len(independent_trust),
             "Stored website extraction text is not independently verified")
    if ambiguous_subjects:
        emit("warning","ambiguous_subject",ambiguous_subjects,
             "Subject ownership must be checked by the reviewer")
    if not bundle["text_coverage_complete"]:
        # Always block even when source inventory is incomplete but not labeled.
        if not bundle["uncovered_required"]:
            _fail("b7_unexplained_source_coverage")
    if len(findings)>256:
        _fail("b7_findings_overflow")
    blockers = sum(f["severity"]=="blocker" for f in findings)
    warnings = sum(f["severity"]=="warning" for f in findings)
    decision = ("blocked" if blockers else
                "ready_with_warnings" if warnings else
                "ready_for_human_review")
    handoff = blockers==0
    fingerprint = canonical_sha({
        "engine":ENGINE,"run_id":rid,"batch_id":bid,
        "blueprint_sha256":canonical_sha(blueprint),"bundle_sha256":bundle_sha,
        "execution_sha256":canonical_sha(execution),
        "b4_report_sha256":normalization["report_sha256"],
        "b5_report_sha256":grounding["report_sha256"],
        "b6_report_sha256":dedup["report_sha256"],
        "b6_members_sha256":canonical_sha(members),
        "b6_groups_sha256":canonical_sha(groups),
        "b6_conflicts_sha256":canonical_sha(conflicts),
        "pack_ledger":audit_packs,
    })
    report={
        "schema_version":VERSION,"gate_version":ENGINE,
        "run_id":rid,"batch_id":bid,
        "input_sha256":fingerprint,
        "decision":decision,"handoff_allowed":handoff,
        "findings":findings,
        "finding_count":len(findings),"blocker_count":blockers,
        "warning_count":warnings,
        "counts":{
            "request_packs":len(packs),"completed_paid_packs":len(attempts),
            "source_fragments":len(source_ids),"source_chunks":len(seen_chunks),
            "candidate_count":sum_candidates,
            "b4_quarantined":norm_report["quarantined_count"],
            "b5_matched":b5_report["matched_count"],
            "b5_rejected":b5_report["rejected_count"],
            "unique_groups":len(groups),
            "exact_duplicates":b6_report["duplicate_count"],
            "potential_conflicts":len(conflicts),
            "subject_ambiguous":ambiguous_subjects,
            "unverified_web_groups":len(independent_trust),
        },
        "pack_audit":audit_packs,
        "b4_report_sha256":normalization["report_sha256"],
        "b5_report_sha256":grounding["report_sha256"],
        "b6_report_sha256":dedup["report_sha256"],
        "source_bundle_sha256":bundle_sha,
        "approved_facts":0,"human_review_status":"not_started",
        "semantic_entailment_checked":False,
        "objective_truth_confirmed":False,
        "source_quote_grounding_only":True,
        "publishing_allowed":False,"provider_calls":0,
    }
    return {"report":report,"report_sha256":canonical_sha(report),
            "input_sha256":fingerprint,"provider_calls":0}


def _existing(conn,pid,dedup_id,computed):
    row=conn.execute(
        """SELECT id,report,report_sha256,input_sha256,decision,
                  handoff_allowed,finding_count,blocker_count,warning_count
             FROM knowledge_fact_quality_gates
            WHERE project_id=%s AND dedup_id=%s""",
        (pid,dedup_id),
    ).fetchone()
    if row is None:
        return None
    report=computed["report"]
    if (row[1]!=report or row[2]!=computed["report_sha256"]
            or canonical_sha(row[1])!=row[2]
            or row[3]!=computed["input_sha256"]
            or row[4]!=report["decision"]
            or row[5]!=report["handoff_allowed"]
            or (row[6],row[7],row[8])!=(
                report["finding_count"],report["blocker_count"],report["warning_count"])):
        _fail("b7_immutable_replay_mismatch")
    return str(row[0])


class FactQualityGateRepository:
    """Persist-or-replay B7 decision; never starts B8 or publishes knowledge."""

    def __init__(self, connection_factory=None, *, verify_local_files=True):
        self.dedup=FactDeduplicationRepository(
            connection_factory,verify_local_files=verify_local_files)
        self.connection_factory=self.dedup.connection_factory
        self.requests=self.dedup.grounding.normalization.requests
        self.execution=FactExecutionRepository(
            connection_factory, verify_local_files=verify_local_files)

    def _material(self,pid,rid):
        b6=self.dedup.materialize(pid,rid)
        b5=self.dedup.grounding.materialize(pid,rid)
        b4=self.dedup.grounding.normalization.materialize(pid,rid)
        batch_id=b6["report"]["batch_id"]
        req=self.requests.materialize(pid,batch_id)
        b1=self.dedup.grounding.normalization.requests.bundles if hasattr(
            self.dedup.grounding.normalization.requests,"bundles") else None
        from .repository import KnowledgeInputRepository
        original=KnowledgeInputRepository(
            self.connection_factory,
            verify_local_files=self.requests.bundles.resolver.verify_local_files
            if hasattr(self.requests,"bundles") else True,
        ).get(pid,req["blueprint"]["bundle_id"])
        if (b6["report"]["b4_report_sha256"]!=b4["report_sha256"]
                or b6["report"]["b5_report_sha256"]!=b5["report_sha256"]
                or b4["report"]["batch_id"]!=batch_id
                or b5["report"]["batch_id"]!=batch_id):
            _fail("b7_original_chain_mismatch")
        run=self.execution.inspect(pid,rid)
        result=evaluate_quality(
            run_id=rid,batch_id=batch_id,blueprint=req["blueprint"],
            source_packs=req["source_packs"],multi_plan=req["multi_plan"],
            bundle=original["bundle"],execution=run,
            normalization=b4,grounding=b5,dedup=b6,
        )
        return result,b6["dedup_id"],b5["grounding_id"],b4["normalization_id"],batch_id

    def assess(self,project_id,run_id):
        """Exactly one immutable assessment for a completed B6 run."""
        pid,rid=_project(project_id),_uuid(run_id,"b7_run_id_invalid")
        computed,did,gid,nid,bid=self._material(pid,rid)
        with self.connection_factory() as conn:
            # Serialize concurrent assess() on an immutable upstream B6 row.
            locked=conn.execute(
                """SELECT id FROM knowledge_fact_dedup_batches
                   WHERE project_id=%s AND id=%s FOR UPDATE""",
                (pid,did),
            ).fetchone()
            if locked is None:
                _fail("b7_dedup_not_found")
            existing=_existing(conn,pid,did,computed)
            if existing is not None:
                return {"quality_gate_id":existing,"created":False,
                        **computed["report"],"provider_calls":0}
            report=computed["report"]
            ident=conn.execute(
                """INSERT INTO knowledge_fact_quality_gates(
                     project_id,dedup_id,grounding_id,normalization_id,run_id,batch_id,
                     decision,handoff_allowed,input_sha256,report_sha256,
                     finding_count,blocker_count,warning_count,report)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (pid,did,gid,nid,rid,bid,report["decision"],report["handoff_allowed"],
                 computed["input_sha256"],computed["report_sha256"],
                 report["finding_count"],report["blocker_count"],
                 report["warning_count"],Jsonb(report)),
            ).fetchone()[0]
        return {"quality_gate_id":str(ident),"created":True,
                **computed["report"],"provider_calls":0}

    def materialize(self,project_id,run_id):
        """Read-only A/B1--B7 historical verification and persisted SHA compare."""
        pid,rid=_project(project_id),_uuid(run_id,"b7_run_id_invalid")
        computed,did,_,_,_=self._material(pid,rid)
        with self.connection_factory() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            ident=_existing(conn,pid,did,computed)
            if ident is None:
                _fail("b7_gate_not_found")
        return {"quality_gate_id":ident,**computed,"provider_calls":0}
