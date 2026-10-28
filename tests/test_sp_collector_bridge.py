"""SP inbox -> Collector durable bridge, offline contract unit tests.

These tests do not claim a PostgreSQL 17 migration or actual DTK session.
"""
from __future__ import annotations

from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from intelligence.taxonomy_builder.pipeline_contracts import sha256, PipelineError
from intelligence.search_planner.handoff.collector_adapter import (
    RealCollectorAdapter, RECEIPT_SCHEMA, EXPECTED_COLLECTOR_SCHEMA,
    verify_collector_receipt,
)
from intelligence.collector.contracts import CollectorError
from intelligence.collector.query_intake import parse_validated_query_set
from intelligence.collector.sp_handoff import (
    _readback, _source, _expected_key, accept_sp_handoff,
)


class FakeCursor:
    def __init__(self, row):
        self.row=row

    def fetchone(self):
        return self.row


class ReadbackConnection:
    def __init__(self, row):
        self.row=row

    def execute(self, sql, args=None):
        assert "collector_sp_intake_receipts" in sql
        return FakeCursor(self.row)


def fixture():
    project="a"*32
    event=str(uuid4())
    pub=str(uuid4())
    generation=str(uuid4())
    query_id=str(uuid4())
    qid=str(uuid4())
    key=_expected_key(event,0)
    raw={
        "schema_version":"validated_query_set/v1",
        "project_id":project,"taxonomy_publication_id":pub,
        "taxonomy_version":3,"taxonomy_sha256":"b"*64,
        "generation_id":generation,"validation_version":"sp-qv-verified/v1",
        "queries":[{
            "query_id":query_id,"hypothesis_id":"hyp.alpha",
            "direction_id":"dir.alpha","mode":"keyword",
            "text":"professional workout routine","language":"en",
            "validation_state":"accepted",
        }],
    }
    parsed=parse_validated_query_set(raw,expected_project_id=project)
    chunksha="c"*64
    srcsha=sha256(raw)
    receipt={
        "schema_version":RECEIPT_SCHEMA,
        "collector_contract":EXPECTED_COLLECTOR_SCHEMA,
        "project_id":project,"event_id":event,"chunk_index":0,
        "chunk_sha256":chunksha,"collector_query_set_sha256":srcsha,
        "idempotency_key":key,"external_query_set_id":qid,
        "external_plan_id":None,"external_task_ids":[],
        "accepted_at":"2026-10-28T13:15:00+00:00",
        "durable":True,"provider_execution_authorized":False,
    }
    receipt["receipt_sha256"]=sha256(receipt)
    row=(event,0,chunksha,srcsha,qid,deepcopy(receipt),
         receipt["receipt_sha256"],raw,parsed.fingerprint,
         generation,pub,"trusted_query_validation/v1")
    return project,key,receipt,row


class CollectorSPBridgeContractTests(TestCase):
    def test_production_adapter_never_claims_missing_db_as_available(self):
        adapter=RealCollectorAdapter()
        with patch("intelligence.taxonomy_builder.pipeline_repository._connect",
                   side_effect=RuntimeError("db not configured")):
            self.assertFalse(adapter.available())
            self.assertEqual(adapter.capabilities()["accepted_contracts"],[])
            with self.assertRaises(PipelineError):
                adapter.accept(project_id="a"*32,handoff={},idempotency_key="bad")

    def test_durable_receipt_matches_collector_storage(self):
        pid,key,receipt,row=fixture()
        verified=_readback(ReadbackConnection(row),pid,key)
        self.assertEqual(verified,receipt)
        self.assertIsNot(verified,receipt)
        chunk={
            "chunk_index":0,"chunk_sha256":receipt["chunk_sha256"],
            "collector_query_set_sha256":receipt["collector_query_set_sha256"],
        }
        self.assertEqual(verify_collector_receipt(
            verified,project_id=pid,event_id=receipt["event_id"],
            chunk=chunk,idempotency_key=key),receipt)

    def test_false_durable_claim_and_corrupted_query_set_fail_closed(self):
        pid,key,receipt,row=fixture()
        for index,value in (
            (6,"f"*64),
            (8,"f"*64),
            (11,"untrusted"),
        ):
            changed=list(row)
            changed[index]=value
            with self.subTest(index=index),self.assertRaises(CollectorError):
                _readback(ReadbackConnection(tuple(changed)),pid,key)
        tampered=deepcopy(row[7])
        tampered["queries"][0]["text"]="wrong search"
        changed=list(row)
        changed[7]=tampered
        with self.assertRaises(CollectorError):
            _readback(ReadbackConnection(tuple(changed)),pid,key)

    def test_no_browser_json_can_invent_unreserved_intake(self):
        pid,key,receipt,row=fixture()
        with self.assertRaises((CollectorError,PipelineError)):
            accept_sp_handoff(pid,{"event_id":receipt["event_id"],
                                   "chunk_index":False},key)
        with self.assertRaises(CollectorError):
            _source(ReadbackConnection(None),pid,receipt["event_id"],0,key,
                    requires_attempt=True)


if __name__=="__main__":
    import unittest
    unittest.main()
