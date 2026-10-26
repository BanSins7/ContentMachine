"""SP-12 strictly opt-in Collector handoff/receipt contracts; no Collector import.

The actual Collector branch has no compatible production source publisher in
main. These classes define an ADAPTER CONTRACT, NOT a live integration.
EmptyCollectorAdapter is the production default; MockCollectorAdapter is QA
ONLY and must never be installed as an operational provider.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Mapping

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.search_planner.contracts import publication_uuid
from intelligence.taxonomy_builder.pipeline_repository import _pid

RECEIPT_SCHEMA = "search-planner-collector-durable-receipt/v1"
EXPECTED_COLLECTOR_SCHEMA = "validated_query_set/v1"
RECEIPT_FIELDS = frozenset((
    "schema_version", "collector_contract", "project_id", "event_id",
    "chunk_index", "chunk_sha256", "collector_query_set_sha256",
    "idempotency_key", "external_query_set_id", "external_plan_id",
    "external_task_ids", "accepted_at", "durable",
    "provider_execution_authorized", "receipt_sha256",
))


def verify_collector_receipt(value, *, project_id, event_id, chunk,
                             idempotency_key):
    pid,eid=_pid(project_id),publication_uuid(event_id)
    if type(value) is not dict or set(value)!=RECEIPT_FIELDS:
        raise PipelineError("Collector returned no versioned durable receipt",409)
    if (value["schema_version"]!=RECEIPT_SCHEMA or
            value["collector_contract"]!=EXPECTED_COLLECTOR_SCHEMA or
            value["project_id"]!=pid or value["event_id"]!=eid or
            type(value["chunk_index"]) is not int or
            value["chunk_index"]!=chunk["chunk_index"] or
            value["chunk_sha256"]!=chunk["chunk_sha256"] or
            value["collector_query_set_sha256"]!=chunk["collector_query_set_sha256"] or
            value["idempotency_key"]!=idempotency_key or
            value["durable"] is not True or
            value["provider_execution_authorized"] is not False or
            type(value["external_task_ids"]) is not list or
            len(value["external_task_ids"])!=0):
        raise PipelineError("Collector receipt does not prove safe durable intake",409)
    query_set_id=value["external_query_set_id"]
    external_plan_id=value["external_plan_id"]
    if (type(query_set_id) is not str or not 1<=len(query_set_id)<=240 or
            (external_plan_id is not None and
             (type(external_plan_id) is not str or not 1<=len(external_plan_id)<=240))):
        raise PipelineError("Missing durable external query-set identity",409)
    timestamp=value["accepted_at"]
    if type(timestamp) is not str:
        raise PipelineError("Collector receipt timestamp missing",409)
    try:
        accepted=datetime.fromisoformat(timestamp.replace("Z","+00:00"))
    except ValueError as exc:
        raise PipelineError("Invalid Collector receipt acceptance timestamp",409) from exc
    if accepted.tzinfo is None or accepted.utcoffset() is None:
        raise PipelineError("Collector receipt must contain an aware timestamp",409)
    digest=value["receipt_sha256"]
    if (type(digest) is not str or
            sha256({k:v for k,v in value.items() if k!="receipt_sha256"}) != digest):
        raise PipelineError("Collector durable receipt digest mismatch",409)
    # The checksum detects corruption, not authenticity. The injected
    # production port must be trusted + independently re-read external DB.
    return deepcopy(value)


class EmptyCollectorAdapter:
    """Default no-network adapter. Never claim missing Collector acceptance."""
    def available(self):
        return False

    def accept(self, *, project_id, handoff, idempotency_key):
        raise PipelineError("Collector durable intake adapter is not installed",503)

    def get_receipt(self, *, project_id, idempotency_key):
        return None


class MockCollectorAdapter:
    """Explicit tests only. A deterministic fake durable store, not a provider."""
    def __init__(self):
        self.receipts={}
        self.calls=0
        self.fail_after_accept=False

    def available(self):
        return True

    def accept(self, *, project_id, handoff, idempotency_key):
        pid=_pid(project_id)
        if type(handoff) is not dict or handoff.get("schema_version")!="search-planner-handoff-chunk/v1":
            raise PipelineError("Mock received untrusted chunk contract",409)
        saved=self.receipts.get((pid,idempotency_key))
        if saved is None:
            self.calls+=1
            saved={
                "schema_version": RECEIPT_SCHEMA,
                "collector_contract": EXPECTED_COLLECTOR_SCHEMA,
                "project_id": pid, "event_id": handoff["event_id"],
                "chunk_index": handoff["chunk_index"],
                "chunk_sha256": handoff["chunk_sha256"],
                "collector_query_set_sha256": handoff["collector_query_set_sha256"],
                "idempotency_key": idempotency_key,
                "external_query_set_id": "qa-queryset-"+sha256([pid,idempotency_key])[:32],
                "external_plan_id": None, "external_task_ids":[],
                "accepted_at":"2026-10-26T00:00:00+00:00",
                "durable":True, "provider_execution_authorized":False,
            }
            saved["receipt_sha256"]=sha256(saved)
            self.receipts[(pid,idempotency_key)]=deepcopy(saved)
        if self.fail_after_accept:
            raise TimeoutError("QA injected lost response after external durability")
        return deepcopy(saved)

    def get_receipt(self, *, project_id, idempotency_key):
        return deepcopy(self.receipts.get((_pid(project_id),idempotency_key)))
