"""B Stage08 — deterministic, strictly offline Python normalization.

Consumes ONLY persisted, completed, billed Stage07 facts-v3 proposal drafts.
Rebinds each source/chunk/quote to the independently replayed B2 EvidencePack.
Never invokes a model, adds facts, semantically rewrites, grounds B5 evidence,
approves reviews, or touches A VideoIntelligence.
"""
from __future__ import annotations

import re
from collections import Counter

from psycopg.types.json import Jsonb

from intelligence.project_sources.context_builder import sha as canonical_sha
from .fact_contracts import MODEL_ID, PROMPT_VERSION, _source_rows, _validator
from .fact_request_repository import FactRequestRepository
from .intake import IntakeError, _project, _uuid

NORMALIZER_VERSION = "b4-python/1.0.0"
SCHEMA_VERSION = "knowledge-normalization/v1"
PROPOSAL_SCHEMA = "knowledge-fact-proposal/v1"
RAW_FIELDS = (
    "kind", "text", "source_chunk_id", "source_quote", "quote_start", "quote_end"
)


def _error(code):
    raise IntakeError(code)


def _space_only(value):
    """No Unicode NFKC, stemming, casefolding, spellcheck or number changes."""
    return re.sub(r"\s+", " ", value).strip()


def _candidate(candidate, source_rows, *, run_id, pack_index,
               candidate_index, pack_manifest, blueprint, request_sha):
    """Return one valid, source-bound proposal OR a quarantined descriptor.

    An invalid source ID, fabricated server role, unexpected extra key or
    forged quote is never repaired by guessing. The raw candidate remains in
    the Stage07 attempt; quarantine retains only SHA and reason.
    """
    raw_digest = canonical_sha(candidate)
    proposal_id = canonical_sha({
        "normalizer_version": NORMALIZER_VERSION, "run_id": run_id,
        "pack_index": pack_index, "candidate_index": candidate_index,
        "raw_candidate_sha256": raw_digest,
    })
    result = {
        "pack_index": pack_index, "candidate_index": candidate_index,
        "proposal_id": proposal_id, "raw_candidate_sha256": raw_digest,
    }

    def quarantine(reason):
        return {**result, "status": "quarantined",
                "reason_code": reason, "normalized": None}

    if not isinstance(candidate, dict):
        return quarantine("b4_candidate_not_object")
    raw = {field: candidate.get(field) for field in RAW_FIELDS}
    if next(_validator().iter_errors({"facts": [raw]}), None):
        return quarantine("b4_facts_v3_schema_invalid")
    chunk_id = raw["source_chunk_id"]
    source = source_rows.get(chunk_id)
    if source is None:
        return quarantine("b4_chunk_not_pinned")
    start, end, quote = raw["quote_start"], raw["quote_end"], raw["source_quote"]
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(source["text"])):
        return quarantine("b4_quote_offsets_invalid")
    if source["text"][start:end] != quote:
        return quarantine("b4_quote_not_verbatim")

    # Reconstruct the complete Stage06 server-owned metadata. In particular,
    # NEVER accept a candidate-supplied subject/source identity as authority.
    expected = {
        "candidate_index": candidate_index,
        "kind": raw["kind"], "text": raw["text"],
        "source_quote": quote, "quote_start": start, "quote_end": end,
        "source_chunk_id": chunk_id,
        "fragment_id": source.get("original_fragment_id", source.get("fragment_id")),
        "source_id": source["source_id"],
        "source_version_id": source["source_version_id"],
        "output_id": source["output_id"],
        "subject_scope": source["subject_scope"],
        "source_role": source["source_role"],
        "subject_id": source["subject_id"],
        "trust_label": source["trust_label"],
        "source_locator": source["locator"],
        "evidence_ids": list(source.get("evidence_ids", [])),
        "model_proposal_only": True, "grounding_status": "pending_b5",
        "human_review_status": "unreviewed",
    }
    if candidate != expected:
        return quarantine("b4_server_provenance_mismatch")
    normalized_text = _space_only(raw["text"])
    if not normalized_text or "\x00" in normalized_text or any(
        0xD800 <= ord(ch) <= 0xDFFF for ch in normalized_text
    ):
        return quarantine("b4_normalized_text_invalid")
    normalized = {
        "schema_version": PROPOSAL_SCHEMA,
        "proposal_id": proposal_id,
        "raw_candidate_sha256": raw_digest,
        "pack_index": pack_index, "candidate_index": candidate_index,
        "kind": raw["kind"], "original_text": raw["text"],
        "text": normalized_text,
        "source_quote": quote, "quote_start": start, "quote_end": end,
        "source_chunk_id": chunk_id,
        "fragment_id": expected["fragment_id"],
        "source_id": expected["source_id"],
        "source_version_id": expected["source_version_id"],
        "output_id": expected["output_id"],
        "source_role": expected["source_role"],
        "subject_scope": expected["subject_scope"],
        "subject_id": expected["subject_id"],
        "source_locator": expected["source_locator"],
        "trust_label": expected["trust_label"],
        "evidence_ids": expected["evidence_ids"],
        "model_id": blueprint["model_id"],
        "prompt_version": blueprint["prompt_version"],
        "request_sha256": request_sha,
        "pack_manifest_sha256": canonical_sha(pack_manifest),
        "pack_payload_sha256": pack_manifest["payload_sha256"],
        "normalizer_version": NORMALIZER_VERSION,
        "grounding_status": "pending_b5",
        "human_review_status": "unreviewed",
        # Ambiguous roles are NOT automatically classified as own_product.
        "requires_subject_review": (
            expected["subject_scope"] == "other"
            or (expected["subject_scope"] == "competitor" and not expected["subject_id"])
        ),
        "model_proposal_only": True,
    }
    return {**result, "status": "pending_grounding",
            "reason_code": None, "normalized": normalized}


