"""GA20 outbox exact-receipt + idempotent redelivery PG17 regressions."""
from __future__ import annotations

import os
import unittest

from intelligence.group_a.cost_resources import RuntimeDenied
from intelligence.group_a.delivery import (
    claim_outbox,acknowledge_delivery,fail_delivery,
    recover_outbox,deliver_once,
)
from intelligence.group_a.invalidation import invalidate_run,read_current_result

def allowed(_):
    return True

@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),
                     "Disposable PostgreSQL 17 DSN missing")
class GA20OutboxPG17(unittest.TestCase):
    def setUp(self):
        from test_group_a_b_handoff import GA19PG17
        GA19PG17.setUp(self)
        self.prepared=GA19PG17.prepared.__get__(self)
        self.publish=GA19PG17.publish.__get__(self)
        p=self.prepared()
        self.ga19=self.publish(p)
        self.conn=self.conn
        self.run=self.r["ga_run_id"]

    def claim(self,worker):
        return claim_outbox(
            self.conn,worker_id=worker,verify_current=allowed,
            lease_seconds=60
        )

    def test_ack_exact_token_only_and_terminal_receipt(self):
        c=self.claim("delivery-worker-1")
        self.assertIsNotNone(c)
        self.assertEqual(c.ga_result_id,self.ga19["ga_result_id"])
        self.assertEqual(c.result_sha256,self.ga19["result_sha256"])
        self.assertIsNone(self.claim("other-worker"))
        receipt={
            "ga_result_id":c.ga_result_id,
            "result_sha256":c.result_sha256,
            "consumer_receipt_sha256":"f"*64,
        }
        with self.assertRaisesRegex(RuntimeDenied,"consumer_receipt_untrusted"):
            acknowledge_delivery(
                self.conn,c,receipt,verify_current=allowed,
                verify_receipt=lambda _:False
            )
        self.assertEqual(
            acknowledge_delivery(
                self.conn,c,receipt,verify_current=allowed,
                verify_receipt=allowed
            ),"delivered"
        )
        with self.assertRaisesRegex(RuntimeDenied,"outbox_lease_stale_or_revoked"):
            acknowledge_delivery(
                self.conn,c,receipt,verify_current=allowed,
                verify_receipt=allowed
            )
        self.assertEqual(self.conn.execute(
            "SELECT state FROM ga_outbox WHERE id=%s",(c.outbox_id,)
        ).fetchone()[0],"delivered")
        self.assertEqual(self.conn.execute(
            "SELECT consumer_receipt_sha256 FROM ga_outbox_ack_receipts WHERE outbox_id=%s",
            (c.outbox_id,)
        ).fetchone()[0],"f"*64)

    def test_timeout_then_replay_same_result_and_key(self):
        c=self.claim("delivery-lost-reply")
        self.assertEqual(
            fail_delivery(self.conn,c,verify_current=allowed),"retry_pending"
        )
        self.assertIsNone(self.claim("too_early"))
        self.conn.execute(
            """UPDATE ga_outbox_attempts
                  SET finished_at=now()-interval '15 seconds'
                WHERE id=%s""",(c.attempt_id,)
        )
        d=self.claim("delivery-worker-2")
        self.assertIsNotNone(d)
        self.assertEqual((d.ga_result_id,d.result_sha256,d.payload_sha256),
                         (c.ga_result_id,c.result_sha256,c.payload_sha256))
        self.assertNotEqual(c.token,d.token)
        with self.assertRaisesRegex(RuntimeDenied,"outbox_lease_stale_or_revoked"):
            acknowledge_delivery(
                self.conn,c,{
                    "ga_result_id":c.ga_result_id,
                    "result_sha256":c.result_sha256,
                    "consumer_receipt_sha256":"a"*64,
                },verify_current=allowed,verify_receipt=allowed
            )

    def test_expired_lease_recovered_and_fenced(self):
        c=self.claim("dead-delivery")
        self.conn.execute(
            """UPDATE ga_outbox SET lease_until=now()-interval '1 second'
               WHERE id=%s""",(c.outbox_id,)
        )
        recover_outbox(self.conn)
        self.assertEqual(self.conn.execute(
            "SELECT state FROM ga_outbox_attempts WHERE id=%s",(c.attempt_id,)
        ).fetchone()[0],"lost")
        self.assertEqual(self.conn.execute(
            "SELECT state FROM ga_outbox WHERE id=%s",(c.outbox_id,)
        ).fetchone()[0],"failed")

    def test_revocation_makes_pending_outbox_non_dispatchable_and_history_persists(self):
        gid=self.ga19["ga_result_id"]
        snapshot=read_current_result(
            self.conn,run_id=self.run,result_id=gid,
            expected_sha256=self.ga19["result_sha256"],
            verify_current=allowed,verify_result=allowed,
        )
        self.assertEqual(snapshot["ga_result_id"],gid)
        invalidate_run(
            self.conn,run_id=self.run,
            reason_code="source_policy_revoked",verify_authority=allowed
        )
        with self.assertRaisesRegex(RuntimeDenied,"result_not_current_or_revoked"):
            read_current_result(
                self.conn,run_id=self.run,result_id=gid,
                expected_sha256=self.ga19["result_sha256"],
                verify_current=allowed,verify_result=allowed,
            )
        self.assertIsNone(self.claim("revoked-worker"))
        historical=self.conn.execute(
            "SELECT result_sha256 FROM ga_results WHERE id=%s",(gid,)
        ).fetchone()
        self.assertEqual(historical[0],self.ga19["result_sha256"])

    def test_post_send_requires_trusted_ack_and_does_not_claim_delivered(self):
        seen=[]
        def transport(payload,*,idempotency_key):
            seen.append(idempotency_key)
            return {
                "ga_result_id":payload["ga_result_id"],
                "result_sha256":payload["result_sha256"],
                "consumer_receipt_sha256":"e"*64,
            }
        with self.assertRaisesRegex(RuntimeDenied,"consumer_receipt_untrusted"):
            deliver_once(
                self.conn,worker_id="transport-worker",
                verify_current=allowed,transport=transport,
                verify_receipt=lambda _:False,
            )
        self.assertEqual(len(seen),1)
        self.assertEqual(self.conn.execute(
            "SELECT state FROM ga_outbox WHERE id=%s",
            (self.ga19["outbox_id"],)
        ).fetchone()[0],"failed")
