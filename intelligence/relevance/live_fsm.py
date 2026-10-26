"""M10 — independent typed LIVE run, item, batch and dispatch-attempt contracts.

Pure value objects / finite-state machines. No DB, network, model, cost or
permission effects. S4 offline_fixture_s4 remains a SEPARATE execution mode.
A validated DTO is NOT a receipt for budget approval, source authorization,
successful dispatch, or human-reviewed classification.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .v1_contracts import (
    canonical_uuid, fingerprint, positive, project_id, record, reject,
    sha256_digest,
)

VERSION = "sre-live-fsm/v1"
MODE = "live"
MAX_BIGINT = 9223372036854775807
MAX_LEAVES = 48

# No alias from S4/offline_fixture to LIVE. These states belong exclusively to
# the future durable M11+ run engine.
RUN_EDGES = {
    "planned": ("pending_consent", "cancelled"),
    "pending_consent": ("queued", "paused_budget", "paused_rights", "waiting_source", "cancelled", "failed"),
    "queued": ("running", "waiting_source", "paused_rights", "paused_budget", "cancelled", "failed"),
    "running": ("waiting_source", "paused_budget", "paused_rights", "unknown", "partial",
                "completed", "failed", "cancelled"),
    "waiting_source": ("queued", "paused_rights", "cancelled", "failed"),
    "paused_budget": ("pending_consent", "queued", "cancelled", "failed"),
    "paused_rights": ("queued", "waiting_source", "cancelled", "failed"),
    "unknown": ("running", "partial", "completed", "failed", "cancelled"),
    "partial": (), "completed": (), "failed": (), "cancelled": (),
}
ITEM_EDGES = {
    "not_evaluated": ("queued", "waiting_source", "paused_rights", "cancelled"),
    "queued": ("running", "waiting_source", "paused_budget", "paused_rights", "cancelled", "failed"),
    "running": ("waiting_source", "paused_budget", "paused_rights", "unknown", "partial",
                "completed", "failed", "cancelled"),
    "waiting_source": ("queued", "paused_rights", "cancelled", "failed"),
    "paused_budget": ("queued", "cancelled", "failed"),
    "paused_rights": ("queued", "waiting_source", "cancelled", "failed"),
    "unknown": ("running", "partial", "completed", "failed", "cancelled"),
    "partial": (), "completed": (), "failed": (), "cancelled": (),
}
BATCH_EDGES = {
    "queued": ("running", "waiting_source", "paused_budget", "paused_rights", "cancelled", "failed"),
    "running": ("waiting_source", "paused_budget", "paused_rights", "unknown", "partial",
                "completed", "failed", "cancelled"),
    "waiting_source": ("queued", "paused_rights", "cancelled", "failed"),
    "paused_budget": ("queued", "cancelled", "failed"),
    "paused_rights": ("queued", "waiting_source", "cancelled", "failed"),
    "unknown": ("running", "partial", "completed", "failed", "cancelled"),
    "partial": (), "completed": (), "failed": (), "cancelled": (),
}
ATTEMPT_EDGES = {
    "reserved": ("dispatch_intent", "abandoned_before_dispatch"),
    "dispatch_intent": ("unknown", "completed", "failed_known"),
    "unknown": ("completed", "failed_known"),
    "completed": (), "failed_known": (), "abandoned_before_dispatch": (),
}
EDGES = {
    "run": RUN_EDGES, "item": ITEM_EDGES,
    "batch": BATCH_EDGES, "attempt": ATTEMPT_EDGES,
}
RUN_STATES = frozenset(RUN_EDGES)
ITEM_STATES = frozenset(ITEM_EDGES)
BATCH_STATES = frozenset(BATCH_EDGES)
ATTEMPT_STATES = frozenset(ATTEMPT_EDGES)
TERMINAL = frozenset(("partial", "completed", "failed", "cancelled"))
ATTEMPT_TERMINAL = frozenset(("completed", "failed_known", "abandoned_before_dispatch"))

# Stable, non-semantic, machine-actionable error taxonomy. "failed_known"
# means definitive provider/transport evidence, not a model's refusal to match.
OUTCOME_CODES = frozenset((
    "success", "known_provider_rejection", "known_validation_failure",
    "rate_limited", "transport_error", "deadline_exceeded",
    "unknown_charge", "rights_revoked", "content_expired",
    "budget_unavailable", "lease_lost", "cancelled_by_owner",
))
OUTCOME_CATEGORIES = frozenset((
    "success", "known_failure", "unknown", "blocked_rights",
    "blocked_budget", "stale_lease", "cancelled",
))
_DECISIONS = frozenset(("match", "no_match", "uncertain"))
_UNKNOWN_DISPATCH = frozenset((
    "rate_limited", "transport_error", "deadline_exceeded", "unknown_charge",
))


def _choice(value, allowed, path):
    if type(value) is not str or value not in allowed:
        reject(path, "unsupported LIVE value")
    return value


def _count(value, path, *, zero=False, maximum=1000000):
    return positive(value, path, zero=zero, maximum=maximum)


def _id(value, path):
    return positive(value, path, maximum=MAX_BIGINT)


def _root(value, path, required):
    record(value, path, required=required)
    if value["schema_version"] != VERSION:
        reject(path+".schema_version", "unsupported LIVE FSM contract", "unsupported_schema_version")
    if value["execution_mode"] != MODE:
        reject(path+".execution_mode", "offline/legacy results are NOT LIVE", "synthetic_result_denied")
    project_id(value["project_id"], path+".project_id")
    canonical_uuid(value["run_id"], path+".run_id")
    return value


def _keys(value, path):
    if (type(value) is not list or not 1 <= len(value) <= MAX_LEAVES):
        reject(path, "expected 1..48 distinct sorted leaf UUIDs")
    for i, key in enumerate(value):
        canonical_uuid(key, f"{path}[{i}]")
    if value != sorted(set(value)):
        reject(path, "hypotheses must be unique and sorted")
    return tuple(value)


def _outcomes(value, expected, path):
    """Never convert partial, unavailable or absent labels into no_match."""
    record(value, path, required=("match", "no_match", "uncertain", "not_evaluated"))
    groups = {}
    for code in ("match", "no_match", "uncertain", "not_evaluated"):
        entries = value[code]
        if type(entries) is not list or entries != sorted(set(entries)):
            reject(path+"."+code, "outcome keys must be a unique sorted list")
        for i, item in enumerate(entries):
            canonical_uuid(item, f"{path}.{code}[{i}]")
        groups[code] = tuple(entries)
    flat = [v for vs in groups.values() for v in vs]
    if len(flat) != len(set(flat)) or set(flat) != set(expected):
        reject(path, "each expected hypothesis must have exactly one outcome",
               "incomplete_coverage")
    return groups


@dataclass(frozen=True, slots=True)
class LiveRun:
    schema_version: Literal["sre-live-fsm/v1"]
    execution_mode: Literal["live"]
    project_id: str
    run_id: str
    publication_id: str
    taxonomy_sha256: str
    pipeline_sha256: str
    request_sha256: str
    state: str
    expected_items: int
    finished_items: int
    blocked_items: int


@dataclass(frozen=True, slots=True)
class LiveItem:
    schema_version: Literal["sre-live-fsm/v1"]
    execution_mode: Literal["live"]
    project_id: str
    run_id: str
    video_id: int
    content_version_id: int
    content_sha256: str
    expected_hypothesis_keys: tuple[str, ...]
    outcomes: dict[str, tuple[str, ...]]
    state: str
    lease_generation: int


@dataclass(frozen=True, slots=True)
class LiveBatch:
    schema_version: Literal["sre-live-fsm/v1"]
    execution_mode: Literal["live"]
    project_id: str
    run_id: str
    batch_id: str
    item_ids: tuple[int, ...]
    input_sha256: str
    state: str
    lease_generation: int


@dataclass(frozen=True, slots=True)
class LiveAttempt:
    schema_version: Literal["sre-live-fsm/v1"]
    execution_mode: Literal["live"]
    project_id: str
    run_id: str
    batch_id: str
    attempt_id: str
    request_sha256: str
    state: str
    lease_generation: int
    dispatch_intent: bool
    provider_request_id: str | None
    outcome_code: str | None


@dataclass(frozen=True, slots=True)
class TechnicalOutcome:
    code: str
    category: str
    retry_policy: str


def validate_live_run(raw):
    r = _root(raw, "live_run", (
        "schema_version", "execution_mode", "project_id", "run_id",
        "publication_id", "taxonomy_sha256", "pipeline_sha256",
        "request_sha256", "state", "expected_items",
        "finished_items", "blocked_items",
    ))
    canonical_uuid(r["publication_id"], "live_run.publication_id")
    for sha in ("taxonomy_sha256", "pipeline_sha256", "request_sha256"):
        sha256_digest(r[sha], "live_run."+sha)
    _choice(r["state"], RUN_STATES, "live_run.state")
    total = _count(r["expected_items"], "live_run.expected_items")
    done = _count(r["finished_items"], "live_run.finished_items", zero=True)
    blocked = _count(r["blocked_items"], "live_run.blocked_items", zero=True)
    if done+blocked > total:
        reject("live_run", "finished + blocked exceeds total")
    if r["state"]=="completed" and (done != total or blocked):
        reject("live_run.state", "completed requires ALL items processed and no blocked items",
               "incomplete_coverage")
    if r["state"]=="partial" and not 0 < done < total:
        reject("live_run.state", "partial requires 0 < completed < total",
               "incomplete_coverage")
    if r["state"] in ("planned", "pending_consent") and (done or blocked):
        reject("live_run.state", "unstarted run cannot already have item outcomes")
    return LiveRun(**r)


def validate_live_item(raw):
    r = _root(raw, "live_item", (
        "schema_version", "execution_mode", "project_id", "run_id",
        "video_id", "content_version_id", "content_sha256",
        "expected_hypothesis_keys", "outcomes", "state", "lease_generation",
    ))
    _id(r["video_id"], "live_item.video_id")
    _id(r["content_version_id"], "live_item.content_version_id")
    sha256_digest(r["content_sha256"], "live_item.content_sha256")
    keys = _keys(r["expected_hypothesis_keys"], "live_item.expected_hypothesis_keys")
    outcomes = _outcomes(r["outcomes"], keys, "live_item.outcomes")
    _choice(r["state"], ITEM_STATES, "live_item.state")
    generation = _count(r["lease_generation"], "live_item.lease_generation", zero=True)
    evaluated = sum(len(outcomes[k]) for k in _DECISIONS)
    not_evaluated = len(outcomes["not_evaluated"])
    if r["state"]=="completed" and (not_evaluated or evaluated != len(keys)):
        reject("live_item.state", "completed requires all leaves actually evaluated",
               "incomplete_coverage")
    if r["state"]=="partial" and not 0 < evaluated < len(keys):
        reject("live_item.state", "partial requires measured subset and explicit not_evaluated",
               "incomplete_coverage")
    if r["state"] in ("not_evaluated", "queued") and evaluated:
        reject("live_item.state", "unstarted item cannot contain inferred decisions")
    return LiveItem(
        schema_version=VERSION, execution_mode=MODE, project_id=r["project_id"],
        run_id=r["run_id"], video_id=r["video_id"],
        content_version_id=r["content_version_id"],
        content_sha256=r["content_sha256"],
        expected_hypothesis_keys=keys, outcomes=outcomes,
        state=r["state"], lease_generation=generation,
    )


def validate_live_batch(raw):
    r = _root(raw, "live_batch", (
        "schema_version", "execution_mode", "project_id", "run_id",
        "batch_id", "item_ids", "input_sha256", "state", "lease_generation",
    ))
    canonical_uuid(r["batch_id"], "live_batch.batch_id")
    sha256_digest(r["input_sha256"], "live_batch.input_sha256")
    if type(r["item_ids"]) is not list or not 1 <= len(r["item_ids"]) <= 1000:
        reject("live_batch.item_ids", "expected 1..1000 item IDs")
    ids = tuple(_id(v, "live_batch.item_ids[]") for v in r["item_ids"])
    if list(ids) != sorted(set(ids)):
        reject("live_batch.item_ids", "items must be distinct and sorted")
    _choice(r["state"], BATCH_STATES, "live_batch.state")
    generation = _count(r["lease_generation"], "live_batch.lease_generation", zero=True)
    return LiveBatch(
        schema_version=VERSION, execution_mode=MODE, project_id=r["project_id"],
        run_id=r["run_id"], batch_id=r["batch_id"], item_ids=ids,
        input_sha256=r["input_sha256"], state=r["state"], lease_generation=generation,
    )


def validate_live_attempt(raw):
    r = _root(raw, "live_attempt", (
        "schema_version", "execution_mode", "project_id", "run_id",
        "batch_id", "attempt_id", "request_sha256", "state",
        "lease_generation", "dispatch_intent", "provider_request_id", "outcome_code",
    ))
    canonical_uuid(r["batch_id"], "live_attempt.batch_id")
    canonical_uuid(r["attempt_id"], "live_attempt.attempt_id")
    sha256_digest(r["request_sha256"], "live_attempt.request_sha256")
    _choice(r["state"], ATTEMPT_STATES, "live_attempt.state")
    generation = _count(r["lease_generation"], "live_attempt.lease_generation", zero=True)
    if type(r["dispatch_intent"]) is not bool:
        reject("live_attempt.dispatch_intent", "expected strict bool")
    if r["provider_request_id"] is not None:
        if (type(r["provider_request_id"]) is not str
                or not 1 <= len(r["provider_request_id"]) <= 200
                or not r["provider_request_id"].isascii()):
            reject("live_attempt.provider_request_id", "invalid provider request ID")
    if r["outcome_code"] is not None:
        _choice(r["outcome_code"], OUTCOME_CODES, "live_attempt.outcome_code")
    if (r["state"] in ("dispatch_intent", "unknown", "completed", "failed_known")
            and not r["dispatch_intent"]):
        reject("live_attempt", "dispatched states require durable dispatch intent")
    if r["state"] in ("reserved", "abandoned_before_dispatch") and r["dispatch_intent"]:
        reject("live_attempt", "undispatched attempt cannot claim send intent")
    if r["state"] in ATTEMPT_TERMINAL | {"unknown"} and r["state"] != "abandoned_before_dispatch":
        if r["outcome_code"] is None:
            reject("live_attempt.outcome_code", "settled or unknown attempt needs outcome")
    elif r["outcome_code"] is not None and r["state"] != "abandoned_before_dispatch":
        reject("live_attempt.outcome_code", "unfinished attempt cannot have outcome")
    if r["state"] == "completed" and r["outcome_code"] != "success":
        reject("live_attempt.outcome_code", "successful attempt requires success")
    if r["state"] == "unknown" and r["outcome_code"] not in _UNKNOWN_DISPATCH:
        reject("live_attempt.outcome_code", "unknown means potentially charged dispatch")
    if r["state"] == "failed_known" and r["outcome_code"] in (_UNKNOWN_DISPATCH | {"success"}):
        reject("live_attempt.outcome_code", "ambiguous charge cannot be a known failure")
    if r["state"] == "abandoned_before_dispatch" and r["outcome_code"] not in (None, "cancelled_by_owner"):
        reject("live_attempt.outcome_code", "abandoned before send may only be cancelled")
    return LiveAttempt(
        schema_version=VERSION,execution_mode=MODE,project_id=r["project_id"],
        run_id=r["run_id"],batch_id=r["batch_id"],attempt_id=r["attempt_id"],
        request_sha256=r["request_sha256"],state=r["state"],
        lease_generation=generation,dispatch_intent=r["dispatch_intent"],
        provider_request_id=r["provider_request_id"],outcome_code=r["outcome_code"],
    )


def transition_live(kind, current, target, *, reconciled=False, coverage=None):
    """Pure transition authorization; NOT a database compare-and-swap.

    'unknown' attempt -> terminal requires a trusted reconciliation receipt,
    never a blind retry. Lease and budget fencing belong to durable M11+.
    A future worker must re-read M08 rights before dispatch and before publish.
    """
    if type(kind) is not str or kind not in EDGES:
        reject("fsm.kind", "unknown LIVE FSM domain")
    states = EDGES[kind]
    _choice(current, states, "fsm.current")
    _choice(target, states, "fsm.target")
    if type(reconciled) is not bool:
        reject("fsm.reconciled", "expected strict boolean")
    if target not in states[current]:
        reject("fsm.transition", "transition forbidden", "invalid_transition")
    if kind == "attempt" and current == "unknown" and not reconciled:
        reject("fsm.reconciled", "unknown provider charge requires audited reconciliation",
               "ambiguous_charge")
    if kind in ("run", "item", "batch") and target in ("completed", "partial"):
        if type(coverage) is not dict:
            reject("fsm.coverage", "settled transition needs exact coverage")
        record(coverage, "fsm.coverage", required=("expected", "evaluated", "blocked"))
        expected = _count(coverage["expected"], "fsm.coverage.expected")
        evaluated = _count(coverage["evaluated"], "fsm.coverage.evaluated", zero=True)
        blocked = _count(coverage["blocked"], "fsm.coverage.blocked", zero=True)
        if evaluated+blocked>expected:
            reject("fsm.coverage", "coverage exceeds expected", "incomplete_coverage")
        if target == "completed" and (evaluated!=expected or blocked):
            reject("fsm.coverage", "no completed state with missing leaves/items",
                   "incomplete_coverage")
        if target == "partial" and not 0<evaluated<expected:
            reject("fsm.coverage", "partial requires nonzero, incomplete actual evaluations",
                   "incomplete_coverage")
    return target


def classify_technical_outcome(code, *, dispatch_intent):
    """Fail closed after any possible send, even for 429/timeouts/network faults."""
    _choice(code, OUTCOME_CODES, "technical_outcome.code")
    if type(dispatch_intent) is not bool:
        reject("technical_outcome.dispatch_intent", "expected strict boolean")
    if code=="success":
        if not dispatch_intent:
            reject("technical_outcome", "cannot have provider success without send")
        return TechnicalOutcome(code,"success","never")
    if code in ("rights_revoked","content_expired"):
        return TechnicalOutcome(code,"blocked_rights","only_after_new_authorization")
    if code=="budget_unavailable":
        return TechnicalOutcome(code,"blocked_budget","only_after_new_authorization")
    if code=="lease_lost":
        return TechnicalOutcome(code,"stale_lease","never")
    if code=="cancelled_by_owner":
        return TechnicalOutcome(code,"cancelled","never")
    if code in _UNKNOWN_DISPATCH:
        if dispatch_intent:
            return TechnicalOutcome(code,"unknown","reconcile_only")
        if code=="unknown_charge":
            reject("technical_outcome", "charge unknown without dispatch intent")
        return TechnicalOutcome(code,"known_failure","safe_after_new_reservation")
    return TechnicalOutcome(code,"known_failure",
                            "never" if dispatch_intent else "safe_after_new_reservation")


__all__ = (
    "VERSION", "MODE", "RUN_STATES", "ITEM_STATES", "BATCH_STATES",
    "ATTEMPT_STATES", "OUTCOME_CODES", "OUTCOME_CATEGORIES", "EDGES",
    "LiveRun", "LiveItem", "LiveBatch", "LiveAttempt", "TechnicalOutcome",
    "validate_live_run", "validate_live_item", "validate_live_batch",
    "validate_live_attempt", "transition_live", "classify_technical_outcome",
)
