"""Atomic successful settlement, complete coverage and Query Validation outbox.

Fake responses only in Stage C; external OpenRouter transport is out of scope.
The same transaction records charge, raw provenance, candidates, coverage,
run completion, and outbox pointer. No partial output is published.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, canonical
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid
from intelligence.search_planner.contracts import publication_uuid
from .contracts import validate_generation_config
from .cost_estimator import money, usd
from .provider_port import ProviderResponse
from .raw_validation import validate_raw_batch
from .repository import _load_attempt_locked

MAX_RAW_BYTES = 2_000_000


def _valid_price(amount):
    cost = money(amount, "provider confirmed charge")
    if cost != cost.quantize(Decimal("0.00000001")):
        raise PipelineError("Provider charge does not fit NUMERIC(18,8)", 422)
    return cost


def _load_contexts(conn, generation_id, hypothesis_ids):
    rows = conn.execute(
        """SELECT hypothesis_id,context,context_sha256
           FROM sqg_contexts WHERE generation_id=%s::uuid
           ORDER BY hypothesis_id""",
        (generation_id,),
    ).fetchall()
    by_id = {r[0]: r[1] for r in rows}
    required = set(hypothesis_ids)
    if len(required) != len(hypothesis_ids) or not required.issubset(by_id):
        raise PipelineError("Batch references missing or repeated source context", 409)
    return [by_id[ident] for ident in hypothesis_ids]


def _validate_provider_reply(reply: ProviderResponse, config: dict, contexts: list):
    if type(reply) is not ProviderResponse:
        raise PipelineError("Incorrect provider result type", 422)
    if (reply.model_id != config["model"]["model_id"] or
            not isinstance(reply.provider_response_id, str) or
            not 1 <= len(reply.provider_response_id) <= 256):
        raise PipelineError("Provider model/response ID mismatch", 422)
    if reply.finish_reason != "stop":
        raise PipelineError("LLM response truncated or not finished", 422)
    if type(reply.usage) is not dict or set(reply.usage) != {
        "prompt_tokens", "completion_tokens", "cost"
    } or any(type(reply.usage[k]) is not int or reply.usage[k] < 0
             for k in ("prompt_tokens", "completion_tokens")):
        raise PipelineError("Provider usage incomplete", 422)
    price = _valid_price(reply.charged_usd)
    if price != _valid_price(reply.usage["cost"]):
        raise PipelineError("Provider charge/usage disagreement", 422)
    if type(reply.raw_json) is not dict:
        raise PipelineError("Raw provider response must be JSON object", 422)
    if len(canonical(reply.raw_json).encode("utf-8")) > MAX_RAW_BYTES:
        raise PipelineError("Provider JSON exceeds size limit", 422)
    validate_raw_batch(reply.raw_json, contexts, config["search_policy"])
    return price


def settle_successful_batch(project_id, attempt_id, reply: ProviderResponse, *,
                            connect=None):
    """Settle one dispatched attempt and publish only after full coverage.

    Raises PipelineError before any partial commit if response is invalid.
    Executor must account for a known billed-invalid response via
    settle_known_failure, not retry the original paid provider call.
    """
    pid = _pid(project_id)
    aid = publication_uuid(attempt_id)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            attempt = _load_attempt_locked(conn, pid, aid)
            if attempt["state"] == "completed":
                return {"attempt_id": aid, "status": "completed", "reused": True}
            if attempt["state"] != "dispatch_intent":
                raise PipelineError("Attempt is not awaiting successful settlement", 409)
            generation_id, batch_id = attempt["gid"], attempt["bid"]
            generation = conn.execute(
                """SELECT g.status,g.cancel_requested,g.config_snapshot,g.publication_id,
                          g.taxonomy_version,g.input_sha256,g.model_id
                   FROM sqg_generations g WHERE g.id=%s::uuid AND g.project_id=%s""",
                (generation_id, pid),
            ).fetchone()
            config = validate_generation_config(generation[2])
            batch = conn.execute(
                """SELECT hypothesis_ids FROM sqg_batches
                   WHERE id=%s::uuid AND generation_id=%s::uuid""",
                (batch_id, generation_id),
            ).fetchone()
            if batch is None or type(batch[0]) is not list:
                raise PipelineError("Planned batch manifest missing", 409)
            contexts = _load_contexts(conn, generation_id, batch[0])
            price = _validate_provider_reply(reply, config, contexts)
            context_map = {c["hypothesis"]["id"]: c for c in contexts}
            generated = reply.raw_json["hypotheses"]
            position = 0
            total = 0
            for output in generated:
                context = context_map[output["hypothesis_id"]]
                hypothesis_id = context["hypothesis"]["id"]
                queries = output["queries"]
                fact_ids = sorted(f["id"] for f in context["approved_facts"])
                for query in queries:
                    conn.execute(
                        """INSERT INTO sqg_candidates
                         (generation_id,batch_id,hypothesis_id,direction_id,branch_id,
                          source_fact_ids,text,query_mode,angle,language,research_stance,
                          rationale,raw_position)
                         VALUES (%s::uuid,%s::uuid,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s)""",
                        (generation_id, batch_id, hypothesis_id,
                         context["direction"]["id"],
                         context["branch"]["id"] if context["branch"] else None,
                         canonical(fact_ids), query["text"], query["query_mode"],
                         query["angle"], query["language"], query["research_stance"],
                         query["rationale"], position),
                    )
                    position += 1
                total += len(queries)
                conn.execute(
                    """INSERT INTO sqg_hypothesis_coverage
                     (generation_id,hypothesis_id,state,candidate_count,reason)
                     VALUES (%s::uuid,%s,%s,%s,%s)""",
                    (generation_id, hypothesis_id,
                     "generated" if queries else "no_viable_queries",
                     len(queries), output["no_viable_reason"]),
                )
            conn.execute(
                """UPDATE sqg_attempts
                   SET status='completed',charged_usd=%s,provider_response_id=%s,
                       usage=%s::jsonb,raw_result=%s::jsonb,
                       finish_reason=%s,ended_at=now()
                   WHERE id=%s::uuid""",
                (str(price), reply.provider_response_id, canonical(reply.usage),
                 canonical(reply.raw_json), reply.finish_reason, aid),
            )
            conn.execute(
                """UPDATE sqg_consents
                   SET reserved_usd=reserved_usd-%s,spent_usd=spent_usd+%s
                   WHERE id=%s::uuid AND project_id=%s""",
                (str(attempt["reservation"]), str(price), attempt["consent_id"], pid),
            )
            conn.execute(
                """UPDATE sqg_batches SET status='completed',updated_at=now()
                   WHERE id=%s::uuid AND generation_id=%s::uuid""",
                (batch_id, generation_id),
            )
            conn.execute(
                """INSERT INTO sqg_events(project_id,generation_id,batch_id,attempt_id,
                   event_kind,payload) VALUES
                   (%s,%s::uuid,%s::uuid,%s::uuid,'batch_completed',%s::jsonb)""",
                (pid, generation_id, batch_id, aid,
                 canonical({"candidate_count": total, "charged_usd": usd(price)})),
            )
            counters = conn.execute(
                """SELECT COUNT(*),COUNT(*) FILTER (WHERE status='completed')
                   FROM sqg_batches WHERE generation_id=%s::uuid""",
                (generation_id,),
            ).fetchone()
            if generation[1]:
                # Cancellation might have cancelled pending batches while one
                # request was already dispatched. Settle the charge but never
                # publish even a partial result or outbox event.
                active = conn.execute(
                    """SELECT COUNT(*) FROM sqg_batches
                       WHERE generation_id=%s::uuid
                         AND status IN ('pending','reserved','dispatching','uncertain')""",
                    (generation_id,),
                ).fetchone()[0]
                if active == 0:
                    conn.execute(
                        """UPDATE sqg_generations SET status='cancelled',updated_at=now()
                           WHERE id=%s::uuid AND project_id=%s""",
                        (generation_id, pid),
                    )
                    final = "cancelled"
                else:
                    final = "uncertain"
            elif counters[0] == counters[1]:
                coverage = conn.execute(
                    """SELECT (SELECT COUNT(*) FROM sqg_contexts WHERE generation_id=%s::uuid),
                              (SELECT COUNT(*) FROM sqg_hypothesis_coverage
                               WHERE generation_id=%s::uuid),
                              (SELECT COUNT(*) FROM sqg_candidates WHERE generation_id=%s::uuid)""",
                    (generation_id, generation_id, generation_id),
                ).fetchone()
                if coverage[0] != coverage[1] or coverage[0] < 1 or (
                        coverage[2] > config["search_policy"]["max_total_candidates"]):
                    raise PipelineError("Cannot finalize without exact hypothesis coverage", 409)
                conn.execute(
                    """UPDATE sqg_generations SET status='completed',completed_at=now(),
                       updated_at=now() WHERE id=%s::uuid AND project_id=%s""",
                    (generation_id, pid),
                )
                conn.execute(
                    """INSERT INTO sqg_outbox(generation_id,event_type,payload_pointer)
                       VALUES (%s::uuid,'query_candidates_ready/v1',%s::jsonb)
                       ON CONFLICT (generation_id,event_type) DO NOTHING""",
                    (generation_id, canonical({
                        "generation_id": generation_id,
                        "schema_version": "query_candidates/v1",
                        "input_sha256": generation[5],
                    })),
                )
                conn.execute(
                    """INSERT INTO sqg_events(project_id,generation_id,event_kind,payload)
                       VALUES (%s,%s::uuid,'generation_completed',%s::jsonb)""",
                    (pid, generation_id,
                     canonical({"coverage": coverage[1], "candidates": coverage[2]})),
                )
                final = "completed"
            else:
                final = "running"
            return {
                "attempt_id": aid, "generation_id": generation_id,
                "status": "completed", "generation_status": final,
                "candidate_count": total, "charged_usd": usd(price), "reused": False,
            }


def get_candidates_bundle(project_id, generation_id, *, connect=None) -> dict:
    """Read-only completed handoff, with server-issued UUIDs and full coverage."""
    pid = _pid(project_id)
    gid = publication_uuid(generation_id)
    factory = connect or _connect
    with factory() as conn:
        generation = conn.execute(
            """SELECT status,publication_id,taxonomy_version,input_sha256,
                      config_snapshot,model_id
               FROM sqg_generations WHERE id=%s::uuid AND project_id=%s""",
            (gid, pid),
        ).fetchone()
        if generation is None:
            raise PipelineError("Search generation not found", 404)
        if generation[0] != "completed":
            raise PipelineError("generation_not_complete", 409)
        config = validate_generation_config(generation[4])
        items = conn.execute(
            """SELECT c.id,c.hypothesis_id,c.direction_id,c.branch_id,
                      c.source_fact_ids,c.text,c.query_mode,c.angle,c.language,
                      c.research_stance,c.rationale
               FROM sqg_candidates c JOIN sqg_batches b ON b.id=c.batch_id
               WHERE c.generation_id=%s::uuid
               ORDER BY b.batch_index,c.raw_position""", (gid,),
        ).fetchall()
        coverage = conn.execute(
            """SELECT hypothesis_id,state,candidate_count,reason
               FROM sqg_hypothesis_coverage WHERE generation_id=%s::uuid
               ORDER BY hypothesis_id""", (gid,),
        ).fetchall()
        return {
            "schema_version": "query_candidates/v1",
            "generation_id": gid, "project_id": pid,
            "taxonomy_publication_id": str(generation[1]),
            "taxonomy_version": generation[2],
            "input_sha256": generation[3],
            "prompt_version": config["model"]["prompt_version"],
            "model_id": generation[5], "status": "completed",
            "candidates": [{
                "candidate_id": str(r[0]), "hypothesis_id": r[1],
                "direction_id": r[2], "branch_id": r[3],
                "source_fact_ids": r[4], "text": r[5], "query_mode": r[6],
                "angle": r[7], "language": r[8], "research_stance": r[9],
                "rationale": r[10],
            } for r in items],
            "hypothesis_coverage": [{
                "hypothesis_id": r[0], "state": r[1],
                "candidate_count": r[2], "reason": r[3],
            } for r in coverage],
        }
