"""Content Machine B Stage10: lossless exact groups and conservative conflict flags.

Not a semantic similarity engine. Does not automatically determine truth,
resolve contradictions, collapse source citations, or approve product claims.
No LLM, embedding, web, video processing, provider calls or side effects.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re

from psycopg.types.json import Jsonb

from intelligence.project_sources.context_builder import sha as canonical_sha
from .grounding import FactGroundingRepository
from .intake import IntakeError, _project, _uuid
from .inventory import text_sha

VERSION = "knowledge-dedup/v1"
ENGINE = "b6-deterministic/1.0.0"
MAX_CONFLICTS = 50_000
ALLOWED_KINDS = frozenset((
    "function","problem","audience","product_type","constraint",
    "metric","positioning","external_claim",
))
NUMBER = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?(?![\w])", re.UNICODE)
WORD = re.compile(r"[^\W_]+", re.UNICODE)
NEGATION = frozenset(("не","not"))
NEGATION_EXCLUSIONS = frozenset(("только","менее","более","only","less","more"))


def _fail(code):
    raise IntakeError(code)


def _key(prop):
    """Exact comparison: B4-preserved text, with NO semantic paraphrasing.

    Unknown subject identities cannot be silently merged across sources.
    Cross-source grouping otherwise preserves each individual citation.
    """
    scope, subject = prop["subject_scope"], prop["subject_id"]
    ambiguous = scope != "own_product" and not subject
    return {
        "kind": prop["kind"], "text": prop["text"],
        "source_role": prop["source_role"],
        "subject_scope": scope, "subject_id": subject,
        "unresolved_identity_source": (
            prop["source_id"] or prop["fragment_id"] if ambiguous else None
        ),
    }


def _conflict_signature(group):
    """Low-recall high-precision lexical candidates. No inference or winner."""
    scope = group["subject_scope"]
    if not (scope == "own_product"
            or (scope == "competitor" and group["subject_id"])):
        return None
    basis = (group["fact_kind"], group["source_role"], scope, group["subject_id"])
    text = group["original_text"].casefold()
    words = WORD.findall(text)
    # Exclude compound negation such as 'not only', 'не менее/не более':
    # stripping it is NOT a safe polarity comparison.
    if len(words) >= 3 and not NEGATION_EXCLUSIONS.intersection(words):
        negative = [i for i, w in enumerate(words) if w in NEGATION]
        if len(negative) == 1:
            index = negative[0]
            common = words[:index] + words[index + 1:]
            if len(common) >= 3:
                yield ("opposite_negation", basis, tuple(common), True)
        elif not negative:
            yield ("opposite_negation", basis, tuple(words), False)
    # A single numeric value in an otherwise identical phrase is a candidate,
    # not an established conflict: time, currencies and definitions may differ.
    numerals = NUMBER.findall(text)
    if len(numerals) == 1 and len(words) >= 4:
        replaced = NUMBER.sub("<n>", text, count=1)
        yield ("different_number_same_phrase", basis, replaced, numerals[0])


def analyze_grounding(*, run_id, batch_id, normalized_rows, grounding_receipts,
                      b4_report_sha256, b5_report_sha256):
    """Pure one-to-one reconciliation with independently verified B5 receipts.

    Accounts for quarantined/rejected candidates in membership ledger but
    never groups them. Every matched proposal keeps its individual exact
    source locator + quote SHA even if grouped as a duplicate.
    """
    run_id = _uuid(run_id, "b6_run_id_invalid")
    batch_id = _uuid(batch_id, "b6_batch_id_invalid")
    if (not isinstance(normalized_rows, list)
            or not isinstance(grounding_receipts, list)
            or not isinstance(b4_report_sha256, str) or len(b4_report_sha256) != 64
            or not isinstance(b5_report_sha256, str) or len(b5_report_sha256) != 64
            or len(normalized_rows) != len(grounding_receipts)
            or len(normalized_rows) > 20480):
        _fail("b6_input_count_invalid")
    by_pos = {}
    for receipt_row in grounding_receipts:
        if not isinstance(receipt_row, dict):
            _fail("b6_receipt_invalid")
        pos = (receipt_row.get("pack_index"), receipt_row.get("candidate_index"))
        if (type(pos[0]) is not int or type(pos[1]) is not int
                or not 0 <= pos[0] <= 255 or not 0 <= pos[1] < 80
                or pos in by_pos):
            _fail("b6_receipt_positions_invalid")
        receipt = receipt_row.get("receipt")
        if (not isinstance(receipt, dict) or
                canonical_sha(receipt) != receipt_row.get("receipt_sha256") or
                receipt.get("proposal_id") != receipt_row.get("proposal_id") or
                receipt.get("status") != receipt_row.get("status") or
                receipt.get("reason_code") != receipt_row.get("reason_code") or
                receipt.get("run_id") != run_id or
                receipt.get("batch_id") != batch_id or
                (receipt.get("pack_index"), receipt.get("candidate_index")) != pos or
                receipt.get("b4_report_sha256") != b4_report_sha256 or
                receipt.get("semantic_entailment_checked") is not False or
                receipt.get("objective_truth_confirmed") is not False or
                receipt.get("human_review_status") != "unreviewed"):
            _fail("b6_receipt_integrity")
        by_pos[pos] = receipt_row

    members = []
    groups_by_key = defaultdict(list)
    seen_proposals = set()
    for row in normalized_rows:
        if not isinstance(row, dict):
            _fail("b6_b4_row_invalid")
        pack_idx, cand_idx, proposal_id = (
            row.get("pack_index"), row.get("candidate_index"), row.get("proposal_id"))
        position = (pack_idx,cand_idx)
        receipt_row = by_pos.pop(position, None)
        if (receipt_row is None or receipt_row["proposal_id"] != proposal_id
                or proposal_id in seen_proposals
                or not isinstance(proposal_id,str) or len(proposal_id) != 64):
            _fail("b6_missing_or_swapped_receipt")
        seen_proposals.add(proposal_id)
        status = receipt_row["status"]
        prop = row.get("normalized")
        reference = None
        keysha = None
        if row.get("status") == "quarantined":
            if status != "skipped_b4_quarantine" or prop is not None:
                _fail("b6_b4_quarantine_mismatch")
            member_status, reason = "skipped_b4", row.get("reason_code") or "b4_quarantined"
        elif row.get("status") == "pending_grounding":
            if not isinstance(prop,dict) or prop.get("proposal_id") != proposal_id:
                _fail("b6_b4_proposal_invalid")
            if status == "rejected":
                if receipt_row["reason_code"] is None:
                    _fail("b6_b5_rejection_missing_reason")
                member_status, reason = "blocked_b5", receipt_row["reason_code"]
            elif status == "source_quote_matched":
                if (prop.get("kind") not in ALLOWED_KINDS
                        or not isinstance(prop.get("text"),str) or not prop["text"]
                        or len(prop["text"]) > 800
                        or prop.get("human_review_status") != "unreviewed"
                        or prop.get("grounding_status") != "pending_b5"
                        or prop.get("model_proposal_only") is not True):
                    _fail("b6_b4_fact_integrity")
                reference = receipt_row["receipt"].get("original_provenance")
                if (not isinstance(reference,dict)
                        or reference.get("source_text_match_only") is not True
                        or reference.get("source_id") != prop.get("source_id")
                        or reference.get("source_version_id") != prop.get("source_version_id")
                        or reference.get("output_id") != prop.get("output_id")
                        or reference.get("fragment_id") != prop.get("fragment_id")
                        or reference.get("original_locator") != prop.get("source_locator")
                        or reference.get("source_role") != prop.get("source_role")
                        or reference.get("subject_scope") != prop.get("subject_scope")
                        or reference.get("subject_id") != prop.get("subject_id")
                        or reference.get("trust_label") != prop.get("trust_label")
                        or reference.get("evidence_ids") != prop.get("evidence_ids")
                        or reference.get("quote_sha256") != text_sha(prop.get("source_quote",""))):
                    _fail("b6_provenance_mismatch")
                member_status, reason = "grouped", None
                keysha = canonical_sha(_key(prop))
                groups_by_key[keysha].append((proposal_id, prop))
            else:
                _fail("b6_unexpected_b5_receipt")
        else:
            _fail("b6_b4_status_invalid")
        members.append({
            "pack_index": pack_idx, "candidate_index": cand_idx,
            "proposal_id": proposal_id,
            "group_id": None,
            "status": member_status, "reason_code": reason,
            "b5_receipt_sha256": receipt_row["receipt_sha256"],
            "provenance": reference,
            "_key": keysha,
        })
    if by_pos:
        _fail("b6_orphan_b5_receipts")
    groups = []
    id_by_key = {}
    for keysha in sorted(groups_by_key):
        items = sorted(groups_by_key[keysha], key=lambda item: item[0])
        first = items[0][1]
        gid = canonical_sha({"engine": ENGINE, "run_id":run_id, "group_key_sha256":keysha})
        id_by_key[keysha] = gid
        groups.append({
            "group_id": gid, "group_key_sha256": keysha,
            "representative_id": items[0][0],
            "fact_kind": first["kind"],
            "source_role": first["source_role"],
            "subject_scope": first["subject_scope"],
            "subject_id": first["subject_id"],
            "original_text": first["text"],
            "member_count": len(items),
            "review_status": "unreviewed",
        })
    groups.sort(key=lambda x:x["group_id"])
    for row in members:
        if row["_key"] is not None:
            row["group_id"] = id_by_key[row["_key"]]
        del row["_key"]
    members.sort(key=lambda x:(x["pack_index"],x["candidate_index"]))

    signatures = defaultdict(list)
    for group in groups:
        for rule, basis, stem, value in _conflict_signature(group):
            signatures[(rule,basis,stem)].append((group["group_id"],value))
    conflicts = []
    for (rule, _, _), collection in sorted(signatures.items(),key=lambda x:str(x[0])):
        collection.sort()
        for i,(a,va) in enumerate(collection):
            for b,vb in collection[i+1:]:
                if va == vb:
                    continue
                pair = sorted((a,b))
                conflicts.append({
                    "conflict_id": canonical_sha({
                        "engine": ENGINE, "run_id":run_id, "rule":rule,
                        "groups":pair,
                    }),
                    "group_a": pair[0], "group_b": pair[1],
                    "rule":rule,
                    "review_state":"needs_review",
                    "reason": (
                        "Opposite explicit lexical negation in otherwise matching claim"
                        if rule=="opposite_negation" else
                        "Different numerical values within same literal claim template"
                    ),
                })
                if len(conflicts)>MAX_CONFLICTS:
                    # No silent truncation that could mislead B7 Quality Gate.
                    _fail("b6_conflict_cap_exceeded")
    conflicts.sort(key=lambda x:(x["group_a"],x["group_b"],x["rule"]))
    eligible=sum(m["status"]=="grouped" for m in members)
    reasons=Counter(m["reason_code"] for m in members if m["reason_code"])
    fingerprint=canonical_sha({
        "engine":ENGINE,"run_id":run_id,"batch_id":batch_id,
        "b4_report_sha256":b4_report_sha256,"b5_report_sha256":b5_report_sha256,
        "members":[{
            "proposal_id":m["proposal_id"],
            "status":m["status"],"receipt":m["b5_receipt_sha256"],
            "group_id":m["group_id"],
        } for m in members],
        "groups":[g["group_id"] for g in groups],
        "conflicts":[c["conflict_id"] for c in conflicts],
    })
    report={
        "schema_version":VERSION,"engine_version":ENGINE,
        "run_id":run_id,"batch_id":batch_id,
        "input_sha256":fingerprint,
        "b4_report_sha256":b4_report_sha256,
        "b5_report_sha256":b5_report_sha256,
        "proposal_count":len(members),
        "eligible_count":eligible,
        "blocked_count":len(members)-eligible,
        "group_count":len(groups),
        "duplicate_count":eligible-len(groups),
        "conflict_candidate_count":len(conflicts),
        "blocked_reasons":dict(sorted(reasons.items())),
        "all_citations_retained":True,
        "exact_text_only":True,
        "conflicts_are_candidates_only":True,
        "review_required":True,
        "semantic_entailment_checked":False,
        "objective_truth_confirmed":False,
        "provider_calls":0,
    }
    return {"report":report,"report_sha256":canonical_sha(report),
            "members":members,"groups":groups,"conflicts":conflicts,
            "input_sha256":fingerprint,"provider_calls":0}


def _stored(conn,pid,did):
    groups=conn.execute(
        """SELECT group_id,group_key_sha256,representative_id,fact_kind,
                  source_role,subject_scope,subject_id,original_text,
                  member_count,review_status
           FROM knowledge_fact_exact_groups
           WHERE project_id=%s AND dedup_id=%s ORDER BY group_id""",
        (pid,did),
    ).fetchall()
    members=conn.execute(
        """SELECT pack_index,candidate_index,proposal_id,group_id,status,
                  reason_code,b5_receipt_sha256,provenance
           FROM knowledge_fact_dedup_members
           WHERE project_id=%s AND dedup_id=%s
           ORDER BY pack_index,candidate_index""",
        (pid,did),
    ).fetchall()
    conflicts=conn.execute(
        """SELECT conflict_id,group_a,group_b,rule,review_state,reason
           FROM knowledge_fact_possible_conflicts
           WHERE project_id=%s AND dedup_id=%s
           ORDER BY group_a,group_b,rule""",
        (pid,did),
    ).fetchall()
    return {
        "groups":[dict(zip(("group_id","group_key_sha256","representative_id",
                           "fact_kind","source_role","subject_scope","subject_id",
                           "original_text","member_count","review_status"),r))
                  for r in groups],
        "members":[dict(zip(("pack_index","candidate_index","proposal_id",
                            "group_id","status","reason_code",
                            "b5_receipt_sha256","provenance"),r))
                   for r in members],
        "conflicts":[dict(zip(("conflict_id","group_a","group_b","rule",
                              "review_state","reason"),r))
                     for r in conflicts],
    }


def _compare(conn,pid,grounding_id,computed):
    row=conn.execute(
        """SELECT id,report,report_sha256,input_sha256,proposal_count,
                  eligible_count,blocked_count,group_count,duplicate_count,
                  conflict_candidate_count
           FROM knowledge_fact_dedup_batches
           WHERE project_id=%s AND grounding_id=%s""",
        (pid,grounding_id),
    ).fetchone()
    if not row:
        return None
    report=computed["report"]
    if (row[1]!=report or row[2]!=computed["report_sha256"]
            or canonical_sha(row[1])!=row[2]
            or row[3]!=computed["input_sha256"]
            or tuple(row[4:])!=tuple(report[k] for k in
                  ("proposal_count","eligible_count","blocked_count","group_count",
                   "duplicate_count","conflict_candidate_count"))
            or _stored(conn,pid,row[0])!={k:computed[k] for k in
                                          ("groups","members","conflicts")}):
        _fail("b6_immutable_replay_mismatch")
    return str(row[0])


class FactDeduplicationRepository:
    """Only B5-independent literal-grounded facts enter exact groupings."""

    def __init__(self, connection_factory=None, *, verify_local_files=True):
        self.grounding=FactGroundingRepository(
            connection_factory, verify_local_files=verify_local_files)
        self.connection_factory=self.grounding.connection_factory

    def _material(self,pid,rid):
        grounded=self.grounding.materialize(pid,rid)
        normal=self.grounding.normalization.materialize(pid,rid)
        if grounded["report"]["normalization_sha256"] != normal["report_sha256"]:
            _fail("b6_b4_b5_report_mismatch")
        if (grounded["report"]["run_id"] != rid
                or normal["report"]["run_id"] != rid
                or grounded["report"]["batch_id"] != normal["report"]["batch_id"]):
            _fail("b6_run_or_batch_mismatch")
        calculated=analyze_grounding(
            run_id=rid, batch_id=grounded["report"]["batch_id"],
            normalized_rows=normal["rows"],
            grounding_receipts=grounded["receipts"],
            b4_report_sha256=normal["report_sha256"],
            b5_report_sha256=grounded["report_sha256"],
        )
        return calculated,grounded["grounding_id"],normal["normalization_id"],normal["report"]["batch_id"]

    def analyze(self,project_id,run_id):
        """Append once; replay all sources/history before each exact grouping."""
        pid,rid=_project(project_id),_uuid(run_id,"b6_run_id_invalid")
        computed,gid,nid,bid=self._material(pid,rid)
        with self.connection_factory() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            conn.execute(
                """SELECT id FROM knowledge_fact_grounding_batches
                   WHERE project_id=%s AND id=%s FOR SHARE""",(pid,gid),
            )
            existing=_compare(conn,pid,gid,computed)
            if existing is not None:
                return {"dedup_id":existing,"created":False,
                        **computed["report"],"provider_calls":0}
            rep=computed["report"]
            did=conn.execute(
                """INSERT INTO knowledge_fact_dedup_batches(
                     project_id,grounding_id,normalization_id,run_id,batch_id,
                     input_sha256,report_sha256,proposal_count,eligible_count,
                     blocked_count,group_count,duplicate_count,
                     conflict_candidate_count,report)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (pid,gid,nid,rid,bid,computed["input_sha256"],
                 computed["report_sha256"],rep["proposal_count"],rep["eligible_count"],
                 rep["blocked_count"],rep["group_count"],rep["duplicate_count"],
                 rep["conflict_candidate_count"],Jsonb(rep)),
            ).fetchone()[0]
            for g in computed["groups"]:
                conn.execute(
                    """INSERT INTO knowledge_fact_exact_groups(
                         project_id,dedup_id,group_id,group_key_sha256,
                         representative_id,fact_kind,source_role,subject_scope,
                         subject_id,original_text,member_count)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (pid,did,g["group_id"],g["group_key_sha256"],g["representative_id"],
                     g["fact_kind"],g["source_role"],g["subject_scope"],g["subject_id"],
                     g["original_text"],g["member_count"]),
                )
            for m in computed["members"]:
                conn.execute(
                    """INSERT INTO knowledge_fact_dedup_members(
                         project_id,dedup_id,grounding_id,normalization_id,run_id,batch_id,
                         pack_index,candidate_index,proposal_id,group_id,status,
                         reason_code,b5_receipt_sha256,provenance)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (pid,did,gid,nid,rid,bid,m["pack_index"],m["candidate_index"],
                     m["proposal_id"],m["group_id"],m["status"],m["reason_code"],
                     m["b5_receipt_sha256"],
                     Jsonb(m["provenance"]) if m["provenance"] is not None else None),
                )
            for c in computed["conflicts"]:
                conn.execute(
                    """INSERT INTO knowledge_fact_possible_conflicts(
                         project_id,dedup_id,conflict_id,group_a,group_b,rule,reason)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                    (pid,did,c["conflict_id"],c["group_a"],c["group_b"],
                     c["rule"],c["reason"]),
                )
        return {"dedup_id":str(did),"created":True,
                **computed["report"],"provider_calls":0}

    def materialize(self,project_id,run_id):
        """Read and independently verify immutable B6 rows + B5/A history."""
        pid,rid=_project(project_id),_uuid(run_id,"b6_run_id_invalid")
        computed,gid,_,_=self._material(pid,rid)
        with self.connection_factory() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            ident=_compare(conn,pid,gid,computed)
            if ident is None:
                _fail("b6_not_found")
        return {"dedup_id":ident,**computed,"provider_calls":0}
