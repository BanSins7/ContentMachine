"""FIX-05: durable, opt-in SRE completion event receiver owned by QDP.

The peer producer owns authentication, its outbox, and its delivery ACK ledger.
The event announces only the finished SRE run. QDP MUST independently read its
authoritative DB results and manifest; no supplied decisions, approvals or
spending authorization are trusted. This module creates NO QDP runs/jobs,
calls NO providers and changes NO peer-owned tables.

Only "sre.classification_run_completed" from the SRE
"sre-analytics-event/v1" contract is accepted in FIX-05; item ready events
belong to another SRE transport and are NOT automatically qualified.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from datetime import datetime

from .contracts import QDPError, project_id, sha256
from .repository import _connect, _sre_snapshot
from .schema import require_qdp_schema

VERSION = "sre-analytics-event/v1"
COMPLETED = "sre.classification_run_completed"
ALLOWED_COLUMNS = frozenset({
    "schema_version", "event_id", "event_type", "project_id",
    "run_id", "publication_id", "taxonomy_version_id", "occurred_at",
})
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


def _canonical_uuid(value, field):
    if type(value) is not str or not UUID_RE.fullmatch(value):
        raise QDPError("sre_event_invalid", "Invalid " + field, 422)
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError("noncanonical UUID")
    except ValueError as exc:
        raise QDPError("sre_event_invalid", "Invalid " + field, 422) from exc
    return value


def _event_id_only(payload):
    if not isinstance(payload, Mapping):
        raise QDPError("sre_event_invalid", "SRE event must be an object", 422)
    return _canonical_uuid(payload.get("event_id"), "event_id")


def validate_completion_event(payload: Mapping) -> dict:
    """Pure compatibility validator for SRE's published summary event v1."""
    if type(payload) is not dict or set(payload) != ALLOWED_COLUMNS:
        raise QDPError("sre_event_invalid", "Unexpected or missing SRE summary fields", 422)
    if payload["schema_version"] != VERSION:
        raise QDPError("sre_event_version", "Unsupported SRE event version", 409)
    if payload["event_type"] != COMPLETED:
        raise QDPError("sre_event_type", "Only completed SRE runs enter QDP", 409)
    eid = _canonical_uuid(payload["event_id"], "event_id")
    rid = _canonical_uuid(payload["run_id"], "run_id")
    publication = _canonical_uuid(payload["publication_id"], "publication_id")
    pid = project_id(payload["project_id"])
    taxid = payload["taxonomy_version_id"]
    if type(taxid) is not int or taxid <= 0:
        raise QDPError("sre_event_invalid", "Invalid SRE taxonomy version", 422)
    occurred = payload["occurred_at"]
    if type(occurred) is not str or len(occurred) > 48:
        raise QDPError("sre_event_invalid", "Invalid SRE event timestamp", 422)
    try:
        parsed = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
    except ValueError as exc:
        raise QDPError("sre_event_invalid", "Malformed SRE event timestamp", 422) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise QDPError("sre_event_invalid", "SRE timestamp must have timezone", 422)
    # Do not normalize/rewrite the transport body: original hash remains exact.
    return dict(payload)


def _quarantine_response(event_id: str, digest: str, code: str) -> dict:
    return {
        "ack": False, "state": "quarantined",
        "event_id": event_id, "payload_sha256": digest,
        "reason_code": code,
    }


def _log_attempt(conn, event_id: str, pid: str | None, observed: str,
                 claimed: str | None, previous: str | None, reason: str):
    """Append an immutable, payload-free forensic fact; repeated delivery safe."""
    conn.execute(
        """INSERT INTO qdp_sre_inbox_quarantine
           (event_id,project_id,observed_sha256,claimed_sha256,
            previous_sha256,reason_code)
           VALUES (%s::uuid,%s,%s,%s,%s,%s)
           ON CONFLICT (event_id,observed_sha256,reason_code) DO NOTHING""",
        (event_id, pid, observed,
         claimed if isinstance(claimed, str) and HEX64.fullmatch(claimed) else None,
         previous, reason),
    )


