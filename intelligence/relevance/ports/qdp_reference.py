"""M36 reference-only QDP contract kit; no QDP imports or side effects.

A downstream consumer uses event_id for idempotence and a tombstone set of
prior_ready_event_id. Never accept a ready event after its invalidation, even
if the ready event arrives late or is redelivered after network failure.
"""
from __future__ import annotations

from ..v1_contracts import (
    canonical_uuid, fingerprint, positive, project_id, record, reject,
    sha256_digest, utc_time,
)
from .outbound import validate_live_result_event

INVALIDATED_VERSION="sre.results_invalidated/v1"
REASONS=frozenset((
    "source_revoked","rights_lost","review_superseded",
    "operator_revoked",
))


def make_invalidation(*,project,prior_ready,reason,event_id,occurred_at):
    ready=validate_live_result_event(prior_ready)
    pid=project_id(project)
    if ready["project_id"]!=pid:
        reject("project_id","foreign original event","rights_denied")
    if type(reason) is not str or reason not in REASONS:
        reject("reason","unsupported invalidation reason","invalid_contract")
    event={
        "schema_version":INVALIDATED_VERSION,
        "event_id":canonical_uuid(event_id,"event_id"),
        "project_id":pid,
        "run_id":ready["run_id"],
        "video_id":ready["video_id"],
        "content_version_id":ready["content_version_id"],
        "content_sha256":ready["content_sha256"],
        "prior_ready_event_id":ready["event_id"],
        "prior_ready_event_sha256":ready["event_sha256"],
        "reason_code":reason,"occurred_at":occurred_at,
    }
    event["event_sha256"]=fingerprint(event)
    return validate_invalidation_event(event)


def validate_invalidation_event(payload):
    record(payload,"invalidation",required=(
        "schema_version","event_id","project_id","run_id","video_id",
        "content_version_id","content_sha256","prior_ready_event_id",
        "prior_ready_event_sha256","reason_code","occurred_at","event_sha256",
    ))
    if payload["schema_version"]!=INVALIDATED_VERSION:
        reject("schema_version","unknown invalidation contract",
               "unsupported_schema_version")
    project_id(payload["project_id"])
    for key in ("event_id","run_id","prior_ready_event_id"):
        canonical_uuid(payload[key],key)
    for key in ("video_id","content_version_id"):
        positive(payload[key],key,maximum=9223372036854775807)
    for key in ("content_sha256","prior_ready_event_sha256","event_sha256"):
        sha256_digest(payload[key],key)
    if payload["reason_code"] not in REASONS:
        reject("reason_code","unexpected invalidation")
    utc_time(payload["occurred_at"],"occurred_at")
    if fingerprint({k:v for k,v in payload.items() if k!="event_sha256"})!=payload["event_sha256"]:
        reject("event_sha256","invalidation content tampered","snapshot_mismatch")
    return dict(payload)


def apply_qdp_reference_event(event, state):
    """Pure reference consumer. Caller owns durable state/transaction.

    Return a NEW state; keep a permanent revoked-ID set, not just latest
    status. A duplicate event ID is harmless only if the original SHA is
    unchanged; a collision is a hard error. No fake/legacy event is accepted.
    """
    if type(state) is not dict or set(state)!={"seen","ready","invalidated"}:
        reject("state","expected consumer materialized state")
    old={k:dict(v) for k,v in state.items()}
    if not all(type(x) is dict for x in old.values()):
        reject("state","invalid consumer mapping")
    if type(event) is not dict:
        reject("event","expected event object")
    version=event.get("schema_version")
    if version=="sre.classifications_ready/v1":
        msg=validate_live_result_event(event)
        ready=True
    elif version==INVALIDATED_VERSION:
        msg=validate_invalidation_event(event)
        ready=False
    else:
        reject("schema_version","unsupported event type",
               "unsupported_schema_version")
    eid=msg["event_id"];digest=msg["event_sha256"]
    seen=old["seen"]
    if eid in seen:
        if seen[eid]!=digest:
            reject("event_id","collision with changed event payload",
                   "snapshot_mismatch")
        return old
    if ready:
        previous_sha=old["invalidated"].get(eid)
        if previous_sha is not None and previous_sha!=digest:
            reject("ready_event", "late ready SHA differs from prior invalidation", "snapshot_mismatch")
        if previous_sha is None:
            old["ready"][eid]=msg
    else:
        previous=msg["prior_ready_event_id"]
        if previous in old["ready"]:
            prior=old["ready"][previous]
            if (prior["event_sha256"]!=msg["prior_ready_event_sha256"] or
                    prior["project_id"]!=msg["project_id"] or
                    prior["content_version_id"]!=msg["content_version_id"] or
                    prior["run_id"]!=msg["run_id"]):
                reject("invalidation","prior event does not match immutable source",
                       "snapshot_mismatch")
            del old["ready"][previous]
        previous_inv=old["invalidated"].get(previous)
        if previous_inv is not None and previous_inv!=msg["prior_ready_event_sha256"]:
            reject("invalidation","conflicting invalidation source digest",
                   "snapshot_mismatch")
        old["invalidated"][previous]=msg["prior_ready_event_sha256"]
    seen[eid]=digest
    return old


def empty_qdp_reference_state():
    return {"seen":{},"ready":{},"invalidated":{}}


__all__=("INVALIDATED_VERSION","REASONS","make_invalidation",
         "validate_invalidation_event","apply_qdp_reference_event",
         "empty_qdp_reference_state")
