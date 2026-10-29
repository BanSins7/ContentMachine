"""GB-MOD-28 frozen synthetic golden cases across B1-B8 UAE candidate gate.

Structural CANDIDATE is not a production qualification, signed QDP/media,
real gold-set acceptance, human review, or any paid provider call.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from intelligence.group_b.integration.analytics_contract import (
    HandoffState, validate_snapshot,
)
from test_group_b_analytics import input_fixture


FIXTURE=(Path(__file__).parent/"fixtures"/"group_b_golden_matrix_v1.json")


def _apply(item,path,value):
    parts=path.split(".")
    target=item
    for part in parts[:-1]:
        target=target[part]
    if parts[-1] not in target:
        raise ValueError("non-existing golden input path: "+path)
    target[parts[-1]]=value


class TestB28GoldenMatrix(unittest.TestCase):
    def test_golden_scenarios_are_frozen_and_all_fail_closed_or_candidate(self):
        manifest=json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"],"group-b-golden-cases/v1")
        self.assertEqual(manifest["kind"],"synthetic_only_no_provider_or_actual_media")
        cases=manifest["cases"]
        self.assertGreaterEqual(len(cases),16)
        ids=set()
        for case in cases:
            cid=case["id"]
            self.assertNotIn(cid,ids)
            ids.add(cid)
            with self.subTest(case=cid):
                kwargs=deepcopy(input_fixture(carousel=case["baseline"]=="carousel"))
                for change in case["changes"]:
                    _apply(kwargs,change["path"],change["value"])
                outcome=validate_snapshot(**kwargs)
                self.assertEqual(outcome.state.value,case["expected"])
                self.assertIs(outcome.uae_qualified,False)
                self.assertFalse(outcome.verify_digest())
                self.assertIsNone(outcome.payload)
                if case["reason"]:
                    self.assertIn(case["reason"],outcome.reasons)
                else:
                    self.assertEqual(outcome.reasons,())
        self.assertTrue({
            "video_silent_original_ocr_absent",
            "video_no_cta_full_scan_test_stub",
            "carousel_mixed_video_regions_not_b5",
            "carousel_video_editing_fabrication",
            "video_a_rerun",
            "video_policy_revoked",
            "video_qdp_input_changed",
        }.issubset(ids))

    def test_no_synthetic_golden_is_a_claim_of_real_quality(self):
        manifest=json.loads(FIXTURE.read_text(encoding="utf-8"))
        for case in manifest["cases"]:
            self.assertNotIn("real_model_pass",case)
            self.assertNotIn("accepted_provider_receipt",case)
            self.assertIn(case["expected"],("blocked","candidate"))


if __name__=="__main__":
    unittest.main()
