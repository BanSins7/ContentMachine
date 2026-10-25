"""Stage09 B5: quote provenance against ORIGINAL pinned A text, not B3 payload alone.

A 'source_quote_matched' receipt certifies an exact substring of the immutable
A processing output / approved A profile field, NOT semantic entailment,
objective truth, independent web retrieval or human approval.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from psycopg.types.json import Jsonb

from intelligence.project_sources.context_builder import sha as canonical_sha
from .fact_contracts import _source_rows
from .full_pack_repository import _load, _rebuild
from .repository import _source_inputs
from .intake import IntakeError, _project, _uuid
from .normalization import FactNormalizationRepository, NORMALIZER_VERSION

VERSION = "knowledge-grounding/v1"
ENGINE = "b5-independent-a/1.0.0"


def _fail(code):
    raise IntakeError(code)


def _digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


def _checked_chunks(source_packs, fragments, original_texts):
    """Validate ALL source chunks against A, even when MiMo cited none of them."""
    if not isinstance(fragments, list) or not isinstance(original_texts, dict):
        _fail("b5_source_inventory_invalid")
    inventory = {f["fragment_id"]: f for f in fragments}
    if len(inventory) != len(fragments) or set(inventory) != set(original_texts):
        _fail("b5_original_inventory_integrity")
    rows_by_pack = []
    for manifest, payload in source_packs:
        rows = _source_rows(manifest, payload)
        inspected = {}
        for chunk_id, source in rows.items():
            fid = source.get("original_fragment_id", source.get("fragment_id"))
            fragment = inventory.get(fid)
            original = original_texts.get(fid)
            if fragment is None or not isinstance(original, str):
                _fail("b5_fragment_not_in_a")
            multi = source.get("kind") == "text_span"
            if multi:
                begin, end = source.get("char_start"), source.get("char_end")
                if (type(begin) is not int or type(end) is not int
                        or not 0 <= begin < end <= len(original)
                        or source.get("original_text_sha256") != fragment["text_sha256"]):
                    _fail("b5_original_span_integrity")
                exact = original[begin:end]
                if source.get("exact_text_sha256") != _digest(exact):
                    _fail("b5_original_span_integrity")
            else:
                begin, end = 0, len(original)
                exact = original
                if source.get("exact_text_sha256") != fragment["text_sha256"]:
                    _fail("b5_original_fragment_integrity")
            for field in ("origin_kind", "source_id", "source_version_id", "output_id",
                          "field_path", "segment_index", "locator", "source_role",
                          "subject_scope", "subject_id", "trust_label", "evidence_ids"):
                if source.get(field) != fragment.get(field):
                    _fail("b5_source_lineage_mismatch")
            if (source.get("text") != exact
                    or _digest(original) != fragment["text_sha256"]
                    or len(original) != fragment["char_count"]):
                _fail("b5_a_text_differs_from_pack")
            inspected[chunk_id] = (fragment, original, begin, end)
        rows_by_pack.append(inspected)
    return rows_by_pack


def ground_proposals(*, run_id, batch_id, normalized, source_packs, fragments,
                     original_texts, context_sha256, source_sha_by_version,
                     normalization_sha256):
    """Pure B5: all-pack A verification plus per-proposal exact quote checks."""
    run_id = _uuid(run_id, "b5_run_id_invalid")
    batch_id = _uuid(batch_id, "b5_batch_id_invalid")
    if (not isinstance(normalized, list) or not isinstance(context_sha256, str)
            or len(context_sha256) != 64 or not isinstance(source_sha_by_version, dict)):
        _fail("b5_grounding_input_invalid")
    source_maps = _checked_chunks(source_packs, fragments, original_texts)
    receipts = []
    seen = set()
    for row in normalized:
        if not isinstance(row, dict):
            _fail("b5_normalized_row_invalid")
        idx, candidate = row.get("pack_index"), row.get("candidate_index")
        if (type(idx) is not int or idx < 0 or idx >= len(source_maps)
                or type(candidate) is not int or not 0 <= candidate < 80
                or (idx, candidate) in seen):
            _fail("b5_normalized_positions_invalid")
        seen.add((idx, candidate))
        pid = row.get("proposal_id")
        if not isinstance(pid, str) or len(pid) != 64:
            _fail("b5_proposal_id_invalid")
        status, reason = "source_quote_matched", None
        details = None
        if row.get("status") == "quarantined":
            if row.get("normalized") is not None:
                _fail("b5_invalid_b4_quarantine")
            status, reason = "skipped_b4_quarantine", "b5_prior_b4_quarantine"
        elif row.get("status") != "pending_grounding":
            _fail("b5_invalid_b4_status")
        else:
            prop = row.get("normalized")
            if not isinstance(prop, dict) or prop.get("proposal_id") != pid:
                _fail("b5_b4_proposal_integrity")
            ref = source_maps[idx].get(prop.get("source_chunk_id"))
            if ref is None:
                status, reason = "rejected", "b5_source_chunk_unknown"
            else:
                f, original, begin, end = ref
                quote = prop.get("source_quote")
                qstart, qend = prop.get("quote_start"), prop.get("quote_end")
                if (type(qstart) is not int or type(qend) is not int
                        or not 0 <= qstart < qend <= end - begin
                        or not isinstance(quote, str)):
                    status, reason = "rejected", "b5_quote_range_invalid"
                elif original[begin+qstart:begin+qend] != quote:
                    status, reason = "rejected", "b5_quote_not_in_original_a"
                elif (prop.get("fragment_id") != f["fragment_id"]
                      or prop.get("source_id") != f["source_id"]
                      or prop.get("source_version_id") != f["source_version_id"]
                      or prop.get("output_id") != f["output_id"]
                      or prop.get("source_locator") != f["locator"]
                      or prop.get("source_role") != f["source_role"]
                      or prop.get("subject_scope") != f["subject_scope"]
                      or prop.get("subject_id") != f["subject_id"]
                      or prop.get("trust_label") != f["trust_label"]
                      or prop.get("evidence_ids") != f["evidence_ids"]
                      or prop.get("pack_payload_sha256") != source_packs[idx][0]["payload_sha256"]
                      or prop.get("human_review_status") != "unreviewed"
                      or prop.get("grounding_status") != "pending_b5"):
                    status, reason = "rejected", "b5_provenance_mismatch"
                else:
                    version = f["source_version_id"]
                    vsha = source_sha_by_version.get((f["source_id"],version)) if version else None
                    if version is not None and not isinstance(vsha,str):
                        _fail("b5_source_version_sha_missing")
                    details = {
                        "origin_kind": f["origin_kind"],
                        "source_id": f["source_id"],
                        "source_version_id": version,
                        "source_version_sha256": vsha,
                        "output_id": f["output_id"],
                        "output_sha256": f["output_sha256"],
                        "profile_revision": f["profile_revision"],
                        "profile_hash": f["profile_hash"],
                        "fragment_id": f["fragment_id"],
                        "original_fragment_sha256": f["text_sha256"],
                        "original_locator": f["locator"],
                        "locator_sha256": canonical_sha(f["locator"]),
                        "source_role": f["source_role"],
                        "subject_scope": f["subject_scope"],
                        "subject_id": f["subject_id"],
                        "trust_label": f["trust_label"],
                        "evidence_ids": f["evidence_ids"],
                        "original_global_quote_start": begin + qstart,
                        "original_global_quote_end": begin + qend,
                        "quote_sha256": _digest(quote),
                        "original_text_verified_from": (
                            "pinned_a_processing_output" if f["output_id"] else
                            "pinned_a_profile_version" if f["source_id"] else
                            "approved_a_context_profile"
                        ),
                        "website_independently_retrieved": False,
                        "source_text_match_only": True,
                    }
        receipt = {
            "schema_version": "knowledge-grounding-receipt/v1",
            "grounding_version": ENGINE,
            "run_id": run_id, "batch_id": batch_id,
            "pack_index": idx, "candidate_index": candidate,
            "proposal_id": pid, "status": status,
            "reason_code": reason,
            "context_sha256": context_sha256,
            "b4_report_sha256": normalization_sha256,
            "original_provenance": details,
            "semantic_entailment_checked": False,
            "objective_truth_confirmed": False,
            "human_review_status": "unreviewed",
            "provider_calls": 0,
        }
        receipts.append({
            "pack_index": idx, "candidate_index": candidate,
            "proposal_id": pid, "status": status, "reason_code": reason,
            "receipt": receipt, "receipt_sha256": canonical_sha(receipt),
        })
    receipts.sort(key=lambda r:(r["pack_index"],r["candidate_index"]))
    counts = Counter(x["status"] for x in receipts)
    reasons = Counter(x["reason_code"] for x in receipts if x["reason_code"])
    fingerprint = canonical_sha({
        "engine": ENGINE, "run_id": run_id, "batch_id": batch_id,
        "context_sha256": context_sha256,
        "b4_report_sha256": normalization_sha256,
        "receipts": [x["receipt_sha256"] for x in receipts],
    })
    report = {
        "schema_version": VERSION, "grounding_version": ENGINE,
        "run_id": run_id, "batch_id": batch_id,
        "input_sha256": fingerprint,
        "normalization_sha256": normalization_sha256,
        "context_sha256": context_sha256,
        "total_count": len(receipts),
        "matched_count": counts["source_quote_matched"],
        "rejected_count": counts["rejected"],
        "skipped_count": counts["skipped_b4_quarantine"],
        "reason_counts": dict(sorted(reasons.items())),
        "literal_quote_grounding_only": True,
        "semantic_entailment_checked": False,
        "human_review_required": True,
        "website_independently_retrieved": False,
        "provider_calls": 0,
    }
    return {"report": report, "report_sha256": canonical_sha(report),
            "receipts": receipts, "input_sha256": fingerprint,
            "provider_calls": 0}


def _rowset(conn, pid, grounding_id):
    rows=conn.execute(
        """SELECT pack_index,candidate_index,proposal_id,status,reason_code,
                  receipt_sha256,receipt
             FROM knowledge_fact_grounding_receipts
            WHERE project_id=%s AND grounding_id=%s
            ORDER BY pack_index,candidate_index""",
        (pid, grounding_id),
    ).fetchall()
    return [{
        "pack_index": x[0], "candidate_index": x[1],
        "proposal_id": x[2], "status": x[3], "reason_code": x[4],
        "receipt_sha256": x[5], "receipt": x[6],
    } for x in rows]


def _existing(conn,pid,normalization_id,calc):
    row=conn.execute(
        """SELECT id,report,report_sha256,input_sha256,total_count,matched_count,
                  rejected_count,skipped_count
             FROM knowledge_fact_grounding_batches
            WHERE project_id=%s AND normalization_id=%s""",
        (pid, normalization_id),
    ).fetchone()
    if not row:
        return None
    report=calc["report"]
    if (row[1] != report or row[2] != calc["report_sha256"]
            or canonical_sha(row[1]) != row[2]
            or row[3] != calc["input_sha256"]
            or (row[4],row[5],row[6],row[7]) != (
                report["total_count"],report["matched_count"],
                report["rejected_count"],report["skipped_count"])
            or _rowset(conn,pid,row[0]) != calc["receipts"]):
        _fail("b5_persisted_receipt_integrity")
    return str(row[0])


class FactGroundingRepository:
    """B4 output -> read A output/profile originals -> append-only B5 receipt."""

    def __init__(self, connection_factory=None, *, verify_local_files=True):
        self.normalization=FactNormalizationRepository(
            connection_factory, verify_local_files=verify_local_files)
        self.connection_factory=self.normalization.connection_factory
        self.resolver=self.normalization.requests.resolver

    def _verify_and_compute(self, conn, pid, rid, norm):
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        # Pin the exact B4 snapshot, not a user's unreviewed JSON replacement.
        row=conn.execute(
            """SELECT id,batch_id,report_sha256,report
                 FROM knowledge_fact_normalization_batches
                WHERE project_id=%s AND run_id=%s FOR SHARE""",
            (pid,rid),
        ).fetchone()
        if row is None or str(row[0]) != norm["normalization_id"] or (
            row[2] != norm["report_sha256"] or row[3] != norm["report"]
        ):
            _fail("b5_normalization_changed")
        bundle_id = norm["source_bundle_id"]
        bundle, envelope = _load(
            conn,pid,bundle_id,self.resolver,require_current=False)
        if canonical_sha(bundle) != norm["source_bundle_sha256"]:
            _fail("b5_bundle_pin_changed")
        inputs = _source_inputs(conn,pid,envelope)
        original_texts = _rebuild(envelope,inputs,bundle)
        source_versions = {
            (s["source_id"],s["source_version_id"]):s["sha256"]
            for s in envelope["context"]["source_manifest"]
            if s["source_version_id"] is not None
        }
        computed=ground_proposals(
            run_id=rid, batch_id=norm["batch_id"], normalized=norm["rows"],
            source_packs=norm["source_packs"],
            fragments=bundle["fragments"], original_texts=original_texts,
            context_sha256=envelope["context_sha256"],
            source_sha_by_version=source_versions,
            normalization_sha256=norm["report_sha256"],
        )
        return computed, bundle["context_id"], str(row[0])

    def _material(self,pid,rid):
        norm=self.normalization.materialize(pid,rid)
        batches=self.normalization.requests.materialize(pid,norm["report"]["batch_id"])
        # Explicitly cross-verify the Stage06 batch and the Stage08 run binding.
        if (norm["report"]["source_batch_sha256"] != canonical_sha(batches["blueprint"])
                or norm["report"]["batch_id"] != batches["batch_id"]):
            _fail("b5_b4_batch_binding_invalid")
        return {
            "normalization_id":norm["normalization_id"],
            "report_sha256":norm["report_sha256"],
            "report":norm["report"], "rows":norm["rows"],
            "source_bundle_id":batches["blueprint"]["bundle_id"],
            "source_bundle_sha256":batches["source_packs"][0][0]["bundle_sha256"],
            "source_packs":batches["source_packs"],
            "batch_id":batches["batch_id"],
        }

    def ground(self, project_id, run_id):
        """No model calls. All A/SQL sources and ALL pack text are verified."""
        pid,rid=_project(project_id),_uuid(run_id,"b5_run_id_invalid")
        norm=self._material(pid,rid)
        with self.connection_factory() as conn:
            computed,context_id,normalization_id = self._verify_and_compute(conn,pid,rid,norm)
            existing=_existing(conn,pid,normalization_id,computed)
            if existing is not None:
                return {"grounding_id":existing,"created":False,
                        **computed["report"],"provider_calls":0}
            report=computed["report"]
            ident=conn.execute(
                """INSERT INTO knowledge_fact_grounding_batches(
                    project_id,normalization_id,run_id,batch_id,context_id,
                    input_sha256,report_sha256,total_count,matched_count,
                    rejected_count,skipped_count,report)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (pid,normalization_id,rid,norm["batch_id"],context_id,
                 computed["input_sha256"],computed["report_sha256"],
                 report["total_count"],report["matched_count"],
                 report["rejected_count"],report["skipped_count"],Jsonb(report)),
            ).fetchone()[0]
            for r in computed["receipts"]:
                conn.execute(
                    """INSERT INTO knowledge_fact_grounding_receipts(
                       project_id,grounding_id,normalization_id,run_id,batch_id,
                       pack_index,candidate_index,proposal_id,status,reason_code,
                       receipt_sha256,receipt)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (pid,ident,normalization_id,rid,norm["batch_id"],
                     r["pack_index"],r["candidate_index"],r["proposal_id"],
                     r["status"],r["reason_code"],r["receipt_sha256"],
                     Jsonb(r["receipt"])),
                )
        return {"grounding_id":str(ident),"created":True,
                **computed["report"],"provider_calls":0}

    def materialize(self,project_id,run_id):
        """Historical replay from A, B1/B2, B3 and B4; no external calls."""
        pid,rid=_project(project_id),_uuid(run_id,"b5_run_id_invalid")
        norm=self._material(pid,rid)
        with self.connection_factory() as conn:
            computed,_,normalization_id=self._verify_and_compute(conn,pid,rid,norm)
            ident=_existing(conn,pid,normalization_id,computed)
            if ident is None:
                _fail("b5_grounding_not_found")
        return {"grounding_id":ident,**computed,"provider_calls":0}
