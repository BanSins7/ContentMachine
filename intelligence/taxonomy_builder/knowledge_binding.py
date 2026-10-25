"""Read-only B9 -> existing C Taxonomy Generation prompt/schema adapter.

This module does NOT hook into legacy run start; C must independently quote,
authorize budget and start an explicitly new knowledge_snapshot_v1 run.
Legacy questionnaire/context input modes continue unchanged.
"""
from __future__ import annotations

from intelligence.knowledge_extraction.knowledge_snapshot import (
    KnowledgeSnapshotRepository, C_MODE,
)
from intelligence.project_sources.context_builder import sha as canonical_sha
from .run_contracts import prompt_for

C_FACT_KINDS = frozenset(("function","problem","audience","product_type"))


def to_taxonomy_generation_payload(handoff, snapshot):
    """Preflight only; adapt B9 vetted facts to native RTB generation contract.

    Rejects mismatched or unapproved records. Non-RTB kinds are excluded
    from the old 4-kind taxonomy schema but are not deleted from B9.
    """
    if (not isinstance(handoff,dict) or not isinstance(snapshot,dict)
            or handoff.get("input_mode") != C_MODE
            or handoff.get("execution_status") != "not_started"
            or handoff.get("independent_c_quote_required") is not True
            or handoff.get("independent_c_consent_required") is not True
            or snapshot.get("schema_version") != "knowledge-snapshot/v1"
            or handoff.get("snapshot_sha256") != canonical_sha(snapshot)
            or handoff.get("project_id") != snapshot.get("project_id")
            or len(handoff.get("facts",[])) != snapshot.get("approved_count")):
        raise ValueError("c_knowledge_binding_invalid")
    rows = []
    for f in snapshot["facts"]:
        if (f.get("review_status") != "human_approved"
                or not isinstance(f.get("citations"),list) or not f["citations"]
                or not f.get("reviewed_text")):
            raise ValueError("c_unapproved_fact")
        if f["kind"] in C_FACT_KINDS:
            rows.append({
                "id": f["fact_id"],"kind":f["kind"],
                "text":f["reviewed_text"],"source_field":"knowledge_snapshot_v1",
                "source_quote":f["citations"][0]["source_quote"],
                "source_group_id":f["group_id"],
                "source_citation_count":len(f["citations"]),
                "subject_scope":f["subject_scope"],"subject_id":f["subject_id"],
                "trust_labels":sorted({c["original_provenance"]["trust_label"]
                                       for c in f["citations"]}),
            })
    if not rows:
        raise ValueError("c_no_taxonomy_eligible_fact_kinds")
    selection={
        "input_mode":C_MODE,
        "snapshot_sha256":handoff["snapshot_sha256"],
        "snapshot_id":handoff["snapshot_id"],
        "approved_facts":rows,
        "niche":"health_fitness",
        "research_goal":"maximize_views",
        "reference_evidence":[],
        "request":"Generate taxonomy grounded only in human-approved B9 facts. "
                  "Keep subject roles distinct. Adjacent ideas are hypotheses; "
                  "do not invent product capabilities.",
        "fact_extraction_disabled":True,
    }
    # Reuse native C taxonomy_generation v2 prompt and grounding schema.
    messages=prompt_for("taxonomy_generation",selection,"taxonomy-v2")
    return {
        "input_mode":C_MODE,
        "role":"taxonomy_generation",
        "prompt_version":"taxonomy-v2",
        "snapshot_sha256":handoff["snapshot_sha256"],
        "approved_fact_count":len(rows),
        "excluded_non_rtb_kind_count":len(snapshot["facts"])-len(rows),
        "prompt_messages":messages,
        "independent_c_quote_required":True,
        "independent_c_consent_required":True,
        "execution_authorized":False,
        "provider_calls":0,
    }


def preview_knowledge_for_c(project_id,session_id,reviewer_id,connection_factory=None,
                            *,verify_local_files=True):
    """Server-internal end-to-end read-only C input adapter."""
    repository=KnowledgeSnapshotRepository(
        connection_factory,verify_local_files=verify_local_files)
    snapshot=repository.materialize(project_id,session_id,reviewer_id)
    handoff=repository.materialize_c_handoff(project_id,session_id,reviewer_id)
    return to_taxonomy_generation_payload(handoff["handoff"],snapshot["snapshot"])
