"""FIX-13: explicit QDP-to-Group-A durable transport and receipt translation.

Group A's observed GA-MOD-04 receiver accepts the *different*
qdp.preparation_ready/v1 envelope, rather than QDP's qdp-event/v1 row.
This adapter binds the original payload SHA in an optional signed pointer
field, signs the GA envelope, and translates only a committed GA inbox +
initial step receipt into the FIX-12 producer ACK contract.

The real GA package is optional and never imported during ordinary QDP
startup. from_installed_group_a() is explicit; no peer means 503, no ACK.
QDP does NOT execute GA analysis, create GA tables, download or decode media.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from ..contracts import QDPError, sha256
from ..outbox_delivery import _uuid
from .contracts import QDPHandoffPointer

GA_ENVELOPE = "qdp.preparation_ready/v1"
GA_RECEIPT = "group-a-receipt/v1"
GA_QDP_PEER = "qdp-handoff/v1"
GA_MEDIA_PEER = "ga-media-manifest/v1"
GA_RECEIPT_STATE = "recorded"
_SOURCE_HASH = re.compile(r"[0-9a-f]{64}\Z")
_RECEIPT_KEY = re.compile(r"[A-Za-z0-9._:/-]{8,160}\Z")


def ga_envelope(pointer: QDPHandoffPointer, payload_sha256: str) -> tuple[dict, str]:
    """Bind QDP original outbox digest inside separately signed GA v1 envelope.

    QDP immutable qdp_outbox remains the canonical source of the event. The
    signed GA envelope is a transport translation, NOT a new qdp_outbox event.
    """
    if not isinstance(pointer, QDPHandoffPointer) or pointer.analysis_group != "A":
        raise QDPError("ga_invalid_pointer", "Expected QDP Group A pointer", 409)
    if type(payload_sha256) is not str or _SOURCE_HASH.fullmatch(payload_sha256) is None:
        raise QDPError("ga_invalid_event_sha", "Original outbox SHA-256 missing", 409)
    raw = {
        "schema_version": "qdp-handoff/v1",
        "event_id": pointer.event_id,
        "project_id": pointer.project_id,
        "qdp_run_id": pointer.qdp_run_id,
        "video_id": pointer.video_id,
        "analysis_group": "A",
        "input_sha256": pointer.input_sha256,
        "event_payload_sha256": payload_sha256,
    }
    # The canonical JSON produced by QDP sha256 is byte-for-byte the GA
    # contracts_v1.canonical_sha256 canonicalization for these ASCII fields.
    event = {"schema_version": GA_ENVELOPE,"pointer": raw}
    return event, sha256(event)


def verify_ga_receipt(raw, pointer: QDPHandoffPointer,
                      ga_envelope_sha256: str) -> dict:
    """GA returns recorded/not_started, not FIX-12 committed/analysis done."""
    if not isinstance(raw, Mapping) or raw.get("schema_version") != GA_RECEIPT or (
            raw.get("state") != GA_RECEIPT_STATE or
            raw.get("analysis_state") != "not_started" or
            raw.get("event_id") != pointer.event_id or
            raw.get("project_id") != pointer.project_id or
            raw.get("qdp_run_id") != pointer.qdp_run_id or
            raw.get("video_id") != pointer.video_id or
            raw.get("payload_sha256") != ga_envelope_sha256):
        raise QDPError("ga_receipt_incompatible", "Group A durable receipt is incompatible", 409)
    run_id=raw.get("run_id")
    try:
        _uuid(run_id,"Group A run")
    except QDPError as exc:
        raise QDPError("ga_receipt_incompatible", "Invalid Group A run receipt", 409) from exc
    job=raw.get("job_id")
    if type(job) is not int or job < 1:
        raise QDPError("ga_receipt_incompatible", "Missing durable Group A step", 409)
    key=f"ga:{run_id}:{job}"
    if not _RECEIPT_KEY.fullmatch(key):
        raise QDPError("ga_receipt_incompatible", "Group A receipt key invalid", 409)
    # This is NOT an assertion that analysis completed, only that the GA
    # receiver committed its inbox+initial job before returning 'recorded'.
    return {"receipt_key":key,"ga_run_id":run_id,"ga_step_id":job}


class GroupAConsumerAdapter:
    """Adapter passed explicitly as receiver= to QDP dispatch_once('group_a').

    ga_receiver must implement GroupAInboxReceiver.receive(envelope,digest),
    which commits peer-owned GA inbox and initial step before returning a GA
    receipt. We do NOT instantiate a mock automatically.
    """
    def __init__(self, ga_receiver):
        if ga_receiver is None or not callable(getattr(ga_receiver,"receive",None)):
            raise QDPError("group_a_unavailable","Group A durable receiver not installed",503)
        self._receiver=ga_receiver

    def receive(self, pointer: QDPHandoffPointer, event_payload_sha256: str) -> dict:
        event, digest=ga_envelope(pointer,event_payload_sha256)
        receipt=self._receiver.receive(event,digest)
        verified=verify_ga_receipt(receipt,pointer,digest)
        return {
            "state":"committed",
            "event_id":pointer.event_id,
            "consumer":"group_a",
            "payload_sha256":event_payload_sha256,
            "receipt_key":verified["receipt_key"],
        }


@dataclass(frozen=True)
class GroupAQDPCapability:
    """Structural PeerCapability; no Group A runtime import for QDP validation."""
    port: str
    state: str
    observed_version: str | None = None
    reason_code: str | None = None


class QDPGroupAReader:
    """Authoritative public QDP + Group A transport reader, SAME GA DB txn.

    Uses the current per-group view, immutable exact preparation and source
    media pinned by the QDP event. No dependency on Group A during QDP tests.
    """
    def __init__(self, conn):
        self._conn=conn

    def capability(self):
        try:
            required=("qdp_group_current_preparations","qdp_preparations",
                      "qdp_outbox","qdp_outbox_deliveries","qdp_invalidations")
            for name in required:
                row=self._conn.execute("SELECT to_regclass(%s)",(name,)).fetchone()
                if not row or next(iter(row.values()) if isinstance(row,dict) else iter(row)) is None:
                    return GroupAQDPCapability("qdp","unavailable",reason_code="qdp_schema_missing")
        except Exception:
            return GroupAQDPCapability("qdp","unavailable",reason_code="qdp_probe_failed")
        return GroupAQDPCapability("qdp","available",GA_QDP_PEER)

    def read_preparation(self, pointer: dict):
        if not isinstance(pointer,dict) or set(pointer)!={
            "schema_version","event_id","project_id","qdp_run_id","video_id",
            "analysis_group","input_sha256","event_payload_sha256"}:
            raise QDPError("ga_pointer_incompatible","Invalid GA peer QDP pointer",409)
        try:
            QDPHandoffPointer(
                event_id=pointer["event_id"],project_id=pointer["project_id"],
                qdp_run_id=pointer["qdp_run_id"],video_id=pointer["video_id"],
                analysis_group=pointer["analysis_group"],
                input_sha256=pointer["input_sha256"],
                schema_version=pointer["schema_version"])
        except (ValueError,TypeError,KeyError) as exc:
            raise QDPError("ga_pointer_incompatible","Invalid QDP handoff identity",409) from exc
        if pointer["analysis_group"]!="A" or type(pointer["event_payload_sha256"]) is not str or (
                not _SOURCE_HASH.fullmatch(pointer["event_payload_sha256"])):
            raise QDPError("ga_pointer_incompatible","Invalid Group A event linkage",409)
        if self.capability().state!="available":
            raise QDPError("ga_qdp_unavailable","Authoritative QDP view missing",503)
        r=self._conn.execute(
            """SELECT p.input_sha256 AS stored_sha,p.inputs AS inputs,
                      o.payload AS event_payload,
                      o.payload_sha256 AS event_hash,
                      o.project_id AS project_id,o.run_id AS run_id,
                      o.video_id AS video_id,o.analysis_group AS analysis_group,
                      e.source_id AS source_id,
                      e.source_policy_version_id AS source_policy_version_id,
                      e.media_asset_id AS media_asset_id,
                      a.permission AS media_permission,a.sha256 AS media_sha,
                      a.media_kind AS media_kind,
                      (a.expires_at IS NULL OR a.expires_at>now()) AS media_current
                 FROM qdp_group_current_preparations curr
                 JOIN qdp_preparations p ON p.run_id=curr.run_id
                   AND p.project_id=curr.project_id AND p.video_id=curr.video_id
                   AND p.analysis_group=curr.analysis_group
                 JOIN qdp_enrichments e ON e.run_id=p.run_id
                   AND e.project_id=p.project_id AND e.video_id=p.video_id
                 JOIN media_assets a ON a.id=e.media_asset_id
                   AND a.video_id=e.video_id AND a.source_id=e.source_id
                 JOIN qdp_outbox o ON o.event_id=%s::uuid
                   AND o.run_id=p.run_id AND o.project_id=p.project_id
                   AND o.video_id=p.video_id AND o.analysis_group='A'
                   AND o.event_type='preparation_ready'
                 JOIN qdp_outbox_deliveries d ON d.event_id=o.event_id
                   AND d.consumer='group_a' AND d.state IN
                       ('pending','leased','acknowledged')
                WHERE curr.project_id=%s AND curr.run_id=%s::uuid
                  AND curr.video_id=%s AND curr.analysis_group='A'
                FOR SHARE OF p,e,a,o,d""",
            (pointer["event_id"],pointer["project_id"],pointer["qdp_run_id"],pointer["video_id"])
        ).fetchone()
        if r is None:
            return None
        if not isinstance(r,dict):
            names=("stored_sha","inputs","event_payload","event_hash",
                   "project_id","run_id","video_id","analysis_group","source_id",
                   "source_policy_version_id","media_asset_id","media_permission",
                   "media_sha","media_kind","media_current")
            r=dict(zip(names,r))
        ev=r["event_payload"]
        inp=r["inputs"]
        if (r["event_hash"]!=pointer["event_payload_sha256"] or
            not isinstance(ev,dict) or sha256(ev)!=r["event_hash"] or
            ev.get("schema_version")!="qdp-event/v1" or
            ev.get("event_type")!="preparation_ready" or
            ev.get("analysis_group")!="A" or
            ev.get("input_sha256")!=r["stored_sha"] or
            ev.get("project_id")!=r["project_id"] or
            ev.get("run_id")!=str(r["run_id"]) or
            ev.get("video_id")!=r["video_id"] or
            pointer["project_id"]!=r["project_id"] or
            pointer["qdp_run_id"]!=str(r["run_id"]) or
            pointer["video_id"]!=r["video_id"] or
            pointer["input_sha256"]!=r["stored_sha"] or
            not isinstance(inp,dict) or sha256(inp)!=r["stored_sha"] or
            inp.get("media_asset_id")!=r["media_asset_id"] or
            inp.get("media_sha256")!=r["media_sha"] or
            r["media_kind"]!="video" or r["media_permission"]!="analysis_allowed" or
            r["media_current"] is not True):
            return None
        return {"pointer":dict(pointer),"state":"ready",
                "source_id":r["source_id"],
                "source_policy_version_id":r["source_policy_version_id"],
                "media_asset_id":r["media_asset_id"]}


def from_installed_group_a(connect, *, media_roots: list[str],
                           enabled: bool = False):
    """Build the REAL Group A GA-MOD-04 inbox adapter after peer is installed.

    No runtime guessing: caller must enable and explicitly supply an
    authorized shared host filesystem and connection factory.
    """
    if enabled is not True or not callable(connect) or not isinstance(media_roots,list) or (
            not media_roots or any(type(r) is not str or not r for r in media_roots)):
        raise QDPError("group_a_unavailable","Group A integration not explicitly enabled",503)
    try:
        from intelligence.group_a.handoff import GroupAInboxReceiver
        from intelligence.group_a.config import GASettings
        from intelligence.group_a.adapters.media_assets import VerifiedMediaCatalog
    except ImportError as exc:
        raise QDPError("group_a_unavailable","Group A consumer package not installed",503) from exc
    receiver=GroupAInboxReceiver(
        connect,
        qdp_factory=lambda conn:QDPGroupAReader(conn),
        media_factory=lambda conn:VerifiedMediaCatalog(
            conn,roots=media_roots,observed_version=GA_MEDIA_PEER),
        settings=GASettings(enabled=True,qdp_handoff_enabled=True))
    return GroupAConsumerAdapter(receiver)
