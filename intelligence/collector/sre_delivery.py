"""VC-MOD-11 — single durable project-scoped Collector → SRE outbox.

No SRE implementation is imported and no network/provider call occurs on
import. Actual delivery is DISABLED by default until SRE M35 exposes an
authenticated durable-inbox adapter returning a persisted ACK. An external
message JSON or its checksum alone cannot mark an outbox row delivered.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import re
from typing import Protocol, runtime_checkable

from intelligence.taxonomy_builder.pipeline_contracts import sha256
from .contracts import (
    CollectorError, HASH_RE, PROJECT_RE, require_id, require_int, require_uuid,
)
from .repository import _connection

OUTBOX_VERSION="collector.sre_ready/v1"
SRE_INBOUND_VERSION="sre.discovery_ready/v1"
ACK_VERSION="sre.inbox_ack/v1"
MAX_ATTEMPTS=8
MAX_LEASE_SECONDS=300
_WORKER_RE=re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,99}\Z")


@runtime_checkable
class DurableSREInboxPort(Protocol):
    """Injected trusted server-only adapter, NOT an untrusted HTTP endpoint."""
    def capabilities(self) -> dict: ...
    def deliver(self, message: dict) -> dict: ...


def _fail(code: str, message: str) -> None:
    raise CollectorError(code,message,409)


def _worker(value: str) -> str:
    return require_id(value,"outbox_worker",_WORKER_RE)


def _timestamp(value: datetime) -> str:
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail("sre_event_invalid","Timestamp requires UTC-aware database clock")
    return value.astimezone(timezone.utc).isoformat()


def _enabled(env=None) -> bool:
    source=os.environ if env is None else env
    return source.get("CM_COLLECTOR_SRE_DELIVERY_ENABLED","0")=="1"


def _verified_port(adapter) -> bool:
    if adapter is None:
        return False
    if not isinstance(adapter,DurableSREInboxPort):
        _fail("sre_port_invalid","SRE inbox adapter protocol missing")
    caps=adapter.capabilities()
    if type(caps) is not dict or set(caps)!={
        "schema_version","authenticated","durable_inbox","ready"
    }:
        _fail("sre_port_invalid","SRE inbox capability handshake malformed")
    if caps["schema_version"]!=SRE_INBOUND_VERSION:
        _fail("sre_port_version","SRE inbox schema incompatible")
    if any(type(caps[k]) is not bool for k in
           ("authenticated","durable_inbox","ready")):
        _fail("sre_port_invalid","SRE advertised capabilities invalid")
    return all(caps[k] for k in ("authenticated","durable_inbox","ready"))


def _strict_event(conn, project_id: str, row: tuple) -> dict:
    """Re-derive all SRE input fields from canonical D1 and QV, never browser JSON."""
    event_id,delivery_key,payload,attempts,generation=row
    evt_id=require_uuid(str(event_id),"event_id")
    if type(payload) is not dict or payload.get("schema_version")!=OUTBOX_VERSION:
        _fail("sre_event_invalid","Unexpected Collector outbox format")
    data=conn.execute(
        """SELECT e.project_id,e.video_id,e.discovery_id,e.source_metadata_id,
                  e.page_id,e.created_at,e.event_payload,e.delivery_key,
                  p.persisted_at,p.page_sha256,p.source_request_id,
                  p.run_id,q.query_set,q.query_set_sha256,
                  m.source_policy_version_id,m.source_id,
                  v.is_active,v.reviewed_at,s.status,s.can_store_metadata
           FROM collector_sre_outbox e
           JOIN collector_ingest_page_videos pv
             ON pv.page_id=e.page_id AND pv.project_id=e.project_id
              AND pv.video_id=e.video_id AND pv.discovery_id=e.discovery_id
              AND pv.source_metadata_id=e.source_metadata_id
           JOIN collector_ingest_pages p ON p.id=e.page_id
             AND p.project_id=e.project_id
           JOIN collector_runs c ON c.run_id=p.run_id
             AND c.project_id=p.project_id
           JOIN collector_query_sets q ON q.id=c.query_set_id
             AND q.project_id=c.project_id
           JOIN source_video_metadata m ON m.id=e.source_metadata_id
             AND m.video_id=e.video_id
           JOIN source_policy_versions v ON v.id=m.source_policy_version_id
             AND v.source_id=m.source_id
           JOIN sources s ON s.id=m.source_id
           WHERE e.id=%s::uuid AND e.project_id=%s""",
        (evt_id,project_id),
    ).fetchone()
    if data is None:
        _fail("sre_d1_provenance_missing","Verified D1/Collector join unavailable")
    (pid,video_id,discovery_id,metadata_id,page_id,occurred_at,stored,
     key,persisted_at,page_sha,source_request_id,run_id,query_set,query_sha,
     policy_id,source_id,active,reviewed,source_status,can_store)=data
    if (pid!=project_id or key!=delivery_key or stored!=payload
        or not active or reviewed is None or source_status!="enabled"
        or can_store is not True or source_id!="dtk_tiktok"):
        _fail("sre_source_rights","Collector D1 source or rights no longer valid")
    if persisted_at is None or occurred_at is None or persisted_at>occurred_at:
        _fail("sre_d1_provenance_missing","Event predates persisted D1 receipt")
    if type(query_set) is not dict or sha256(query_set)!=query_sha:
        _fail("sre_qv_integrity","Trusted QuerySet changed")
    if (payload.get("project_id")!=project_id
        or payload.get("delivery_key")!=delivery_key
        or payload.get("source_id")!=source_id
        or payload.get("relevance_state")!="not_evaluated"
        or payload.get("evidence_scope")!="metadata_only"
        or payload.get("page_sha256")!=page_sha
        or payload.get("source_request_id")!=str(source_request_id)
        or payload.get("run_id")!=str(run_id)
        or payload.get("page_id")!=str(page_id)
        or payload.get("video")!={"video_id":str(video_id),
                                    "discovery_id":str(discovery_id),
                                    "source_metadata_id":str(metadata_id)}):
        _fail("sre_event_invalid","Persisted event conflicts with canonical D1")
    associations=conn.execute(
        """SELECT l.candidate_id,t.hypothesis_id
           FROM collector_page_video_queries l JOIN collector_task_queries t
             ON t.source_request_id=l.source_request_id
             AND t.query_id=l.candidate_id
           WHERE l.project_id=%s AND l.page_id=%s AND l.video_id=%s
             AND l.source_request_id=%s
           ORDER BY l.candidate_id""",
        (project_id,page_id,video_id,source_request_id),
    ).fetchall()
    candidate_ids=[q for q,_ in associations]
    hypotheses=sorted(set(h for _,h in associations))
    if (not candidate_ids or
        len(set(candidate_ids))!=len(candidate_ids) or
        candidate_ids!=sorted(candidate_ids) or
        payload.get("candidate_ids")!=candidate_ids or
        sorted(payload.get("hypothesis_ids",[]))!=hypotheses):
        _fail("sre_qv_integrity","Outbox candidate/hypothesis links conflict with D1")
    for candidate in candidate_ids:
        require_uuid(candidate,"collector_query_id")
    # SRE M02 requires a sorted deduplicated canonical UUID array.
    sre_event={
        "schema_version":SRE_INBOUND_VERSION,
        "event_id":evt_id,
        "producer":"video_collector",
        "project_id":pid,
        "video_id":video_id,
        "discovery_id":discovery_id,
        "source_id":source_id,
        "source_policy_version_id":policy_id,
        "source_request_id":source_request_id,
        "collector_query_ids":candidate_ids,
        "d1_persisted_at":_timestamp(persisted_at),
        "occurred_at":_timestamp(occurred_at),
    }
    sre_event["event_sha256"]=sha256(sre_event)
    source_provenance={
        "project_id":pid,
        "taxonomy_publication_id":query_set.get("taxonomy_publication_id"),
        "taxonomy_version":query_set.get("taxonomy_version"),
        "taxonomy_sha256":query_set.get("taxonomy_sha256"),
        "generation_id":query_set.get("generation_id"),
        "candidate_ids":candidate_ids,
        "hypothesis_ids":hypotheses,
        "page_sha256":page_sha,
        "video_id":video_id,
        "discovery_id":discovery_id,
        "source_metadata_id":metadata_id,
        "relevance_state":"not_evaluated",
    }
    return {"schema_version":"collector.sre_delivery_envelope/v1",
            "collector_outbox_id":evt_id,
            "delivery_key":delivery_key,
            "project_id":project_id,
            "lease_generation":generation,
            "attempts":attempts,
            "sre_event":sre_event,
            "collector_provenance":source_provenance}


def claim_one(project_id: str,worker_id: str,*,connect=None,
              lease_seconds: int=90) -> dict | None:
    """One project-only lease, no provider/network I/O, no event fabrication."""
    pid=require_id(project_id,"project_id",PROJECT_RE)
    owner=_worker(worker_id)
    lease_seconds=require_int(lease_seconds,"lease_seconds",1,MAX_LEASE_SECONDS)
    with _connection(connect) as conn:
        with conn.transaction():
            selected=conn.execute(
                """SELECT id,delivery_key,event_payload,attempts,lease_generation
                   FROM collector_sre_outbox
                   WHERE project_id=%s AND state='pending'
                     AND next_delivery_at<=now() AND attempts<%s
                   ORDER BY next_delivery_at,id
                   FOR UPDATE SKIP LOCKED LIMIT 1""",
                (pid,MAX_ATTEMPTS),
            ).fetchone()
            if selected is None:
                return None
            event_id=str(selected[0])
            claimed=conn.execute(
                """UPDATE collector_sre_outbox
                   SET state='leased',leased_by=%s,
                       lease_until=now()+(%s*interval '1 second'),
                       attempts=attempts+1,
                       lease_generation=lease_generation+1,
                       last_error_code=NULL
                   WHERE id=%s::uuid AND project_id=%s AND state='pending'
                   RETURNING attempts,lease_generation""",
                (owner,lease_seconds,event_id,pid),
            ).fetchone()
            if claimed is None:
                _fail("sre_claim_conflict","Outbox claim unexpectedly lost")
            return {"collector_outbox_id":event_id,"project_id":pid,
                    "delivery_key":selected[1],"lease_generation":claimed[1],
                    "attempts":claimed[0]}


def _guard_lease(conn, pid: str, event_id: str, worker_id: str,
                 generation: int) -> dict:
    row=conn.execute(
        """SELECT event_payload,state,attempts
           FROM collector_sre_outbox
           WHERE id=%s::uuid AND project_id=%s AND state='leased'
             AND leased_by=%s AND lease_generation=%s AND lease_until>now()
           FOR UPDATE""",
        (event_id,pid,worker_id,generation),
    ).fetchone()
    if row is None:
        _fail("sre_lost_lease","Stale or expired outbox lease")
    return row[0]


def _validate_ack(receipt: dict,event: dict) -> None:
    required={"schema_version","event_id","project_id","event_sha256",
              "persisted","durable_receipt_id"}
    if (type(receipt) is not dict or set(receipt)!=required
        or receipt["schema_version"]!=ACK_VERSION
        or receipt["persisted"] is not True
        or receipt["event_id"]!=event["event_id"]
        or receipt["project_id"]!=event["project_id"]
        or receipt["event_sha256"]!=event["event_sha256"]
        or type(receipt["durable_receipt_id"]) is not str
        or not 1<=len(receipt["durable_receipt_id"])<=160):
        _fail("sre_ack_invalid","Durable SRE inbox receipt does not confirm this event")
    if any(ord(c)<32 for c in receipt["durable_receipt_id"]):
        _fail("sre_ack_invalid","Unprintable SRE receipt identifier")


def ack_delivery(project_id: str,worker_id: str,generation: int,
                 event: dict,receipt: dict,*,connect=None) -> dict:
    """The *server-owned* transport adapter has reported persisted SRE inbox ACK.

    Never mount this method directly on public HTTP without independent
    authenticated receipt verification. Bare JSON hashes are NOT signatures.
    """
    pid=require_id(project_id,"project_id",PROJECT_RE)
    owner=_worker(worker_id)
    gen=require_int(generation,"lease_generation",1,2**63-1)
    if type(event) is not dict or event.get("project_id")!=pid:
        _fail("sre_ack_invalid","Event is not scoped to the provided project")
    event_id=require_uuid(event.get("event_id"),"event_id")
    _validate_ack(receipt,event)
    with _connection(connect) as conn:
        with conn.transaction():
            stored=_guard_lease(conn,pid,event_id,owner,gen)
            if stored.get("delivery_key")!=event.get("delivery_key"):
                _fail("sre_ack_invalid","Collector immutable delivery key mismatch")
            updated=conn.execute(
                """UPDATE collector_sre_outbox SET state='delivered',
                     delivered_at=now(),leased_by=NULL,lease_until=NULL,
                     last_error_code=NULL
                   WHERE id=%s::uuid AND project_id=%s AND state='leased'
                     AND leased_by=%s AND lease_generation=%s
                     AND lease_until>now() RETURNING id""",
                (event_id,pid,owner,gen),
            ).fetchone()
            if updated is None:
                _fail("sre_lost_lease","Delivery ACK lost lease before commit")
    return {"status":"delivered","project_id":pid,"event_id":event_id,
            "persisted":True,"receipt_confirmed":True}


def fail_delivery(project_id: str,worker_id: str,event_id: str,
                  generation: int,*,error_code: str,permanent: bool=False,
                  connect=None) -> dict:
    """Durable exponential delay, no raw provider errors in the database."""
    pid=require_id(project_id,"project_id",PROJECT_RE)
    owner=_worker(worker_id)
    eid=require_uuid(event_id,"event_id")
    gen=require_int(generation,"lease_generation",1,2**63-1)
    codes={"sre_unavailable","sre_ack_invalid","sre_send_failed",
           "sre_source_rights","sre_event_invalid","sre_qv_integrity",
           "sre_d1_provenance_missing","sre_port_invalid","sre_port_version"}
    if error_code not in codes:
        _fail("sre_error_invalid","Unknown safe outbox failure code")
    with _connection(connect) as conn:
        with conn.transaction():
            _guard_lease(conn,pid,eid,owner,gen)
            count=conn.execute(
                """SELECT attempts FROM collector_sre_outbox
                   WHERE id=%s::uuid AND project_id=%s""",(eid,pid),
            ).fetchone()
            if count is None:
                _fail("sre_lost_lease","Delivery attempt disappeared")
            block=bool(permanent or count[0]>=MAX_ATTEMPTS)
            delay=min(3600,5*2**min(count[0]-1,10))
            updated=conn.execute(
                """UPDATE collector_sre_outbox
                   SET state=%s,leased_by=NULL,lease_until=NULL,
                       next_delivery_at=now()+(%s*interval '1 second'),
                       last_error_code=%s
                   WHERE id=%s::uuid AND project_id=%s AND state='leased'
                     AND leased_by=%s AND lease_generation=%s
                   RETURNING id""",
                ("blocked" if block else "pending",delay,error_code,
                 eid,pid,owner,gen),
            ).fetchone()
            if updated is None:
                _fail("sre_lost_lease","Outbox failure update lost lease")
    return {"status":"blocked" if block else "pending",
            "error_code":error_code,"retry_seconds":None if block else delay,
            "project_id":pid,"event_id":eid}


def recover_expired(project_id: str,*,connect=None) -> dict:
    """Project-scoped leased rows with expired lease, no redelivery of ACKed rows."""
    pid=require_id(project_id,"project_id",PROJECT_RE)
    with _connection(connect) as conn:
        with conn.transaction():
            rows=conn.execute(
                """UPDATE collector_sre_outbox SET
                     state=CASE WHEN attempts>=%s THEN 'blocked' ELSE 'pending' END,
                     leased_by=NULL,lease_until=NULL,
                     next_delivery_at=now(),
                     last_error_code='lease_expired'
                   WHERE project_id=%s AND state='leased' AND lease_until<now()
                   RETURNING id,state""",(MAX_ATTEMPTS,pid),
            ).fetchall()
    return {"project_id":pid,"recovered":len(rows),
            "pending":sum(state=="pending" for _,state in rows),
            "blocked":sum(state=="blocked" for _,state in rows)}


def dispatch_one(project_id: str,worker_id: str,*,adapter=None,connect=None,
                 env=None) -> dict:
    """Opt-in standalone dispatcher. No live SRE implementation is invented."""
    pid=require_id(project_id,"project_id",PROJECT_RE)
    owner=_worker(worker_id)
    if not _enabled(env):
        return {"status":"disabled","project_id":pid,"external_calls":0}
    if not _verified_port(adapter):
        return {"status":"sre_unavailable","project_id":pid,"external_calls":0}
    claim=claim_one(pid,owner,connect=connect)
    if claim is None:
        return {"status":"idle","project_id":pid,"external_calls":0}
    # The lease transaction is committed before any remote delivery. A
    # malformed DB event is blocked DURABLY instead of rolling back the claim
    # and spinning on the same poisoned pending event forever.
    try:
        with _connection(connect) as conn:
            raw=conn.execute(
                """SELECT event_payload,delivery_key,attempts,lease_generation
                   FROM collector_sre_outbox
                   WHERE id=%s::uuid AND project_id=%s AND state='leased'
                     AND leased_by=%s AND lease_generation=%s
                     AND lease_until>now()""",
                (claim["collector_outbox_id"],pid,owner,claim["lease_generation"]),
            ).fetchone()
            if raw is None:
                _fail("sre_lost_lease","Outbox lease expired before dispatch")
            payload,key,attempts,generation=raw
            message=_strict_event(conn,pid,(
                claim["collector_outbox_id"],key,payload,attempts,generation))
    except CollectorError as exc:
        if exc.code=="sre_lost_lease":
            return {"status":"lost_lease","project_id":pid,
                    "event_id":claim["collector_outbox_id"],"external_calls":0}
        code=exc.code if exc.code in (
            "sre_source_rights","sre_event_invalid","sre_qv_integrity",
            "sre_d1_provenance_missing") else "sre_event_invalid"
        state=fail_delivery(pid,owner,claim["collector_outbox_id"],
                            claim["lease_generation"],error_code=code,
                            permanent=True,connect=connect)
        return {**state,"external_calls":0,"delivered":False}
    event=message["sre_event"]
    try:
        # Only an operator-installed adapter with real authenticated durable
        # SRE inbox can return a valid receipt. SRE M35 is not available yet.
        receipt=adapter.deliver(dict(event))
        _validate_ack(receipt,event)
    except Exception:
        state=fail_delivery(pid,owner,claim["collector_outbox_id"],
                            claim["lease_generation"],error_code="sre_send_failed",
                            connect=connect)
        return {**state,"external_calls":1,"delivered":False}
    try:
        final=ack_delivery(
            pid,owner,claim["lease_generation"],
            {**event,"delivery_key":claim["delivery_key"]},receipt,connect=connect,
        )
    except CollectorError as exc:
        # External SRE may already have committed; next lease retry uses
        # exactly the same event_id. Receiver MUST dedupe by event_id.
        return {"status":"ack_pending","project_id":pid,
                "event_id":claim["collector_outbox_id"],
                "error_code":exc.code,"external_calls":1,"delivered":False}
    return {**final,"external_calls":1}