def normalize_drafts(*, run_id, batch_id, blueprint, source_packs, attempts):
    """Pure, reproducible B4 operation; no SQL, networking or LLM.

    Stage07 completed only after strict initial schema/quote validation.
    Bad individual stored proposals are quarantined *without dropping their
    positions*. Invalid attempt/pack manifests block the entire operation.
    """
    run_id = _uuid(run_id, "b4_run_id_invalid")
    batch_id = _uuid(batch_id, "b4_batch_id_invalid")
    parts = blueprint.get("parts") if isinstance(blueprint, dict) else None
    if (not isinstance(parts, list) or not parts or len(parts) > 256
            or len(parts) != len(source_packs) or len(parts) != len(attempts)
            or blueprint.get("schema_version") != "facts-v3-request-batch/v1"
            or blueprint.get("model_id") != MODEL_ID
            or blueprint.get("prompt_version") != PROMPT_VERSION):
        _error("b4_batch_incomplete")
    rows, groups, inputs = [], [], []
    for idx, (part, source_pair, attempt) in enumerate(zip(parts, source_packs, attempts)):
        if not isinstance(attempt, dict) or not isinstance(part, dict):
            _error("b4_attempt_registry_invalid")
        manifest, payload = source_pair
        if (attempt.get("pack_index") != idx
                or attempt.get("status") != "completed"
                or attempt.get("request_sha256") != part.get("request_sha256")
                or part.get("pack_payload_sha256") != manifest.get("payload_sha256")
                or part.get("pack_manifest_sha256") != canonical_sha(manifest)
                or attempt.get("charged_usd") is None
                or not isinstance(attempt.get("usage"), dict)
                or not isinstance(attempt.get("draft_candidates"), dict)):
            _error("b4_attempt_integrity")
        draft = attempt["draft_candidates"]
        candidates = draft.get("candidates")
        if (draft.get("schema_version") != "facts-v3-draft/v1"
                or draft.get("pack_payload_sha256") != manifest["payload_sha256"]
                or draft.get("requires_grounding_and_human_review") is not True
                or draft.get("provider_calls") != 0
                or not isinstance(candidates, list) or len(candidates) > 80
                or type(draft.get("candidate_count")) is not int
                or draft["candidate_count"] != len(candidates)
                or attempt.get("candidate_count") != len(candidates)
                or draft.get("candidate_limit_reached") is not (len(candidates) == 80)
                or attempt.get("candidate_limit_reached") is not (len(candidates) == 80)):
            _error("b4_draft_integrity")
        try:
            sources = _source_rows(manifest, payload)
        except (ValueError, TypeError, KeyError) as exc:
            raise IntakeError("b4_original_pack_invalid") from exc
        pack_rows = [
            _candidate(candidate, sources, run_id=run_id,
                       pack_index=idx, candidate_index=j,
                       pack_manifest=manifest, blueprint=blueprint,
                       request_sha=part["request_sha256"])
            for j, candidate in enumerate(candidates)
        ]
        rows.extend(pack_rows)
        groups.append({
            "pack_index": idx,
            "candidate_count": len(candidates),
            "pending_grounding_count": sum(x["status"] == "pending_grounding" for x in pack_rows),
            "quarantined_count": sum(x["status"] == "quarantined" for x in pack_rows),
            "candidate_limit_reached": len(candidates) == 80,
        })
        inputs.append({
            "pack_index": idx, "request_sha256": part["request_sha256"],
            "pack_payload_sha256": manifest["payload_sha256"],
            "attempt_status": attempt["status"],
            "charged_usd": str(attempt["charged_usd"]),
            "usage_sha256": canonical_sha(attempt["usage"]),
            "draft_sha256": canonical_sha(draft),
        })
    fingerprint = canonical_sha({
        "schema_version": SCHEMA_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "run_id": run_id, "batch_id": batch_id,
        "batch_sha256": canonical_sha(blueprint),
        "parts": inputs,
    })
    rejected = Counter(x["reason_code"] for x in rows if x["status"] == "quarantined")
    good = sum(x["status"] == "pending_grounding" for x in rows)
    report = {
        "schema_version": SCHEMA_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "run_id": run_id, "batch_id": batch_id,
        "source_batch_sha256": canonical_sha(blueprint),
        "input_sha256": fingerprint,
        "proposal_count": len(rows),
        "normalized_count": good,
        "quarantined_count": len(rows) - good,
        "quarantine_reasons": dict(sorted(rejected.items())),
        "packs": groups,
        "status": "normalized_with_quarantine" if good != len(rows) else "normalized",
        "grounding_status": "pending_b5",
        "human_review_status": "unreviewed",
        "all_packs_accounted_for": True,
        "semantic_completeness_proven": False,
        "video_mimo_calls": 0,
        "provider_calls": 0,
    }
    return {"report": report, "report_sha256": canonical_sha(report),
            "rows": rows, "input_sha256": fingerprint, "provider_calls": 0}


