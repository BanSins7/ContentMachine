"""Conditional contract conformance against actual PIE branch after merge.

This is NOT live peer authorization. When PIE package is not in UAE's
standalone branch, tests skip instead of claiming compatibility passed.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import unittest

from test_unified_analytics_pie_research_handoff import TemporaryApproval, SECRET
from intelligence.unified_analytics import pie_research_handoff as uae


class PIEConsumerWireContract(unittest.TestCase):
    def setUp(self):
        try:
            from intelligence.patterns_insights.contracts import PIEDatasetPointer
            from intelligence.patterns_insights.dataset_contract import load_dataset_json
            from intelligence.patterns_insights.intake.uae import (
                UAEExportReceipt,UAEExportEnvelope,HMACReceiptVerifier
            )
        except ImportError:
            self.skipTest("PIE peer package not present on standalone UAE branch")
        self.ptr=PIEDatasetPointer
        self.load=load_dataset_json
        self.receipt=UAEExportReceipt
        self.envelope=UAEExportEnvelope
        self.verifier=HMACReceiptVerifier
        self.mock=TemporaryApproval()
        self.mock.setUp()

    def _export(self):
        signed=self.mock.export()
        m=json.loads(signed.raw_manifest)
        p=self.ptr(
            project_id=m["scope"]["project_id"],
            source_id=m["scope"]["source_id"],
            source_policy_version_id=m["scope"]["source_policy_version_id"],
            uae_engine_version=m["producer"]["engine_version"],
            uae_dataset_sha256=signed.dataset_sha256,
            uae_manifest_sha256=signed.manifest_sha256,
            calculation_config_id=m["scope"]["calculation_config_id"],
            source_metric_definition=m["scope"]["source_metric_definition"],
            source_metric_version=m["scope"]["source_metric_version"],
            as_of=m["scope"]["as_of"],
        )
        receipt=self.receipt(**asdict(signed.receipt))
        env=self.envelope(signed.raw_manifest,receipt)
        return p,env,signed

    def test_actual_pie_dataset_parser_accepts_uae_v1_golden(self):
        ptr,env,signed=self._export()
        observed=self.load(ptr,env.raw_manifest)
        self.assertEqual(observed.sample_size,5)
        self.assertEqual(observed.manifest_sha256,signed.manifest_sha256)
        self.assertEqual(observed.dataset_sha256,signed.dataset_sha256)
        self.assertFalse(observed.verified_live)

    def test_actual_pie_hmac_verifier_trusts_only_matching_key(self):
        _,env,_=self._export()
        good=self.verifier({"unit-test.key.v1":SECRET})
        bad=self.verifier({"unit-test.key.v1":b"incorrect-test-key-length-minimum-32!!"})
        self.assertTrue(good.verify(env.receipt))
        self.assertFalse(bad.verify(env.receipt))


if __name__=="__main__":
    unittest.main()
