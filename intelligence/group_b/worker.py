"""GB-MOD-11: opt-in, fenced LOCAL work cycle and dry-run preview only.

No B5 algorithm is implemented until GB-MOD-12. A result may be marked
complete only if a separately installed trusted B5 verifier attests the
content-addressed output; all absent/failed checks stay partial. Paid jobs
are not executed: stage09 transport remains unconditionally blocked.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import canonical_hash
from .errors import DependencyUnavailable, ExecutionNotApproved
from .orchestrator import (
    JobClaim, claim_local, finish_job, _row, _uuid,
)
from .readiness import require_fresh_block

@dataclass(frozen=True,slots=True)
class WorkerSources:
    """Trusted per-run selectors; caller must authenticate project/run."""
    ga_result_id: int
    ga_result_sha256: str
    media_roots: tuple[str,...]
    observation_catalog: Any


@dataclass(frozen=True,slots=True)
class LocalResult:
    """Stage12 will define actual B5 typed output; this is only its envelope."""
    block: str
    input_evidence_sha256: str
    output: dict
    output_sha256: str

    def verify_digest(self) -> bool:
        try:
            return (type(self.output) is dict and
                    canonical_hash(self.output)==self.output_sha256)
        except (TypeError,ValueError):
            return False


@dataclass(frozen=True,slots=True)
class WorkCycle:
    status: str
    block: str | None
    job_id: str | None
    generation: int | None
    reason: str | None = None


class LocalWorker:
    """Caller-owned PG connection factory; no background thread or timers.

    One transaction claims the lease and commits before any expensive
    media I/O. A second connection rechecks QDP/A/rights/bytes right before
    deterministic local execution. A third transaction fences finalization.
    Stale workers cannot complete a cancelled or stolen lease. The caller
    must arrange heartbeats if local execution lasts beyond lease_seconds.
    """
    def __init__(self, connection_factory, *,
                 executor=None, result_verifier=None):
        if not callable(connection_factory):
            raise TypeError("Connection factory required")
        self._connect=connection_factory
        self._executor=executor
        self._verifier=result_verifier

    def preview(self,*,project_id: str) -> tuple[str,...]:
        """Read-only dry-run; does not claim/heartbeat/execute or bill."""
        with self._connect() as conn:
            return tuple(row[0] for row in conn.execute(
                """SELECT j.block FROM group_b_jobs j
                   JOIN group_b_runs r ON r.project_id=j.project_id AND r.id=j.run_id
                   JOIN group_b_inbox i ON i.id=r.inbox_id AND i.project_id=r.project_id
                  WHERE j.project_id=%s AND j.kind='local'
                    AND j.state='ready' AND j.attempts<j.max_attempts
                    AND r.state IN ('queued','running','partial')
                    AND i.status='accepted'
                    AND NOT EXISTS (
                     SELECT 1 FROM unnest(j.depends_on) dep WHERE NOT EXISTS (
                       SELECT 1 FROM group_b_jobs predecessor
                       WHERE predecessor.project_id=j.project_id
                         AND predecessor.run_id=j.run_id
                         AND predecessor.block=dep AND predecessor.state='complete'
                     ))
                  ORDER BY j.created_at,j.id""",(project_id,)
            ).fetchall())

    def run_once(self,*,project_id: str, sources: WorkerSources | None,
                 lease_seconds: int = 60, dry_run: bool = False) -> WorkCycle:
        if dry_run:
            previews=self.preview(project_id=project_id)
            return WorkCycle("dry_run",previews[0] if previews else None,None,None,
                             "no_eligible_jobs" if not previews else None)
        if type(project_id) is not str:
            raise ValueError("Project ID required")
        with self._connect() as conn:
            with conn.transaction():
                claim=claim_local(conn,project_id=project_id,
                                  lease_seconds=lease_seconds)
        if claim is None:
            return WorkCycle("empty",None,None,None,"no_eligible_jobs")
        if claim.kind!="local" or claim.block!="B5":
            raise ExecutionNotApproved()
        if sources is None or not isinstance(sources,WorkerSources):
            self._partial(claim,"preflight_unconfigured")
            return WorkCycle("partial",claim.block,claim.id,claim.generation,
                             "preflight_unconfigured")
        try:
            with self._connect() as conn:
                with conn.transaction():
                    require_fresh_block(
                        conn,project_id=claim.project_id,b_run_id=claim.run_id,
                        ga_result_id=sources.ga_result_id,
                        ga_result_sha256=sources.ga_result_sha256,
                        media_roots=sources.media_roots,
                        observation_catalog=sources.observation_catalog,
                        block=claim.block,
                        expected_manifest_sha256=claim.evidence_sha256
                    )
        except Exception:
            # This stage cannot adjudicate an unexpected adapter failure as
            # a measured B5 no-cuts result. Keep an explicit partial.
            self._partial(claim,"preflight_unavailable")
            return WorkCycle("partial",claim.block,claim.id,claim.generation,
                             "preflight_unavailable")
        executor=getattr(self._executor,"execute_local",None)
        verify=getattr(self._verifier,"verify_local_result",None)
        if not callable(executor) or not callable(verify):
            self._partial(claim,"local_engine_not_connected")
            return WorkCycle("partial",claim.block,claim.id,claim.generation,
                             "local_engine_not_connected")
        try:
            result=executor(claim)
        except Exception:
            self._partial(claim,"local_engine_error")
            return WorkCycle("partial",claim.block,claim.id,claim.generation,
                             "local_engine_error")
        if (not isinstance(result,LocalResult) or result.block!=claim.block or
            result.input_evidence_sha256!=claim.evidence_sha256 or
            not result.verify_digest()):
            self._partial(claim,"local_result_invalid")
            return WorkCycle("partial",claim.block,claim.id,claim.generation,
                             "local_result_invalid")
        try:
            accepted=verify(claim,result)
        except Exception:
            accepted=False
        if accepted is not True:
            self._partial(claim,"local_result_unverified")
            return WorkCycle("partial",claim.block,claim.id,claim.generation,
                             "local_result_unverified")
        with self._connect() as conn:
            with conn.transaction():
                finish_job(conn,claim,state="complete",
                           result_sha256=result.output_sha256)
        return WorkCycle("complete",claim.block,claim.id,claim.generation)

    def _partial(self,claim: JobClaim,reason: str):
        with self._connect() as conn:
            with conn.transaction():
                finish_job(conn,claim,state="partial",reason=reason)


class PaidWorker:
    """Deliberate fail-closed paid path: reservation != dispatch approval.

    GB11 does not have a real authenticated provider receipt verifier, nor
    a production QDP+GA catalog; do not enable POST by accepting a dict
    approved_attempt. GB12+ may implement local B5, later integration
    can introduce a distinct trusted provider transport/worker.
    """
    def run_once(self,*args,**kwargs):
        raise ExecutionNotApproved()
