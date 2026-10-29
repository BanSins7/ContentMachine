"""GA-MOD-24 observed QDP FIX-15 revocation wire and exact merge gates.

No QDP/Group B imports, no external DB, media, paid API or network. These
tests document the peer compatibility; they do not certify a live peer.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4
import unittest

from intelligence.group_a.contracts_v1 import canonical_sha256
from intelligence.group_a.qdp_revocation import (
    PostgresQDPRevocationReceiver,QDPRevocationDenied,_pointer,_notice,
)
from intelligence.group_a.integration_readiness import (
    inspect_merged_schema,require_exact_peer_wires,WIRE_VERSIONS,
    REQUIRED_EXTERNAL_GATES,
)

PROJECT="a"*32
INPUT="b"*64
PROVENANCE="c"*64


def fake_qdp_revocation():
    pointer=SimpleNamespace(
        schema_version="qdp-handoff/v1",
        event_id=str(uuid4()),project_id=PROJECT,
        qdp_run_id=str(uuid4()),video_id=191,
        analysis_group="A",input_sha256=INPUT,
    )
    notice={
        "schema_version":"qdp-event/v1",
        "event_type":"preparation_invalidated",
        "project_id":PROJECT,"run_id":pointer.qdp_run_id,
        "video_id":pointer.video_id,
        "analysis_group":"A",
        "input_sha256":INPUT,
        "provenance_sha256":PROVENANCE,
        "invalidation_generation":1,
        "origin":"human_review",
    }
    return pointer,notice,canonical_sha256(notice)


class ForbiddenDB:
    def __call__(self):
        raise AssertionError("untrusted QDP notice must be rejected before DB")


class StubConn:
    def __init__(self,relations):
        self.relations=relations
    def execute(self,query,args):
        name=args[0].removeprefix("public.")
        return SimpleNamespace(fetchone=lambda:((name if name in self.relations else None),))


class GA24RevocationContract(unittest.TestCase):
    def test_canonical_QDP_pointer_and_SHA_without_peer_import(self):
        pointer,notice,sha=fake_qdp_revocation()
        p=_pointer(pointer)
        self.assertEqual(p["event_id"],pointer.event_id)
        self.assertEqual(_notice(notice,p,sha),notice)
        self.assertIsInstance(PostgresQDPRevocationReceiver(ForbiddenDB()),PostgresQDPRevocationReceiver)

    def test_forged_message_or_SHA_rejected_before_database_write(self):
        pointer,notice,sha=fake_qdp_revocation()
        receiver=PostgresQDPRevocationReceiver(ForbiddenDB())
        with self.assertRaisesRegex(QDPRevocationDenied,"revocation_payload_hash_mismatch"):
            receiver.revoke(pointer,"d"*64,notice)
        fake=dict(notice,analysis_group="B")
        with self.assertRaisesRegex(QDPRevocationDenied,"revocation_notice_identity_mismatch"):
            receiver.revoke(pointer,canonical_sha256(fake),fake)
        fake=dict(notice,invalidation_generation=0)
        with self.assertRaises(QDPRevocationDenied):
            receiver.revoke(pointer,canonical_sha256(fake),fake)
        with self.assertRaises(QDPRevocationDenied):
            receiver.revoke(pointer,sha,dict(notice,project_id="f"*32))

    def test_wrong_pointer_group_version_and_noncanonical_uuid_denied(self):
        pointer,notice,sha=fake_qdp_revocation()
        with self.assertRaises(QDPRevocationDenied):
            _pointer(SimpleNamespace(**{**vars(pointer),"analysis_group":"C"}))
        with self.assertRaises(QDPRevocationDenied):
            _pointer(SimpleNamespace(**{**vars(pointer),"schema_version":"qdp-handoff/v2"}))
        with self.assertRaises(QDPRevocationDenied):
            _pointer(SimpleNamespace(**{**vars(pointer),"event_id":"not-uuid"}))

    def test_schema_probe_never_claims_live_dependency(self):
        all_objects={
            t for required in __import__(
                "intelligence.group_a.integration_readiness",fromlist=["SCHEMAS"]
            ).SCHEMAS.values() for t in required
        }
        report=inspect_merged_schema(StubConn(all_objects))
        self.assertFalse(report["release_ready"])
        self.assertEqual(report["overall"],"unverified")
        self.assertTrue(all(x["status"]=="schema_present_not_authenticated"
                            for x in report["schema"].values()))
        self.assertTrue(all(x["authenticated_live"] is False
                            for x in report["schema"].values()))
        self.assertIn("group_b_true_durable_consumer_receipt",
                      REQUIRED_EXTERNAL_GATES)
        absent=inspect_merged_schema(StubConn([]))
        self.assertEqual(absent["schema"]["qdp"]["status"],"schema_missing")

    def test_version_gate_no_downgrade(self):
        self.assertEqual(require_exact_peer_wires(WIRE_VERSIONS),())
        x=dict(WIRE_VERSIONS,qdp_to_a="qdp-handoff/v9")
        self.assertIn("qdp_to_a_version_mismatch",require_exact_peer_wires(x))
        x=dict(WIRE_VERSIONS)
        del x["b_contract"]
        self.assertIn("b_contract_unavailable",require_exact_peer_wires(x))


if __name__=="__main__":
    unittest.main()
