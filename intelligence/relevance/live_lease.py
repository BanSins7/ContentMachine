"""M14: strictly fenced LIVE item claim/heartbeat/release, no provider calls.

Internal project-authenticated worker port, NOT a paid dispatch permission.
A run must already have been moved into 'queued'/'running' by an authorized,
durable consent/budget service (future stages). This module never performs
that transition; pending_consent is unclaimable. Rights are rechecked from
live D1 inside the claim transaction; future paid send MUST recheck again.

PostgreSQL row locking and lease comparisons use the DATABASE clock.
Generations fence stale workers even if a token is accidentally reused.
"""
from __future__ import annotations

from uuid import uuid4

from .v1_contracts import (
    SREContractError, canonical_uuid, positive, project_id, reject, sha256_digest,
)

MIN_LEASE_SECONDS = 5
MAX_LEASE_SECONDS = 300
MAX_ATTEMPTS = 100

# M15: all claim/heartbeat/release transactions hold a SHARED workspace lock.
# Cancellation/pause/recovery take EXCLUSIVE workspace lock first, then run
# and item rows. Multiple workers may still claim different items in parallel.
# This closes the race in which a claim observes an active parent just before
# a concurrent cancellation invalidates the parent's leases.
WORKSPACE_SHARE_SQL = (
    "SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR SHARE"
)


def _share_workspace(conn, pid):
    if conn.execute(WORKSPACE_SHARE_SQL, (pid,)).fetchone() is None:
        reject("project_id", "LIVE workspace unavailable", "rights_denied")


CLAIM_SQL = """WITH candidate AS (
    SELECT i.id
    FROM sre_live_items i
    JOIN sre_live_runs r
      ON r.run_id=i.run_id AND r.project_id=i.project_id
    JOIN sre_live_batches b
      ON b.run_id=i.run_id AND b.project_id=i.project_id
    JOIN sre_live_batch_members m
      ON m.batch_id=b.batch_id AND m.run_id=i.run_id
     AND m.project_id=i.project_id AND m.item_id=i.id
    WHERE i.project_id=%s
      AND r.execution_mode='live'
      AND r.state IN ('queued','running')
      AND r.cancel_requested=FALSE
      AND b.state='queued'
      AND i.attempts < %s
      -- A stale queued/expired item carrying an unresolved technical
      -- reservation must first go through M15 recovery. A possible paid
      -- POST anywhere in the parent run freezes all automatic claims.
      AND NOT EXISTS (
        SELECT 1 FROM sre_live_attempt_fences a
         WHERE a.project_id=i.project_id AND a.run_id=i.run_id
           AND a.state IN ('dispatch_intent','unknown','completed','failed_known')
      )
      AND NOT EXISTS (
        SELECT 1 FROM sre_live_attempt_fences a
         WHERE a.project_id=i.project_id AND a.run_id=i.run_id
           AND a.item_id=i.id AND a.state='reserved'
      )
      AND (
        (i.state='queued' AND i.worker_token IS NULL AND i.lease_until IS NULL)
        OR
        (i.state='running' AND i.worker_token IS NOT NULL
         AND i.lease_until <= clock_timestamp())
      )
      AND (SELECT count(*) FROM sre_live_expected_pairs p
           WHERE p.project_id=i.project_id AND p.run_id=i.run_id
             AND p.item_id=i.id AND p.batch_id=b.batch_id
             AND p.state='not_evaluated') =
          jsonb_array_length(i.expected_hypothesis_keys)
    ORDER BY i.run_id,i.video_id,i.id
    FOR UPDATE OF i SKIP LOCKED
    LIMIT 1
)
UPDATE sre_live_items i
SET state='running',
    lease_generation=i.lease_generation+1,
    worker_token=%s::uuid,
    lease_until=clock_timestamp()+(%s * INTERVAL '1 second'),
    attempts=i.attempts+1,
    updated_at=clock_timestamp()
FROM candidate c
WHERE i.id=c.id
RETURNING i.id,i.run_id::text,i.video_id,i.content_version_id,
          i.content_sha256,i.lease_generation,i.worker_token::text,
          i.lease_until,i.attempts"""

HEARTBEAT_SQL = """UPDATE sre_live_items i
SET lease_until=clock_timestamp()+(%s * INTERVAL '1 second'),
    updated_at=clock_timestamp()
FROM sre_live_runs r
WHERE i.project_id=%s AND i.id=%s
  AND i.run_id=r.run_id AND i.project_id=r.project_id
  AND r.execution_mode='live' AND r.state IN ('queued','running')
  AND r.cancel_requested=FALSE
  AND i.state='running'
  AND i.worker_token=%s::uuid AND i.lease_generation=%s
  AND i.lease_until>clock_timestamp()
RETURNING i.lease_until"""

RELEASE_SQL = """UPDATE sre_live_items i
SET state='queued', worker_token=NULL, lease_until=NULL,
    updated_at=clock_timestamp()
FROM sre_live_runs r
WHERE i.project_id=%s AND i.id=%s
  AND i.run_id=r.run_id AND i.project_id=r.project_id
  AND r.execution_mode='live' AND r.state IN ('queued','running')
  AND r.cancel_requested=FALSE
  AND i.state='running'
  AND i.worker_token=%s::uuid AND i.lease_generation=%s
  AND i.lease_until>clock_timestamp()
RETURNING i.id"""

