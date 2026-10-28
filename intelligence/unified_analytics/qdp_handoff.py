"""UA-MOD-07: UAE-owned QDP handoff idempotency and collision quarantine.

Persist only AFTER a source-backed current QDP Qualified Set read and the
exact QDP outbox event have both been checked in one caller-injected DB
transaction. The receipt means accepted event identity, NOT A/B/C completion.
An authenticated caller/DB role and replay policy are external obligations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from uuid import UUID

from .contracts import AnalyticsBoundaryError
from .dto import InputScope, ReadState
from .qdp_adapter import QDPQualifiedSetReader, _qdp_hash, _sha, _uuid, _one, _db

EVENT_SQL = """
SELECT event_id,project_id,run_id,video_id,analysis_group,
       event_type,payload,payload_sha256
FROM qdp_outbox WHERE event_id=%s::uuid AND project_id=%s
"""
INSERT_SQL = """
INSERT INTO uae_qdp_handoff_inbox (
 event_id,project_id,qdp_run_id,video_id,analysis_group,
 input_sha256,payload_sha256,verified_reference_sha256)
VALUES (%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s)
ON CONFLICT (event_id) DO NOTHING RETURNING event_id
"""
STORED_SQL = """
SELECT event_id,project_id,qdp_run_id,video_id,analysis_group,
       input_sha256,payload_sha256,verified_reference_sha256
FROM uae_qdp_handoff_inbox WHERE event_id=%s::uuid
"""
QUARANTINE_SQL = """
INSERT INTO uae_qdp_handoff_quarantine(
 event_id,project_id,observed_payload_sha256,
 recorded_payload_sha256,reason_code)
