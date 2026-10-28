"""SP-12 optional Collector intake port: durable receipt, no source execution.

All tests use MockCollectorAdapter, never the unmerged Collector branch.
"""
from __future__ import annotations

from copy import deepcopy
import unittest
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.search_planner.handoff.collector_adapter import (
    RECEIPT_SCHEMA,EmptyCollectorAdapter,MockCollectorAdapter,verify_collector_receipt,
)
from test_search_planner_stage11_handoff import synthetic_trusted_source
from intelligence.search_planner.handoff.projection import project_verified_query_handoff

PID="a"*32
EID="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
KEY="sp12-"+EID+"-0"


def payload():
    proj=project_verified_query_handoff(synthetic_trusted_source(1))
    return {**proj["chunks"][0],"event_id":EID}


class CollectorAdapterSP12(unittest.TestCase):
    def test_default_adapter_has_no_io_and_no_receipt(self):
        adapter=EmptyCollectorAdapter()
        self.assertFalse(adapter.available())
        self.assertIsNone(adapter.get_receipt(project_id=PID,idempotency_key=KEY))
        with self.assertRaises(PipelineError):
            adapter.accept(project_id=PID,handoff=payload(),idempotency_key=KEY)

    def test_mock_persists_and_rereads_same_durable_receipt(self):
        port=MockCollectorAdapter()
        self.assertTrue(port.available())
        result=port.accept(project_id=PID,handoff=payload(),idempotency_key=KEY)
        self.assertEqual(port.calls,1)
        self.assertEqual(result["schema_version"],RECEIPT_SCHEMA)
        self.assertTrue(result["durable"])
        self.assertFalse(result["provider_execution_authorized"])
        self.assertEqual(result["external_task_ids"],[])
        self.assertEqual(result,port.get_receipt(project_id=PID,idempotency_key=KEY))
        verified=verify_collector_receipt(
            result,project_id=PID,event_id=EID,
            chunk=payload(),idempotency_key=KEY,
        )
        self.assertEqual(verified,result)
        second=port.accept(project_id=PID,handoff=payload(),idempotency_key=KEY)
        self.assertEqual(second,result)
        self.assertEqual(port.calls,1)

    def test_lost_response_preserves_first_durable_external_receipt(self):
        port=MockCollectorAdapter()
        port.fail_after_accept=True
        with self.assertRaises(TimeoutError):
            port.accept(project_id=PID,handoff=payload(),idempotency_key=KEY)
        self.assertEqual(port.calls,1)
        receipt=port.get_receipt(project_id=PID,idempotency_key=KEY)
        self.assertIsNotNone(receipt)
        self.assertEqual(verify_collector_receipt(
            receipt,project_id=PID,event_id=EID,
            chunk=payload(),idempotency_key=KEY,
        ),receipt)

    def test_forged_receipt_hash_mismatch_or_execution_claim_rejected(self):
        receipt=MockCollectorAdapter().accept(
            project_id=PID,handoff=payload(),idempotency_key=KEY)
        for field,value in (
            ("receipt_sha256","0"*64),
            ("project_id","b"*32),
            ("event_id","bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            ("chunk_index",1),
            ("chunk_sha256","0"*64),
            ("collector_query_set_sha256","0"*64),
            ("durable",False),
            ("provider_execution_authorized",True),
            ("external_task_ids",["not_authorized"]),
            ("external_query_set_id",""),
            ("collector_contract","validated_query_set/v2"),
            ("accepted_at","not-iso"),
        ):
            fake=deepcopy(receipt)
            fake[field]=value
            with self.subTest(field=field),self.assertRaises(PipelineError):
                verify_collector_receipt(
                    fake,project_id=PID,event_id=EID,
                    chunk=payload(),idempotency_key=KEY)
        forged=deepcopy(receipt)
        forged["external_query_set_id"]="ffffffff-ffff-4fff-8fff-ffffffffffff"
        forged["receipt_sha256"]=sha256({k:v for k,v in forged.items() if k!="receipt_sha256"})
        # Self-hash cannot authenticate a malicious adapter. Only a trusted
        # installed port PLUS an independent external get_receipt can do so.
        self.assertEqual(verify_collector_receipt(
            forged,project_id=PID,event_id=EID,
            chunk=payload(),idempotency_key=KEY),forged)

    def test_adapter_manifest_declares_exact_v1_and_receipt_reinspection(self):
        m=MockCollectorAdapter().capabilities()
        self.assertEqual(m["schema_version"],
                         "search-planner-collector-adapter-capabilities/v1")
        self.assertEqual(m["accepted_contracts"],["validated_query_set/v1"])
        self.assertEqual(m["max_queries_per_set"],50)
        self.assertTrue(m["durable_receipt_readback"])
        self.assertTrue(m["provider_execution_separate"])

    def test_invalid_collector_manifest_refused_before_network_io(self):
        from intelligence.search_planner.handoff.dispatcher import _port
        class Unsupported(MockCollectorAdapter):
            def capabilities(self):
                raw=super().capabilities()
                raw["accepted_contracts"]=["validated_query_set/v2"]
                return raw
        port=Unsupported()
        with self.assertRaisesRegex(PipelineError,"cannot safely accept"):
            _port(port)
        self.assertEqual(port.calls,0)
        class MissingManifest:
            def available(self):return True
            def accept(self,**kwargs):raise AssertionError("Must not call")
            def get_receipt(self,**kwargs):return None
        with self.assertRaises(PipelineError):
            _port(MissingManifest())

    def test_bad_mock_handoff_is_rejected_before_durable_write(self):
        port=MockCollectorAdapter()
        with self.assertRaises(PipelineError):
            port.accept(project_id=PID,handoff={"status":"accepted"},
                        idempotency_key=KEY)
        self.assertEqual(port.calls,0)


if __name__=="__main__":
    unittest.main()
