"""One explicitly invoked scheduling step for the B+C certified fan-in.

This is intentionally not a daemon: deployment supplies trusted ports,
connection lifetime, scheduler cadence and current source verifiers.
"""
from __future__ import annotations

from dataclasses import dataclass

from .pipeline import (
    PROJECT, GroupBCPipeline, JointBlocked, JointReceipt, _record,
)


@dataclass(frozen=True, slots=True)
class StepResult:
    prepared: JointReceipt | None
    delivered: JointReceipt | None
    blocked_pairs: tuple[str, ...]
    candidates_seen: int


def find_ready_pairs(conn, *, project_id: str, limit: int = 32) -> tuple[tuple[str, str], ...]:
    if not PROJECT.fullmatch(project_id) or type(limit) is not int or not 1 <= limit <= 100:
        raise JointBlocked("invalid_candidate_scope")
    cursor = conn.execute(
        """SELECT br.id::text AS b_run_id,cr.id::text AS c_run_id
           FROM group_b_runs br
           JOIN group_b_inbox bi
             ON bi.project_id=br.project_id AND bi.id=br.inbox_id
            AND bi.video_id=br.video_id AND bi.source_id=br.source_id
           JOIN group_c_runs cr
             ON cr.project_id=br.project_id AND cr.video_id=br.video_id
            AND cr.source_id=br.source_id
            AND cr.source_policy_version_id=br.source_policy_version_id
           JOIN group_c_inbox ci
             ON ci.project_id=cr.project_id AND ci.event_id=cr.inbox_event_id
            AND ci.video_id=cr.video_id AND ci.qdp_run_id=bi.qdp_run_id
            AND ci.input_sha256=cr.qdp_input_sha256
          WHERE br.project_id=%s AND br.state='completed'
            AND cr.state='completed'
            AND bi.status='accepted' AND ci.state='accepted'
            AND br.result_analysis_id IS NOT NULL
            AND cr.calculated_metric_id IS NOT NULL
            AND br.source_policy_version_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM group_bc_joint_receipts j
                 WHERE j.project_id=br.project_id
                   AND j.b_run_id=br.id AND j.c_run_id=cr.id
            )
          ORDER BY GREATEST(br.updated_at,cr.updated_at) DESC,br.id,cr.id
          LIMIT %s""",
        (project_id, limit),
    )
    results = cursor.fetchall()
    if not results:
        return ()
    from collections.abc import Mapping
    return tuple(
        (str(row["b_run_id"]), str(row["c_run_id"]))
        if isinstance(row, Mapping) else (str(row[0]), str(row[1]))
        for row in results
    )


class GroupBCSupervisor:
    """Complete one safe, bounded joining + optional delivery step.

    Invoke after B/C worker completion or from the host's existing scheduler.
    The host owns every privileged adapter. An unverified pair is skipped,
    not converted to a success flag. Any surprise DB/provider failure escapes.
    """

    def __init__(self, connection_factory, *, pipeline: GroupBCPipeline | None = None):
        if not callable(connection_factory):
            raise JointBlocked("connection_factory_required")
        self.connection_factory = connection_factory
        self.pipeline = pipeline or GroupBCPipeline()

    def step(self, *, project_id: str, b_current_provider,
             b_compatibility, b_verifier, c_ports, sink=None,
             max_candidates: int = 32) -> StepResult:
        if not PROJECT.fullmatch(project_id):
            raise JointBlocked("invalid_project")
        if not callable(b_current_provider):
            raise JointBlocked("current_source_port_required")
        with self.connection_factory() as conn:
            pairs = find_ready_pairs(conn, project_id=project_id,
                                     limit=max_candidates)
        prepared = None
        blocked = []
        for b_run_id, c_run_id in pairs:
            try:
                with self.connection_factory() as conn:
                    prepared = self.pipeline.prepare(
                        conn, project_id=project_id,
                        b_run_id=b_run_id, c_run_id=c_run_id,
                        # Actual QDP/GA freshness is loaded INSIDE prepare's
                        # serializable transaction, not earlier on the browser.
                        b_current=b_current_provider,
                        b_compatibility=b_compatibility,
                        b_verifier=b_verifier, c_ports=c_ports,
                    )
                break
            except JointBlocked as exc:
                blocked.append(exc.code)

        delivered = None
        if sink is not None:
            with self.connection_factory() as conn:
                delivered = self.pipeline.deliver_one(
                    conn, project_id=project_id,
                    b_current_provider=b_current_provider,
                    b_compatibility=b_compatibility, b_verifier=b_verifier,
                    c_ports=c_ports, sink=sink,
                    # Skip stale older queued pairs when a new certified
                    # receipt is ready. Operator can retry old IDs separately.
                    receipt_id=prepared.receipt_id if prepared is not None else None,
                )
        return StepResult(
            prepared=prepared, delivered=delivered,
            blocked_pairs=tuple(blocked), candidates_seen=len(pairs),
        )