def _attempts(conn, pid, run_id, batch_id):
    records = conn.execute(
        """SELECT pack_index,status,request_sha256,charged_usd,usage,
                  draft_candidates,candidate_count,candidate_limit_reached
             FROM knowledge_fact_execution_attempts
            WHERE project_id=%s AND run_id=%s AND batch_id=%s
            ORDER BY pack_index FOR SHARE""",
        (pid, run_id, batch_id),
    ).fetchall()
    return [{
        "pack_index": r[0], "status": r[1],
        "request_sha256": r[2], "charged_usd": r[3], "usage": r[4],
        "draft_candidates": r[5], "candidate_count": r[6],
        "candidate_limit_reached": r[7],
    } for r in records]


def _saved_rows(conn, pid, run_id, normalization_id):
    stored = conn.execute(
        """SELECT pack_index,candidate_index,proposal_id,raw_candidate_sha256,
                  status,reason_code,normalized
             FROM knowledge_fact_normalized_proposals
            WHERE project_id=%s AND run_id=%s AND normalization_id=%s
            ORDER BY pack_index,candidate_index""",
        (pid, run_id, normalization_id),
    ).fetchall()
    return [{
        "pack_index": r[0], "candidate_index": r[1], "proposal_id": r[2],
        "raw_candidate_sha256": r[3], "status": r[4],
        "reason_code": r[5], "normalized": r[6],
    } for r in stored]


def _compare_saved(conn, pid, run_id, batch_id, calculated):
    row = conn.execute(
        """SELECT id,input_sha256,report_sha256,proposal_count,
                  normalized_count,quarantined_count,report
             FROM knowledge_fact_normalization_batches
            WHERE project_id=%s AND run_id=%s AND batch_id=%s""",
        (pid, run_id, batch_id),
    ).fetchone()
    if row is None:
        return None
    report = calculated["report"]
    if (row[1] != calculated["input_sha256"]
            or row[2] != calculated["report_sha256"]
            or row[3] != report["proposal_count"]
            or row[4] != report["normalized_count"]
            or row[5] != report["quarantined_count"]
            or row[6] != report
            or canonical_sha(row[6]) != row[2]
            or _saved_rows(conn, pid, run_id, row[0]) != calculated["rows"]):
        _error("b4_immutable_replay_mismatch")
    return str(row[0])


