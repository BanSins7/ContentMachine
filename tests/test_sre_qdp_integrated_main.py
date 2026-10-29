"""Offline SRE M36 → QDP modern qualification/ACK contract tests."""
from __future__ import annotations
import unittest
from unittest.mock import Mock,MagicMock,patch
from uuid import uuid4

from intelligence.qualified_data.contracts import QDPError
from intelligence.qualified_data.qualification import resolve_qualified_set
from intelligence.relevance.sre_qdp_dispatch import dispatch_one_sre_qdp


def source_pair(*,decision="match",modern=True):
    result=str(uuid4()) if modern else 101
    review=str(uuid4()) if modern else 202
    row={
        "video_id":17,"result_id":result,
        "category_key":str(uuid4()),
        "model_decision":"match",
        "effective_decision":decision,
        "content_readable":True,
        "content_version_id":123,
        "result_content_sha256":"b"*64,
        "human_label_id":review,
        "human_label_decision":decision,
        "review_created_at":"2026-10-29T04:00:00+00:00",
        "source_id":"tiktok_dtk","source_policy_version_id":5,
        "evidence":{"m30_verified_chunk_sha256":"a"*64},
    }
    if modern:
        row["source_schema"]="sre_live_m36"
    return row


class TestModernSREQualification(unittest.TestCase):
    def test_modern_uuid_result_and_review_keep_original_identity(self):
        src=source_pair()
        result=resolve_qualified_set([src],strict=True)
        self.assertEqual(result["totals"],{
            "qualified":1,"needs_review":0,"excluded":0})
        self.assertEqual(result["qualified"][0]["result_ids"],[src["result_id"]])
        self.assertEqual(result["details"][0]["pairs"][0]["human_label_id"],
                         src["human_label_id"])
        self.assertEqual(result["details"][0]["pairs"][0]["source_schema"],
                         "sre_live_m36")

    def test_legacy_int_result_and_label_remain_supported(self):
        src=source_pair(modern=False)
        result=resolve_qualified_set([src],strict=True)
        self.assertEqual(result["qualified"][0]["result_ids"],[101])
        self.assertNotIn("source_schema",result["details"][0]["pairs"][0])

    def test_malformed_modern_identifiers_denied(self):
        for field,value in (("result_id",101),("human_label_id",202),
                            ("result_id","not-a-uuid")):
            src=source_pair()
            src[field]=value
            with self.subTest(field=field),self.assertRaises(QDPError):
                resolve_qualified_set([src],strict=True)

    def test_modern_verdict_not_manufactured(self):
        row=source_pair(decision="uncertain")
        result=resolve_qualified_set([row],strict=True)
        self.assertEqual(result["totals"]["qualified"],0)
        self.assertEqual(result["needs_review"],[17])


class TestSREToQDPProducerAck(unittest.TestCase):
    def test_only_ack_after_durable_destination_receipt_and_recheck(self):
        pid="a"*32
        eid=str(uuid4())
        event={"event_id":eid,"project_id":pid,"event_sha256":"b"*64,
               "schema_version":"sre.classifications_ready/v1"}
        claim={"event":event,"lease_token":str(uuid4()),"lease_generation":1}
        receipt={"event_id":eid,"project_id":pid,"event_sha256":"b"*64,
                 "persisted":True,"qualification_created":False}
        with patch("intelligence.relevance.sre_qdp_dispatch.claim_sre_result_event",
                   return_value=claim),patch(
             "intelligence.relevance.sre_qdp_dispatch.receive_sre_live_item",
                   return_value=receipt) as received,patch(
             "intelligence.relevance.sre_qdp_dispatch.get_sre_live_receipt",
                   return_value=receipt) as readback,patch(
             "intelligence.relevance.sre_qdp_dispatch.acknowledge_sre_result_event",
                   return_value={"acknowledged":True,"event_id":eid}) as ack:
            result=dispatch_one_sre_qdp(
                pid,sre_connect=MagicMock(),actor_id="operator",
                actor_authorizer=lambda *args: True,
            )
        self.assertEqual(result["state"],"acknowledged")
        self.assertFalse(result["qualification_created"])
        received.assert_called_once()
        readback.assert_called_once()
        ack.assert_called_once()

    def test_mismatched_receipt_never_acknowledged(self):
        pid="a"*32
        eid=str(uuid4())
        event={"event_id":eid,"project_id":pid,"event_sha256":"b"*64,
               "schema_version":"sre.classifications_ready/v1"}
        with patch("intelligence.relevance.sre_qdp_dispatch.claim_sre_result_event",
                   return_value={"event":event}),patch(
             "intelligence.relevance.sre_qdp_dispatch.receive_sre_live_item",
                   return_value={"event_id":eid,"project_id":pid,
                                 "event_sha256":"c"*64,"persisted":True,
                                 "qualification_created":False}),patch(
             "intelligence.relevance.sre_qdp_dispatch.acknowledge_sre_result_event"
            ) as ack:
            with self.assertRaises(Exception):
                dispatch_one_sre_qdp(
                    pid,sre_connect=MagicMock(),actor_id="operator",
                    actor_authorizer=lambda *args: True,
                )
            ack.assert_not_called()


if __name__=="__main__":
    unittest.main()
