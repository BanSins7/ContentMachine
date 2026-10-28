"""M35 optional Collector -> SRE inbox, grounded in C9 and canonical D1.

This is an INTERNAL trusted-host service, not an HTTP webhook. A SHA256 on a
transport envelope is not sender authentication. The host must authenticate
the project and the upstream producer, and supply an authorizer independent
of the payload. Neither Collector code nor a paid/provider client is imported.

The only intake side effect is S3 attach_discovery(), under the same SQL
transaction as immutable event ACK. Invalid persisted D1 provenance is
quarantined without attaching source content. Backfill is an independently
authorized direct D1 path; no Collector installation is needed.
"""
from __future__ import annotations

from .intake import _authority, attach_discovery
from .opus_consent import _actor, _authorized
from .opus_dispatch_intent import _committable
from .ports.d1_provenance import read_d1_provenance
from .ports.inbound import validate_collector_delivery
from .source_policy import verified_rights
from .taxonomy_adapter import read_authorized_taxonomy
from .v1_contracts import (
    SREContractError, canonical_uuid, positive, project_id, reject,
)

VERSION = "sre-collector-inbox/v1"
MAX_BACKFILL = 100
MAX_BIGINT = 9223372036854775807
ALLOWED_QUARANTINE_REASONS = frozenset((
    "rights_denied", "rights_expired", "missing_provenance",
    "snapshot_mismatch", "invalid_contract",
))

WORKSPACE_LOCK = """SELECT 1 FROM sre_workspaces
                    WHERE project_id=%s FOR UPDATE"""
INBOX_READ = """SELECT project_id,event_sha256,publication_id::text,
                       discovery_id,video_id,source_id,source_policy_version_id,
                       source_request_id,state,content_version_id,
                       content_sha256,quarantine_reason
                FROM sre_collector_inbox WHERE event_id=%s::uuid FOR UPDATE"""
INBOX_INSERT = """INSERT INTO sre_collector_inbox (
    event_id,project_id,event_sha256,publication_id,discovery_id,video_id,
    source_id,source_policy_version_id,source_request_id,
    occurred_at,d1_persisted_at,state,content_version_id,
    content_sha256,quarantine_reason,actor_id)
    VALUES (%s::uuid,%s,%s,%s::uuid,%s,%s,%s,%s,%s,
            %s::timestamptz,%s::timestamptz,%s,%s,%s,%s,%s)"""


def _approved(conn, project, publication):
    """Check approved C9 H1/H2 and SRE mirrored publication inside this snapshot."""
    pid = project_id(project)
    pub = canonical_uuid(publication, "approved_publication_id")
    # Unlike the public read-only loader, this is part of an atomic
    # REPEATABLE READ write transaction; it reads exactly the same snapshot
    # as the eventual S3 attach and durable ACK.
    read_authorized_taxonomy(conn, pid, pub)
    return pub


def _d1_event_check(conn, event):
    """Never translate Collector UUID query IDs into D1 BIGINT query IDs."""
    video, source, query, request, src, policy = _authority(
        conn, event["discovery_id"]
    )
    if (video != event["video_id"] or source != event["source_id"]
            or request != event["source_request_id"]
            or policy["id"] != event["source_policy_version_id"]):
        reject("collector_event.d1",
               "persisted D1 discovery/source/request/policy differs",
               "snapshot_mismatch")
    verified_rights(src, policy)  # Reviewed active source, not event rights.
    # query is the real D1 nullable BIGINT; Collector UUID candidates are
    # deliberately never used in SQL, compared to it, or cast to BIGINT.
    return query


def _result(event, publication, status, *, replayed, content_id=None,
            content_sha=None, reason=None, available_now=False):
    return {
        "schema_version": VERSION,
        "project_id": event["project_id"],
        "event_id": event["event_id"],
        "approved_publication_id": publication,
        "state": status,
        "replayed": replayed,
        "content_version_id": content_id,
        "content_sha256": content_sha,
        "quarantine_reason": reason,
        "readable_now": available_now,
        "paid_calls": 0,
    }


