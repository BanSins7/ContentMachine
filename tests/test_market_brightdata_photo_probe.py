"""Bright Data TikTok Photo Mode post identity regression tests.

All provider calls are mocked. These validate a prior one-time snapshot
against its exact platform-native post ID; no paid trigger or raw data leak.
"""
from __future__ import annotations
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from scripts import probe_market_brightdata_one as probe

PHOTO="https://www.tiktok.com/@luke.swan89/photo/7689988051296210209"
VIDEO="https://www.tiktok.com/@luke.swan89/video/7689988051296210209"
OTHER="https://www.tiktok.com/@luke.swan89/photo/7689988051296210210"


class PhotoMetricsMatching(unittest.TestCase):
    def setUp(self):
        self.t=TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.path=Path(self.t.name)
        self.state=self.path/"provider-state.json"
        self.output=self.path/"metrics.json"
        self.state.write_text(json.dumps({
            "schema_version":probe.SCHEMA,"platform":"tiktok",
            "url":PHOTO,"snapshot_id":"s_abcdefghijklmnopqrstuvwxyz012345",
            "dataset_id":"gd_lu702nij2f790tmv9h","state":"ready"}),encoding="utf-8")

    def _record(self,url):
        return {"url":url,"play_count":13650,
                "digg_count":1572,"comment_count":15,
                "share_count":91,"collect_count":137}

    def test_photo_url_variant_matches_same_native_id_without_new_post(self):
        with patch.object(probe.bd,"progress",return_value="ready"),patch.object(
            probe.bd,"download",return_value=[self._record(VIDEO)]) as downloaded,\
             patch.object(probe.bd,"trigger",
                          side_effect=AssertionError("must never retrigger")):
            result=probe.collect(state_file=self.state,metrics_file=self.output)
        downloaded.assert_called_once()
        self.assertEqual(result["post_native_id"],"7689988051296210209")
        self.assertEqual(result["identity_match_rule"],"exact_platform_and_native_id")
        self.assertTrue(result["provider_url_variant_differs"])
        self.assertEqual(result["views"],13650)
        self.assertTrue(result["observation_time_assumed"])
        self.assertTrue(result["not_full_group_c_certification"])
        self.assertIsNone(result["provider_observed_at"])
        self.assertTrue(self.output.is_file())

    def test_photo_canonical_match_is_exact(self):
        with patch.object(probe.bd,"progress",return_value="ready"),patch.object(
            probe.bd,"download",return_value=[self._record(PHOTO)]):
            result=probe.collect(state_file=self.state,metrics_file=self.output)
        self.assertEqual(result["identity_match_rule"],"exact_canonical_url")
        self.assertFalse(result["provider_url_variant_differs"])

    def test_other_post_is_rejected_without_metrics_file(self):
        with patch.object(probe.bd,"progress",return_value="ready"),patch.object(
            probe.bd,"download",return_value=[self._record(OTHER)]):
            with self.assertRaisesRegex(ValueError,"single_post_metrics_not_unambiguous"):
                probe.collect(state_file=self.state,metrics_file=self.output)
        self.assertFalse(self.output.exists())

    def test_two_same_id_rows_are_ambiguous_not_arbitrarily_chosen(self):
        with patch.object(probe.bd,"progress",return_value="ready"),patch.object(
            probe.bd,"download",
            return_value=[self._record(PHOTO),self._record(VIDEO)]):
            with self.assertRaisesRegex(ValueError,"single_post_metrics_not_unambiguous"):
                probe.collect(state_file=self.state,metrics_file=self.output)
        self.assertFalse(self.output.exists())

    def test_dataset_auth_does_not_imply_trigger_consent(self):
        with self.assertRaisesRegex(ValueError,"paid_trigger_requires_explicit_consent"):
            probe.start(platform="tiktok",url=PHOTO,
                        state_file=self.path/"trigger.json",consent=False)
        self.assertFalse((self.path/"trigger.json").exists())


if __name__=="__main__":
    unittest.main()
