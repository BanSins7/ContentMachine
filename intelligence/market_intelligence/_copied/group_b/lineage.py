"""GB-MOD-05: opt-in exact Group A binding for a *draft* Group B run.

Reads the completed GA v1 snapshot with independent source/media verification,
then pins A003/A004 ID + result SHA + manifest SHA + policy in ONE transaction.
No QDP ACK, model execution, mutable "latest", or automatic background work.
"""
from __future__ import annotations
from collections.abc import Callable
from typing import Any

from .adapters.group_a import GroupAExactPin, read_exact_a
from .errors import DependencyUnavailable, DependencyContractMismatch
from .repository import require_runtime_schema


def bind_exact_group_a(connection_factory: Callable[[], Any], *, b_run_id: str,
                       ga_result_id: int, expected_sha256: str) -> GroupAExactPin:
    """Attach only one completed A version, preserving historical B draft identity.

    The owning factory must commit the outermost transaction on context exit.
    Caller must have authenticated access to the exact B project/run; this
    low-level method is not an HTTP permission boundary.
    """
    if not callable(connection_factory):
        raise ValueError("Explicit owned connection factory is required")
    with connection_factory() as conn:
        with conn.transaction():
            require_runtime_schema(conn)
            try:
                lock = conn.execute(
                    """SELECT project_id,state,video_a_analysis_id,
                              carousel_a_analysis_id,a_snapshot_sha256,
                              media_manifest_sha256,source_policy_version_id
                         FROM group_b_runs WHERE id=%s::uuid FOR UPDATE""",
                    (b_run_id,)
                ).fetchone()
            except Exception:
                raise DependencyUnavailable("storage", "b_run_lock_unavailable") from None
            if lock is None:
                raise DependencyUnavailable("storage", "b_run_not_found")
            if lock[1] != "draft":
                raise DependencyUnavailable("group_a", "b_run_not_draft")
            pinned = read_exact_a(conn, b_run_id=b_run_id, ga_result_id=ga_result_id,
                                  expected_sha256=expected_sha256)
            video_a = pinned.a_analysis_id if pinned.content_type == "video" else None
            carousel_a = pinned.a_analysis_id if pinned.content_type == "carousel" else None
            expected = (video_a, carousel_a, pinned.result_sha256,
                        pinned.manifest_sha256, pinned.source_policy_version_id)
            current = (lock[2],lock[3],lock[4],lock[5],lock[6])
            if current == expected:
                return pinned
            if current != (None,None,None,None,None):
                raise DependencyUnavailable("group_a", "b_a_pin_conflict")
            result = conn.execute(
                """UPDATE group_b_runs
                      SET video_a_analysis_id=%s,
                          carousel_a_analysis_id=%s,
                          a_snapshot_sha256=%s,
                          media_manifest_sha256=%s,
                          source_policy_version_id=%s,
                          updated_at=now()
                    WHERE id=%s::uuid AND project_id=%s
                      AND state='draft'
                      AND video_a_analysis_id IS NULL AND carousel_a_analysis_id IS NULL
                      AND a_snapshot_sha256 IS NULL AND media_manifest_sha256 IS NULL
                      AND source_policy_version_id IS NULL
                    RETURNING id""",
                (*expected,b_run_id,pinned.project_id)
            ).fetchone()
            if result is None:
                raise DependencyUnavailable("group_a", "a_pin_concurrent_change")
    return pinned