class FactNormalizationRepository:
    """One append-only normalization snapshot per completed B3 execution run."""

    def __init__(self, connection_factory=None, *, verify_local_files=True):
        self.requests = FactRequestRepository(
            connection_factory, verify_local_files=verify_local_files)
        self.connection_factory = self.requests.connection_factory

    def _compute(self, conn, pid, run_id, info):
        run = conn.execute(
            """SELECT batch_id,status,next_pack_index,reserved_usd
               FROM knowledge_fact_execution_runs
              WHERE project_id=%s AND id=%s FOR UPDATE""",
            (pid, run_id),
        ).fetchone()
        if run is None:
            _error("b4_execution_run_not_found")
        batch_id = str(run[0])
        if batch_id != info["batch_id"]:
            _error("b4_execution_batch_mismatch")
        if (run[1] != "ready_for_normalization"
                or run[2] != len(info["source_packs"]) or run[3] != 0):
            _error("b4_run_not_complete")
        return normalize_drafts(
            run_id=run_id, batch_id=batch_id,
            blueprint=info["blueprint"], source_packs=info["source_packs"],
            attempts=_attempts(conn, pid, run_id, batch_id),
        )

    def normalize(self, project_id, run_id):
        """Transactional persist-or-verify. No paid calls, no implicit B5."""
        pid = _project(project_id)
        rid = _uuid(run_id, "b4_run_id_invalid")
        with self.connection_factory() as conn:
            scope = conn.execute(
                """SELECT batch_id FROM knowledge_fact_execution_runs
                   WHERE project_id=%s AND id=%s""", (pid, rid),
            ).fetchone()
            if scope is None:
                _error("b4_execution_run_not_found")
            bid = str(scope[0])
        # Historical A source content must be immutably replayable, not the
        # latest profile. This read never dispatches or calls prepare().
        info = self.requests.materialize(pid, bid)
        info["batch_id"] = bid
        with self.connection_factory() as conn:
            calculated = self._compute(conn, pid, rid, info)
            existing = _compare_saved(conn, pid, rid, bid, calculated)
            if existing is not None:
                return {"normalization_id": existing, "created": False,
                        **calculated["report"], "provider_calls": 0}
            report = calculated["report"]
            record_id = conn.execute(
                """INSERT INTO knowledge_fact_normalization_batches(
                   project_id,run_id,batch_id,normalizer_version,input_sha256,
                   report_sha256,proposal_count,normalized_count,quarantined_count,report)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (pid, rid, bid, NORMALIZER_VERSION, calculated["input_sha256"],
                 calculated["report_sha256"], report["proposal_count"],
                 report["normalized_count"], report["quarantined_count"], Jsonb(report)),
            ).fetchone()[0]
            for item in calculated["rows"]:
                conn.execute(
                    """INSERT INTO knowledge_fact_normalized_proposals(
                       project_id,normalization_id,run_id,batch_id,pack_index,
                       candidate_index,proposal_id,raw_candidate_sha256,status,
                       reason_code,normalized)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (pid, record_id, rid, bid, item["pack_index"],
                     item["candidate_index"], item["proposal_id"],
                     item["raw_candidate_sha256"], item["status"],
                     item["reason_code"],
                     Jsonb(item["normalized"]) if item["normalized"] is not None else None),
                )
        return {"normalization_id": str(record_id), "created": True,
                **calculated["report"], "provider_calls": 0}

    def materialize(self, project_id, run_id):
        """Historical integrity replay, including each immutable proposal row."""
        pid = _project(project_id)
        rid = _uuid(run_id, "b4_run_id_invalid")
        with self.connection_factory() as conn:
            scope = conn.execute(
                """SELECT batch_id FROM knowledge_fact_execution_runs
                   WHERE project_id=%s AND id=%s""", (pid, rid),
            ).fetchone()
            if scope is None:
                _error("b4_execution_run_not_found")
            bid = str(scope[0])
        info = self.requests.materialize(pid, bid)
        info["batch_id"] = bid
        with self.connection_factory() as conn:
            calculated = self._compute(conn, pid, rid, info)
            ident = _compare_saved(conn, pid, rid, bid, calculated)
            if ident is None:
                _error("b4_normalization_not_found")
        return {"normalization_id": ident, **calculated, "provider_calls": 0}
