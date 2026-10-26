"""SP-10 immutable owner-approved held plan persistence, NOT Collector handoff.

Owner authorization is an injected SERVER-side predicate verified before DB
writes. The existing QV/RTB evidence is rechecked under the same PostgreSQL
transaction that appends the snapshot + approval audit row. No provider work.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from intelligence.taxonomy_builder.pipeline_contracts import (
    PipelineError, canonical, sha256,
)
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid
from intelligence.search_planner.contracts import publication_uuid
from intelligence.search_planner.generation_workflow import OwnerActionGuard, _guard
from .service import PLAN_SCHEMA

ACTION = "search_planner.plan.commit_held"


def persist_approved_held_plan(project_id: str, validation_run_id: str,
                               plan: dict, idempotency_key: str, *,
                               owner_action_guard: OwnerActionGuard | None = None,
                               connect=None, now: datetime | None = None) -> dict:
    """Exactly-once project/idempotency approval of a non-executable preview.

    Do not expose plan or the owner guard as request JSON. Production must
    regenerate plan from trusted QV/Collector-capability ports (TaskPlanner).
    """
    pid, vid = _pid(project_id), publication_uuid(validation_run_id)
    if (type(idempotency_key) is not str or
            not 12 <= len(idempotency_key) <= 128):
        raise PipelineError("Invalid held-plan idempotency key", 422)
    if (type(plan) is not dict or
            plan.get("schema_version") != PLAN_SCHEMA or
            plan.get("project_id") != pid or
            plan.get("validation_run_id") != vid or
            plan.get("status") != "held_preview_no_dispatch" or
            plan.get("execution_enabled") is not False or
            plan.get("external_dispatch_authorized") is not False or
            plan.get("provider_requests_in_this_plan") != 0 or
            plan.get("dollars_spent_in_this_plan") != "0.00000000" or
            plan.get("plan_hash") is None or
            sha256({k:v for k,v in plan.items() if k != "plan_hash"}) != plan["plan_hash"]):
        raise PipelineError("Untrusted or executable held-plan snapshot", 409)
    for field in ("generation_id", "taxonomy_publication_id", "qv_outbox_id"):
        publication_uuid(plan.get(field))
    clk = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(clk,datetime) or clk.tzinfo is None or clk.utcoffset() is None:
        raise PipelineError("Invalid held-plan verification clock", 422)
    try:
        expires = datetime.fromisoformat(plan["capability_expires_at"].replace("Z","+00:00"))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PipelineError("Invalid pinned capability expiry", 409) from exc
    if expires.tzinfo is None or expires.astimezone(timezone.utc) <= clk.astimezone(timezone.utc):
        raise PipelineError("Held plan requires current source snapshot", 409)
    _guard(owner_action_guard, ACTION, pid, vid)
    factory = connect if connect is not None else _connect
    with factory() as conn:
        with conn.transaction():
            # Serializes separate approval requests for the same project.
            # Unique indexes are the final idempotency/concurrency boundary.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(pid,))
            existing=conn.execute(
                """SELECT a.plan_id,p.validation_run_id,p.plan_hash
                   FROM sp_held_plan_approvals a
                   JOIN sp_held_task_plans p
                     ON p.id=a.plan_id AND p.project_id=a.project_id
                   WHERE a.project_id=%s AND a.idempotency_key=%s
                   FOR SHARE OF a,p""",(pid,idempotency_key)
            ).fetchone()
            if existing is not None:
                if (str(existing[1])!=vid or existing[2]!=plan["plan_hash"]):
                    raise PipelineError("Held-plan idempotency key conflicts with prior approved plan",409)
                return {
                    "plan_id":str(existing[0]), "plan_hash":plan["plan_hash"],
                    "status":"held_approved_not_dispatched",
                    "reused":True, "external_dispatch_authorized":False,
                }
            root=conn.execute(
                """SELECT r.generation_id,r.embedding_mode,r.status,
                          r.config_sha256,r.source_bundle_sha256,
                          g.publication_id,g.taxonomy_sha256,g.input_sha256,
                          g.status,o.id
                   FROM qv_runs r
                   JOIN sqg_generations g
                     ON g.id=r.generation_id AND g.project_id=r.project_id
                   JOIN qv_outbox o ON o.run_id=r.id
                   WHERE r.id=%s::uuid AND r.project_id=%s""",(vid,pid)
            ).fetchone()
            if root is None:
                raise PipelineError("Approved QV source for held plan not found",404)
            if (str(root[0])!=plan["generation_id"] or
                    root[1]!="semantic_local" or root[2]!="completed" or
                    root[3]!=plan["qv_config_sha256"] or
                    root[4]!=plan["qv_source_bundle_sha256"] or
                    str(root[5])!=plan["taxonomy_publication_id"] or
                    root[6]!=plan["taxonomy_sha256"] or
                    root[7]!=plan["generation_input_sha256"] or
                    root[8]!="completed" or str(root[9])!=plan["qv_outbox_id"]):
                raise PipelineError("Held plan has stale or foreign QV/C9 immutable inputs",409)
            saved=conn.execute(
                """INSERT INTO sp_held_task_plans
                      (project_id,validation_run_id,plan_hash,snapshot)
                   VALUES (%s,%s::uuid,%s,%s::jsonb)
                   ON CONFLICT (project_id,validation_run_id,plan_hash)
                     DO NOTHING RETURNING id""",
                (pid,vid,plan["plan_hash"],canonical(plan))
            ).fetchone()
            if saved is None:
                saved=conn.execute(
                    """SELECT id,snapshot FROM sp_held_task_plans
                       WHERE project_id=%s AND validation_run_id=%s::uuid
                         AND plan_hash=%s FOR SHARE""",
                    (pid,vid,plan["plan_hash"])
                ).fetchone()
                if saved is None or saved[1]!=plan:
                    raise PipelineError("Immutable plan hash already stored with different content",409)
            plan_id=str(saved[0])
            existing_approval=conn.execute(
                """SELECT id FROM sp_held_plan_approvals
                   WHERE plan_id=%s::uuid AND project_id=%s FOR SHARE""",
                (plan_id,pid)
            ).fetchone()
            if existing_approval is None:
                conn.execute(
                    """INSERT INTO sp_held_plan_approvals
                         (project_id,plan_id,idempotency_key,owner_action)
                       VALUES (%s,%s::uuid,%s,%s)""",
                    (pid,plan_id,idempotency_key,ACTION)
                )
            return {
                "plan_id":plan_id, "plan_hash":plan["plan_hash"],
                "status":"held_approved_not_dispatched",
                "reused":existing_approval is not None,
                "external_dispatch_authorized":False,
            }


def get_existing_held_approval(project_id: str, validation_run_id: str,
                               idempotency_key: str, *, limits,
                               owner_action_guard: OwnerActionGuard | None,
                               connect=None) -> dict | None:
    """Stable idempotency replay from immutable DB evidence.

    A catalog's captured_at/TTL may change during a retry. Read the original
    approved plan instead of recomputing a new quote/plan hash. The request's
    normalized original limits and exact QV identity must remain identical.
    """
    from .service import validate_plan_limits
    pid, vid = _pid(project_id), publication_uuid(validation_run_id)
    if type(idempotency_key) is not str or not 12 <= len(idempotency_key) <= 128:
        raise PipelineError("Invalid held-plan idempotency key", 422)
    policy = validate_plan_limits(limits)
    _guard(owner_action_guard, ACTION, pid, vid)
    factory = connect if connect is not None else _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            existing = conn.execute(
                """SELECT p.id,p.validation_run_id,p.plan_hash,p.snapshot
                   FROM sp_held_plan_approvals a
                   JOIN sp_held_task_plans p
                     ON p.id=a.plan_id AND p.project_id=a.project_id
                   WHERE a.project_id=%s AND a.idempotency_key=%s""",
                (pid,idempotency_key),
            ).fetchone()
    if existing is None:
        return None
    p_id, p_qv, digest, saved = existing
    if (str(p_qv) != vid or type(saved) is not dict or
            saved.get("plan_hash") != digest or
            saved.get("project_id") != pid or
            saved.get("validation_run_id") != vid or
            saved.get("schema_version") != PLAN_SCHEMA or
            saved.get("status") != "held_preview_no_dispatch" or
            saved.get("external_dispatch_authorized") is not False or
            saved.get("execution_enabled") is not False or
            saved.get("limits") != policy or
            sha256({k:v for k,v in saved.items() if k != "plan_hash"}) != digest):
        raise PipelineError("Idempotency key conflicts with original immutable HELD plan",409)
    return {
        "plan_id": str(p_id), "plan_hash": digest,
        "status": "held_approved_not_dispatched",
        "reused": True, "external_dispatch_authorized": False,
    }
