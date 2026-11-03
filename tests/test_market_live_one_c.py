"""Offline proof that a real Bright Data JSON reaches copied Group C math.

Zero network requests, paid triggers, QDP calls, and DB access.
"""
from __future__ import annotations

import copy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.analyze_market_live_one_c import analyze,main

URL="https://www.tiktok.com/@luke.swan89/video/7701927285129055520"
SNAPSHOT="sd_mvwt0nki3kl9vmqz6"
DATASET="gd_lu702nij2f790tmv9h"


def fixture(directory:Path):
    metrics=directory/"metrics.json"
    state=directory/"state.json"
    saved={
        "schema_version":"market-brightdata-single-video-metrics/v1",
        "provider":"brightdata","snapshot_id":SNAPSHOT,
        "dataset_id":DATASET,"platform":"tiktok",
        "url_sha256":sha256(URL.encode()).hexdigest(),
        "published_at":"2026-10-29T03:31:59+00:00",
        "observed_at":"2026-10-31T20:04:03.902692+00:00",
        "views":13700,"likes":1570,"comments":15,
        "shares":91,"saves":137,
        "numeric_fields":{
            "digg_count":1570,"share_count":91,"collect_count":137,
            "comment_count":15,"play_count":13700,
            "video_duration":20,"profile_followers":73400,
            "num_share_count":91,
        },
        "metric_coverage":{
            "field_count":8,"has_watch_time_field":False,
            "has_retention_field":False},
        "not_full_group_c_certification":True,
        "not_a_second_metric_snapshot":True,
        "collection_state":"one_observation_only",
    }
    metrics.write_text(json.dumps(saved),encoding="utf-8")
    current={
        "schema_version":"market-brightdata-single-url-test/v1",
        "url":URL,"platform":"tiktok","dataset_id":DATASET,
        "snapshot_id":SNAPSHOT,"state":"metrics_saved",
        "recovery_kind":"verified_one_video_snapshot_from_read_only_listing",
        "metrics_file_sha256":sha256(metrics.read_bytes()).hexdigest(),
    }
    state.write_text(json.dumps(current),encoding="utf-8")
    return state,metrics,saved,current


def change(metrics:Path,state:Path,payload:dict,fix_hash=True):
    metrics.write_text(json.dumps(payload),encoding="utf-8")
    if fix_hash:
        data=json.loads(state.read_text())
        data["metrics_file_sha256"]=sha256(metrics.read_bytes()).hexdigest()
        state.write_text(json.dumps(data),encoding="utf-8")