BLOCK_RIGHTS_SQL = """UPDATE sre_live_items
SET state='paused_rights',worker_token=NULL,lease_until=NULL,
    updated_at=clock_timestamp()
WHERE project_id=%s AND id=%s
  AND state='running' AND worker_token=%s::uuid
  AND lease_generation=%s
RETURNING id"""


def _duration(value):
    seconds = positive(value, "lease_seconds", maximum=MAX_LEASE_SECONDS)
    if seconds < MIN_LEASE_SECONDS:
        reject("lease_seconds", "lease duration below five seconds")
    return seconds


def _generation(value):
    return positive(value, "lease_generation", maximum=9223372036854775807)


def _item_id(value):
    return positive(value, "item_id", maximum=9223372036854775807)


def claim_live_item(conn, project, *, lease_seconds=60):
    """Claim at most one unblocked registered item, with a new fenced token.

    Uses READ COMMITTED + SELECT FOR UPDATE SKIP LOCKED: a concurrently
    locked eligible item is skipped, never double-leased. The returned object
    contains no provider credentials, approval flag or price authority.
    """
    from .live_content_gate import read_live_content_gate

    pid = project_id(project)
    seconds = _duration(lease_seconds)
    token = str(uuid4())
    with conn.transaction():
        _share_workspace(conn, pid)
        row = conn.execute(
            CLAIM_SQL, (pid, MAX_ATTEMPTS, token, seconds),
        ).fetchone()
        if row is None:
            return None
        (item_id, rid, video_id, cid, sha, generation,
         returned_token, expires_at, attempts) = row
        if returned_token != token:
            reject("worker_token", "database returned another worker token",
                   "lease_lost")
        try:
            gate = read_live_content_gate(
                conn, pid, cid, phase="before_external_send",
                external_llm=True, pins={"video_id": video_id, "content_version_id": cid,
                      "content_sha256": sha},
            )
            if (gate["content"]["project_id"] != pid
                    or gate["content"]["video_id"] != video_id
                    or gate["content"]["content_sha256"] != sha):
                reject("content", "claim data no longer matches D1",
                       "snapshot_mismatch")
        except SREContractError as exc:
            # Revocation or expiry is a BLOCKED item, not a positive
            # classification or a candidate for immediate paid retries.
            if exc.code not in (
                "rights_denied", "rights_expired", "snapshot_mismatch",
                "missing_provenance",
            ):
                raise
            blocked = conn.execute(
                BLOCK_RIGHTS_SQL, (pid, item_id, token, generation),
            ).fetchone()
            if blocked is None:
                reject("worker_token", "claim lost during rights check",
                       "lease_lost")
            return {
                "project_id": pid, "run_id": rid, "item_id": item_id,
                "status": "paused_rights", "reason_code": exc.code,
                "leased": False, "dispatch_allowed": False,
            }
        return {
            "project_id": pid, "run_id": rid, "item_id": item_id,
            "video_id": video_id, "content_version_id": cid,
            "content_sha256": sha, "status": "running",
            "lease_generation": generation, "worker_token": token,
            "lease_until": expires_at, "attempts": attempts,
            "leased": True, "dispatch_allowed": False,
        }


def heartbeat_live_item(conn, project, item_id, worker_token,
                        lease_generation, *, lease_seconds=60):
    """Only a still-current, unexpired DB lease can renew itself."""
    pid = project_id(project)
    item = _item_id(item_id)
    token = canonical_uuid(worker_token, "worker_token")
    generation = _generation(lease_generation)
    seconds = _duration(lease_seconds)
    with conn.transaction():
        _share_workspace(conn, pid)
        row = conn.execute(
            HEARTBEAT_SQL, (seconds, pid, item, token, generation),
        ).fetchone()
        if row is None:
            reject("worker_token", "stale/expired/cancelled LIVE worker lease",
                   "lease_lost")
        return {
            "project_id": pid, "item_id": item,
            "worker_token": token, "lease_generation": generation,
            "lease_until": row[0], "status": "running",
            "dispatch_allowed": False,
        }


def release_live_item(conn, project, item_id, worker_token, lease_generation):
    """A live lease owner may relinquish back to queue; never settle labels."""
    pid = project_id(project)
    item = _item_id(item_id)
    token = canonical_uuid(worker_token, "worker_token")
    generation = _generation(lease_generation)
    with conn.transaction():
        _share_workspace(conn, pid)
        row = conn.execute(
            RELEASE_SQL, (pid, item, token, generation),
        ).fetchone()
        if row is None:
            reject("worker_token", "stale/expired/cancelled LIVE worker lease",
                   "lease_lost")
        return {
            "project_id": pid, "item_id": item,
            "state": "queued", "released": True,
            "dispatch_allowed": False,
        }


__all__ = (
    "MIN_LEASE_SECONDS", "MAX_LEASE_SECONDS", "MAX_ATTEMPTS",
    "claim_live_item", "heartbeat_live_item", "release_live_item",
)
