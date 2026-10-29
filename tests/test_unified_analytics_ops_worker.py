"""UA-MOD-28 worker telemetry opt-in behavior under synthetic leases."""
from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics import worker as w
from intelligence.unified_analytics.ops_observability import SafeEventCounters
from test_unified_analytics_worker import FakeDB, lease, limits


class FailingQueue:
    def __init__(self,db):self.db=db
    def fail(self,*a,**kw):return "retry"
    def recover_expired(self,*a,**kw):
        return (("redacted-job-1","retry"),("redacted-job-2","dead"))


class DeadQueue(FailingQueue):
    def fail(self,*a,**kw):return "dead"


class ObservedWorkerTests(unittest.TestCase):
    def setUp(self):
        self.db=FakeDB()
        self.events=SafeEventCounters()
        self.worker=w.QualificationWorker(
            lambda:self.db,limits(),telemetry=self.events,
        )

    def test_no_event_on_constructor(self):
        self.assertEqual(sum(self.events.snapshot()["events"].values()),0)

    def test_success_emits_only_one_safe_success_category(self):
        with patch.object(self.worker,"_atomic_snapshot_and_ack",
                          return_value=SimpleNamespace(snapshot_id=17)):
            result=self.worker.process(lease())
        self.assertEqual(result.state,"succeeded")
        self.assertEqual(self.events.snapshot()["events"]["queue_succeeded"],1)
        self.assertEqual(sum(self.events.snapshot()["events"].values()),1)

    def test_join_unavailable_maps_only_fixed_code(self):
        with patch.object(self.worker,"_atomic_snapshot_and_ack",
                          side_effect=AnalyticsBoundaryError(
                              "worker_candidate_discovery_unavailable",
                              "secret peer payload content"
                          )),patch.object(w,"QualificationJobQueue",FailingQueue):
            got=self.worker.process(lease())
        self.assertEqual(got.state,"retry")
        self.assertEqual(self.events.snapshot()["events"]["join_failed"],1)
        self.assertEqual(self.events.snapshot()["events"]["queue_retry"],1)
        self.assertNotIn("secret",str(self.events.snapshot()))

    def test_storage_failure_maps_retry_not_dsn(self):
        with patch.object(self.worker,"_atomic_snapshot_and_ack",
                          side_effect=AnalyticsBoundaryError(
                              "worker_storage_unavailable","postgres://pass@internal"
                          )),patch.object(w,"QualificationJobQueue",DeadQueue):
            got=self.worker.process(lease())
        self.assertEqual(got.state,"dead")
        self.assertEqual(self.events.snapshot()["events"]["pg_retry"],1)
        self.assertEqual(self.events.snapshot()["events"]["queue_dead"],1)
        self.assertNotIn("postgres://",str(self.events.snapshot()))

    def test_lease_lost_only_records_fixed_category(self):
        with patch.object(self.worker,"_atomic_snapshot_and_ack",
                          side_effect=AnalyticsBoundaryError(
                              "worker_lease_lost","token:secret"
                          )):
            got=self.worker.process(lease())
        self.assertEqual(got.state,"lost")
        self.assertEqual(self.events.snapshot()["events"]["queue_lease_lost"],1)

    def test_manual_recovery_records_bounded_safe_counts(self):
        with patch.object(w,"QualificationJobQueue",FailingQueue):
            result=self.worker.recover()
        self.assertEqual(len(result),2)
        self.assertEqual(
            self.events.snapshot()["events"]["queue_lease_recovered"],2
        )
        self.assertNotIn("redacted-job",str(self.events.snapshot()))


if __name__=="__main__":
    unittest.main()
