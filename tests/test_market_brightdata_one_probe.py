"""Offline contract tests for opt-in single-url Bright Data probe.

These must NEVER contact Bright Data or create billable provider jobs.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from intelligence.market_intelligence.contracts import MarketError
from scripts.probe_market_brightdata_one import start,poll,collect,_safe_read

URL="https://www.tiktok.com/@creator/video/1234567890123456789"
SID="s_abcd123456789"


class OneBrightDataVideo(unittest.TestCase):
    def test_no_consent_never_calls_provider_or_preflight(self):
        with tempfile.TemporaryDirectory() as temp,patch(
            "scripts.probe_market_brightdata_one.bd.trigger") as trigger,patch(
            "scripts.probe_market_brightdata_one.preflight.check") as check:
            state=Path(temp)/"bd.json"
            with self.assertRaisesRegex(ValueError,"paid_trigger_requires_explicit_consent"):
                start(platform="tiktok",url=URL,state_file=state,consent=False)
            self.assertFalse(state.exists())
            trigger.assert_not_called()
            check.assert_not_called()

    def test_flags_not_ready_prevent_paid_trigger(self):
        with tempfile.TemporaryDirectory() as temp,patch(
            "scripts.probe_market_brightdata_one.bd.readiness",
            return_value={"ready":False}),patch(
            "scripts.probe_market_brightdata_one.bd.trigger") as trigger:
            state=Path(temp)/"bd.json"
            with self.assertRaisesRegex(ValueError,"metrics_disabled_or_rights_unverified"):
                start(platform="tiktok",url=URL,state_file=state,consent=True)
            self.assertFalse(state.exists())
            trigger.assert_not_called()

    def test_dataset_not_listed_prevents_paid_trigger(self):
        with tempfile.TemporaryDirectory() as temp,patch(
            "scripts.probe_market_brightdata_one.bd.readiness",
            return_value={"ready":True}),patch(
            "scripts.probe_market_brightdata_one.preflight.check",
            return_value={"status":"authenticated","targets":{
                "tiktok":{"listed":False,"video_dataset":True}}}),patch(
            "scripts.probe_market_brightdata_one.bd.trigger") as trigger:
            state=Path(temp)/"bd.json"
            with self.assertRaisesRegex(ValueError,"video_dataset_unavailable_in_catalog"):
                start(platform="tiktok",url=URL,state_file=state,consent=True)
            self.assertFalse(state.exists())
            trigger.assert_not_called()

    def test_exactly_one_paid_trigger_claimed_on_disk_before_dispatch(self):
        with tempfile.TemporaryDirectory() as temp,patch(
            "scripts.probe_market_brightdata_one._authorize") as authorize,patch(
            "scripts.probe_market_brightdata_one.bd.trigger",return_value=SID) as trigger:
            state=Path(temp)/"bd.json"
            report=start(platform="tiktok",url=URL,state_file=state,consent=True)
            self.assertEqual(report["state"],"submitted")
            self.assertEqual(report["snapshot_id"],SID)
            self.assertEqual(trigger.call_count,1)
            self.assertEqual(_safe_read(state)["snapshot_id"],SID)
            with self.assertRaisesRegex(ValueError,"probe_state_already_exists_no_double_trigger"):
                start(platform="tiktok",url=URL,state_file=state,consent=True)
            self.assertEqual(trigger.call_count,1)
            authorize.assert_called_once()

    def test_ambiguous_provider_failure_preserves_no_retry_marker(self):
        with tempfile.TemporaryDirectory() as temp,patch(
            "scripts.probe_market_brightdata_one._authorize"),patch(
            "scripts.probe_market_brightdata_one.bd.trigger",
            side_effect=MarketError("brightdata_network","sanitized")) as trigger:
            state=Path(temp)/"bd.json"
            with self.assertRaises(MarketError):
                start(platform="tiktok",url=URL,state_file=state,consent=True)
            saved=_safe_read(state)
            self.assertEqual(saved["state"],"trigger_uncertain_no_automatic_retry")
            self.assertIsNone(saved["snapshot_id"])
            with self.assertRaisesRegex(ValueError,"probe_state_already_exists_no_double_trigger"):
                start(platform="tiktok",url=URL,state_file=state,consent=True)
            self.assertEqual(trigger.call_count,1)

    def test_poll_never_triggers_duplicate_paid_request(self):
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp)/"bd.json"
            state.write_text(json.dumps({"schema_version":"market-brightdata-single-url-test/v1",
                "platform":"tiktok","url":URL,"snapshot_id":SID,"state":"submitted"}))
            with patch("scripts.probe_market_brightdata_one.bd.progress",
                       return_value="running") as progress,patch(
                 "scripts.probe_market_brightdata_one.bd.trigger") as trigger:
                report=poll(state_file=state)
                self.assertEqual(report["state"],"running")
                progress.assert_called_once_with(SID)
                trigger.assert_not_called()

    def test_collect_numeric_only_never_raw_comments_or_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp)/"bd.json"
            result_path=Path(temp)/"metrics.json"
            state.write_text(json.dumps({"schema_version":"market-brightdata-single-url-test/v1",
                "platform":"tiktok","url":URL,"snapshot_id":SID,
                "dataset_id":"gd_lu702nij2f790tmv9h","state":"ready"}))
            payload=[{"url":URL,"play_count":1001,"digg_count":50,"comment_count":4,
                      "statistics":{"average_watch_time":8.2,"retention_curve":[90,60,20]},
                      "comments":[{"text":"secret comment","likes":5}]}]
            with patch("scripts.probe_market_brightdata_one.bd.progress",return_value="ready"),patch(
                 "scripts.probe_market_brightdata_one.bd.download",return_value=payload),patch(
                 "scripts.probe_market_brightdata_one.bd.trigger") as trigger:
                report=collect(state_file=state,metrics_file=result_path)
                self.assertEqual(report["views"],1001)
                self.assertEqual(report["numeric_fields"]["statistics.average_watch_time"],8.2)
                self.assertTrue(report["metric_coverage"]["has_retention_field"])
                self.assertIsNone(report["saves"])
                self.assertNotIn("secret comment",result_path.read_text())
                self.assertNotIn(URL,result_path.read_text())
                trigger.assert_not_called()

    def test_collect_requires_ready_without_download(self):
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp)/"bd.json"
            state.write_text(json.dumps({"schema_version":"market-brightdata-single-url-test/v1",
                "platform":"tiktok","url":URL,"snapshot_id":SID,"state":"submitted"}))
            with patch("scripts.probe_market_brightdata_one.bd.progress",return_value="running"),patch(
                 "scripts.probe_market_brightdata_one.bd.download") as download:
                with self.assertRaisesRegex(ValueError,"snapshot_not_ready_running"):
                    collect(state_file=state,metrics_file=Path(temp)/"metrics.json")
                download.assert_not_called()


if __name__=="__main__":
    unittest.main()
