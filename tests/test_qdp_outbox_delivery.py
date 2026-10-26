"""FIX-12 source-integrity, canonical handoff and receipt validation."""
import copy
import unittest
import uuid

from intelligence.qualified_data.contracts import QDPError, sha256
from intelligence.qualified_data.outbox_delivery import (
    CONSUMER_GROUP, verify_delivery_event, verify_receipt,
    dispatch_once, settle_delivery, DELIVERY_LEASE_SECONDS,
)

RUN=str(uuid.uuid4())
EVENT=str(uuid.uuid4())
PROJECT="a"*32
INPUT_SHA="b"*64


def event_row(group="C"):
    data={"schema_version":"qdp-event/v1","event_type":"preparation_ready",
          "project_id":PROJECT,"run_id":RUN,"video_id":49,
          "analysis_group":group,"state":"ready","reason_code":None,
          "input_sha256":INPUT_SHA}
    return {"event_id":uuid.UUID(EVENT),"project_id":PROJECT,
            "run_id":uuid.UUID(RUN),"video_id":49,"analysis_group":group,
            "event_type":"preparation_ready","payload":data,
            "payload_sha256":sha256(data)}


class DeliveryContractTests(unittest.TestCase):
    def test_groups_map_one_to_one(self):
        self.assertEqual(CONSUMER_GROUP,{"group_a":"A","group_b":"B","group_c":"C"})
        self.assertGreaterEqual(DELIVERY_LEASE_SECONDS,30)

    def test_valid_pointer_only_exact_input_hash(self):
        row=event_row()
        p=verify_delivery_event(row,"group_c")
        self.assertEqual(p.event_id,EVENT)
        self.assertEqual(p.qdp_run_id,RUN)
        self.assertEqual(p.video_id,49)
        self.assertEqual(p.analysis_group,"C")
        self.assertEqual(p.input_sha256,INPUT_SHA)

    def test_payload_corruption_rejected_before_consumer(self):
        row=event_row()
        for patch in ({"project_id":"f"*32},{"event_type":"run_completed"},
                      {"input_sha256":"a"*64},{"reason_code":"not_ready"}):
            with self.subTest(patch=patch),self.assertRaises(QDPError):
                changed=copy.deepcopy(row)
                changed["payload"].update(patch)
                verify_delivery_event(changed,"group_c")

    def test_group_swap_rejected(self):
        with self.assertRaises(QDPError):
            verify_delivery_event(event_row("B"),"group_c")

    def test_valid_committed_receipt(self):
        row=event_row()
        p=verify_delivery_event(row,"group_c")
        receipt={"event_id":EVENT,"consumer":"group_c","state":"committed",
                 "payload_sha256":row["payload_sha256"],"receipt_key":"durable-test-1"}
        self.assertEqual(verify_receipt(receipt,p,"group_c",row["payload_sha256"]),
                         "durable-test-1")

    def test_ack_never_accepts_uncommitted_or_wrong_receipt(self):
        row=event_row()
        p=verify_delivery_event(row,"group_c")
        valid={"event_id":EVENT,"consumer":"group_c","state":"committed",
               "payload_sha256":row["payload_sha256"],"receipt_key":"durable-test-1"}
        for patch in ({"state":"queued"},{"state":"received"},
                      {"event_id":str(uuid.uuid4())},{"consumer":"group_a"},
                      {"payload_sha256":"0"*64},{"receipt_key":"short"}):
            with self.subTest(patch=patch),self.assertRaises(QDPError):
                verify_receipt(dict(valid,**patch),p,"group_c",row["payload_sha256"])

    def test_unavailable_peer_does_not_claim_or_ack(self):
        class Bomb:
            def __call__(self):
                raise AssertionError("No DB claim when receiver unavailable")
        with self.assertRaises(QDPError) as ctx:
            dispatch_once("group_c",receiver=None,connect=Bomb())
        self.assertEqual(ctx.exception.code,"consumer_unavailable")

    def test_unknown_consumer_rejected(self):
        with self.assertRaises(QDPError):
            dispatch_once("analytics",receiver=None)
        with self.assertRaises(QDPError):
            verify_delivery_event(event_row(),"group_unknown")

    def test_invalid_settlement_rejected(self):
        with self.assertRaises(QDPError):
            settle_delivery({"event_id":EVENT},receipt=None,error_code=None)

if __name__=="__main__":
    unittest.main()
