"""Stage 12: bind an approved immutable ProjectContext to a budgeted RTB run.

All lookups are in the caller's transaction and project-scoped. No provider calls.
"""
from __future__ import annotations
import hashlib
from uuid import UUID

from intelligence.project_sources.context_builder import choose_evidence, sha as context_sha
from intelligence.project_sources.context_service import _snapshot, _assemble
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256

MODE = "approved_project_context_v1"
SELECTOR_VERSION = "rtb-evidence-selector/1"

def uuid_value(value, label):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PipelineError("Invalid " + label, 400) from exc

def context_material(conn, pid, context_id, token_budget=1200, *, require_current=True):
    """Verify every Stage11 pin and assemble a reproducible, grounded selection."""
    if type(token_budget) is not int or not 100 <= token_budget <= 8000:
        raise PipelineError("Context token_budget must be between 100 and 8000", 400)
    try:
        cid = uuid_value(context_id, "context_id")
        snap = _snapshot(conn, pid, cid)
        if not snap["approved"] or snap["approval"]["decision"] not in ("approved", "approved_partial"):
            raise PipelineError("Only explicitly approved context snapshots are eligible", 409)
        if require_current and context_sha(_assemble(conn, pid)) != snap["context_sha256"]:
            raise PipelineError("Approved context is stale; build and approve a new snapshot", 409)
        ctx = snap["context"]
        if ctx["schema_version"] != "project-context/v1" or ctx["project_id"] != pid:
            raise PipelineError("Invalid context schema/project", 409)
        rows = conn.execute(
            """SELECT e.id,e.exact_text_or_observation
                 FROM project_context_evidence_pins p
                 JOIN project_evidence e ON e.project_id=p.project_id AND e.id=p.evidence_id
                 WHERE p.project_id=%s AND p.context_id=%s""", (pid, cid)).fetchall()
        raw = {str(k): v for k, v in rows}
        meta = {e["evidence_id"]: e for e in ctx["evidence_manifest"]}
        if set(raw) != set(meta):
            raise PipelineError("Context Evidence pins differ from stored Evidence", 409)
        for eid, quote in raw.items():
            if hashlib.sha256(quote.encode("utf-8")).hexdigest() != meta[eid]["exact_text_sha256"]:
                raise PipelineError("Source evidence text changed", 409)
        # Stage11's selection is deterministic and budgeted, but can include
        # pending review. Stage12 never feeds pending/rejected Evidence to RTB.
        reviewed_ctx = dict(ctx)
        reviewed_ctx["evidence_manifest"] = [
            x for x in ctx["evidence_manifest"] if x["review_decision"] == "approved"]
        selected = choose_evidence(reviewed_ctx, raw, token_budget=token_budget)
        sources = {s["source_id"]: s for s in ctx["source_manifest"]}
        enriched = []
        for row in selected["selected"]:
            # A redacted quote would no longer be an exact substring of the
            # immutable original. Never offer such synthetic spans to the LLM.
            if row["text"] != raw[row["evidence_id"]][:1700]:
                continue
            ev = meta[row["evidence_id"]]
            src = sources[ev["source_id"]]
            kinds = []
            if ev["kind"] == "user_claim" and ev["knowledge_scope"] == "operational_product":
                kinds = ["function", "problem", "audience", "product_type"]
            elif ev["kind"] == "external_claim" and ev["knowledge_scope"] in (
                    "operational_product", "general_research"):
                kinds = ["problem", "audience"]
            enriched.append({
                **row,
                "evidence_kind": ev["kind"],
                "source_id": ev["source_id"],
                "source_version_id": ev["source_version_id"],
                "source_type": src["source_type"],
                "source_sha256_kind": src["sha256_kind"],
                "allowed_fact_kinds": kinds,
            })
        if not enriched or not any(x["allowed_fact_kinds"] for x in enriched):
            raise PipelineError("No eligible approved factual Evidence fits the context budget", 409)
        selection_hash = sha256(enriched)
        binding = {
            "mode": MODE,
            "context_id": cid,
            "context_sha256": snap["context_sha256"],
            "approval_id": snap["approval"]["approval_id"],
            "approval_decision": snap["approval"]["decision"],
            "selection_sha256": selection_hash,
            "selector_version": SELECTOR_VERSION,
            "token_budget": token_budget,
            "selected_evidence_ids": [e["evidence_id"] for e in enriched],
            "excluded_evidence_count": len(meta) - len(enriched),
            "excluded_source_ids": sorted(
                s["source_id"] for s in ctx["unavailable_or_excluded_sources"]),
        }
        return {"binding": binding, "selection": enriched,
                "profile_revision": snap["profile_revision"],
                "profile_hash": snap["profile_hash"],
                "profile_fields": ctx["profile"]["fields"]}
    except PipelineError:
        raise
    except Exception as exc:
        # Includes Stage11's ContextProblem, missing/cross-project snapshots,
        # invalid pins and unexpected DB failures: fail closed, no paid call.
        raise PipelineError("Approved context cannot be verified: " + type(exc).__name__, 409) from exc

def binding_source_hash(profile_hash, documents, binding):
    return sha256({"profile_hash": profile_hash, "documents": documents,
                   "context_binding": binding})

def verify_context_quote(conn, pid, quote):
    """Use immutable quote details as the canonical input-mode discriminator."""
    details = quote["details"]
    mode = details.get("input_mode", "legacy_questionnaire_v1")
    if mode == "legacy_questionnaire_v1":
        return None
    if mode != MODE:
        raise PipelineError("Unknown quote input mode", 409)
    bound = details.get("context_binding")
    if not isinstance(bound, dict):
        raise PipelineError("Context quote has no immutable binding", 409)
    material = context_material(conn, pid, bound.get("context_id"),
                                bound.get("token_budget"))
    if material["binding"] != bound:
        raise PipelineError("Quote context/selection/approval changed", 409)
    record = conn.execute(
        """SELECT context_id,approval_id,context_sha256,selection_sha256,token_budget
           FROM tb_context_quote_bindings WHERE project_id=%s AND quote_id=%s""",
        (pid, quote["quote_id"])).fetchone()
    if not record or [str(record[0]), str(record[1]), record[2], record[3], record[4]] != [
            bound["context_id"], bound["approval_id"], bound["context_sha256"],
            bound["selection_sha256"], bound["token_budget"]]:
        raise PipelineError("Immutable quote binding is missing or corrupted", 409)
    return material

def run_context(conn, pid, run_id):
    record = conn.execute(
        """SELECT q.id,c.context_id,c.approval_id,c.context_sha256,
                  c.selection_sha256,c.token_budget,q.quote_details
           FROM tb_context_run_bindings b
           JOIN tb_context_quote_bindings c ON c.project_id=b.project_id AND c.quote_id=b.quote_id
           JOIN tb_cost_quotes q ON q.project_id=b.project_id AND q.id=b.quote_id
           WHERE b.project_id=%s AND b.run_id=%s""", (pid, run_id)).fetchone()
    if not record:
        return None
    qid, cid, aid, digest, selection_hash, budget, details = record
    bound = details.get("context_binding")
    if (details.get("input_mode") != MODE or not isinstance(bound, dict)
        or bound["context_id"] != str(cid) or bound["approval_id"] != str(aid)
        or bound["context_sha256"] != digest or
            bound["selection_sha256"] != selection_hash or bound["token_budget"] != budget):
        raise PipelineError("Run context does not match quote consent", 409)
    material = context_material(conn, pid, str(cid), budget)
    if material["binding"] != bound:
        raise PipelineError("Context changed since budget approval", 409)
    return material