def receive_sre_completion(payload: dict, announced_sha256: str, *,
                           connect=None, _before_commit=None) -> dict:
    """Durably consume one SRE run-completed notification; ACK only post-COMMIT.

    Transactional semantics:
    - Same event_id + same hash: replay prior durable outcome, with no new work.
    - Same event_id + different hash: unchanged original row + conflict audit.
    - Valid new event: DB LIVE run, same publication and taxonomy,
      exact manifest/results/evidence and review re-read before ACCEPTED.
    - Semantically bad but well-formed summary: persist QUARANTINED, never ACK.
    - Missing integration schema (503): roll back, no ACK; sender may retry.
    - _before_commit is a local fault-injection seam; NEVER wire to HTTP.
    """
    eid = _event_id_only(payload)
    if type(announced_sha256) is not str or not HEX64.fullmatch(announced_sha256):
        raise QDPError("sre_event_digest_invalid", "Expected transport SHA256", 422)
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, default=str)
    if len(serialized.encode("utf-8")) > 8192:
        raise QDPError("sre_event_oversize", "SRE run completion envelope too large", 422)
    digest = sha256(payload)
    pid = payload.get("project_id") if type(payload.get("project_id")) is str else None
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            require_qdp_schema(conn)
            existing = conn.execute(
                """SELECT event_id,project_id,sre_run_id,payload_sha256,
                          state,reason_code
                   FROM qdp_sre_inbox WHERE event_id=%s::uuid FOR UPDATE""",
                (eid,),
            ).fetchone()
            if existing:
                if (existing["payload_sha256"] != digest or
                        announced_sha256 != digest):
                    _log_attempt(conn, eid, pid, digest, announced_sha256,
                                 existing["payload_sha256"], "event_id_hash_conflict")
                    outcome = _quarantine_response(eid, digest, "event_id_hash_conflict")
                elif existing["state"] == "accepted":
                    outcome = {"ack": True, "state": "accepted", "replayed": True,
                               "event_id": eid, "project_id": existing["project_id"],
                               "sre_run_id": str(existing["sre_run_id"]),
                               "payload_sha256": digest}
                elif existing["state"] == "quarantined":
                    outcome = _quarantine_response(eid, digest,
                                                    existing["reason_code"])
                else:
                    # A crashed pre-FIX-05 "received" record must NOT be
                    # accepted without re-reading SRE; quarantine explicitly.
                    _log_attempt(conn, eid, pid, digest, announced_sha256,
                                 existing["payload_sha256"], "unverified_pending_event")
                    outcome = _quarantine_response(eid, digest,
                                                    "unverified_pending_event")
            elif announced_sha256 != digest:
                _log_attempt(conn, eid, pid, digest, announced_sha256,
                             None, "transport_hash_mismatch")
                outcome = _quarantine_response(eid, digest, "transport_hash_mismatch")
            else:
                # Malformed events with an event UUID are recorded in the
                # payload-free quarantine log. They cannot get an accepted ACK.
                try:
                    event = validate_completion_event(payload)
                except QDPError as exc:
                    _log_attempt(conn, eid, pid, digest, announced_sha256,
                                 None, exc.code)
                    outcome = _quarantine_response(eid, digest, exc.code)
                else:
                    # A project must exist before inserting FK-scoped inbox.
                    found = conn.execute(
                        "SELECT 1 FROM project_registry WHERE id=%s",
                        (event["project_id"],)).fetchone()
                    if not found:
                        _log_attempt(conn, eid, pid, digest, announced_sha256,
                                     None, "project_not_found")
                        outcome = _quarantine_response(eid, digest,
                                                        "project_not_found")
                    else:
                        # Concurrent INSERTs on event_id serialize via unique
                        # index; ON CONFLICT ensures only one actual inbox row.
                        inserted = conn.execute(
                            """INSERT INTO qdp_sre_inbox
                               (event_id,project_id,sre_run_id,schema_version,
                                event_type,payload_sha256,payload)
                               VALUES (%s::uuid,%s,%s::uuid,%s,%s,%s,%s::jsonb)
                               ON CONFLICT (event_id) DO NOTHING
                               RETURNING event_id""",
                            (eid, event["project_id"], event["run_id"], VERSION,
                             COMPLETED, digest, serialized),
                        ).fetchone()
                        if not inserted:
                            # Same transaction only reaches here when another
                            # writer committed after our earlier SELECT. Fail
                            # closed rather than acknowledging unseen state.
                            _log_attempt(conn, eid, pid, digest, announced_sha256,
                                         None, "concurrent_delivery")
                            outcome = _quarantine_response(eid, digest,
                                                            "concurrent_delivery")
                        else:
                            try:
                                run, selection = _sre_snapshot(
                                    conn, event["project_id"], event["run_id"])
                                if run["taxonomy_version_id"] != event["taxonomy_version_id"]:
                                    raise QDPError("sre_event_taxonomy_mismatch",
                                                   "Event taxonomy differs from DB", 409)
                                snap = run.get("dataset_snapshot")
                                if (not isinstance(snap, dict) or
                                        snap.get("publication_id") != event["publication_id"]):
                                    raise QDPError("sre_event_publication_mismatch",
                                                   "Event publication differs from DB", 409)
                            except QDPError as exc:
                                if exc.status == 503:
                                    # No premature quarantine of a valid event
                                    # when only an upstream migration is absent.
                                    raise
                                conn.execute(
                                    """UPDATE qdp_sre_inbox
                                       SET state='quarantined',reason_code=%s,
                                           processed_at=now()
                                       WHERE event_id=%s::uuid AND state='received'""",
                                    (exc.code, eid),
                                )
                                _log_attempt(conn, eid, pid, digest,
                                             announced_sha256, None, exc.code)
                                outcome = _quarantine_response(eid, digest, exc.code)
                            else:
                                conn.execute(
                                    """UPDATE qdp_sre_inbox
                                       SET state='accepted',processed_at=now()
                                       WHERE event_id=%s::uuid AND state='received'""",
                                    (eid,),
                                )
                                # The event does not authorize create_run,
                                # enqueue a job, or call the SRE provider.
                                outcome = {
                                    "ack": True, "state": "accepted",
                                    "replayed": False, "event_id": eid,
                                    "project_id": event["project_id"],
                                    "sre_run_id": event["run_id"],
                                    "payload_sha256": digest,
                                    "manifest_sha256": run["manifest_digest"],
                                    "qualified_count": selection["totals"]["qualified"],
                                }
            if _before_commit is not None:
                _before_commit()
        # Only here has transaction.__exit__ successfully committed. The
        # caller/peer records upstream ACK after observing this return value.
        return outcome
