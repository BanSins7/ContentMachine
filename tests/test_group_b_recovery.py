"""GB-MOD-27 pure concurrency/cost and failure-policy security tests."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4
import unittest

from intelligence.group_b.monitoring import (
    ThroughputPolicy,MonitoringDenied,ExecutionMetric,
    FailureClass,admit_resource_request,classify_failure,plan_queue_batch,
)

P="a"*32
R=str(uuid4())
J=str(uuid4())
SHA="b"*64


class TestB27Monitoring(unittest.TestCase):
    def test_no_paid_worker_even_if_caller_requests_concurrency(self):
        policy=ThroughputPolicy()
        self.assertEqual(policy.max_running_paid,0)
        self.assertTrue(admit_resource_request(policy,kind="local",
            prompt_tokens=0,completion_tokens=0,images=0,context_bytes=900))
        with self.assertRaises(MonitoringDenied):
            ThroughputPolicy(max_running_paid=1)
        with self.assertRaises(MonitoringDenied):
            admit_resource_request(policy,kind="paid",
                prompt_tokens=20,completion_tokens=10,images=1,context_bytes=100)

    def test_input_limits_strict_no_bool_or_negative_or_image_forgery(self):
        p=ThroughputPolicy(max_prompt_tokens=1000,max_completion_tokens=500,
                           max_images=5,max_context_bytes=100000)
        for extras in ({"prompt_tokens":1001},{"completion_tokens":501},
                       {"images":6},{"context_bytes":100001},
                       {"images":True},{"prompt_tokens":None},
                       {"context_bytes":-1}):
            params=dict(prompt_tokens=100,completion_tokens=100,
                        images=3,context_bytes=2000)
            params.update(extras)
            with self.subTest(params=params),self.assertRaises(MonitoringDenied):
                admit_resource_request(p,kind="local",**params)
        with self.assertRaises(MonitoringDenied):
            ThroughputPolicy(max_batch_jobs=1001)

    def test_rate_limited_before_submission_can_backoff_but_never_charge(self):
        decision=classify_failure(status_code=429,request_submitted=False)
        self.assertEqual(decision.category,FailureClass.RATE_LIMIT)
        self.assertTrue(decision.retryable)
        self.assertFalse(decision.requires_receipt_reconciliation)
        self.assertEqual(decision.code,"provider_rate_limited")

    def test_post_submission_429_timeout_and_5xx_are_uncertain_not_replayed(self):
        for status in (429,408,500,503):
            d=classify_failure(status_code=status,request_submitted=True)
            self.assertEqual(d.category,FailureClass.UNKNOWN_CHARGE)
            self.assertFalse(d.retryable)
            self.assertTrue(d.requires_receipt_reconciliation)
            self.assertNotIn(str(status),d.code)

    def test_even_confirmed_post_submission_429_is_not_replayed(self):
        d=classify_failure(status_code=429,request_submitted=True,
                           charge_verified=True)
        self.assertFalse(d.retryable)
        self.assertFalse(d.requires_receipt_reconciliation)
        self.assertEqual(d.code,"provider_attempt_consumed")

    def test_no_raw_provider_failure_is_exposed(self):
        for status,code in ((401,None),(500,None),(None,"response_not_json"),
                            (None,"source_revoked"),(None,"lease_expired")):
            d=classify_failure(status_code=status,provider_code=code)
            self.assertNotIn("password",d.code)
            self.assertFalse(d.requires_receipt_reconciliation)
        with self.assertRaises(MonitoringDenied):
            classify_failure(provider_code="Authorization:Bearer SUPERSECRET")
        with self.assertRaises(MonitoringDenied):
            classify_failure(status_code=True)

    def test_metric_generations_are_pinned_and_no_fake_usage_cost(self):
        event=ExecutionMetric(
            P,R,J,"B5","local",1,"complete",120,"c"*64,
        )
        self.assertTrue(event.validate())
        self.assertEqual(event.trace_sha256,event.trace_sha256)
        self.assertNotEqual(event.trace_sha256,
                            replace(event,lease_generation=2).trace_sha256)
        self.assertFalse(replace(event,charged_usd=Decimal("NaN")).validate())
        self.assertFalse(replace(event,queue_kind="paid",
                                 charged_usd=Decimal("0.1")).validate())
        self.assertFalse(replace(event,outcome="lease_expired").validate())
        self.assertFalse(replace(event,duration_ms=-1).validate())
        self.assertFalse(replace(event,prompt_tokens=True).validate())

    def test_unknown_duration_crash_has_no_fake_elapsed(self):
        event=ExecutionMetric(P,R,J,"B5","local",1,"lease_expired",None,
                              None,"lease_exhausted")
        self.assertTrue(event.validate())
        self.assertIsNone(event.duration_ms)
        self.assertFalse(replace(event,duration_ms=60000).validate())

    def test_synthetic_thousand_job_batch_creates_cpu_waves_never_paid_replay(self):
        p=ThroughputPolicy(max_batch_jobs=1000,max_running_local=4)
        rows=[(str(uuid4()),"paid" if i%9==0 else "local")
              for i in range(1000)]
        result=plan_queue_batch(p,rows)
        self.assertEqual(result.total,1000)
        self.assertEqual(result.max_concurrency,4)
        self.assertEqual(len(result.paid_deferred),112)
        self.assertEqual(sum(map(len,result.local_waves)),888)
        self.assertEqual(len(result.local_waves),222)
        with self.assertRaises(MonitoringDenied):
            plan_queue_batch(p,rows+[(str(uuid4()),"local")])
        with self.assertRaises(MonitoringDenied):
            plan_queue_batch(p,rows[:-1]+[rows[0]])


if __name__=="__main__":
    unittest.main()
