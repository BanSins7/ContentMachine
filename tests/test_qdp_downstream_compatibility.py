"""FIX-14 strictly independent Group B, C and UAE compatibility/ACK."""
import unittest
import uuid
from intelligence.qualified_data.contracts import QDPError
from intelligence.qualified_data.integrations.contracts import (
    QDPHandoffPointer,CapabilityState)
from intelligence.qualified_data.integrations.group_b import (
    group_b_capability,GroupBConsumerAdapter,from_installed_group_b)
from intelligence.qualified_data.integrations.group_c import (
    translate_c_receipt,GroupCConsumerAdapter,from_installed_group_c)
from intelligence.qualified_data.integrations.unified_analytics import (
    uae_capability,UAECompletedResults)


def ptr(group="C"):
    return QDPHandoffPointer(
        event_id=str(uuid.uuid4()),project_id="a"*32,
        qdp_run_id=str(uuid.uuid4()),video_id=42,analysis_group=group,
        input_sha256="b"*64)


class DownstreamBoundaryTests(unittest.TestCase):
    def test_group_b_old_pinned_contract_rejected(self):
        state=group_b_capability(
            peer_event_contract="qdp-handoff/v1(pointer-as-event)",
            peer_preparation_contract="qdp-preparation/v1")
        self.assertEqual(state.state,CapabilityState.INCOMPATIBLE)
        recv=GroupBConsumerAdapter(None,
            peer_event_contract="qdp-handoff/v1(pointer-as-event)",
            peer_preparation_contract="qdp-preparation/v1")
        with self.assertRaises(QDPError) as ctx:
            recv.receive(ptr("B"),"c"*64)
        self.assertEqual(ctx.exception.code,"group_b_incompatible")

    def test_b_missing_peer_never_acks(self):
        self.assertEqual(group_b_capability().state,CapabilityState.UNAVAILABLE)
        with self.assertRaises(QDPError):
            from_installed_group_b(enabled=True)

    def test_b_future_explicit_version_must_have_receiver(self):
        future=GroupBConsumerAdapter(None,peer_event_contract="qdp-event/v1",
              peer_preparation_contract="qdp-preparation/v2")
        with self.assertRaises(QDPError) as x:
            future.receive(ptr("B"),"d"*64)
        self.assertEqual(x.exception.code,"group_b_unavailable")

    def test_c_received_receipt_translates_after_commit(self):
        pointer=ptr()
        run=str(uuid.uuid4())
        value=translate_c_receipt(
            {"ack":True,"event_id":pointer.event_id,"run_id":run,
             "job_id":11,"receipt_key":run},pointer,"c"*64)
        self.assertEqual(value["state"],"committed")
        self.assertEqual(value["consumer"],"group_c")
        self.assertEqual(value["receipt_key"],"gc:"+run+":11")

    def test_c_uncommitted_or_missing_job_never_acks(self):
        p=ptr()
        r=str(uuid.uuid4())
        for changed in (
            {"ack":False},{"event_id":str(uuid.uuid4())},
            {"job_id":None},{"job_id":0},{"run_id":"not-uuid"},
            {"receipt_key":"fabricated"}
        ):
            x={"ack":True,"event_id":p.event_id,"run_id":r,"job_id":1,"receipt_key":r}
            x.update(changed)
            with self.subTest(changed=changed),self.assertRaises(QDPError):
                translate_c_receipt(x,p,"d"*64)

    def test_c_consumer_without_peer_fails_closed(self):
        with self.assertRaises(QDPError):
            from_installed_group_c(None,enabled=True)
        with self.assertRaises(QDPError):
            from_installed_group_c(lambda:None,enabled=False)

    def test_c_receiver_requires_c_pointer(self):
        class Consumer:
            def receive(self,*a):
                raise AssertionError("Wrong group never reaches peer")
        r=GroupCConsumerAdapter(Consumer(),lambda v:v,lambda:None)
        with self.assertRaises(QDPError):
            r.receive(ptr("B"),"d"*64)

    def test_uae_missing_completed_outputs_not_qualified(self):
        self.assertEqual(uae_capability().state,CapabilityState.UNAVAILABLE)
        x=uae_capability(observed_reader_schema="uae-qdp-reader/v1",
                        observed_qdp_wire="qdp-handoff/v1")
        self.assertEqual(x.state,CapabilityState.UNAVAILABLE)
        self.assertEqual(x.reason_code,"completed_a_b_c_evidence_not_installed")
        # UI/transport flags cannot manufacture A/B/C completed evidence.
        spoof=uae_capability(observed_reader_schema="uae-qdp-reader/v1",
                        observed_qdp_wire="qdp-handoff/v1",
                        verified_complete_group_sources=True)
        self.assertEqual(spoof.state,CapabilityState.UNAVAILABLE)
        self.assertEqual(uae_capability(observed_reader_schema="wrong",
                         observed_qdp_wire="qdp-handoff/v1").state,
                         CapabilityState.INCOMPATIBLE)

    def test_uae_never_upgrades_preparations_to_completed_results(self):
        with self.assertRaises(QDPError):
            UAECompletedResults().read_completed(None,"a"*32,str(uuid.uuid4()),1)
        class Fake:
            def read_completed(self,*args):
                return {"qualified":True,"A":{"state":"ready"},
                        "B":{"state":"completed","verified":True},
                        "C":{"state":"completed","verified":True}}
        with self.assertRaises(QDPError):
            UAECompletedResults(Fake()).read_completed(None,"a"*32,str(uuid.uuid4()),1)

if __name__=="__main__":
    unittest.main()