def receive_collector_delivery(
    conn, delivery, approved_publication_id, *,
    actor_id, actor_authorizer,
):
    """ACK once, or immutable quarantine, for a verified persisted D1 event.

    Only an authenticated producer/project host may call this; neither an
    incoming project ID nor the self-hash is sufficient authority. Use an
    idle autocommit PG connection to make ACK and S3 intake crash-atomic.
    Replay cannot restore a revoked S3 revision or change immutable event ID.
    """
    event = validate_collector_delivery(delivery)
    pid = project_id(event["project_id"])
    pub = canonical_uuid(approved_publication_id, "approved_publication_id")
    actor = _actor(actor_id)
    _authorized(actor_authorizer, pid, actor, "receive_sre_collector_delivery")
    _committable(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        if conn.execute(WORKSPACE_LOCK, (pid,)).fetchone() is None:
            reject("project_id", "project workspace not available",
                   "rights_denied")
        _approved(conn, pid, pub)
        existing = conn.execute(INBOX_READ, (event["event_id"],)).fetchone()
        if existing is not None:
            identity = (
                pid, event["event_sha256"], pub, event["discovery_id"],
                event["video_id"], event["source_id"],
                event["source_policy_version_id"], event["source_request_id"],
            )
            if tuple(existing[:8]) != identity:
                reject("event_id", "event id reused with a different project/pin",
                       "snapshot_mismatch")
            status, cid, digest, reason = existing[8:12]
            readable = False
            if status == "acked":
                try:
                    proof = read_d1_provenance(conn, pid, cid)
                    readable = proof["content_sha256"] == digest
                except SREContractError:
                    # Historical ACK remains immutable, not current access.
                    readable = False
            return _result(event, pub, status, replayed=True, content_id=cid,
                           content_sha=digest, reason=reason, available_now=readable)

        # Nested PG transaction is a SAVEPOINT. A denied D1 event must not
        # persist a partial S3 version; the outer transaction can still write
        # its minimal, non-content quarantine receipt.
        reason = None
        snapshot = None
        proof = None
        try:
            with conn.transaction():
                _d1_event_check(conn, event)
                snapshot = attach_discovery(conn, pid, event["discovery_id"])
                proof = read_d1_provenance(
                    conn, pid, snapshot["content_version_id"]
                )
                if (proof["video_id"] != event["video_id"]
                        or proof["discovery_id"] != event["discovery_id"]
                        or proof["source_id"] != event["source_id"]
                        or proof["source_policy_version_id"]
                           != event["source_policy_version_id"]
                        or proof["source_request_id"]
                           != event["source_request_id"]):
                    reject("collector_event.d1",
                           "attached D1 lineage is not event-pinned",
                           "snapshot_mismatch")
        except SREContractError as exc:
            reason = (exc.code if exc.code in ALLOWED_QUARANTINE_REASONS
                      else "invalid_contract")
        status = "quarantined" if reason else "acked"
        cid = snapshot["content_version_id"] if reason is None else None
        digest = snapshot["content_sha256"] if reason is None else None
        conn.execute(INBOX_INSERT, (
            event["event_id"], pid, event["event_sha256"], pub,
            event["discovery_id"], event["video_id"], event["source_id"],
            event["source_policy_version_id"], event["source_request_id"],
            event["occurred_at"], event["d1_persisted_at"], status,
            cid, digest, reason, actor,
        ))
        return _result(event, pub, status, replayed=False, content_id=cid,
                       content_sha=digest, reason=reason,
                       available_now=status == "acked")


def backfill_selected_d1_discoveries(
    conn, project, approved_publication_id, discovery_ids, *,
    actor_id, actor_authorizer,
):
    """Operator-selected real D1 imports. No Collector event or inbox receipt.

    This explicit source list is mandatory: never scan global discoveries and
    silently associate them to an unrelated project. Replay is idempotent via
    S3 intake_fingerprint; every record is validated by D1/S3 before commit.
    """
    pid = project_id(project)
    pub = canonical_uuid(approved_publication_id, "approved_publication_id")
    actor = _actor(actor_id)
    _authorized(actor_authorizer, pid, actor, "backfill_sre_d1")
    if type(discovery_ids) not in (list, tuple) or not 1 <= len(discovery_ids) <= MAX_BACKFILL:
        reject("discovery_ids", "explicit 1..100 D1 discovery IDs required")
    ids = [positive(i, "discovery_ids", maximum=MAX_BIGINT) for i in discovery_ids]
    if len(set(ids)) != len(ids):
        reject("discovery_ids", "duplicate D1 discovery IDs are forbidden")
    _committable(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        if conn.execute(WORKSPACE_LOCK, (pid,)).fetchone() is None:
            reject("project_id", "project workspace not available",
                   "rights_denied")
        _approved(conn, pid, pub)
        records = []
        for did in sorted(ids):
            snapshot = attach_discovery(conn, pid, did)
            proof = read_d1_provenance(conn, pid, snapshot["content_version_id"])
            if proof["discovery_id"] != did:
                reject("discovery_id", "D1 backfill provenance mismatch",
                       "snapshot_mismatch")
            records.append({
                "discovery_id": did,
                "content_version_id": snapshot["content_version_id"],
                "content_sha256": snapshot["content_sha256"],
                "replayed": snapshot["replayed"],
            })
        return {
            "schema_version": VERSION, "project_id": pid,
            "approved_publication_id": pub,
            "imported": len(records), "records": records,
            "collector_required": False, "paid_calls": 0,
        }


__all__ = (
    "VERSION", "receive_collector_delivery",
    "backfill_selected_d1_discoveries",
)
