"""GA-MOD-24: QDP FIX-15 compatible *durable* compensating revocation.

Designed against feature/qualified-data-pipeline-v1-20261023 at
a0bb24e1c2d6ebd7be0a69c57a1978b66747cbfa. No QDP import, QDP
migration edit or standalone server auto-registration. Producer calls:
  qdp.revocation_delivery.dispatch_invalidation_once(
      "group_a",receiver=PostgresQDPRevocationReceiver(connect),connect=connect)
After an authenticated QDP invalidation outbox event exists, revoke()
records a GA-owned immutable receipt and invalidates all matching active GA
runs in one transaction. No delivery success is returned until COMMIT.
"""
from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from .contracts_v1 import canonical_sha256
from .cost_resources import RuntimeDenied,POOL_LOCK_A,POOL_LOCK_B
from .invalidation import _terminalize_active

SCHEMA="qdp-event/v1"
ORIGINS=frozenset(("sre","human_review","d1_source",
                   "retention","configuration","operator"))
SHA_CHARS=frozenset("0123456789abcdef")


class QDPRevocationDenied(RuntimeError):
    def __init__(self,code):
        self.code=code
        super().__init__("GA QDP revocation rejected: "+code)


def _uuid(text):
    try:
        if type(text) is not str or str(UUID(text))!=text:
            raise ValueError("noncanonical uuid")
    except (ValueError,TypeError,AttributeError) as exc:
        raise QDPRevocationDenied("invalid_revocation_id") from exc
    return text


def _sha(text):
    if (type(text) is not str or len(text)!=64 or
        any(c not in SHA_CHARS for c in text)):
        raise QDPRevocationDenied("invalid_revocation_sha")
    return text


def _pointer(pointer):
    required=("event_id","project_id","qdp_run_id","video_id",
              "analysis_group","input_sha256","schema_version")
    if not all(hasattr(pointer,key) for key in required):
        raise QDPRevocationDenied("revocation_pointer_required")
    event_id=_uuid(pointer.event_id)
    run_id=_uuid(pointer.qdp_run_id)
    project=pointer.project_id
    if (type(project) is not str or len(project)!=32 or
        any(c not in SHA_CHARS for c in project)):
        raise QDPRevocationDenied("invalid_revocation_project")
    if (type(pointer.video_id) is not int or pointer.video_id<1 or
        pointer.analysis_group!="A" or
        pointer.schema_version!="qdp-handoff/v1"):
        raise QDPRevocationDenied("wrong_revocation_group")
    return {
        "event_id":event_id,"project_id":project,"run_id":run_id,
        "video_id":pointer.video_id,"input_sha256":_sha(pointer.input_sha256),
    }


def _notice(raw,pointer,event_sha):
    if not isinstance(raw,Mapping):
        raise QDPRevocationDenied("invalid_revocation_notice")
    event=dict(raw)
    required={"schema_version","event_type","project_id","run_id",
              "video_id","analysis_group","input_sha256",
              "provenance_sha256","invalidation_generation","origin"}
    if not required.issubset(event):
        raise QDPRevocationDenied("revocation_notice_incomplete")
    if (event["schema_version"]!=SCHEMA or
        event["event_type"]!="preparation_invalidated" or
        event["analysis_group"]!="A" or
        event["project_id"]!=pointer["project_id"] or
        event["run_id"]!=pointer["run_id"] or
        event["video_id"]!=pointer["video_id"] or
        event["input_sha256"]!=pointer["input_sha256"] or
        type(event["invalidation_generation"]) is not int or
        event["invalidation_generation"]<1 or
        event["origin"] not in ORIGINS):
        raise QDPRevocationDenied("revocation_notice_identity_mismatch")
    _sha(event["provenance_sha256"])
    if canonical_sha256(event)!=_sha(event_sha):
        raise QDPRevocationDenied("revocation_payload_hash_mismatch")
    return event


def _row(cursor):
    raw=cursor.fetchone()
    if raw is None:
        return None
    if isinstance(raw,Mapping):
        return dict(raw)
    return dict(zip((column.name for column in cursor.description),raw))


