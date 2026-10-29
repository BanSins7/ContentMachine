"""QDP FIX-11 bounded backoff and event type contracts (no peer calls)."""
import unittest
from intelligence.qualified_data.contracts import QDPError
from intelligence.qualified_data.worker_hardening import (
    CONSUMER,retry_delay_seconds,append_event,
)


class WorkerFencingContractTests(unittest.TestCase):
    def test_exponential_retry_is_bounded(self):
        self.assertEqual([retry_delay_seconds(i) for i in range(1,7)],
                         [30,60,120,240,480,960])
        self.assertEqual(retry_delay_seconds(1000),1800)

    def test_invalid_attempt_count_rejected(self):
        for x in (0,-1,True,1.0):
            with self.subTest(value=x),self.assertRaises(QDPError):
                retry_delay_seconds(x)

    def test_event_consumer_names_are_exact(self):
        self.assertEqual(CONSUMER,{"A":"group_a","B":"group_b","C":"group_c"})

    def test_no_invalid_or_fake_handoff_event(self):
        class DB:
            def execute(self, *args):
                raise AssertionError("Invalid event must never reach DB")
        kwargs={"project":"a"*32,"run_id":"b"*36,"video_id":1}
        with self.assertRaises(QDPError):
            append_event(DB(),**kwargs,group="X",
                         event_type="preparation_ready",inputs_hash="a"*64)
        with self.assertRaises(QDPError):
            append_event(DB(),**kwargs,group="A",
                         event_type="preparation_ready",inputs_hash="invalid")
        with self.assertRaises(QDPError):
            append_event(DB(),**kwargs,group="A",event_type="peer_job_completed")

if __name__=="__main__":
    unittest.main()