VALUES (%s::uuid,%s,%s,%s,%s)
ON CONFLICT (event_id,observed_payload_sha256,reason_code)
DO NOTHING
"""


@dataclass(frozen=True, slots=True)
class QDPHandoffEvent:
    event_id: str
    scope: InputScope
    group: str
    preparation_sha256: str
    payload_sha256: str
    schema_version: str = "qdp-handoff/v1"

    def __post_init__(self) -> None:
        if not _uuid(self.event_id):
            raise AnalyticsBoundaryError("invalid_handoff_event", "Canonical event UUID required")
        if not isinstance(self.scope, InputScope):
            raise AnalyticsBoundaryError("invalid_handoff_scope", "Expected QDP input scope")
        if self.group not in ("A","B","C") or type(self.group) is not str:
            raise AnalyticsBoundaryError("invalid_handoff_group", "Invalid QDP analysis group")
        if not _sha(self.preparation_sha256) or not _sha(self.payload_sha256):
            raise AnalyticsBoundaryError("invalid_handoff_hash", "SHA256 required")
        if self.schema_version != "qdp-handoff/v1":
            raise AnalyticsBoundaryError("unsupported_schema_version", "Unknown QDP handoff")


@dataclass(frozen=True, slots=True)
class QDPHandoffOutcome:
    state: str  # accepted | replayed | quarantined
    event_id: str
    group: str
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.state not in ("accepted","replayed","quarantined"):
            raise ValueError("invalid QDP handoff outcome")
        if (self.state == "quarantined") is (self.reason_code is None):
            raise ValueError("quarantine requires a reason; no-success has none")


def register_verified_handoff(
    conn: Any, reader: QDPQualifiedSetReader, event: QDPHandoffEvent,
) -> QDPHandoffOutcome:
    """Call from authorized scope under transaction on SAME connection.

    A QDP event without a current qualified group can never be accepted.
    Duplicate exact event is an idempotent replay. A reused event ID with
    different SHA/group is recorded as a *quarantined observation*, never an
    accepted alternate interpretation. Calling code must COMMIT quarantine
    outcomes rather than raising and rolling back its transaction.
    """
    if not isinstance(reader, QDPQualifiedSetReader) or not isinstance(event, QDPHandoffEvent):
        raise AnalyticsBoundaryError("invalid_handoff", "Trusted UAE reader and event required")
    scope = event.scope
    if reader._conn is not conn or reader._pins.scope != scope:
        raise AnalyticsBoundaryError("qdp_handoff_scope_mismatch", "Same current DB scope required")
    validated = reader.read_qualified_membership(scope)
    if validated.state is not ReadState.LOCATED or validated.reference is None:
        raise AnalyticsBoundaryError("qdp_handoff_unqualified", "Current qualified QDP row required")
    group_pos = ("A","B","C").index(event.group)
    if event.preparation_sha256 != reader._pins.preparation_shas[group_pos]:
        raise AnalyticsBoundaryError("qdp_handoff_preparation_mismatch", "Unexpected preparation SHA")
    original = _one(conn, EVENT_SQL, (event.event_id, scope.project_id))
    if original is None:
        raise AnalyticsBoundaryError("qdp_handoff_event_missing", "No authoritative QDP outbox event")
    if (str(original.get("event_id")) != event.event_id
            or original.get("project_id") != scope.project_id
            or str(original.get("run_id")) != scope.qdp_run_id
            or original.get("video_id") != scope.video_id
            or original.get("analysis_group") != event.group
            or original.get("event_type") != "preparation_ready"):
        raise AnalyticsBoundaryError("qdp_handoff_event_scope_mismatch",
                                     "Outbox event does not match current preparation")
    payload = original.get("payload")
    if (type(payload) is not dict or
            _qdp_hash(payload) != original.get("payload_sha256") or
            payload.get("schema_version") != "qdp-event/v1" or
            payload.get("event_type") != "preparation_ready" or
            payload.get("project_id") != scope.project_id or
            payload.get("run_id") != scope.qdp_run_id or
            payload.get("video_id") != scope.video_id or
            payload.get("analysis_group") != event.group or
            payload.get("state") != "ready" or
            payload.get("reason_code") is not None or
            payload.get("input_sha256") != event.preparation_sha256):
        raise AnalyticsBoundaryError("qdp_handoff_event_integrity_failed",
                                     "QDP original immutable event invalid")

    def quarantine(existing: Mapping[str,Any] | None, why: str) -> QDPHandoffOutcome:
        _db(conn, QUARANTINE_SQL, (
            event.event_id, scope.project_id, event.payload_sha256,
            existing.get("payload_sha256") if existing else original["payload_sha256"],
            why,
        ))
        return QDPHandoffOutcome("quarantined",event.event_id,event.group,why)

    if event.payload_sha256 != original["payload_sha256"]:
        return quarantine(_one(conn, STORED_SQL, (event.event_id,)),
                          "event_payload_collision")
    inserted = _one(conn, INSERT_SQL, (
        event.event_id, scope.project_id, scope.qdp_run_id, scope.video_id,
        event.group, event.preparation_sha256, event.payload_sha256,
        validated.reference.lineage_sha256,
    ))
    if inserted is not None:
        return QDPHandoffOutcome("accepted",event.event_id,event.group)
    existing = _one(conn, STORED_SQL, (event.event_id,))
    if existing is None:
        raise AnalyticsBoundaryError("qdp_handoff_race", "Could not resolve existing event")
    if (existing.get("project_id") != scope.project_id or
            str(existing.get("qdp_run_id")) != scope.qdp_run_id or
            existing.get("video_id") != scope.video_id or
            existing.get("analysis_group") != event.group or
            existing.get("input_sha256") != event.preparation_sha256 or
            existing.get("payload_sha256") != event.payload_sha256):
        return quarantine(existing,"event_preparation_collision")
    # Full lineage SHA can change after an authorized rights recheck: it is
    # evidence context, not a permit to change immutable source identity.
    return QDPHandoffOutcome("replayed",event.event_id,event.group)


__all__ = (
    "QDPHandoffEvent", "QDPHandoffOutcome", "register_verified_handoff",
    "EVENT_SQL", "INSERT_SQL", "STORED_SQL", "QUARANTINE_SQL",
)
