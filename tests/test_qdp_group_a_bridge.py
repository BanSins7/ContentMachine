"""FIX-13 observed Group A v1 wire/receipt contract, without peer import."""
from __future__ import annotations
import unittest
import uuid

from intelligence.qualified_data.contracts import QDPError, sha256
from intelligence.qualified_data.integrations.contracts import QDPHandoffPointer
from intelligence.qualified_data.integrations.group_a import (
    GA_ENVELOPE, GA_QDP_PEER, GroupAConsumerAdapter, ga_envelope,
    verify_ga_receipt, from_installed_group_a, QDPGroupAReader
)


def pointer():
    return QDPHandoffPointer(event_id=str(uuid.uuid4()),project_id="a"*32,
       qdp_run_id=str(uuid.uuid4()),video_id=23,analysis_group="A",
       input_sha256="b"*64)


def receipt(event, digest, **patch):
    p=event["pointer"]
    out={"schema_version":"group-a-receipt/v1","state":"recorded",
         "analysis_state":"not_started","event_id":p["event_id"],
         "project_id":p["project_id"],"qdp_run_id":p["qdp_run_id"],
         "video_id":p["video_id"],"run_id":str(uuid.uuid4()),
         "job_id":71,"payload_sha256":digest,"replayed":False}
    out.update(patch)
    return out


class GroupABridgeContracts(unittest.TestCase):
    def test_translated_envelope_contains_source_outbox_sha(self):
        p=pointer()
        event,digest=ga_envelope(p,"c"*64)
        self.assertEqual(event["schema_version"],GA_ENVELOPE)
        self.assertEqual(event["pointer"]["event_payload_sha256"],"c"*64)
        self.assertEqual(event["pointer"]["schema_version"],GA_QDP_PEER)
        self.assertEqual(digest,sha256(event))
        self.assertNotIn("media_path",str(event))
        self.assertEqual(event["pointer"]["input_sha256"],p.input_sha256)

    def test_real_ga_recorded_receipt_maps_to_committed_qdp_ack(self):
        p=pointer()
        class GA:
            def receive(self,event,digest):
                self.event=event
                self.digest=digest
                return receipt(event,digest)
        ga=GA()
        result=GroupAConsumerAdapter(ga).receive(p,"d"*64)
        self.assertEqual(result["state"],"committed")
        self.assertEqual(result["consumer"],"group_a")
        self.assertEqual(result["payload_sha256"],"d"*64)
        self.assertEqual(result["event_id"],p.event_id)
        self.assertTrue(result["receipt_key"].startswith("ga:"))
        self.assertEqual(ga.digest,sha256(ga.event))

    def test_tampered_or_queued_ga_receipts_are_never_acknowledged(self):
        p=pointer()
        valid_event,digest=ga_envelope(p,"d"*64)
        for patch in (
            {"state":"queued"},{"state":"committed"},
            {"analysis_state":"completed"},
            {"payload_sha256":"0"*64},
            {"project_id":"f"*32},{"qdp_run_id":str(uuid.uuid4())},
            {"video_id":22},{"event_id":str(uuid.uuid4())},
            {"job_id":0},{"job_id":None},{"run_id":"not-a-uuid"},
            {"schema_version":"group-a-receipt/v2"}
        ):
            with self.subTest(patch=patch),self.assertRaises(QDPError):
                verify_ga_receipt(receipt(valid_event,digest,**patch),p,digest)

    def test_b_c_pointers_cannot_route_to_a(self):
        p=pointer()
        from dataclasses import replace
        for g in ("B","C"):
            with self.subTest(group=g),self.assertRaises(QDPError):
                ga_envelope(replace(p,analysis_group=g),"a"*64)

    def test_source_digest_missing_rejected(self):
        with self.assertRaises(QDPError):
            ga_envelope(pointer(),"not-a-hash")

    def test_ga_receiver_not_installed_fails_closed(self):
        with self.assertRaises(QDPError) as ctx:
            GroupAConsumerAdapter(None)
        self.assertEqual(ctx.exception.status,503)
        with self.assertRaises(QDPError):
            from_installed_group_a(lambda:object(),media_roots=["/tmp"],enabled=False)

    def test_ga_receiver_exception_is_not_ack(self):
        p=pointer()
        class CrashedGA:
            def receive(self,*_):
                raise RuntimeError("did not commit")
        with self.assertRaises(RuntimeError):
            GroupAConsumerAdapter(CrashedGA()).receive(p,"d"*64)

    def test_ga_reader_incompatible_pointer_rejected_before_db(self):
        class DB:
            def execute(self,*_):
                raise AssertionError("Invalid pointer must not access database")
        with self.assertRaises(QDPError):
            QDPGroupAReader(DB()).read_preparation({"schema_version":"qdp-handoff/v1"})

if __name__=="__main__":
    unittest.main()
