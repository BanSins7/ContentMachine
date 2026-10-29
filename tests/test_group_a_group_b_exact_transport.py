"""GA24 fake exact B binding proves no false ACK or duplicate binding.

All B method callbacks here are mocks and never certify B implementation.
"""
from __future__ import annotations

from uuid import uuid4
import unittest

from intelligence.group_a.group_b_exact_transport import (
    GroupBExactBindTransport,GroupBBindDenied
)
from intelligence.group_a.contracts_v1 import canonical_sha256

RUN=str(uuid4())
B_RUN=str(uuid4())
QDP_RUN=str(uuid4())


def video_event():
    return {
        "schema_version":"group_a.result_ready/v1",
        "ga_run_id":RUN,"ga_result_id":41,"result_sha256":"a"*64,
        "project_id":"b"*32,"qdp_run_id":QDP_RUN,"video_id":102,
        "source_id":"unit-source","source_policy_version_id":7,
        "manifest_sha256":"c"*64,"content_kind":"video",
        "video_a_analysis_id":99,"carousel_a_analysis_id":None,
    }


def key(event):
    return f'{event["ga_run_id"]}:{event["ga_result_id"]}:{event["result_sha256"]}'


class GA24FakeBTransport(unittest.TestCase):
    def fake(self):
        event=video_event()
        existing={}
        binds=[]
        def bind(**kwargs):
            binds.append(kwargs)
            existing["pin"]={
                "b_run_id":B_RUN,"project_id":event["project_id"],
                "qdp_run_id":event["qdp_run_id"],"video_id":event["video_id"],
                "source_id":event["source_id"],
                "result_sha256":event["result_sha256"],
                "manifest_sha256":event["manifest_sha256"],
                "source_policy_version_id":event["source_policy_version_id"],
                "video_a_analysis_id":event["video_a_analysis_id"],
                "carousel_a_analysis_id":event["carousel_a_analysis_id"],
                "pinned":True,
            }
        transport=GroupBExactBindTransport(
            select_b_run=lambda _: B_RUN,
            bind_exact_group_a=bind,
            read_b_committed_pin=lambda _:existing.get("pin"),
            verify_pin_authenticity=lambda _:True,
        )
        return transport,event,existing,binds

    def test_commit_then_replay_uses_same_B_pin_and_exact_receipt(self):
        transport,event,existing,binds=self.fake()
        receipt=transport(event,idempotency_key=key(event))
        self.assertEqual(receipt["ga_result_id"],event["ga_result_id"])
        self.assertEqual(receipt["result_sha256"],event["result_sha256"])
        self.assertEqual(len(receipt["consumer_receipt_sha256"]),64)
        self.assertEqual(len(binds),1)
        self.assertEqual(binds[0],{
            "b_run_id":B_RUN,"ga_result_id":41,
            "expected_sha256":"a"*64,
        })
        self.assertEqual(transport(event,idempotency_key=key(event)),receipt)
        self.assertEqual(len(binds),1)

    def test_rejects_wrong_nonce_partial_media_and_source_rebinding(self):
        transport,event,existing,binds=self.fake()
        with self.assertRaisesRegex(GroupBBindDenied,"idempotency_key_mismatch"):
            transport(event,idempotency_key="invented")
        with self.assertRaisesRegex(GroupBBindDenied,"b_content_kind_not_supported"):
            alternate=dict(event,content_kind="image_post")
            transport(alternate,idempotency_key=key(alternate))
        with self.assertRaisesRegex(GroupBBindDenied,"invalid_native_a_analysis_pin"):
            alternate=dict(event,carousel_a_analysis_id=15)
            transport(alternate,idempotency_key=key(alternate))
        self.assertEqual(binds,[])

    def test_untrusted_external_B_proof_blocks_ack_even_if_B_wrote(self):
        transport,event,existing,binds=self.fake()
        transport.verify_pin_authenticity=lambda _:False
        with self.assertRaisesRegex(GroupBBindDenied,"b_commit_not_authenticated"):
            transport(event,idempotency_key=key(event))
        self.assertEqual(len(binds),1)
        with self.assertRaisesRegex(GroupBBindDenied,"b_commit_not_authenticated"):
            transport(event,idempotency_key=key(event))
        self.assertEqual(len(binds),1)

    def test_B_scope_or_hash_mismatch_never_counts_as_ack(self):
        transport,event,existing,binds=self.fake()
        def bind_wrong(**kw):
            binds.append(kw)
            existing["pin"]={
                "b_run_id":B_RUN,"project_id":"f"*32,
                "qdp_run_id":QDP_RUN,"video_id":102,
                "source_id":"unit-source","result_sha256":"a"*64,
                "manifest_sha256":"c"*64,
                "source_policy_version_id":7,
                "video_a_analysis_id":99,
                "carousel_a_analysis_id":None,"pinned":True,
            }
        transport.bind_exact_group_a=bind_wrong
        with self.assertRaisesRegex(GroupBBindDenied,"b_committed_pin_mismatch"):
            transport(event,idempotency_key=key(event))


if __name__=="__main__":
    unittest.main()
