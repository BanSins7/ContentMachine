"""GB-MOD-04: atomic QDP intake and post-commit acknowledgment.

Only a *current* authoritative QDP outbox/preparation can create a B draft.
Durable receipt + draft job + eight not_run states share one transaction.
The optional upstream ACK happens only after the connection commits/closes.
No Group A analysis or paid LLM is performed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import Any, Protocol
import hashlib

from .adapters.qdp import QDPHandoff, VerifiedPreparation, read_current_preparation, require_qdp_schema, _sha
from .contracts import ContractError, canonical_hash
from .errors import DependencyUnavailable, DependencyProbeFailed
from .repository import require_runtime_schema, _sha256_migration


class QDPAcknowledger(Protocol):
    def acknowledge(self, *, event_id: str, durable_receipt: str) -> None: ...


@dataclass(frozen=True, slots=True)
class InboxReceipt:
    event_id: str
    project_id: str
    inbox_id: str
    run_id: str
    receipt_key: str
    newly_created: bool
    acknowledged: bool = False

    @property
    def execution_ready(self) -> bool:
        # QDP ACK is not proof that A/media/LLM B-analysis is complete.
        return False


@dataclass(frozen=True, slots=True)
class _TransactionOutcome:
    receipt: InboxReceipt | None
    conflict_code: str | None


def _require_consumer_tables(conn) -> None:
    require_runtime_schema(conn)
    rows = conn.execute(
        "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' "
        "AND tablename = ANY(%s)", (["group_b_inbox_receipts","group_b_inbox_conflicts"],)
    ).fetchall()
    if {r[0] for r in rows} != {"group_b_inbox_receipts","group_b_inbox_conflicts"}:
        raise DependencyUnavailable("storage", "group_b_receipt_schema_missing")
    installed = conn.execute(
        "SELECT sha256 FROM schema_migrations WHERE version=%s",
        ("056_group_b_qdp_receipts.sql",)
    ).fetchone()
    if not installed or installed[0] != _sha256_migration("056_group_b_qdp_receipts.sql"):
        raise DependencyUnavailable("storage", "group_b_receipt_migration_mismatch")


def _receipt_key(prep: VerifiedPreparation) -> str:
    data = ("group-b-ack/v1:" + prep.project_id + ":" + prep.event_id + ":" +
            prep.event_sha256 + ":" + prep.input_sha256)
    return "gb-" + hashlib.sha256(data.encode("ascii")).hexdigest()


def _run_key(prep: VerifiedPreparation) -> str:
    return canonical_hash({
        "schema": "group-b-qdp-intake/v1", "event_id": prep.event_id,
        "project_id": prep.project_id, "qdp_run_id": prep.qdp_run_id,
        "video_id": prep.video_id, "source_id": prep.source_id,
        "input_sha256": prep.input_sha256, "event_sha256": prep.event_sha256,
        "content_kind": prep.content_kind,
    })


def _record_conflict(conn, *, handoff: QDPHandoff, received_sha: str,
                     old: Mapping[str, Any]) -> str:
    if old["event_sha256"] != received_sha:
        code = "event_hash_conflict"
    elif old["preparation_sha256"] != handoff.input_sha256:
        code = "preparation_hash_conflict"
    else:
        code = "identity_conflict"
    conn.execute(
        """INSERT INTO group_b_inbox_conflicts(
             project_id,inbox_id,event_key,expected_sha256,received_sha256,reason_code)
           VALUES (%s,%s::uuid,%s,%s,%s,%s)
           ON CONFLICT (project_id,event_key,received_sha256,reason_code) DO NOTHING""",
        (handoff.project_id,old["id"],handoff.event_id,
         old["event_sha256"],received_sha,code),
    )
    return code


def _existing(conn, event: QDPHandoff) -> dict | None:
    cur = conn.execute(
        """SELECT id::text AS id,event_sha256,preparation_sha256,video_id,
                  source_id,qdp_run_id::text AS qdp_run_id,status
             FROM group_b_inbox WHERE project_id=%s AND event_key=%s FOR UPDATE""",
        (event.project_id,event.event_id),
    )
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    return dict(zip((d.name for d in cur.description),row))


def _identity_conflict(old: Mapping[str, Any], event: QDPHandoff,
                       announced_sha: str, source_id: str | None = None) -> bool:
    if old["event_sha256"] != announced_sha or old["preparation_sha256"] != event.input_sha256:
        return True
    if old["video_id"] != event.video_id or old["qdp_run_id"] != event.qdp_run_id:
        return True
    if source_id is not None and old["source_id"] != source_id:
        return True
    return False


def _lock_source_rights(conn, prep: VerifiedPreparation) -> None:
    """Lock current D1 permissions/asset/metadata until local commit."""
    cur = conn.execute(
        """SELECT 1 FROM sources s
             JOIN source_policy_versions sp
               ON sp.id=%s AND sp.source_id=s.id
             JOIN media_assets m ON m.id=%s AND m.source_id=s.id AND m.video_id=%s
             JOIN videos v ON v.id=m.video_id
            WHERE s.id=%s AND s.status='enabled' AND s.can_analyze_media
              AND sp.is_active AND sp.permissions->>'can_analyze_media'='true'
              AND m.permission<>'metadata_only'
              AND (m.expires_at IS NULL OR m.expires_at>now())
              AND m.sha256=%s AND v.content_kind=%s
            FOR SHARE OF s,sp,m,v""",
        (prep.source_policy_version_id,prep.media_asset_id,prep.video_id,
         prep.source_id,prep.media_sha256,prep.content_kind),
    )
    if cur.fetchone() is None:
        raise DependencyUnavailable("qdp", "current_source_or_media_revoked")


def _receive_tx(conn, event: QDPHandoff, announced_sha: str, *, preparation_reader=None) -> _TransactionOutcome:
    """Caller MUST own the transaction; do not expose a success ACK in here."""
    _require_consumer_tables(conn)
    _sha(announced_sha, "event_sha256")
    old = _existing(conn,event)
    if old and _identity_conflict(old,event,announced_sha):
        reason = _record_conflict(conn,handoff=event,received_sha=announced_sha,old=old)
        return _TransactionOutcome(None,reason)
    # A replay must also prove current QDP state and rights before ACK.
    verifier = preparation_reader if preparation_reader is not None else read_current_preparation
    if not callable(verifier):
        raise DependencyUnavailable('qdp', 'current_preparation_port_missing')
    prep = verifier(conn,event,announced_sha)
    # Defense in depth: even an injected/misconfigured reader cannot swap the
    # authenticated QDP project, publication or event with a different record.
    if (prep.project_id != event.project_id or prep.event_id != event.event_id or
        prep.qdp_run_id != event.qdp_run_id or prep.video_id != event.video_id or
        prep.input_sha256 != event.input_sha256 or prep.event_sha256 != announced_sha):
        raise ContractError("qdp_identity_mismatch", "QDP verifier changed event identity")
    _lock_source_rights(conn,prep)
    if old and _identity_conflict(old,event,announced_sha,prep.source_id):
        reason = _record_conflict(conn,handoff=event,received_sha=announced_sha,old=old)
        return _TransactionOutcome(None,reason)
    created = False
    if old is None:
        inserted = conn.execute(
            """INSERT INTO group_b_inbox(
                   project_id,video_id,source_id,event_key,qdp_run_id,
                   producer_schema,event_sha256,preparation_sha256)
               VALUES (%s,%s,%s,%s,%s::uuid,%s,%s,%s)
               ON CONFLICT(project_id,event_key) DO NOTHING RETURNING id::text""",
            (prep.project_id,prep.video_id,prep.source_id,prep.event_id,
             prep.qdp_run_id,"qdp-handoff/v1",prep.event_sha256,prep.input_sha256)
        ).fetchone()
        if inserted:
            created = True
        old = _existing(conn,event)
        if old is None:
            raise DependencyUnavailable("storage","inbox_not_durable")
        if _identity_conflict(old,event,announced_sha,prep.source_id):
            reason = _record_conflict(conn,handoff=event,received_sha=announced_sha,old=old)
            return _TransactionOutcome(None,reason)
    run_key = _run_key(prep)
    run = conn.execute(
        """INSERT INTO group_b_runs(
              project_id,inbox_id,video_id,source_id,content_type,
              config_sha256,idempotency_key,state)
            VALUES (%s,%s::uuid,%s,%s,%s,%s,%s,'draft')
            ON CONFLICT(project_id,idempotency_key) DO NOTHING
            RETURNING id::text""",
        (prep.project_id,old["id"],prep.video_id,prep.source_id,
         prep.content_type,canonical_hash({"intake_contract":"group-b-qdp-intake/v1"}),run_key)
    ).fetchone()
    if run is None:
        run = conn.execute(
            """SELECT id::text,inbox_id::text,video_id,source_id,content_type
               FROM group_b_runs WHERE project_id=%s AND idempotency_key=%s""",
            (prep.project_id,run_key)
        ).fetchone()
        if run is None or run[1:] != (old["id"],prep.video_id,prep.source_id,prep.content_type):
            raise DependencyUnavailable("storage","idempotency_ownership_conflict")
        run_id = run[0]
    else:
        run_id = run[0]
    for block in ("B1","B2","B3","B4","B5","B6","B7","B8"):
        conn.execute(
            """INSERT INTO group_b_block_states(project_id,run_id,block,status)
               VALUES (%s,%s::uuid,%s,'not_run')
               ON CONFLICT(project_id,run_id,block) DO NOTHING""",
            (prep.project_id,run_id,block)
        )
    if old["status"] == "received":
        conn.execute(
            "UPDATE group_b_inbox SET status='accepted' WHERE project_id=%s AND id=%s::uuid",
            (prep.project_id,old["id"])
        )
    elif old["status"] != "accepted":
        raise DependencyUnavailable("storage","inbox_not_accepted")
    key = _receipt_key(prep)
    conn.execute(
        """INSERT INTO group_b_inbox_receipts(
               project_id,inbox_id,run_id,event_id,event_sha256,
               preparation_sha256,receipt_key)
           VALUES (%s,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s)
           ON CONFLICT(project_id,inbox_id) DO NOTHING""",
        (prep.project_id,old["id"],run_id,prep.event_id,
         prep.event_sha256,prep.input_sha256,key)
    )
    check = conn.execute(
        """SELECT run_id::text,receipt_key,event_sha256,preparation_sha256
           FROM group_b_inbox_receipts WHERE project_id=%s AND inbox_id=%s::uuid""",
        (prep.project_id,old["id"])
    ).fetchone()
    if check != (run_id,key,prep.event_sha256,prep.input_sha256):
        raise DependencyUnavailable("storage","receipt_integrity_failure")
    return _TransactionOutcome(InboxReceipt(
        prep.event_id,prep.project_id,old["id"],run_id,key,created
    ),None)


def receive_qdp_delivery(connection_factory: Callable[[], Any], payload: dict,
                         announced_event_sha256: str, *,
                         acknowledger: QDPAcknowledger | None = None,
                         preparation_reader=None) -> InboxReceipt:
    """Commit inbox+draft+8 block states+receipt, THEN optionally ACK QDP.

    The factory must own a real context-managed DB connection: exiting its
    context commits the outermost transaction. No free-floating open connection
    or caller-owned savepoint can allow early ACK.
    """
    event = QDPHandoff.parse(payload)
    _sha(announced_event_sha256,"event_sha256")
    if not callable(connection_factory):
        raise ValueError("Owned DB connection factory required")
    with connection_factory() as conn:
        # The outer connection context commits even if transaction() is a savepoint.
        with conn.transaction():
            outcome = _receive_tx(conn,event,announced_event_sha256,
                                  preparation_reader=preparation_reader)
    if outcome.conflict_code:
        raise ContractError(outcome.conflict_code,"QDP event replay quarantined")
    receipt = outcome.receipt
    if receipt is None:
        raise DependencyUnavailable("storage","missing_durable_receipt")
    if acknowledger is not None:
        try:
            acknowledger.acknowledge(event_id=receipt.event_id,
                                      durable_receipt=receipt.receipt_key)
        except Exception as exc:
            # Durable inbox remains; retrying the same event is idempotent.
            raise DependencyProbeFailed("qdp_ack") from exc
        return InboxReceipt(receipt.event_id,receipt.project_id,receipt.inbox_id,
                            receipt.run_id,receipt.receipt_key,
                            receipt.newly_created,acknowledged=True)
    return receipt
