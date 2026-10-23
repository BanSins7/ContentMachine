"""Search Planner Stage B: immutable quotes and standalone spending consent.

All operations are project-scoped and explicitly called by trusted server
code. No provider call, worker, hidden retry, or budget inherited from RTB.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, canonical, sha256
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid
from intelligence.search_planner.contracts import publication_uuid

from .contracts import validate_generation_config
from .cost_estimator import approved_budget, estimate_generation
from .model_manager import current_priced_model

QUOTE_TTL = timedelta(minutes=30)


def _now():
    return datetime.now(timezone.utc)


def _run_locked(conn, project_id, generation_id):
    row = conn.execute(
        """SELECT id,project_id,publication_id,config_id,input_sha256,
                  status,quote_id,consent_id,config_snapshot,cancel_requested
           FROM sqg_generations WHERE id=%s::uuid AND project_id=%s FOR UPDATE""",
        (generation_id, project_id),
    ).fetchone()
    if row is None:
        raise PipelineError("Search generation not found in project", 404)
    return {
        "id": str(row[0]), "project_id": row[1], "publication_id": str(row[2]),
        "config_id": str(row[3]), "input_sha256": row[4], "status": row[5],
        "quote_id": str(row[6]) if row[6] else None,
        "consent_id": str(row[7]) if row[7] else None,
        "config": validate_generation_config(row[8]), "cancel_requested": row[9],
    }


def _latest_quote(conn, run):
    return conn.execute(
        """SELECT q.id,q.quote_low_usd,q.quote_expected_usd,q.quote_high_usd,
                  q.pricing_snapshot,q.batch_manifest_sha256,q.expires_at,
                  q.model_catalog_sha256
           FROM sqg_quotes q WHERE q.project_id=%s AND q.publication_id=%s
             AND q.config_id=%s::uuid
             AND q.pricing_snapshot->>'generation_id'=%s
           ORDER BY q.created_at DESC,q.id DESC LIMIT 1""",
        (run["project_id"], run["publication_id"], run["config_id"], run["id"]),
    ).fetchone()


def create_generation_quote(project_id: str, generation_id: str, *,
                            connect=None, now=None) -> dict:
    """Price already persisted batch plan from a fresh verified model catalog.

    The estimate is a *quote*, not an external provider maximum or a receipt.
    Creates a quote only for one exact generation; no consent implied.
    """
    pid = _pid(project_id)
    gid = publication_uuid(generation_id)
    now = _now() if now is None else now
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            run = _run_locked(conn, pid, gid)
            if run["status"] != "awaiting_authorization" or run["cancel_requested"] or run["consent_id"]:
                raise PipelineError("Only pending unauthorized generation may be quoted", 409)
            config = run["config"]
            model = current_priced_model(conn, config)
            batch_rows = conn.execute(
                """SELECT id,input_sha256,estimated_input_tokens,output_token_cap
                   FROM sqg_batches WHERE generation_id=%s::uuid
                   ORDER BY batch_index""", (gid,),
            ).fetchall()
            all_contexts = {
                row[0]: row[1] for row in conn.execute(
                    """SELECT hypothesis_id,context FROM sqg_contexts
                       WHERE generation_id=%s::uuid""", (gid,),
                ).fetchall()
            }
            batch_hypotheses = {
                str(row[0]): row[1] for row in conn.execute(
                    """SELECT id,hypothesis_ids FROM sqg_batches
                       WHERE generation_id=%s::uuid""", (gid,),
                ).fetchall()
            }
            batches = [{
                "id": str(r[0]), "input_sha256": r[1],
                "estimated_input_tokens": r[2], "output_token_cap": r[3],
                "contexts": [all_contexts[hyp_id] for hyp_id in
                             batch_hypotheses[str(r[0])]],
            } for r in batch_rows]
            config_record = conn.execute(
                """SELECT config_sha256 FROM sqg_config_versions
                   WHERE id=%s::uuid AND project_id=%s""",
                (run["config_id"], pid),
            ).fetchone()
            if config_record is None or config_record[0] != sha256(config):
                raise PipelineError("Pinned generation config changed", 409)
            estimate = estimate_generation(
                batches, model, generation_id=gid,
                input_sha256=run["input_sha256"], config_sha256=config_record[0],
                generation_config=config,
            )
            existing = _latest_quote(conn, run)
            if (existing and existing[6] > now and
                    existing[5] == estimate["batch_manifest_sha256"] and
                    existing[7] == model["catalog_sha256"] and
                    existing[4] == estimate["pricing_snapshot"]):
                # Preserve the same quote for duplicate user actions.
                quote_id = str(existing[0])
                reused = True
            else:
                expires_at = now + QUOTE_TTL
                inserted = conn.execute(
                    """INSERT INTO sqg_quotes
                       (project_id,publication_id,config_id,config_sha256,
                        model_catalog_sha256,pricing_snapshot,batch_manifest_sha256,
                        quote_low_usd,quote_expected_usd,quote_high_usd,expires_at)
                       VALUES (%s,%s::uuid,%s::uuid,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)
                       RETURNING id""",
                    (pid, run["publication_id"], run["config_id"], config_record[0],
                     model["catalog_sha256"], canonical(estimate["pricing_snapshot"]),
                     estimate["batch_manifest_sha256"], estimate["low_usd"],
                     estimate["expected_usd"], estimate["high_usd"], expires_at),
                ).fetchone()
                quote_id = str(inserted[0])
                reused = False
            conn.execute(
                """UPDATE sqg_generations SET quote_id=%s::uuid,updated_at=now()
                   WHERE id=%s::uuid AND project_id=%s""",
                (quote_id, gid, pid),
            )
            conn.execute(
                """INSERT INTO sqg_events(project_id,generation_id,event_kind,payload)
                   VALUES (%s,%s::uuid,'quote_created',%s::jsonb)""",
                (pid, gid, canonical({"quote_id": quote_id, "reused": reused,
                                     "high_usd": estimate["high_usd"]})),
            )
            return {
                "quote_id": quote_id, "generation_id": gid, "currency": "USD",
                "low_usd": estimate["low_usd"],
                "expected_usd": estimate["expected_usd"],
                "high_usd": estimate["high_usd"],
                "batch_count": len(batches),
                "reused": reused,
                "assumptions": estimate["pricing_snapshot"]["assumptions"],
            }


def authorize_generation_budget(project_id: str, generation_id: str, quote_id: str,
                                authorized_usd, *, connect=None, now=None) -> dict:
    """Explicit server-side user approval, distinct from RTB consent.

    Passing this function without genuine owner confirmation is a caller
    security bug. It does not dispatch a provider request; worker is absent.
    """
    pid = _pid(project_id)
    gid = publication_uuid(generation_id)
    qid = publication_uuid(quote_id)
    now = _now() if now is None else now
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            run = _run_locked(conn, pid, gid)
            if run["cancel_requested"]:
                raise PipelineError("Search generation has been cancelled", 409)
            if run["consent_id"]:
                existing = conn.execute(
                    """SELECT c.id,c.quote_id,c.authorized_usd,c.revoked_at
                       FROM sqg_consents c WHERE c.id=%s::uuid AND c.project_id=%s""",
                    (run["consent_id"], pid),
                ).fetchone()
                if existing is None or existing[3] is not None or str(existing[1]) != qid:
                    raise PipelineError("Consent does not match current quote", 409)
                amount = approved_budget(authorized_usd, "0")
                if amount != existing[2]:
                    raise PipelineError("Consent already exists with another amount", 409)
                return {"consent_id": str(existing[0]), "generation_id": gid,
                        "authorized_usd": str(existing[2]), "status": run["status"],
                        "reused": True}
            if run["status"] != "awaiting_authorization" or run["quote_id"] != qid:
                raise PipelineError("Generation not awaiting this quote", 409)
            quote = _latest_quote(conn, run)
            if quote is None or str(quote[0]) != qid or quote[6] <= now:
                raise PipelineError("Quote expired or superseded", 409)
            model = current_priced_model(conn, run["config"])
            if model["catalog_sha256"] != quote[7]:
                raise PipelineError("OpenRouter model price changed since quote", 409)
            pinned = quote[4]
            if (pinned.get("generation_id") != gid or
                pinned.get("generation_input_sha256") != run["input_sha256"] or
                pinned.get("catalog_snapshot_id") != model["snapshot_id"] or
                pinned.get("rates_usd_per_token") != model["rates"]):
                raise PipelineError("Quote source or pricing snapshot changed", 409)
            budget = approved_budget(authorized_usd, str(quote[3]))
            expiration = min(now + timedelta(days=1), quote[6] + timedelta(days=1))
            row = conn.execute(
                """INSERT INTO sqg_consents
                     (project_id,quote_id,publication_id,authorized_usd,expires_at)
                   VALUES (%s,%s::uuid,%s::uuid,%s,%s)
                   RETURNING id""",
                (pid, qid, run["publication_id"], str(budget), expiration),
            ).fetchone()
            consent_id = str(row[0])
            conn.execute(
                """UPDATE sqg_generations
                   SET consent_id=%s::uuid,status='queued',updated_at=now()
                   WHERE id=%s::uuid AND project_id=%s""",
                (consent_id, gid, pid),
            )
            conn.execute(
                """INSERT INTO sqg_events(project_id,generation_id,event_kind,payload)
                   VALUES (%s,%s::uuid,'consent_authorized',%s::jsonb)""",
                (pid, gid, canonical({
                    "consent_id": consent_id, "authorized_usd": str(budget),
                    "quote_id": qid, "provider_calls": 0,
                })),
            )
            return {"consent_id": consent_id, "generation_id": gid,
                    "authorized_usd": str(budget), "status": "queued",
                    "reused": False}


def revoke_generation_budget(project_id: str, consent_id: str, *,
                             connect=None, now=None) -> dict:
    pid = _pid(project_id)
    cid = publication_uuid(consent_id)
    now = _now() if now is None else now
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            # Lock generation first: same lock order as quote/consent/worker.
            run_row = conn.execute(
                """SELECT id,status FROM sqg_generations
                   WHERE project_id=%s AND consent_id=%s::uuid FOR UPDATE""",
                (pid, cid),
            ).fetchone()
            if run_row is None:
                raise PipelineError("Search consent not found in project", 404)
            generation_id, status = str(run_row[0]), run_row[1]
            consent = conn.execute(
                """SELECT revoked_at FROM sqg_consents
                   WHERE id=%s::uuid AND project_id=%s FOR UPDATE""", (cid, pid),
            ).fetchone()
            if consent is None:
                raise PipelineError("Search consent not found in project", 404)
            revoked = consent[0] is not None
            if not revoked:
                conn.execute(
                    """UPDATE sqg_consents SET revoked_at=%s
                       WHERE id=%s::uuid AND project_id=%s AND revoked_at IS NULL""",
                    (now, cid, pid),
                )
                if status in ("queued", "running"):
                    conn.execute(
                        """UPDATE sqg_generations SET status='paused_budget',updated_at=now()
                           WHERE id=%s::uuid AND project_id=%s""", (generation_id, pid),
                    )
                conn.execute(
                    """INSERT INTO sqg_events(project_id,generation_id,event_kind,payload)
                       VALUES (%s,%s::uuid,'consent_revoked',%s::jsonb)""",
                    (pid, generation_id, canonical({"consent_id": cid})),
                )
            return {"consent_id": cid, "generation_id": generation_id,
                    "revoked": True, "reused": revoked}


def get_generation_budget(project_id: str, generation_id: str, *, connect=None):
    """Pure read; no paid calls, no approval or state mutations."""
    pid = _pid(project_id)
    gid = publication_uuid(generation_id)
    factory = connect or _connect
    with factory() as conn:
        row = conn.execute(
            """SELECT g.status,c.id,c.authorized_usd,c.spent_usd,c.reserved_usd,
                      c.revoked_at,c.expires_at
               FROM sqg_generations g LEFT JOIN sqg_consents c
                 ON c.id=g.consent_id AND c.project_id=g.project_id
               WHERE g.id=%s::uuid AND g.project_id=%s""",
            (gid, pid),
        ).fetchone()
        if row is None:
            raise PipelineError("Search generation not found in project", 404)
        if row[1] is None:
            return {"generation_id": gid, "status": row[0], "consent": None}
        return {
            "generation_id": gid, "status": row[0],
            "consent": {
                "consent_id": str(row[1]),
                "authorized_usd": str(row[2]),
                "spent_usd": str(row[3]), "reserved_usd": str(row[4]),
                "available_usd": str(row[2] - row[3] - row[4]),
                "revoked": row[5] is not None,
                "expires_at": row[6].isoformat(),
            },
        }
