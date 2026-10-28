"""GA-MOD-23 conditional REAL PostgreSQL 17 integration contract tests.

This suite NEVER imports an unpublished Group B, QDP, SRE or Analytics branch.
It exercises GA04/QDP pinned inbox -> GA18 A003 -> GA19 immutable result ->
GA20 currentness -> fake exact B consumer ACK on a disposable PG17 DB.

Install peer modules together on a throwaway integration checkout and execute
the independent peer tests before declaring true cross-module E2E PASS.
The fake adapter here is *not* evidence that real Group B is integrated.

Only run with an explicitly DISPOSABLE database and
CM_GA23_DISPOSABLE_ACK=yes. No external paid service or real user data.
"""
from __future__ import annotations

import os
import unittest

from intelligence.group_a.contracts_v1 import canonical_sha256,group_a_to_b_v1
from intelligence.group_a.delivery import (
    claim_outbox, acknowledge_delivery, fail_delivery,
)
from intelligence.group_a.invalidation import invalidate_run,read_current_result
from intelligence.group_a.cost_resources import RuntimeDenied

DB_ENABLED=bool(os.getenv("CM_TEST_PG_DSN")) and (
    os.getenv("CM_GA23_DISPOSABLE_ACK")=="yes"
)


def trust_fixture(_):
    """INSECURE fake for isolated tests; NEVER use as a production verifier."""
    return True


class FakePinnedBConsumer:
    """Compatibility-shaped exact result reader; NOT a real B classifier."""
    def consume(self, db_result, *, result_id, result_sha256, current_scope):
        if type(result_id) is not int or result_id<=0:
            raise ValueError("invalid_ga_result_pin")
        if not current_scope:
            raise ValueError("qdp_rights_missing")
        result=group_a_to_b_v1(db_result)
        if canonical_sha256(result)!=result_sha256:
            raise ValueError("a_result_sha_changed")
        if result["analysis_state"]!="completed":
            raise ValueError("a_partial_is_not_B_ready")
        if not all(s["status"]=="complete" for s in result["streams"].values()):
            raise ValueError("a_incomplete_stream_not_B_ready")
        if "cta_start_sec" in result or "cta_slide_index" in result:
            raise ValueError("B7_CTA_is_B_owned")
        return {
            "ga_result_id":result_id,
            "result_sha256":result_sha256,
            # A fake deterministic receipt is not a real B commit signature.
            "consumer_receipt_sha256":canonical_sha256({
                "schema_version":"ga23-fake-b-consumer-receipt/v1",
                "ga_result_id":result_id,"result_sha256":result_sha256,
            }),
        }


class GA23PurePeerProtocol(unittest.TestCase):
    def test_exact_v1_refuses_unpinned_and_unreviewed_delivery(self):
        peer=FakePinnedBConsumer()
        with self.assertRaisesRegex(ValueError,"invalid_ga_result_pin"):
            peer.consume({},result_id=0,result_sha256="a"*64,
                         current_scope=True)
        with self.assertRaisesRegex(ValueError,"qdp_rights_missing"):
            peer.consume({},result_id=1,result_sha256="a"*64,
                         current_scope=False)


@unittest.skipUnless(DB_ENABLED,
    "PG17 integration requires disposable CM_TEST_PG_DSN + CM_GA23_DISPOSABLE_ACK=yes")
