"""Zero-provider-charge regression for recovery of ambiguous Bright Data triggers."""
from __future__ import annotations

import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.probe_market_brightdata_one import _safe_read
from scripts.recover_market_brightdata_snapshots import (
    list_candidates,verify_and_attach,diagnose_history
)

URL="https://www.tiktok.com/@luke.swan89/video/7701927285129055520"
DATASET="gd_lu702nij2f790tmv9h"
SID="s_example123456789"
NOW=datetime.now(timezone.utc)
FIRST=(NOW-timedelta(minutes=12)).isoformat()
SECOND=(NOW-timedelta(minutes=8)).isoformat()
CREATED=(NOW-timedelta(minutes=7)).isoformat()


def _write(folder,name,started):
    state=Path(folder)/name
    state.write_text(json.dumps({
        "schema_version":"market-brightdata-single-url-test/v1",
        "platform":"tiktok","dataset_id":DATASET,
        "started_at":started,"url":URL,
        "state":"trigger_uncertain_no_automatic_retry",
        "snapshot_id":None,"trigger_attempted":True,
    }))
    return state


def _rows():
    return [
        {"id":SID,"created":CREATED,"dataset_id":DATASET,
         "status":"ready","trigger":{"type":"API"}},
        {"id":"s_another123456789","created":CREATED,
         "dataset_id":DATASET,"status":"ready","trigger":{"type":"CP"}},
        {"id":"s_foreign123456789","created":CREATED,
         "dataset_id":"gd_other12345678","status":"ready","trigger":{"type":"API"}},
        {"id":"s_ancient123456789",
         "created":(NOW-timedelta(days=9)).isoformat(),
         "dataset_id":DATASET,"status":"ready","trigger":{"type":"API"}},
    ]


def _request(method,path,token,*,query=None,payload=None):
    if method!="GET" or path!="/datasets/v3/snapshots" or payload is not None:
        raise AssertionError("no paid POST allowed")
    if query["dataset_id"]!=DATASET or query["trigger_type"]!="API":
        raise AssertionError("unexpected filter")
    return _rows()


class BrightDataRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.token=patch("scripts.recover_market_brightdata_snapshots.bd._token",
                         return_value="mock-only-token")
        self.token.start()
        self.addCleanup(self.token.stop)

    def test_list_two_failed_state_files_without_paid_post(self):
        with TemporaryDirectory() as tmp:
            first=_write(tmp,"first.json",FIRST)
            second=_write(tmp,"second.json",SECOND)
            result=list_candidates(state_files=[first,second],request=_request)
            self.assertEqual(result["candidate_count"],1)
            self.assertEqual(result["candidates"][0]["snapshot_id"],SID)
            self.assertFalse(result["matching_source_url_verified"])
            self.assertTrue(result["only_get_requests"])
            self.assertIsNone(_safe_read(first)["snapshot_id"])
            self.assertIsNone(_safe_read(second)["snapshot_id"])

    def test_no_snapshot_candidates_means_no_assumed_success(self):
        with TemporaryDirectory() as tmp:
            state=_write(tmp,"second.json",SECOND)
            result=list_candidates(state_files=[state],request=lambda *a,**k:[])
            self.assertEqual(result["candidate_count"],0)
            self.assertIsNone(_safe_read(state)["snapshot_id"])

    def test_verify_only_when_one_result_is_exact_same_url(self):
        with TemporaryDirectory() as tmp:
            state=_write(tmp,"second.json",SECOND)
            result=verify_and_attach(state_file=state,snapshot_id=SID,
                request=_request,progress=lambda _: "ready",
                download=lambda _:[{"url":URL,"play_count":150,"digg_count":10}])
            self.assertEqual(result["state"],"ready")
            self.assertTrue(result["video_url_exact_match"])
            self.assertTrue(result["only_get_requests"])
            saved=_safe_read(state)
            self.assertEqual(saved["snapshot_id"],SID)
            self.assertEqual(saved["recovery_kind"],
                             "verified_one_video_snapshot_from_read_only_listing")

    def test_wrong_video_does_not_change_state(self):
        with TemporaryDirectory() as tmp:
            state=_write(tmp,"second.json",SECOND)
            with self.assertRaisesRegex(ValueError,"candidate_video_url_does_not_match"):
                verify_and_attach(state_file=state,snapshot_id=SID,
                    request=_request,progress=lambda _:"ready",
                    download=lambda _:[{
                        "url":"https://www.tiktok.com/@other/video/1234567890123456789",
                        "play_count":150}])
            self.assertIsNone(_safe_read(state)["snapshot_id"])

    def test_failed_running_or_multiple_records_cannot_claim_one_video(self):
        with TemporaryDirectory() as tmp:
            state=_write(tmp,"second.json",SECOND)
            with self.assertRaisesRegex(ValueError,"candidate_snapshot_not_ready_running"):
                verify_and_attach(state_file=state,snapshot_id=SID,
                    request=_request,progress=lambda _:"running",
                    download=lambda _: self.fail("premature download"))
            with self.assertRaisesRegex(ValueError,"candidate_not_exactly_one_video_record"):
                verify_and_attach(state_file=state,snapshot_id=SID,
                    request=_request,progress=lambda _:"ready",
                    download=lambda _:[
                        {"url":URL,"play_count":150},
                        {"url":URL,"play_count":151}])
            self.assertIsNone(_safe_read(state)["snapshot_id"])

    def test_fabricated_id_not_in_snapshot_listing_is_rejected(self):
        with TemporaryDirectory() as tmp:
            state=_write(tmp,"second.json",SECOND)
            with self.assertRaisesRegex(ValueError,"snapshot_not_in_verified_time_and_dataset_listing"):
                verify_and_attach(state_file=state,snapshot_id="s_missing12345678",
                    request=_request,progress=lambda _:self.fail("must not query"))
            self.assertIsNone(_safe_read(state)["snapshot_id"])

    def test_control_panel_snapshot_not_attached(self):
        with TemporaryDirectory() as tmp:
            state=_write(tmp,"second.json",SECOND)
            with self.assertRaisesRegex(ValueError,"snapshot_not_in_verified_time_and_dataset_listing"):
                verify_and_attach(state_file=state,snapshot_id="s_another123456789",
                    request=_request,progress=lambda _:self.fail("must not query"))

    def test_unfiltered_get_diagnosis_counts_why_entries_not_matched(self):
        with TemporaryDirectory() as tmp:
            first=_write(tmp,"first.json",FIRST)
            second=_write(tmp,"second.json",SECOND)
            seen=[]
            def getter(method,path,token,*,query=None,payload=None):
                seen.append((method,path,query,payload))
                return _rows()
            result=diagnose_history(state_files=[first,second],request=getter)
            self.assertEqual(len(seen),1)
            self.assertEqual(seen[0][0:2],("GET","/datasets/v3/snapshots"))
            self.assertEqual(seen[0][2],{
                "dataset_id":DATASET,"skip":0,"limit":200})
            self.assertIsNone(seen[0][3])
            self.assertEqual(result["unfiltered_dataset_history_rows_received"],4)
            self.assertEqual(result["reason_counts"]["potential_api_snapshot_metadata"],1)
            self.assertEqual(result["reason_counts"]["non_api_trigger"],1)
            self.assertEqual(result["reason_counts"]["different_or_missing_dataset_id"],1)
            self.assertEqual(result["reason_counts"]["outside_attempt_time_window"],1)
            self.assertEqual(result["potential_candidates"][0]["snapshot_id"],SID)
            self.assertTrue(result["read_only"])
            self.assertEqual(result["paid_post_requests"],0)
            self.assertFalse(result["video_url_verified"])
            self.assertIsNone(_safe_read(first)["snapshot_id"])

    def test_empty_history_is_inconclusive_but_no_paid_post(self):
        with TemporaryDirectory() as tmp:
            state=_write(tmp,"second.json",SECOND)
            report=diagnose_history(state_files=[state],
                                     request=lambda *args,**kwargs:[])
            self.assertEqual(report["unfiltered_dataset_history_rows_received"],0)
            self.assertFalse(report["history_truncation_possible"])
            self.assertEqual(report["potential_candidates"],[])
            self.assertFalse(report["video_url_verified"])

    def test_missing_creation_metadata_is_reported_not_fabricated(self):
        with TemporaryDirectory() as tmp:
            state=_write(tmp,"second.json",SECOND)
            row={"id":SID,"dataset_id":DATASET,"status":"ready",
                 "trigger":{"type":"API"}}
            report=diagnose_history(state_files=[state],
                request=lambda *args,**kwargs:[row])
            self.assertEqual(
                report["reason_counts"]["missing_or_invalid_created_at"],1)
            self.assertEqual(report["reason_counts"]["potential_api_snapshot_metadata"],0)

    def test_diagnosis_cannot_combine_different_sources(self):
        with TemporaryDirectory() as tmp:
            first=_write(tmp,"first.json",FIRST)
            second=_write(tmp,"second.json",SECOND)
            doc=json.loads(second.read_text())
            doc["dataset_id"]="gd_other12345678"
            second.write_text(json.dumps(doc))
            with self.assertRaisesRegex(ValueError,"states_different_video_or_dataset"):
                diagnose_history(state_files=[first,second],
                    request=lambda *a,**k:self.fail("must not GET"))

    def test_different_state_targets_not_merged(self):
        with TemporaryDirectory() as tmp:
            first=_write(tmp,"first.json",FIRST)
            second=_write(tmp,"second.json",SECOND)
            v=json.loads(second.read_text())
            v["url"]="https://www.tiktok.com/@other/video/1234567890123456789"
            second.write_text(json.dumps(v))
            with self.assertRaisesRegex(ValueError,"states_different_video_or_dataset"):
                list_candidates(state_files=[first,second],request=_request)


if __name__=="__main__":
    unittest.main()
