"""B Stage06 immutable facts-v3 *draft request* registry, never dispatches.

Every pack is reconstructed from A/B1, and every provider request is serialized
and cost-preflighted offline. Stages07+ own consent, execution and paid-attempts.
No raw research text or API keys are persisted in request blueprint tables.
"""
from __future__ import annotations

from decimal import Decimal

from psycopg.types.json import Jsonb

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.taxonomy_builder.catalog import latest_verified
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from .fact_contracts import (
    MODEL_ID, PROMPT_VERSION, SCHEMA_SHA256, SYSTEM_PROMPT,
    prepare_request, validate_candidate_response,
)
from .full_pack_repository import FullContextPackRepository, _load
from .multi_pack_repository import MultiPackRepository
from .repository import KnowledgeInputRepository
from .intake import IntakeError, _project, _uuid


def _origin(full_pack_id, multi_plan_id):
    if (full_pack_id is None) == (multi_plan_id is None):
        raise IntakeError("facts_select_one_pack_type")
    return ("full", _uuid(full_pack_id, "invalid_full_pack_id")) if full_pack_id is not None else (
        "multi", _uuid(multi_plan_id, "invalid_multi_plan_id"))


def _pack_set(reader, kind, pid, ident):
    if kind == "full":
        data = reader.full.materialize(pid, ident)
        return str(data["bundle_id"]), [(data["manifest"], data["payload"])], None
    data = reader.multi.materialize(pid, ident)
    return str(data["bundle_id"]), data["parts"], data["plan"]


def _snapshot(conn, model_id, version, digest):
    row = conn.execute(
        """SELECT catalog_sha256,capabilities,pricing FROM tb_model_snapshots
           WHERE id=%s AND model_id=%s""", (version, model_id),
    ).fetchone()
    if row is None or row[0] != digest:
        raise IntakeError("facts_model_snapshot_missing")
    return {"model_id": model_id, "catalog_version_id": version,
            "catalog_sha256": row[0], "capabilities": row[1], "pricing": row[2]}


def _blueprint(bundle_id, kind, source_id, manifests, plan, snap):
    parts = []
    total = Decimal("0")
    for index, (manifest, payload) in enumerate(manifests):
        if kind == "multi" and manifest["pack_index"] != index:
            raise IntakeError("facts_pack_order_invalid")
        draft = prepare_request(manifest, payload, snap, multi_plan=plan)
        total += Decimal(draft["indicative_reservation_usd"])
        parts.append({
            "pack_index": index,
            "request_sha256": draft["request_sha256"],
            "request_bytes": draft["request_bytes"],
            "pack_payload_sha256": draft["pack_payload_sha256"],
            "pack_manifest_sha256": draft["pack_manifest_sha256"],
            "source_chunk_count": len(draft["source_chunk_ids"]),
            "quoted_reservation_usd": draft["indicative_reservation_usd"],
        })
    if not parts or len(parts) > 256:
        raise IntakeError("facts_invalid_batch_size")
    import hashlib
    batch = {
        "schema_version": "facts-v3-request-batch/v1",
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "schema_sha256": SCHEMA_SHA256,
        "bundle_id": bundle_id,
        "pack_kind": kind,
        "source_pack_id": source_id,
        "model_id": MODEL_ID,
        "catalog_version_id": snap["catalog_version_id"],
        "catalog_sha256": snap["catalog_sha256"],
        "request_count": len(parts),
        "parts": parts,
        "total_indicative_reservation_usd": str(total),
        "requires_new_cost_consent": True,
        "tokenizer_check": "conservative_final_request_bytes_preflight",
        "provider_calls": 0,
        "execution_status": "not_dispatched",
    }
    return batch


