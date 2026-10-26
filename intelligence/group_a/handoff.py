"""GA-MOD-04: durable, idempotent QDP preparation-ready intake.

This is an opt-in, injectable public integration boundary. No QDP imports,
media downloads, provider calls, automatic worker or user-facing route.
Success receipt is built *after* the transaction manager confirms COMMIT.
The QDP adapter and media adapter MUST be authoritative and version negotiated.
"""
from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from .config import GASettings
from .contracts_v1 import (
    ContractViolation, canonical_sha256, ensure_same_lineage,
    media_manifest_v1, qdp_handoff_v1,
)
from .ports import PEER_VERSIONS, STATE_AVAILABLE

EVENT_SCHEMA = "qdp.preparation_ready/v1"
RECEIPT_SCHEMA = "group-a-receipt/v1"
INGEST_CONFIG = {"schema_version": "group-a-intake/v1", "mode": "queued_only"}
STEP_KIND = {
    "video":"technical", "slideshow_video":"technical",
    "photo_carousel":"slides", "image_post":"slides",
    "mixed_carousel":"mixed_slides",
}


class HandoffRejected(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__("Group A handoff rejected: " + code)


def validate_event(event, claimed_payload_sha256):
    if type(event) is not dict or set(event) != {"schema_version", "pointer"}:
        raise ContractViolation("invalid_event", "envelope")
    if event["schema_version"] != EVENT_SCHEMA:
        raise ContractViolation("incompatible_schema", "envelope.schema_version")
    pointer = qdp_handoff_v1(event["pointer"])
    computed = canonical_sha256(event)
    if type(claimed_payload_sha256) is not str or claimed_payload_sha256 != computed:
        raise HandoffRejected("payload_sha256_mismatch")
    return pointer, computed


def _capability(reader, name):
    try:
        cap = reader.capability()
    except Exception as exc:
        raise HandoffRejected(name + "_unavailable") from exc
    if (getattr(cap, "port", None) != name
        or getattr(cap, "state", None) != STATE_AVAILABLE
        or getattr(cap, "observed_version", None) != PEER_VERSIONS[name]):
        raise HandoffRejected(name + "_unavailable")


def _checked_manifest(reader, pointer, selection):
    _capability(reader, "media")
    try:
        raw = reader.read_verified_manifest(pointer)
    except (OSError,RuntimeError,TimeoutError,ConnectionError) as exc:
        raise HandoffRejected("media_unavailable") from exc
    if raw is None:
        raise HandoffRejected("media_manifest_missing")
    m = media_manifest_v1(raw)
    ensure_same_lineage(pointer,m)
    if (m["source_id"]!=selection["source_id"]
        or m["source_policy_version_id"]!=selection["source_policy_version_id"]
        or selection["media_asset_id"] not in
            {x["media_asset_id"] for x in m["items"]}):
        raise HandoffRejected("qdp_media_selection_mismatch")
    return m


def _verify_preparation(reader, pointer):
    _capability(reader, "qdp")
    try:
        current = reader.read_preparation(pointer)
    except (OSError,RuntimeError,TimeoutError,ConnectionError) as exc:
        raise HandoffRejected("qdp_unavailable") from exc
    if current is None:
        raise HandoffRejected("qdp_preparation_missing")
    required={"pointer","state","source_id","source_policy_version_id","media_asset_id"}
    if type(current) is not dict or set(current) != required:
        raise ContractViolation("invalid_authoritative_preparation", "qdp.reader")
    if (type(current["source_id"]) is not str or not current["source_id"]
        or type(current["source_policy_version_id"]) is not int
        or current["source_policy_version_id"]<1
        or type(current["media_asset_id"]) is not int
        or current["media_asset_id"]<1):
        raise ContractViolation("invalid_authoritative_preparation","qdp.media_selection")
    latest = qdp_handoff_v1(current["pointer"])
    if latest != pointer or current["state"] != "ready":
        raise HandoffRejected("qdp_preparation_revoked")
    return current


def _rows_as_tuple(row):
    return tuple(row) if row is not None else None


class GroupAInboxReceiver:
    """Single-call durable receive. conn_factory returns a PostgreSQL connection.

    qdp_factory and media_factory accept that SAME connection. An explicit
    GASettings(enabled=True,qdp_handoff_enabled=True) is required. No factory
    or flag is read from global environment, and no peer is auto-wired.

    Receipt can be shared with QDP only after the scope exits (COMMIT).
    """

    def __init__(self, connect, qdp_factory, media_factory, *, settings=None):
        if not callable(connect) or not callable(qdp_factory) or not callable(media_factory):
            raise TypeError("Explicit connection/QDP/media factories required")
        self._connect=connect
        self._qdp=qdp_factory
        self._media=media_factory
        self.settings=GASettings() if settings is None else settings
        if not isinstance(self.settings,GASettings):
            raise TypeError("GASettings required")

    def _configured(self):
        if not (self.settings.enabled and self.settings.qdp_handoff_enabled):
            raise HandoffRejected("handoff_disabled")

    def receive(self, envelope, payload_sha256):
        self._configured()
        pointer, digest = validate_event(envelope,payload_sha256)
        conflict = None
        ack = None
        with self._connect() as conn:
            # psycopg transaction() commits on successful exit; on error,
            # no durable ACK survives. Never keep a DB transaction open during
            # FFmpeg/video processing (which is not launched here).
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                             (pointer["event_id"],))
                existing=conn.execute(
                    """SELECT run_id::text,project_id,qdp_run_id::text,video_id,
                              input_sha256,payload_sha256,pointer
                       FROM ga_inbox WHERE event_id=%s::uuid FOR UPDATE""",
                    (pointer["event_id"],),
                ).fetchone()
                if existing:
                    (run_id,project,run,video,input_sha,old_digest,old_pointer)=existing
                    if (old_digest!=digest or old_pointer!=pointer or
                        project!=pointer["project_id"] or run!=pointer["qdp_run_id"] or
                        video!=pointer["video_id"] or input_sha!=pointer["input_sha256"]):
                        old_pointer_hash=canonical_sha256(pointer)
                        conn.execute(
                            """INSERT INTO ga_inbox_conflicts
                               (event_id,recorded_payload_sha256,observed_payload_sha256,
                                observed_pointer_sha256,reason_code)
                               VALUES(%s::uuid,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (pointer["event_id"],old_digest,digest,old_pointer_hash,
                             "event_payload_conflict" if old_digest!=digest
                             else "event_identity_conflict"),
                        )
                        conflict="event_id_conflict"
                    else:
                        step=conn.execute(
                            "SELECT id FROM ga_steps WHERE run_id=%s::uuid AND step_key='initial:v1'",
                            (run_id,),
                        ).fetchone()
                        if not step:
                            raise HandoffRejected("durable_job_missing")
                        ack=self._ack(pointer,digest,run_id,step[0],replayed=True)
                else:
                    qdp=self._qdp(conn)
                    media=self._media(conn)
                    selection=_verify_preparation(qdp,pointer)
                    manifest=_checked_manifest(media,pointer,selection)
                    ack=self._insert(conn,pointer,digest,manifest)
        # A conflicting event has been committed to the quarantine table, so
        # throw *outside* the transaction. No false ACK and no rollback of log.
        if conflict:
            raise HandoffRejected(conflict)
        if ack is None:
            raise HandoffRejected("intake_not_committed")
        return ack

    def _insert(self, conn, p, digest, m):
        from psycopg.types.json import Jsonb
        config_sha=canonical_sha256(INGEST_CONFIG)
        created=conn.execute(
            """INSERT INTO ga_runs
               (project_id,qdp_run_id,video_id,source_id,source_policy_version_id,
                qdp_input_sha256,manifest_sha256,config_sha256,config,
                expected_media_items,content_kind)
               VALUES(%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(project_id,qdp_run_id,video_id,qdp_input_sha256,
                           manifest_sha256,config_sha256) DO NOTHING RETURNING id::text""",
            (p["project_id"],p["qdp_run_id"],p["video_id"],m["source_id"],
             m["source_policy_version_id"],p["input_sha256"],
             m["manifest_sha256"],config_sha,Jsonb(INGEST_CONFIG),
             len(m["items"]),m["content_kind"]),
        ).fetchone()
        if created:
            run_id=created[0]
        else:
            prior=conn.execute(
                """SELECT id::text,source_id,source_policy_version_id,content_kind,
                          expected_media_items,state FROM ga_runs
                   WHERE project_id=%s AND qdp_run_id=%s::uuid AND video_id=%s
                     AND qdp_input_sha256=%s AND manifest_sha256=%s
                     AND config_sha256=%s FOR UPDATE""",
                (p["project_id"],p["qdp_run_id"],p["video_id"],
                 p["input_sha256"],m["manifest_sha256"],config_sha),
            ).fetchone()
            if (prior is None or
                prior[1:5] != (m["source_id"],m["source_policy_version_id"],
                               m["content_kind"],len(m["items"])) or
                prior[5] in ("blocked","cancelled","invalidated","failed")):
                raise HandoffRejected("run_lineage_changed_or_terminal")
            run_id=prior[0]
        for item in m["items"]:
            conn.execute(
                """INSERT INTO ga_media_refs
                   (run_id,project_id,video_id,source_id,slide_index,media_asset_id,
                    media_kind,asset_sha256,byte_size,verified_at)
                   VALUES(%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamptz)
                   ON CONFLICT(run_id,slide_index) DO NOTHING""",
                (run_id,p["project_id"],p["video_id"],m["source_id"],
                 item["index"],item["media_asset_id"],item["kind"],
                 item["sha256"],item["byte_size"],m["verified_at"]),
            )
        pins=conn.execute(
            """SELECT slide_index,media_asset_id,media_kind,asset_sha256,byte_size
               FROM ga_media_refs WHERE run_id=%s::uuid ORDER BY slide_index""",
            (run_id,),
        ).fetchall()
        observed=[tuple(row) for row in pins]
        expected=[(i["index"],i["media_asset_id"],i["kind"],i["sha256"],
                   i["byte_size"]) for i in m["items"]]
        if observed != expected:
            raise HandoffRejected("media_pin_conflict")
        step=conn.execute(
            """INSERT INTO ga_steps
               (run_id,project_id,step_key,step_kind,input_sha256)
               VALUES(%s::uuid,%s,'initial:v1',%s,%s)
               ON CONFLICT(run_id,step_key) DO NOTHING RETURNING id""",
            (run_id,p["project_id"],STEP_KIND[m["content_kind"]],m["manifest_sha256"]),
        ).fetchone()
        if step:
            step_id=step[0]
        else:
            found=conn.execute(
                """SELECT id,step_kind,input_sha256 FROM ga_steps
                   WHERE run_id=%s::uuid AND step_key='initial:v1'""",
                (run_id,),
            ).fetchone()
            if not found or found[1:] != (STEP_KIND[m["content_kind"]],
                                         m["manifest_sha256"]):
                raise HandoffRejected("job_input_conflict")
            step_id=found[0]
        conn.execute(
            """INSERT INTO ga_inbox
               (event_id,run_id,project_id,qdp_run_id,video_id,
                input_sha256,payload_sha256,pointer)
               VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s)
               """,
            (p["event_id"],run_id,p["project_id"],p["qdp_run_id"],
             p["video_id"],p["input_sha256"],digest,Jsonb(p)),
        )
        return self._ack(p,digest,run_id,step_id,replayed=False)

    @staticmethod
    def _ack(p,digest,run_id,step_id,*,replayed):
        return {
            "schema_version": RECEIPT_SCHEMA,
            "state":"recorded",
            "event_id":p["event_id"],
            "project_id":p["project_id"],
            "qdp_run_id":p["qdp_run_id"],
            "video_id":p["video_id"],
            "run_id":str(run_id),
            "job_id":step_id,
            "payload_sha256":digest,
            "replayed":replayed,
            "analysis_state":"not_started",
        }

    def read_receipt(self, project_id, event_id):
        """Project-scoped lookup. Historical receipt != current readiness."""
        self._configured()
        from .contracts_v1 import PROJECT, _uuid, _token
        _token(project_id, PROJECT, "project_id")
        _uuid(event_id,"event_id")
        with self._connect() as conn:
            row=conn.execute(
                """SELECT i.run_id::text,i.qdp_run_id::text,i.video_id,
                          i.payload_sha256,s.id
                   FROM ga_inbox i JOIN ga_steps s ON s.run_id=i.run_id
                     AND s.step_key='initial:v1'
                   WHERE i.project_id=%s AND i.event_id=%s::uuid""",
                (project_id,event_id),
            ).fetchone()
        if row is None:
            return None
        p={"event_id":event_id,"project_id":project_id,
           "qdp_run_id":row[1],"video_id":row[2]}
        return self._ack(p,row[3],row[0],row[4],replayed=True)