class GA23PG17QDPToB(unittest.TestCase):
    def setUp(self):
        # Creates original GA04 run, real GA18 immutable A003 (including
        # source hashes), using the existing conditional GA19 DB fixture.
        from test_group_a_b_handoff import GA19PG17
        self.peer=GA19PG17
        GA19PG17.setUp(self)
        self.run_id=self.r["ga_run_id"]
        self.consumer=FakePinnedBConsumer()

    def prepared(self):
        return self.peer.prepared(self)

    def publish(self,prepared):
        return self.peer.publish(self,prepared)

    def test_durable_qdp_GA04_A003_GA19_A_to_B_exact_delivery(self):
        prepared=self.prepared()
        pin=self.publish(prepared)
        self.assertEqual(pin["state"],"completed")
        self.assertEqual(pin["delivery_state"],"pending_outbox")

        # Check the real immutable DB result and same QDP run that entered GA04.
        original=self.conn.execute(
            """SELECT g.id,g.run_id::text,g.result_sha256,g.snapshot,
                      r.qdp_run_id::text,r.manifest_sha256,r.source_policy_version_id
               FROM ga_results g JOIN ga_runs r ON r.id=g.run_id
               WHERE g.id=%s""",(pin["ga_result_id"],)
        ).fetchone()
        self.assertIsNotNone(original)
        self.assertEqual(original[1],self.run_id)
        self.assertEqual(original[2],pin["result_sha256"])
        self.assertEqual(original[4],self.manifest["qdp_run_id"])
        self.assertEqual(original[5],self.manifest["manifest_sha256"])
        self.assertEqual(original[6],self.manifest["source_policy_version_id"])

        current=read_current_result(
            self.conn,run_id=self.run_id,result_id=pin["ga_result_id"],
            expected_sha256=pin["result_sha256"],
            verify_current=trust_fixture,verify_result=trust_fixture,
        )
        self.assertEqual(current["snapshot"],original[3])
        claimed=claim_outbox(
            self.conn,worker_id="ga23-disposable-peer",
            verify_current=trust_fixture,
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.ga_result_id,pin["ga_result_id"])
        receipt=self.consumer.consume(
            current["snapshot"],result_id=claimed.ga_result_id,
            result_sha256=claimed.result_sha256,current_scope=True,
        )
        self.assertEqual(acknowledge_delivery(
            self.conn,claimed,receipt,verify_current=trust_fixture,
            verify_receipt=trust_fixture,
        ),"delivered")
        rows=self.conn.execute(
            """SELECT o.state,ack.result_id,ack.result_sha256,
                      ack.consumer_receipt_sha256
               FROM ga_outbox o
               JOIN ga_outbox_ack_receipts ack ON ack.outbox_id=o.id
               WHERE o.id=%s""",(claimed.outbox_id,)
        ).fetchone()
        self.assertEqual(rows,(
            "delivered",pin["ga_result_id"],pin["result_sha256"],
            receipt["consumer_receipt_sha256"],
        ))
        # Original A003 must NOT have been backfilled with B7 CTA inference.
        self.assertIsNone(self.conn.execute(
            "SELECT cta_start_sec FROM video_feature_a_analyses WHERE id=%s",
            (pin["video_a_analysis_id"],)
        ).fetchone()[0])

    def test_revocation_hides_historical_A_and_blocks_B_outbox(self):
        pin=self.publish(self.prepared())
        self.assertEqual(invalidate_run(
            self.conn,run_id=self.run_id,
            reason_code="qdp_revoked",verify_authority=trust_fixture,
        ),"invalidated")
        with self.assertRaisesRegex(RuntimeDenied,"result_not_current_or_revoked"):
            read_current_result(
                self.conn,run_id=self.run_id,result_id=pin["ga_result_id"],
                expected_sha256=pin["result_sha256"],
                verify_current=trust_fixture,verify_result=trust_fixture,
            )
        self.assertIsNone(claim_outbox(
            self.conn,worker_id="ga23-no-revoked-delivery",
            verify_current=trust_fixture,
        ))
        self.assertEqual(self.conn.execute(
            "SELECT result_sha256 FROM ga_results WHERE id=%s",
            (pin["ga_result_id"],)
        ).fetchone()[0],pin["result_sha256"])

    def test_idempotency_same_qdp_run_and_exact_result_digest(self):
        first=self.publish(self.prepared())
        second=self.publish(self.prepared())
        self.assertEqual(first,second)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM ga_results WHERE run_id=%s::uuid",
            (self.run_id,)
        ).fetchone()[0],1)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM ga_outbox WHERE run_id=%s::uuid",
            (self.run_id,)
        ).fetchone()[0],1)
        wrong="0"*64 if first["result_sha256"]!="0"*64 else "f"*64
        with self.assertRaisesRegex(RuntimeDenied,"result_not_current_or_revoked"):
            read_current_result(
                self.conn,run_id=self.run_id,
                result_id=first["ga_result_id"],expected_sha256=wrong,
                verify_current=trust_fixture,verify_result=trust_fixture,
            )


if __name__=="__main__":
    unittest.main()