class PostgresQDPRevocationReceiver:
    """QDP FIX-15 receiver, opt-in via host dependency injection only.

    conn_factory MUST point at same trusted PostgreSQL DB hosting both QDP
    qdp_outbox and GA tables. A forged pointer/notice cannot authorize a
    revocation: the immutable QDP outbox payload and SHA are re-read under
    a database lock before GA writes. Absence of any GA runs is still a
    valid durable revocation ACK (QDP current-preparation check prevents
    any subsequently delivered outdated preparation from running).
    """
    def __init__(self,conn_factory):
        if not callable(conn_factory):
            raise TypeError("trusted PostgreSQL connection factory required")
        self._connect=conn_factory

    def revoke(self,pointer,event_payload_sha256,notice):
        ref=_pointer(pointer)
        msg=_notice(notice,ref,event_payload_sha256)
        with self._connect() as conn:
            with conn.transaction():
                conn.execute(
                    "SELECT pg_advisory_xact_lock(%s,%s)",
                    (POOL_LOCK_A,POOL_LOCK_B)
                )
                # The producer QDP dispatcher owns the original event
                # and validates signature+origin before calling this method.
                # GA independently validates the exact immutable DB row.
                origin=_row(conn.execute(
                    """SELECT o.event_id::text AS event_id,o.project_id,
                              o.run_id::text AS run_id,o.video_id,
                              o.analysis_group,o.event_type,o.payload,
                              o.payload_sha256,
                              d.state AS delivery_state
                       FROM qdp_outbox o
                       JOIN qdp_outbox_deliveries d
                         ON d.event_id=o.event_id AND d.consumer='group_a'
                       WHERE o.event_id=%s::uuid
                       FOR SHARE OF o,d""",
                    (ref["event_id"],)
                ))
                if (origin is None or
                    origin["event_id"]!=ref["event_id"] or
                    origin["project_id"]!=ref["project_id"] or
                    origin["run_id"]!=ref["run_id"] or
                    origin["video_id"]!=ref["video_id"] or
                    origin["analysis_group"]!="A" or
                    origin["event_type"]!="preparation_invalidated" or
                    origin["payload_sha256"]!=event_payload_sha256 or
                    origin["payload"]!=msg or
                    origin["delivery_state"] not in ("leased","acknowledged")):
                    raise QDPRevocationDenied("authoritative_qdp_revocation_missing")
                recorded=_row(conn.execute(
                    """SELECT event_payload_sha256,provenance_sha256,
                              invalidation_generation,origin,affected_runs
                       FROM ga_qdp_revocation_receipts
                       WHERE event_id=%s::uuid FOR SHARE""",
                    (ref["event_id"],)
                ))
                if recorded is not None:
                    if (
                        recorded["event_payload_sha256"]!=event_payload_sha256 or
                        recorded["provenance_sha256"]!=msg["provenance_sha256"] or
                        recorded["invalidation_generation"]!=msg["invalidation_generation"] or
                        recorded["origin"]!=msg["origin"]
                    ):
                        raise QDPRevocationDenied("revocation_event_id_conflict")
                    # Caller already has a real durable GA receipt. No
                    # reactivation or second invalidation write on retries.
                    affected=recorded["affected_runs"]
                else:
                    affected=0
                    rows=conn.execute(
                        """SELECT id::text,state
                           FROM ga_runs
                           WHERE project_id=%s AND qdp_run_id=%s::uuid
                             AND video_id=%s AND qdp_input_sha256=%s
                           ORDER BY id FOR UPDATE""",
                        (ref["project_id"],ref["run_id"],ref["video_id"],
                         ref["input_sha256"])
                    ).fetchall()
                    for run_id,state in rows:
                        if state in ("invalidated","cancelled","blocked"):
                            continue
                        # Existing completed history persists, but is no
                        # longer externally readable as current; active
                        # attempts/GA->B outbox are revoked atomically.
                        if state in ("queued","running","partial","failed","completed"):
                            _terminalize_active(conn,run_id,"qdp_revoked")
                            conn.execute(
                                """UPDATE ga_runs
                                     SET state='invalidated',
                                         worker_id=NULL,lease_until=NULL,
                                         invalidated_at=now(),
                                         invalidated_reason='qdp_revoked',
                                         updated_at=now()
                                   WHERE id=%s::uuid""",(run_id,)
                            )
                            conn.execute(
                                """INSERT INTO ga_invalidation_audit
                                   (run_id,previous_state,reason_code)
                                   VALUES (%s::uuid,%s,'qdp_revoked')
                                   ON CONFLICT(run_id) DO NOTHING""",
                                (run_id,state)
                            )
                            affected+=1
                    conn.execute(
                        """INSERT INTO ga_qdp_revocation_receipts
                           (event_id,project_id,qdp_run_id,video_id,input_sha256,
                            event_payload_sha256,provenance_sha256,
                            invalidation_generation,origin,affected_runs)
                           VALUES(%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s)""",
                        (ref["event_id"],ref["project_id"],ref["run_id"],
                         ref["video_id"],ref["input_sha256"],event_payload_sha256,
                         msg["provenance_sha256"],msg["invalidation_generation"],
                         msg["origin"],affected)
                    )
        # After actual database COMMIT only, QDP FIX-12 expects this ACK.
        return {
            "state":"committed","consumer":"group_a",
            "event_id":ref["event_id"],
            "payload_sha256":event_payload_sha256,
            "receipt_key":"ga:revocation:"+ref["event_id"],
        }