class LiveOneCOffline(unittest.TestCase):
    def test_actual_report_numbers_with_copied_group_c_math(self):
        with TemporaryDirectory() as tmp:
            state,metrics,_,_=fixture(Path(tmp))
            result=analyze(state_file=state,metrics_file=metrics)
            self.assertEqual(result["snapshot_id"],SNAPSHOT)
            self.assertEqual(result["c4"]["engagement_rate_fraction"],
                             "0.122335766423")
            self.assertTrue(result["c4"]["saves_excluded_from_official_c4"])
            self.assertTrue(result["c4"]["duplicate_share_aliases_not_double_counted"])
            self.assertEqual(result["extra_descriptive_metrics_not_c4"][
                "total_public_interactions_with_saves"],1813)
            self.assertEqual(result["independent_numeric_fields"],7)
            self.assertEqual(result["c0"]["state"],"unavailable")
            self.assertEqual(result["c3"]["state"],"unavailable")
            self.assertEqual(result["c5"]["state"],"unavailable")
            self.assertEqual(result["c7"]["state"],"unavailable")
            self.assertFalse(result["group_c_full_certification"])
            self.assertFalse(result["provider_observation_timestamp_verified"])
            self.assertEqual(result["c6"]["state"],
                             "clock_based_preview_only_not_source_verified")
            self.assertFalse(result["c6"]["eligible_as_original_group_c_c6"])
            self.assertEqual(result["paid_triggers"],0)
            self.assertEqual(result["database_writes"],0)

    def test_report_does_not_reach_provider(self):
        with TemporaryDirectory() as tmp:
            state,metrics,_,_=fixture(Path(tmp))
            with patch("intelligence.market_intelligence.brightdata_provider._api",
                       side_effect=AssertionError("network forbidden")) as api:
                analyze(state_file=state,metrics_file=metrics)
                api.assert_not_called()

    def test_changed_metrics_file_rejected_by_sha(self):
        with TemporaryDirectory() as tmp:
            state,metrics,doc,_=fixture(Path(tmp))
            doc["views"]=13701
            change(metrics,state,doc,fix_hash=False)
            with self.assertRaisesRegex(ValueError,"metrics_file_sha256_mismatch"):
                analyze(state_file=state,metrics_file=metrics)

    def test_wrong_video_or_snapshot_rejected_even_with_valid_sha(self):
        with TemporaryDirectory() as tmp:
            state,metrics,doc,_=fixture(Path(tmp))
            for key,value,reason in (
                ("url_sha256","0"*64,"metrics_video_identity_mismatch"),
                ("snapshot_id","sd_different1234567","metrics_source_mismatch_snapshot_id")
            ):
                changed=copy.deepcopy(doc)
                changed[key]=value
                change(metrics,state,changed)
                with self.assertRaisesRegex(ValueError,reason):
                    analyze(state_file=state,metrics_file=metrics)

    def test_missing_counter_never_fabricated_as_zero(self):
        with TemporaryDirectory() as tmp:
            state,metrics,doc,_=fixture(Path(tmp))
            doc.pop("comments")
            change(metrics,state,doc)
            with self.assertRaisesRegex(ValueError,"invalid_exact_counter_comments"):
                analyze(state_file=state,metrics_file=metrics)

    def test_duplicate_share_alias_disagreement_is_rejected(self):
        with TemporaryDirectory() as tmp:
            state,metrics,doc,_=fixture(Path(tmp))
            doc["numeric_fields"]["num_share_count"]=9999
            change(metrics,state,doc)
            with self.assertRaisesRegex(ValueError,"numeric_alias_mismatch_num_share_count"):
                analyze(state_file=state,metrics_file=metrics)

    def test_unverified_state_rejected_without_network(self):
        with TemporaryDirectory() as tmp:
            state,metrics,_,doc=fixture(Path(tmp))
            doc.pop("recovery_kind")
            state.write_text(json.dumps(doc),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"video_source_verification_missing"):
                analyze(state_file=state,metrics_file=metrics)

    def test_naive_timestamp_rejected_as_unverifiable(self):
        with TemporaryDirectory() as tmp:
            state,metrics,doc,_=fixture(Path(tmp))
            doc["observed_at"]="2026-10-31T20:04:03"
            change(metrics,state,doc)
            with self.assertRaisesRegex(ValueError,"local_saved_at_invalid"):
                analyze(state_file=state,metrics_file=metrics)

    def test_cli_writes_once_no_repeated_work(self):
        with TemporaryDirectory() as tmp:
            home=Path(tmp)
            state,metrics,_,_=fixture(home)
            output=home/"c-report.json"
            self.assertEqual(main(["--state",str(state),"--metrics",str(metrics),
                                   "--output",str(output)]),0)
            report=json.loads(output.read_text())
            self.assertFalse(report["group_c_full_certification"])
            self.assertTrue(report["actual_public_counter_observation"])
            self.assertEqual(report["c4"]["source_counters"]["views"],13700)
            with patch("sys.stderr") as stderr:
                with self.assertRaises(SystemExit) as ex:
                    main(["--state",str(state),"--metrics",str(metrics),
                          "--output",str(output)])
                self.assertEqual(ex.exception.code,2)

if __name__=="__main__":
    unittest.main()
