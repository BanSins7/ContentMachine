"""Durable SQG planning from approved RTB Deep V2 without fake V1 rows."""
from __future__ import annotations

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256, canonical
from intelligence.taxonomy_builder.pipeline_repository import _pid, _connect
from intelligence.search_planner.contracts import publication_uuid
from intelligence.search_planner.generation_engine.contracts import validate_generation_config
from intelligence.search_planner.generation_engine.batch_manager import make_batches
from intelligence.search_planner.integration.rtb_v2_pipeline import (
    SOURCE_V2, verified_v2_export, build_v2_contexts,
)

def create_planned_generation_v2(project_id: str, publication_id: str, config_id: str,
                              idempotency_key: str, *, connect=None,
                                 v2_export_supplier=None) -> dict:
    """Persist zero-cost pending plan. Never queue/dispatch without consent.

    Existing matching idempotency key → same ID/status. Conflicting key usage
    is rejected. Authorizations and transitions to queued are Engine-B.
    """
    pid = _pid(project_id)
    pubid = publication_uuid(publication_id)
    cid = publication_uuid(config_id)
    if not isinstance(idempotency_key, str) or not 12 <= len(idempotency_key) <= 128:
        raise PipelineError("Invalid idempotency key", 422)
    signed = verified_v2_export(pid, pubid, supplier=v2_export_supplier)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            # Serialize concurrent generation creation within the project;
            # approved RTB publications and config versions are immutable.
            scoped_project = conn.execute(
                "SELECT id FROM project_registry WHERE id=%s FOR UPDATE", (pid,)
            ).fetchone()
            if scoped_project is None:
                raise PipelineError("Project not found in PostgreSQL", 404)
            config_row = conn.execute(
                """SELECT config,config_sha256 FROM sqg_config_versions
                   WHERE id=%s::uuid AND project_id=%s""", (cid, pid),
            ).fetchone()
            if config_row is None:
                raise PipelineError("Approved Search Planner configuration not found", 404)
            config = validate_generation_config(config_row[0])
            if sha256(config) != config_row[1]:
                raise PipelineError("Generation configuration checksum mismatch", 409)
            publication, contexts = build_v2_contexts(signed, config["search_policy"])
            batches = make_batches(contexts, config)
            fingerprint = sha256({
                "project_id": pid,
                "taxonomy_publication_id": pubid,
                "taxonomy_version": publication["taxonomy_version"],
                "taxonomy_sha256": publication["taxonomy_sha256"],
                "contexts": [sha256(c) for c in contexts],
                "config_sha256": config_row[1],
                "publication_source": SOURCE_V2,
                "rtb_v2_export_sha256": signed["export_sha256"],
            })
            inserted = conn.execute(
                """INSERT INTO sqg_generations
                     (project_id,rtb_v2_publication_id,rtb_v2_run_id,
                      rtb_v2_export_sha256,taxonomy_version,config_id,
                      idempotency_key,input_sha256,taxonomy_sha256,
                      model_id,config_snapshot,status)
                   VALUES (%s,%s::uuid,%s::uuid,%s,%s,%s::uuid,%s,%s,%s,%s,%s::jsonb,
                           'awaiting_authorization')
                   ON CONFLICT (project_id,idempotency_key) DO NOTHING
                   RETURNING id""",
                (pid, pubid, publication["rtb_run_id"], signed["export_sha256"],
                 publication["taxonomy_version"], cid, idempotency_key,
                 fingerprint, publication["taxonomy_sha256"],
                 config["model"]["model_id"], canonical(config)),
            ).fetchone()
            if inserted is None:
                prior = conn.execute(
                    """SELECT id,COALESCE(publication_id,rtb_v2_publication_id),config_id,input_sha256,status
                       FROM sqg_generations WHERE project_id=%s AND idempotency_key=%s""",
                    (pid, idempotency_key),
                ).fetchone()
                if prior is None or str(prior[1]) != pubid or str(prior[2]) != cid or prior[3] != fingerprint:
                    raise PipelineError("Idempotency key reused for a different generation input", 409)
                return {
                    "generation_id": str(prior[0]),
                    "project_id": pid,
                    "taxonomy_publication_id": pubid,
                    "input_sha256": fingerprint,
                    "status": prior[4],
                    "reused": True,
                }

            generation_id = str(inserted[0])
            for context in contexts:
                conn.execute(
                    """INSERT INTO sqg_contexts
                       (generation_id,hypothesis_id,direction_id,branch_id,context,context_sha256)
                       VALUES (%s::uuid,%s,%s,%s,%s::jsonb,%s)""",
                    (generation_id, context["hypothesis"]["id"], context["direction"]["id"],
                     context["branch"]["id"] if context["branch"] else None,
                     canonical(context), sha256(context)),
                )
            for batch in batches:
                conn.execute(
                    """INSERT INTO sqg_batches
                       (generation_id,batch_index,direction_id,hypothesis_ids,input_sha256,
                        estimated_input_tokens,output_token_cap,status)
                       VALUES (%s::uuid,%s,%s,%s::jsonb,%s,%s,%s,'pending')""",
                    (generation_id, batch["batch_index"], batch["direction_id"],
                     canonical(batch["hypothesis_ids"]), batch["input_sha256"],
                     batch["estimated_input_tokens"], batch["output_token_cap"]),
                )
            conn.execute(
                """INSERT INTO sqg_events
                   (project_id,generation_id,event_kind,payload)
                   VALUES (%s,%s::uuid,'generation_created',%s::jsonb)""",
                (pid, generation_id, canonical({
                    "hypothesis_count": len(contexts),
                    "batch_count": len(batches),
                    "spent_usd": "0",
                    "authorized": False,
                    "publication_source": SOURCE_V2,
                    "rtb_v2_export_sha256": signed["export_sha256"],
                })),
            )
            return {
                "generation_id": generation_id,
                "project_id": pid,
                "taxonomy_publication_id": pubid,
                "input_sha256": fingerprint,
                "status": "awaiting_authorization",
                "hypothesis_count": len(contexts),
                "batch_count": len(batches),
                "paid_calls": 0,
                "publication_source": SOURCE_V2,
                "rtb_v2_export_sha256": signed["export_sha256"],
                "reused": False,
            }


