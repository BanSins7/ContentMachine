"""GB-MOD-11 durable per-block DAG, local claim fencing, cancellation.

The DB is the only authority for job/lease state. No HTTP, LLM, media
classification, fake completed B result, or paid worker on this path.
Readiness is a prerequisite to QUEUING; stage07 must run again at use time.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Mapping
from uuid import UUID
import re

from .contracts import canonical_hash
from .dependency_rules import BLOCKS, ReadinessReport, ReadinessState
from .errors import DependencyUnavailable, ExecutionNotApproved

_JOB_ERROR = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
DAG_VERSION = "group-b-dag/v1"
# B5 is a locally measured, deterministic step, but B5 implementation is
# GB-MOD-12. Visual/B3 depend on a verified B2 format result; B6 on B4.
DEPENDENCIES = {
    "B1":(),"B2":(),"B3":("B2",),"B4":("B2",),
    "B5":(),"B6":("B4",),"B7":(),"B8":(),
}
KIND = {block:("local" if block=="B5" else "paid") for block in BLOCKS}


def _deny(code):
    raise DependencyUnavailable("jobs",code)


def _uuid(v):
    if type(v) is not str:
        return False
    try:
        return str(UUID(v))==v
    except ValueError:
        return False


def _hash(v):
    return type(v) is str and _SHA.fullmatch(v) is not None


def _row(cursor):
    r=cursor.fetchone()
    if r is None:
        return None
    if isinstance(r,Mapping):
        return dict(r)
    return dict(zip((c.name for c in cursor.description),r))


@dataclass(frozen=True,slots=True)
class JobClaim:
    id: str
    project_id: str
    run_id: str
    block: str
    kind: str
    evidence_sha256: str
    lease_token: str
    generation: int
    attempts: int
    max_attempts: int


def _append(conn,job_id,project_id,run_id,generation,kind,evidence,*,result=None,reason=None):
    conn.execute(
        """INSERT INTO group_b_job_events(project_id,run_id,job_id,lease_generation,
                 event_type,evidence_sha256,result_sha256,reason_code)
           VALUES(%s,%s::uuid,%s::uuid,%s,%s,%s,%s,%s)""",
        (project_id,run_id,job_id,generation,kind,evidence,result,reason)
    )


def enqueue_jobs(conn,*,project_id: str,run_id: str,
                 readiness: ReadinessReport,
                 paid_attempts: Mapping[str,str] | None = None,
                 selected_blocks: tuple[str,...] = BLOCKS,
                 dry_run: bool = False) -> tuple[str,...]:
    """Durably enqueue only ready blocks with all intra-run predecessors.

    User-provided 'ready' is never a paid grant. Paid blocks must have a real
    stored RESERVED attempt and matching quote evidence; actual HTTP remains
    denied. Dry-run makes zero writes and requires no paid attempt.
    """
    if (not isinstance(readiness,ReadinessReport) or
        not _uuid(run_id) or type(project_id) is not str or
        not _hash(readiness.manifest_sha256)):
        _deny("enqueue_identity_invalid")
    if (readiness.project_id!=project_id or readiness.run_id!=run_id or
        type(dry_run) is not bool or type(selected_blocks) is not tuple or
        len(selected_blocks)!=len(set(selected_blocks)) or
        any(b not in BLOCKS for b in selected_blocks)):
        _deny("readiness_identity_mismatch")
    ready={b for b in BLOCKS if readiness.for_block(b).state is ReadinessState.READY
           and readiness.for_block(b).evidence_sha256==readiness.manifest_sha256}
    selected=set(selected_blocks)
    admissible=tuple(b for b in BLOCKS if b in ready and b in selected
                     and all(dep in ready and dep in selected for dep in DEPENDENCIES[b]))
    if dry_run:
        return admissible
    if not admissible:
        return ()
    run=_row(conn.execute(
        """SELECT r.id::text AS id,r.state,r.media_manifest_sha256,
                  r.a_snapshot_sha256,i.status AS inbox_status
             FROM group_b_runs r
             JOIN group_b_inbox i ON i.id=r.inbox_id AND i.project_id=r.project_id
            WHERE r.project_id=%s AND r.id=%s::uuid FOR UPDATE OF r""",
        (project_id,run_id)
    ))
    if (run is None or run["state"] not in ("draft","quoted","partial","needs_review","queued") or
        run["inbox_status"]!="accepted" or
        run["a_snapshot_sha256"] is None or
        run["media_manifest_sha256"] is None):
        _deny("run_not_eligible")
    attempts=paid_attempts or {}
    ids=[]
    for block in admissible:
        paid_id=attempts.get(block)
        if KIND[block]=="paid":
            if not _uuid(paid_id):
                raise ExecutionNotApproved()
            approved=_row(conn.execute(
                """SELECT a.id::text AS id FROM group_b_model_attempts a
                   JOIN group_b_model_approvals p
                     ON p.id=a.approval_id AND p.project_id=a.project_id
                    AND p.run_id=a.run_id AND p.quote_id=a.quote_id
                   JOIN group_b_model_quotes q ON q.id=a.quote_id
                    AND q.project_id=a.project_id AND q.run_id=a.run_id
                  WHERE a.project_id=%s AND a.run_id=%s::uuid
                    AND a.id=%s::uuid AND a.charge_state='reserved'
                    AND a.request_sha256=q.request_sha256
                    AND q.evidence_sha256=%s
                    AND q.quote_details->>'block'=%s
                    AND q.expires_at>now()""",
                (project_id,run_id,paid_id,readiness.manifest_sha256,block)
            ))
            if approved is None:
                raise ExecutionNotApproved()
        elif paid_id is not None:
            _deny("local_block_cannot_be_paid")
        row=_row(conn.execute(
            """INSERT INTO group_b_jobs(project_id,run_id,block,kind,
                       depends_on,attempt_id,evidence_sha256,max_attempts)
                  VALUES(%s,%s::uuid,%s,%s,%s::text[],%s::uuid,%s,%s)
                  ON CONFLICT(project_id,run_id,block) DO NOTHING
                  RETURNING id::text AS id,lease_generation""",
            (project_id,run_id,block,KIND[block],list(DEPENDENCIES[block]),
             paid_id,readiness.manifest_sha256,1 if KIND[block]=="paid" else 3)
        ))
        if row is None:
            existing=_row(conn.execute(
                """SELECT id::text AS id,block,kind,depends_on,attempt_id::text AS attempt_id,
                          evidence_sha256,state,lease_generation
                     FROM group_b_jobs
                    WHERE project_id=%s AND run_id=%s::uuid AND block=%s""",
                (project_id,run_id,block)
            ))
            if (existing is None or existing["evidence_sha256"]!=readiness.manifest_sha256
                or existing["kind"]!=KIND[block] or
                tuple(existing["depends_on"])!=DEPENDENCIES[block] or
                existing["attempt_id"]!=paid_id):
                _deny("existing_job_identity_conflict")
            ids.append(existing["id"])
            continue
        ids.append(row["id"])
        _append(conn,row["id"],project_id,run_id,0,"enqueued",readiness.manifest_sha256)
        conn.execute(
            """UPDATE group_b_block_states
                  SET status='ready',evidence_sha256=%s,error_code=NULL,
                      updated_at=now()
                WHERE project_id=%s AND run_id=%s::uuid
                  AND block=%s AND status IN ('not_run','ready','partial')
                RETURNING block""",
            (readiness.manifest_sha256,project_id,run_id,block)
        )
    conn.execute(
        """UPDATE group_b_runs SET state='queued',updated_at=now()
           WHERE project_id=%s AND id=%s::uuid AND state IN ('draft','quoted','partial','needs_review')""",
        (project_id,run_id)
    )
    return tuple(ids)


def claim_local(conn,*,project_id: str,lease_seconds: int = 60) -> JobClaim | None:
    """Atomic PostgreSQL SKIP LOCKED claim of ONE local B5 job.

    Expired local leases may be stolen only with a fresh UUID+generation.
    Paid jobs are never claimed, because provider authorization is not yet
    wired; uncertain remote work can never replay through lease expiry.
    """
    if type(project_id) is not str or type(lease_seconds) is not int or not 5<=lease_seconds<=300:
        _deny("claim_limits_invalid")
    # Stable lock order: first run, then job. Enqueue/cancellation also
    # take run->job locks; reverse order can deadlock across two workers.
    owner=_row(conn.execute(
        """SELECT r.id::text AS run_id
             FROM group_b_runs r
             JOIN group_b_inbox i ON i.id=r.inbox_id AND i.project_id=r.project_id
            WHERE r.project_id=%s AND r.state IN ('queued','running','partial')
              AND r.media_manifest_sha256 IS NOT NULL
              AND r.a_snapshot_sha256 IS NOT NULL AND i.status='accepted'
              AND EXISTS (
                SELECT 1 FROM group_b_jobs candidate
                 WHERE candidate.project_id=r.project_id AND candidate.run_id=r.id
                   AND candidate.kind='local' AND candidate.attempts<candidate.max_attempts
                   AND (candidate.state='ready' OR
                        (candidate.state='running' AND candidate.lease_until<now()))
                   AND NOT EXISTS (
                     SELECT 1 FROM unnest(candidate.depends_on) dep
                      WHERE NOT EXISTS (
                        SELECT 1 FROM group_b_jobs predecessor
                         WHERE predecessor.project_id=candidate.project_id
                           AND predecessor.run_id=candidate.run_id
                           AND predecessor.block=dep AND predecessor.state='complete'
                      ))
              )
            ORDER BY r.created_at,r.id
            FOR UPDATE OF r SKIP LOCKED LIMIT 1""",
        (project_id,)
    ))
    if owner is None:
        return None
    row=_row(conn.execute(
        """SELECT j.id::text AS id,j.project_id,j.run_id::text AS run_id,
                  j.block,j.kind,j.evidence_sha256,j.state,
                  j.attempts,j.max_attempts,j.lease_generation
             FROM group_b_jobs j
            WHERE j.project_id=%s AND j.run_id=%s::uuid AND j.kind='local'
              AND (j.state='ready' OR
                   (j.state='running' AND j.lease_until < now()))
              AND j.attempts<j.max_attempts
              AND NOT EXISTS (
                SELECT 1 FROM unnest(j.depends_on) dep
                 WHERE NOT EXISTS (
                    SELECT 1 FROM group_b_jobs predecessor
                     WHERE predecessor.project_id=j.project_id
                       AND predecessor.run_id=j.run_id
                       AND predecessor.block=dep AND predecessor.state='complete'
                 ))
            ORDER BY j.created_at,j.id
            FOR UPDATE OF j SKIP LOCKED LIMIT 1""",
        (project_id,owner["run_id"])
    ))
    if row is None:
        return None
    previous=row["state"]
    updated=_row(conn.execute(
        """UPDATE group_b_jobs
              SET state='running',attempts=attempts+1,
                  lease_generation=lease_generation+1,
                  lease_token=gen_random_uuid(),
                  lease_until=now()+(%s * INTERVAL '1 second'),
                  updated_at=now()
            WHERE project_id=%s AND id=%s::uuid
            RETURNING lease_token::text AS lease_token,
                      lease_generation,attempts""",
        (lease_seconds,project_id,row["id"])
    ))
    if updated is None:
        _deny("claim_lost")
    row.update(updated)
    _append(conn,row["id"],project_id,row["run_id"],row["lease_generation"],
            "reclaimed" if previous=="running" else "claimed",row["evidence_sha256"])
    conn.execute(
        """UPDATE group_b_block_states SET status='running',updated_at=now()
            WHERE project_id=%s AND run_id=%s::uuid AND block=%s
              AND status IN ('ready','running','partial')""",
        (project_id,row["run_id"],row["block"])
    )
    conn.execute(
        """UPDATE group_b_runs SET state='running',updated_at=now()
            WHERE project_id=%s AND id=%s::uuid AND state IN ('queued','partial')""",
        (project_id,row["run_id"])
    )
    return JobClaim(row["id"],project_id,row["run_id"],row["block"],
                    row["kind"],row["evidence_sha256"],row["lease_token"],
                    row["lease_generation"],row["attempts"],row["max_attempts"])


def _lock_claim(conn,claim: JobClaim):
    # Stable run->job lock order shared with claim and cancellation.
    run=_row(conn.execute(
        """SELECT state FROM group_b_runs WHERE project_id=%s
             AND id=%s::uuid FOR UPDATE""",
        (claim.project_id,claim.run_id)
    ))
    if run is None or run["state"]!="running":
        _deny("lease_stolen_or_cancelled")
    row=_row(conn.execute(
        """SELECT j.id::text AS id,j.project_id,j.run_id::text AS run_id,
                  j.block,j.kind,j.state,j.evidence_sha256,j.lease_token::text,
                  j.lease_generation,j.lease_until
             FROM group_b_jobs j
            WHERE j.project_id=%s AND j.id=%s::uuid FOR UPDATE OF j""",
        (claim.project_id,claim.id)
    ))
    if (row is None or row["run_id"]!=claim.run_id or
        row["block"]!=claim.block or row["kind"]!=claim.kind or
        row["evidence_sha256"]!=claim.evidence_sha256 or
        row["state"]!="running" or
        row["lease_token"]!=claim.lease_token or
        row["lease_generation"]!=claim.generation):
        _deny("lease_stolen_or_cancelled")
    fresh=conn.execute("SELECT %s > now()", (row["lease_until"],)).fetchone()
    if fresh is None or fresh[0] is not True:
        _deny("lease_expired")
    return row


def heartbeat(conn,claim: JobClaim,*,lease_seconds: int = 60) -> None:
    if not 5<=lease_seconds<=300:
        _deny("heartbeat_limits_invalid")
    _lock_claim(conn,claim)
    conn.execute(
        """UPDATE group_b_jobs
              SET lease_until=now()+(%s * INTERVAL '1 second'),updated_at=now()
            WHERE project_id=%s AND id=%s::uuid""",
        (lease_seconds,claim.project_id,claim.id)
    )
    _append(conn,claim.id,claim.project_id,claim.run_id,claim.generation,
            "heartbeat",claim.evidence_sha256)


def finish_job(conn,claim: JobClaim,*,state: str,
               result_sha256: str | None = None, reason: str | None = None) -> None:
    """Job completion requires a stage-specific VERIFIED output SHA.

    No result means 'partial', never a fabricated complete. The run itself
    remains partial (not completed) until the B analysis validator/storage
    stages create a canonical approved content_b_analyses record.
    """
    if state not in ("complete","partial","failed","blocked"):
        _deny("finish_state_invalid")
    if (state=="complete" and not _hash(result_sha256)) or (
        state!="complete" and result_sha256 is not None):
        _deny("output_proof_invalid")
    if reason is not None and (type(reason) is not str or _JOB_ERROR.fullmatch(reason) is None):
        _deny("error_code_invalid")
    _lock_claim(conn,claim)
    conn.execute(
        """UPDATE group_b_jobs SET state=%s,result_sha256=%s,last_error_code=%s,
               lease_token=NULL,lease_until=NULL,updated_at=now()
            WHERE project_id=%s AND id=%s::uuid""",
        (state,result_sha256,reason,claim.project_id,claim.id)
    )
    conn.execute(
        """UPDATE group_b_block_states
              SET status=%s,evidence_sha256=%s,error_code=%s,updated_at=now()
            WHERE project_id=%s AND run_id=%s::uuid AND block=%s""",
        ("complete" if state=="complete" else
         "partial" if state=="partial" else
         "failed" if state=="failed" else "unavailable",
         claim.evidence_sha256,reason,claim.project_id,claim.run_id,claim.block)
    )
    _append(conn,claim.id,claim.project_id,claim.run_id,claim.generation,
            "completed" if state=="complete" else state,claim.evidence_sha256,
            result=result_sha256,reason=reason)
    conn.execute(
        """UPDATE group_b_runs SET state='partial',updated_at=now()
            WHERE project_id=%s AND id=%s::uuid AND state='running'
              AND NOT EXISTS (
                SELECT 1 FROM group_b_jobs j
                 WHERE j.project_id=%s AND j.run_id=%s::uuid AND j.state='running'
              )""",
        (claim.project_id,claim.run_id,claim.project_id,claim.run_id)
    )


def cancel_run(conn,*,project_id: str,run_id: str,reason: str = "cancelled") -> int:
    """Cancellation fences every outstanding claim, never frees paid holds."""
    if not _uuid(run_id) or _JOB_ERROR.fullmatch(reason) is None:
        _deny("cancel_input_invalid")
    run=_row(conn.execute(
        """SELECT state FROM group_b_runs
            WHERE project_id=%s AND id=%s::uuid FOR UPDATE""",(project_id,run_id)
    ))
    if run is None:
        _deny("run_not_found")
    if run["state"]=="cancelled":
        return 0
    if run["state"] in ("completed","failed"):
        _deny("terminal_run_cannot_cancel")
    # The owning caller must retain separate outstanding paid reservations;
    # never mutate the budget ledger or pretend an uncertain charge is zero.
    rows=conn.execute(
        """UPDATE group_b_jobs SET state='cancelled',lease_token=NULL,
                     lease_until=NULL,last_error_code=%s,updated_at=now()
            WHERE project_id=%s AND run_id=%s::uuid
              AND state IN ('ready','running','partial')
            RETURNING id::text,block,lease_generation,evidence_sha256""",
        (reason,project_id,run_id)
    ).fetchall()
    for job_id,block,generation,evidence in rows:
        _append(conn,job_id,project_id,run_id,generation,"cancelled",evidence,reason=reason)
        conn.execute(
            """UPDATE group_b_block_states
                  SET status='unavailable',error_code=%s,updated_at=now()
                WHERE project_id=%s AND run_id=%s::uuid AND block=%s
                  AND status NOT IN ('complete','failed')""",
            (reason,project_id,run_id,block)
        )
    conn.execute(
        """UPDATE group_b_runs SET state='cancelled',lease_token=NULL,
              lease_until=NULL,last_error_code=%s,updated_at=now()
            WHERE project_id=%s AND id=%s::uuid""",(reason,project_id,run_id)
    )
    return len(rows)
