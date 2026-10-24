"""Opt-in maintenance cadence regression. Never sleeps or touches PostgreSQL."""
from __future__ import annotations
import unittest
from unittest.mock import patch

from intelligence.analytics.worker import main

class AnalyticsWorkerWatchTests(unittest.TestCase):
    def test_watch_purges_once_and_runs_bounded_jobs_with_explicit_opt_in(self):
        with patch("intelligence.analytics.worker.purge_unreadable",
                   return_value={"deleted_unified_snapshots":2,"deleted_revoked_jobs":1}) as purge,\
             patch("intelligence.analytics.worker.process_one",return_value=None) as run,\
             patch("intelligence.analytics.worker.time.sleep",
                   side_effect=KeyboardInterrupt) as sleep:
            self.assertEqual(main(["--watch","--interval-minutes","60"]),0)
            purge.assert_called_once()
            run.assert_called_once()
            sleep.assert_called_once_with(3600)

    def test_one_off_purge_does_not_spawn_watch(self):
        with patch("intelligence.analytics.worker.purge_unreadable",
                   return_value={"deleted_unified_snapshots":0,"deleted_revoked_jobs":0}) as purge,\
             patch("intelligence.analytics.worker.time.sleep") as sleep:
            self.assertEqual(main(["--purge"]),0)
            purge.assert_called_once()
            sleep.assert_not_called()

if __name__=="__main__":
    unittest.main()