class FactRequestRepository:
    def __init__(self, connection_factory=None, *, verify_local_files=True):
        bundles = KnowledgeInputRepository(
            connection_factory, verify_local_files=verify_local_files)
        self.connection_factory = bundles.connection_factory
        self.resolver = bundles.resolver
        self.full = FullContextPackRepository(
            self.connection_factory, verify_local_files=verify_local_files)
        self.multi = MultiPackRepository(
            self.connection_factory, verify_local_files=verify_local_files)

    def prepare(self, project_id, *, full_pack_id=None, multi_plan_id=None):
        """Persist an immutable UNSENT request blueprint, never the prompt text."""
        pid = _project(project_id)
        kind, source_id = _origin(full_pack_id, multi_plan_id)
        bundle_id, manifests, plan = _pack_set(self, kind, pid, source_id)
        with self.connection_factory() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            if conn.execute(
                "SELECT id FROM project_registry WHERE id=%s FOR UPDATE", (pid,),
            ).fetchone() is None:
                raise IntakeError("project_not_found")
            # Freshness guard AFTER historical pack materialization prevents
            # starting a new paid lineage against stale A profile/source.
            bundle, envelope = _load(
                conn, pid, bundle_id, self.resolver, require_current=True)
            if (kind == "full" and manifests[0][0]["bundle_sha256"] != canonical_sha(bundle)) or (
                kind == "multi" and plan["bundle_sha256"] != canonical_sha(bundle)
            ):
                raise IntakeError("facts_bundle_pin_mismatch")
            try:
                verified = latest_verified(conn, {MODEL_ID})[MODEL_ID]
            except (PipelineError, KeyError) as exc:
                raise IntakeError("facts_catalog_not_verified") from exc
            model_pin = manifests[0][0] if kind == "full" else plan
            if (verified["catalog_version_id"] != model_pin["catalog_version_id"] or
                verified["catalog_sha256"] != model_pin["catalog_sha256"]):
                raise IntakeError("facts_model_catalog_stale")
            batch = _blueprint(bundle_id, kind, source_id, manifests, plan, verified)
            digest = canonical_sha(batch)
            source_col = "full_pack_id" if kind == "full" else "multi_plan_id"
            existing = conn.execute(
                f"""SELECT id,batch_sha256,batch FROM knowledge_fact_request_batches
                   WHERE project_id=%s AND {source_col}=%s
                     AND prompt_sha256=%s AND schema_sha256=%s""",
                (pid, source_id, batch["prompt_sha256"], SCHEMA_SHA256),
            ).fetchone()
            if existing:
                batch_id, stored_hash, record = existing
                if (digest != stored_hash or record != batch
                    or canonical_sha(record) != stored_hash):
                    raise IntakeError("facts_request_idempotency_conflict")
                _verify_parts(conn, pid, str(batch_id), bundle_id, batch)
                return {"batch_id": str(batch_id), "created": False,
                        "batch_sha256": digest,
                        "request_count": len(batch["parts"]),
                        "indicative_reservation_usd": batch["total_indicative_reservation_usd"],
                        "provider_calls": 0, "execution_status": "not_dispatched"}
            batch_id = conn.execute(
                """INSERT INTO knowledge_fact_request_batches(
                    project_id,bundle_id,full_pack_id,multi_plan_id,
                    model_snapshot_id,model_id,catalog_sha256,prompt_version,
                    prompt_sha256,schema_sha256,batch_sha256,request_count,
                    reserved_estimate_usd,batch)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                  RETURNING id""",
                (pid, bundle_id,
                 source_id if kind == "full" else None,
                 source_id if kind == "multi" else None,
                 verified["catalog_version_id"], MODEL_ID,
                 verified["catalog_sha256"], PROMPT_VERSION,
                 batch["prompt_sha256"], batch["schema_sha256"], digest,
                 len(batch["parts"]), batch["total_indicative_reservation_usd"],
                 Jsonb(batch)),
            ).fetchone()[0]
            for item in batch["parts"]:
                conn.execute(
                    """INSERT INTO knowledge_fact_request_parts(
                        project_id,batch_id,bundle_id,pack_index,request_sha256,
                        request_bytes,pack_payload_sha256,pack_manifest_sha256,
                        quoted_reservation_usd,source_chunk_count)
                     VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (pid, batch_id, bundle_id, item["pack_index"],
                     item["request_sha256"], item["request_bytes"],
                     item["pack_payload_sha256"], item["pack_manifest_sha256"],
                     item["quoted_reservation_usd"], item["source_chunk_count"]),
                )
            return {"batch_id": str(batch_id), "created": True,
                    "batch_sha256": digest,
                    "request_count": len(batch["parts"]),
                    "indicative_reservation_usd": batch["total_indicative_reservation_usd"],
                    "provider_calls": 0, "execution_status": "not_dispatched"}

    def materialize(self, project_id, batch_id):
        """Read-only historical replay, plus exact request and SQL hash checks."""
        pid = _project(project_id)
        ident = _uuid(batch_id, "invalid_batch_id")
        with self.connection_factory() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = conn.execute(
                """SELECT bundle_id,full_pack_id,multi_plan_id,model_snapshot_id,
                          model_id,catalog_sha256,prompt_version,prompt_sha256,
                          schema_sha256,batch_sha256,request_count,
                          reserved_estimate_usd,batch
                    FROM knowledge_fact_request_batches
                   WHERE project_id=%s AND id=%s""",
                (pid, ident),
            ).fetchone()
            if not row:
                raise IntakeError("facts_request_batch_not_found")
            (bid, full_id, multi_id, model_version, model_id, catalog_sha, prompt,
             prompt_hash, schema_hash, batch_hash, count, cost, record) = row
            if ((full_id is None) == (multi_id is None) or
                model_id != MODEL_ID or prompt != PROMPT_VERSION or
                schema_hash != SCHEMA_SHA256 or
                canonical_sha(record) != batch_hash or record.get("bundle_id") != str(bid) or
                record.get("catalog_version_id") != model_version or
                record.get("catalog_sha256") != catalog_sha or
                record.get("request_count") != count or
                record.get("prompt_sha256") != prompt_hash or
                record.get("schema_sha256") != schema_hash or
                Decimal(record["total_indicative_reservation_usd"]) != cost):
                raise IntakeError("facts_request_registry_integrity")
            snap = _snapshot(conn, model_id, model_version, catalog_sha)
            _verify_parts(conn, pid, ident, str(bid), record)
        kind = "full" if full_id is not None else "multi"
        source_id = str(full_id or multi_id)
        pinned_bundle, manifests, plan = _pack_set(self, kind, pid, source_id)
        if pinned_bundle != str(bid):
            raise IntakeError("facts_request_bundle_mismatch")
        rebuilt = _blueprint(str(bid), kind, source_id, manifests, plan, snap)
        if rebuilt != record or canonical_sha(rebuilt) != batch_hash:
            raise IntakeError("facts_request_replay_mismatch")
        requests = [
            prepare_request(manifest, payload, snap, multi_plan=plan)
            for manifest, payload in manifests
        ]
        return {"batch_id": ident, "batch_sha256": batch_hash,
                "blueprint": record, "parts": requests,
                "source_packs": manifests, "model_snapshot": snap,
                "multi_plan": plan, "provider_calls": 0,
                "execution_status": "not_dispatched"}

    def inspect_response(self, project_id, batch_id, part_index, response):
        """Offline simulated response: no provider calls or fact publication."""
        info = self.materialize(project_id, batch_id)
        if (type(part_index) is not int or part_index < 0 or
            part_index >= len(info["source_packs"])):
            raise IntakeError("facts_part_index_invalid")
        manifest, payload = info["source_packs"][part_index]
        result = validate_candidate_response(response, manifest, payload)
        result["batch_id"] = str(info["batch_id"])
        result["pack_index"] = part_index
        result["model_id"] = MODEL_ID
        return result


def _verify_parts(conn, pid, batch_id, bid, batch):
    rows = conn.execute(
        """SELECT pack_index,request_sha256,request_bytes,pack_payload_sha256,
                  pack_manifest_sha256,quoted_reservation_usd,source_chunk_count
             FROM knowledge_fact_request_parts
            WHERE project_id=%s AND batch_id=%s AND bundle_id=%s
            ORDER BY pack_index""",
        (pid, batch_id, bid),
    ).fetchall()
    if len(rows) != len(batch["parts"]):
        raise IntakeError("facts_request_parts_count_mismatch")
    for index, row in enumerate(rows):
        entry = batch["parts"][index]
        if (row[0] != index or row[1] != entry["request_sha256"]
            or row[2] != entry["request_bytes"]
            or row[3] != entry["pack_payload_sha256"]
            or row[4] != entry["pack_manifest_sha256"]
            or row[5] != Decimal(entry["quoted_reservation_usd"])
            or row[6] != entry["source_chunk_count"]):
            raise IntakeError("facts_request_part_integrity")
