"""GB-MOD-28 provider/paid transport negative proof, no network secrets."""
from __future__ import annotations

from unittest.mock import Mock
import unittest

from intelligence.group_b.errors import ExecutionNotApproved
from intelligence.group_b.providers.openrouter import (
    OpenRouterTransport, CHAT_ENDPOINT,
)
from intelligence.group_b.worker import PaidWorker
from intelligence.group_b.monitoring import classify_failure,FailureClass


class TestB28PaidProviderSafety(unittest.TestCase):
    def test_post_unconditionally_denied_even_with_forged_paid_headers(self):
        opener=Mock(side_effect=AssertionError("must not open network"))
        transport=OpenRouterTransport(opener=opener)
        with self.assertRaises(ExecutionNotApproved):
            transport._send("POST",CHAT_ENDPOINT,{
                "Authorization":"Bearer FAKE_SECRET_NEVER_LOG",
                "Content-Type":"application/json",
            },{"model":"test/paid","messages":[]},30,1024)
        opener.assert_not_called()

    def test_ledger_reservation_does_not_activate_paid_worker(self):
        with self.assertRaises(ExecutionNotApproved):
            PaidWorker().run_once(
                project_id="0"*32,
                approval={"status":"approved","authorized_usd":"10"},
                request_key="forged-replay-key",
            )

    def test_charge_uncertain_after_transmitted_429_and_timeout(self):
        for status in (408,429,500,502,503,504):
            decision=classify_failure(
                status_code=status,request_submitted=True,
                charge_verified=False,
            )
            self.assertIs(decision.category,FailureClass.UNKNOWN_CHARGE)
            self.assertFalse(decision.retryable)
            self.assertTrue(decision.requires_receipt_reconciliation)
            self.assertNotIn("Bearer",decision.code)


if __name__=="__main__":
    unittest.main()
