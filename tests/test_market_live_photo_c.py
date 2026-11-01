"""Real numeric TikTok Photo Mode one-snapshot offline Group C QA.

Uses the source counters actually observed in a user-run provider snapshot,
but the fixtures are local copies and do not perform network requests.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.analyze_market_live_one_c import analyze,main

URL="https://www.tiktok.com/@luke.swan89/photo/7689988051296210209"
SID="sd_mvxdevhz202ah32q5t"
DATASET="gd_lu702nij2f790tmv9h"


def make_fixture(root:Path,*,published=True):
    state=root/"state.json"
    metrics=root/"metrics.json"
    document={
        "schema_version":"market-brightdata-single-video-metrics/v1",
        "provider":"brightdata","snapshot_id":SID,"dataset_id":DATASET,
        "platform":"tiktok","post_native_id":"7689988051296210209",
        "identity_match_rule":"exact_platform_and_native_id",
        "provider_url_variant_differs":True,
        "url_sha256":sha256(URL.encode()).hexdigest(),
        "observed_at":"2026-11-01T04:35:00+00:00",
        "observation_time_assumed":True,
        "provider_observed_at":None,
        "published_at":"2026-10-29T04:00:00+00:00" if published else None,
        "views":424000,"likes":25400,"comments":53,
        "shares":1359,"saves":17710,
        "numeric_fields":{"play_count":424000,"digg_count":25400,
                          "comment_count":53,"share_count":1359,
                          "collect_count":17710},
        "not_full_group_c_certification":True,
        "not_a_second_metric_snapshot":True,
        "collection_state":"one_observation_only",
    }
    metrics.write_text(json.dumps(document),encoding="utf-8")
    journal={
        "schema_version":"market-brightdata-single-url-test/v1",
        "platform":"tiktok","url":URL,"snapshot_id":SID,
        "state":"metrics_saved","dataset_id":DATASET,
        "trigger_attempted":True,"billable_when_provider_succeeds":True,
        "max_trigger_requests":1,"started_at":"2026-11-01T04:30:00+00:00",
        "metrics_file_sha256":sha256(metrics.read_bytes()).hexdigest(),
    }
    state.write_text(json.dumps(journal),encoding="utf-8")
    return state,metrics


class RealPhotoC4(unittest.TestCase):
    def test_real_user_counters_are_exact_no_new_provider_requests(self):
        with TemporaryDirectory() as tmp:
            state,metrics=make_fixture(Path(tmp))
            with patch("intelligence.market_intelligence.brightdata_provider._api",
                       side_effect=AssertionError("network forbidden")):
                report=analyze(state_file=state,metrics_file=metrics)
            self.assertEqual(report["post_native_id"],"7689988051296210209")
            self.assertEqual(report["content_kind"],"photo_carousel")
            self.assertEqual(report["source_snapshot_provenance"],
                             "one_time_saved_trigger_and_snapshot")
            self.assertEqual(report["c4"]["engagement_rate_fraction"],
                             "0.063235849057")
            self.assertEqual(report["c4"]["weighted_engagement_fraction"],
                             "0.069708726415")
            self.assertTrue(report["c4"]["saves_excluded_from_official_c4"])
            self.assertEqual(report["extra_descriptive_metrics_not_c4"][
                             "total_public_interactions_with_saves"],44522)
            self.assertEqual(report["extra_descriptive_metrics_not_c4"][
                             "saves_count"],17710)
            self.assertEqual(report["c0"]["state"],"unavailable")
            self.assertEqual(report["c3"]["state"],"unavailable")
            self.assertFalse(report["group_c_full_certification"])
            self.assertEqual(report["network_requests"],0)
            self.assertEqual(report["paid_triggers"],0)

    def test_missing_publication_time_keeps_c4_and_blocks_c6(self):
        with TemporaryDirectory() as tmp:
            state,metrics=make_fixture(Path(tmp),published=False)
            result=analyze(state_file=state,metrics_file=metrics)
            self.assertEqual(result["c4"]["source_counters"]["views"],424000)
            self.assertEqual(result["c6"]["state"],"unavailable")
            self.assertEqual(result["c6"]["reason"],
                             "provider_publication_time_missing")
            self.assertIsNone(result["c6"]["freshness_score"])

    def test_rejects_fake_photo_post_id_even_if_hash_recalculated(self):
        with TemporaryDirectory() as tmp:
            state,metrics=make_fixture(Path(tmp))
            value=json.loads(metrics.read_text("utf-8"))
            value["post_native_id"]="7689988051296210210"
            metrics.write_text(json.dumps(value),encoding="utf-8")
            journal=json.loads(state.read_text("utf-8"))
            journal["metrics_file_sha256"]=sha256(metrics.read_bytes()).hexdigest()
            state.write_text(json.dumps(journal),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,
                                        "metrics_post_native_identity_mismatch"):
                analyze(state_file=state,metrics_file=metrics)

    def test_rejects_unsourced_state_even_with_plausible_numbers(self):
        with TemporaryDirectory() as tmp:
            state,metrics=make_fixture(Path(tmp))
            journal=json.loads(state.read_text("utf-8"))
            journal["trigger_attempted"]=False
            state.write_text(json.dumps(journal),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,
                                        "video_source_verification_missing"):
                analyze(state_file=state,metrics_file=metrics)

    def test_offline_cli_writes_once_without_paid_triggers(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            state,metrics=make_fixture(root)
            output=root/"offline-c.json"
            with patch("intelligence.market_intelligence.brightdata_provider._api",
                       side_effect=AssertionError("network forbidden")):
                self.assertEqual(main(["--state",str(state),"--metrics",str(metrics),
                                       "--output",str(output)]),0)
            report=json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["content_kind"],"photo_carousel")
            self.assertEqual(report["snapshot_id"],SID)
            self.assertFalse(report["group_c_full_certification"])


if __name__=="__main__":
    unittest.main()
