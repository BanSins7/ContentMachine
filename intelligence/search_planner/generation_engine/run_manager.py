"""Transactional config registration and read-only RTB→planned Generation Run.

Stage Engine-A/B planning only: no authorizations, no quotes, no worker,
no OpenRouter calls and no money movement. All Run creation is ONE DB
transaction containing verified immutable input, contexts, batches and event.
"""
from __future__ import annotations

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256, canonical
from intelligence.taxonomy_builder.pipeline_repository import _pid, _connect
from intelligence.search_planner.contracts import publication_uuid
from intelligence.search_planner.input_loader import read_published_snapshot
from intelligence.search_planner.context_builder import build_hypothesis_contexts

from .batch_manager import make_batches
from .contracts import validate_generation_config


def register_config_version(project_id: str, config: dict, *, connect=None) -> dict:
    """Pin an existing OpenRouter catalog snapshot; NO financial authorization.

    Caller is the trusted server-side owner/config approval path, NOT raw HTTP.
    This does not start any work. Catalog capabilities are checked again before
    any future paid dispatch, as snapshots can become stale.
    """
    pid = _pid(project_id)
    verified = validate_generation_config(config)
    model = verified["model"]
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            row = conn.execute(
                """SELECT model_id,catalog_sha256,capabilities
                   FROM tb_model_snapshots WHERE id=%s""",
                (model["catalog_snapshot_id"],),
            ).fetchone()
            if row is None or row[0] != model["model_id"] or row[1] != model["model_catalog_sha256"]:
                raise PipelineError("OpenRouter model snapshot does not match pinned configuration", 409)
            supported = row[2].get("supported_parameters") if isinstance(row[2], dict) else None
            if not isinstance(supported, list) or "response_format" not in supported:
                raise PipelineError("Model snapshot lacks verified structured output support", 409)
            # Safe for concurrent owner actions on same project.
            scoped_project = conn.execute(
                "SELECT id FROM project_registry WHERE id=%s FOR UPDATE", (pid,)
            ).fetchone()
            if scoped_project is None:
                raise PipelineError("Project not found in PostgreSQL", 404)
            # Same configuration can be submitted twice by a browser or after
            # a lost response. The project row lock serializes concurrent
            # submissions. Reuse the original immutable config ID/revision
            # instead of making a second cost/config authority.
            prior = conn.execute(
                """SELECT id,revision FROM sqg_config_versions
                   WHERE project_id=%s AND config_sha256=%s
                   ORDER BY revision DESC LIMIT 1""",
                (pid,sha256(verified)),
            ).fetchone()
            if prior is not None:
                return {
                    "config_id":str(prior[0]), "project_id":pid,
                    "revision":prior[1],"config_sha256":sha256(verified),
                    "status":"approved_config_no_spending_consent",
                    "reused":True,
                }
            revision = conn.execute(
                """SELECT COALESCE(MAX(revision),0)+1
                     FROM sqg_config_versions WHERE project_id=%s""",
                (pid,),
            ).fetchone()[0]
            saved = conn.execute(
                """INSERT INTO sqg_config_versions
                     (project_id,revision,config,config_sha256,model_catalog_snapshot_id,
                      prompt_sha256,raw_schema_sha256)
                   VALUES (%s,%s,%s::jsonb,%s,%s,%s,%s)
                   RETURNING id""",
                (pid, revision, canonical(verified), sha256(verified),
                 model["catalog_snapshot_id"], model["prompt_sha256"], model["raw_schema_sha256"]),
            ).fetchone()
            return {
                "config_id": str(saved[0]),
                "project_id": pid,
                "revision": revision,
                "config_sha256": sha256(verified),
                "status": "approved_config_no_spending_consent",
            }


def create_planned_generation(project_id: str, publication_id: str, config_id: str,
                              idempotency_key: str, *, connect=None) -> dict:
    """Persist zero-cost pending plan. Never queue/dispatch without consent.

    Existing matching idempotency key → same ID/status. Conflicting key usage
    is rejected. Authorizations and transitions to queued are Engine-B.
    """
    pid = _pid(project_id)
    pubid = publication_uuid(publication_id)
    cid = publication_uuid(config_id)
    if not isinstance(idempotency_key, str) or not 12 <= len(idempotency_key) <= 128:
        raise PipelineError("Invalid idempotency key", 422)
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
            publication = read_published_snapshot(conn, pid, pubid)
            config_row = conn.execute(
                """SELECT config,config_sha256 FROM sqg_config_versions
                   WHERE id=%s::uuid AND project_id=%s""", (cid, pid),
            ).fetchone()
            if config_row is None:
                raise PipelineError("Approved Search Planner configuration not found", 404)
            config = validate_generation_config(config_row[0])
            if sha256(config) != config_row[1]:
                raise PipelineError("Generation configuration checksum mismatch", 409)
            contexts = build_hypothesis_contexts(publication, config["search_policy"])
            batches = make_batches(contexts, config)
            fingerprint = sha256({
                "project_id": pid,
                "taxonomy_publication_id": pubid,
                "taxonomy_version": publication["taxonomy_version"],
                "taxonomy_sha256": publication["taxonomy_sha256"],
                "contexts": [sha256(c) for c in contexts],
                "config_sha256": config_row[1],
            })
            inserted = conn.execute(
                """INSERT INTO sqg_generations
                     (project_id,rtb_run_id,publication_id,taxonomy_version,
                      config_id,idempotency_key,input_sha256,taxonomy_sha256,
                      model_id,config_snapshot,status)
                   VALUES (%s,%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s::jsonb,
                           'awaiting_authorization')
                   ON CONFLICT (project_id,idempotency_key) DO NOTHING
                   RETURNING id""",
                (pid, publication["rtb_run_id"], pubid, publication["taxonomy_version"],
                 cid, idempotency_key, fingerprint, publication["taxonomy_sha256"],
                 config["model"]["model_id"], canonical(config)),
            ).fetchone()
            if inserted is None:
                prior = conn.execute(
                    """SELECT id,publication_id,config_id,input_sha256,status
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
                "reused": False,
            }


def get_planned_generation(project_id: str, generation_id: str, *, connect=None) -> dict:
    """Read-only summary. GET MUST NEVER create requests, authorizations or batches."""
    pid = _pid(project_id)
    gid = publication_uuid(generation_id)
    factory = connect or _connect
    with factory() as conn:
        row = conn.execute(
            """SELECT g.id,COALESCE(g.publication_id,g.rtb_v2_publication_id),g.input_sha256,g.status,
                      (SELECT COUNT(*) FROM sqg_contexts c WHERE c.generation_id=g.id),
                      (SELECT COUNT(*) FROM sqg_batches b WHERE b.generation_id=g.id)
               FROM sqg_generations g WHERE g.id=%s::uuid AND g.project_id=%s""",
            (gid, pid),
        ).fetchone()
        if row is None:
            raise PipelineError("Search generation not found", 404)
        return {
            "generation_id": str(row[0]),
            "taxonomy_publication_id": str(row[1]),
            "input_sha256": row[2],
            "status": row[3],
            "hypothesis_count": row[4],
            "batch_count": row[5],
        }
