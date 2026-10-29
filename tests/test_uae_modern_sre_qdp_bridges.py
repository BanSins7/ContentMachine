"""Offline contracts for modern M30/M32/M36 SRE -> QDP -> UAE readers.

No DB, worker, payment or model call. A separate disposable PG17 E2E
must exercise the SQL, source permissions and cross-module foreign keys.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from intelligence.unified_analytics.contracts import AnalyticsBoundaryError, canonical_hash
from intelligence.unified_analytics.dto import InputScope
from intelligence.unified_analytics.sre_adapter import (
    _effective_modern_decisions, SRE_RUN_ITEM_SQL, SRE_MODERN_RESULTS_SQL,
)
from intelligence.unified_analytics.qdp_adapter import (
    QDPQualifiedPins, RUN_SQL, MODERN_LINEAGE_SQL,
)
from intelligence.unified_analytics.lineage import D1_IDENTITY_SQL

HASH = "a"*64
RUN = str(uuid4())
CAT1 = str(uuid4())
CAT2 = str(uuid4())


def pins(result_ids, keys):
    scope=InputScope(
        project_id="a"*32,video_id=15,source_id="tiktok",
        source_policy_version_id=4,content_kind="video",
        qdp_run_id=RUN,qdp_input_sha256=HASH,
    )
    return QDPQualifiedPins(
        scope=scope,sre_run_id=RUN,sre_taxonomy_version_id=3,
        taxonomy_sha256=HASH,qdp_manifest_sha256=HASH,
        qdp_selected_sha256=HASH,sre_content_version_id=2,
        sre_content_sha256=HASH,metadata_id=1,
        preparation_shas=(HASH,HASH,HASH),
        approved_category_keys=tuple(sorted(keys)),
        sre_result_ids=tuple(result_ids if len({type(x) for x in result_ids})>1 else sorted(result_ids)),
        group_c_snapshot_ids=(10,11),group_c_config_id=2,
    )


class TestModernUAESourceContracts(unittest.TestCase):
    def test_all_three_core_readers_have_modern_run_and_source_paths(self):
        self.assertIn("LEFT JOIN sre_live_runs lr",D1_IDENTITY_SQL)
        self.assertIn("COALESCE(r.id, lr.run_id)",D1_IDENTITY_SQL)
        self.assertIn("LEFT JOIN sre_classification_runs c",SRE_RUN_ITEM_SQL)
        self.assertIn("sre_m36_live_only_export",SRE_MODERN_RESULTS_SQL)
        self.assertIn("qdp_sre_live_events",SRE_MODERN_RESULTS_SQL)
        self.assertIn("qdp_sre_live_tombstones",SRE_MODERN_RESULTS_SQL)
        self.assertIn("COALESCE(r.sre_run_id,r.sre_live_run_id)",RUN_SQL)
        self.assertIn("sre_live_verified_pair_results",MODERN_LINEAGE_SQL)
        self.assertIn("sre_live_human_reviews",MODERN_LINEAGE_SQL)
        self.assertIn("sre_live_result_invalidation_intents",MODERN_LINEAGE_SQL)

    def test_modern_qualified_pins_accept_uuid_without_fake_integer_id(self):
        result_id=str(uuid4())
        obj=pins([result_id],[CAT1])
        self.assertEqual(obj.sre_result_ids,(result_id,))
        self.assertEqual(len(obj.binding_sha256),64)

    def test_legacy_qualified_pins_still_accept_real_bigint(self):
        self.assertEqual(pins([81],[CAT1]).sre_result_ids,(81,))

    def test_modern_pins_reject_mixed_id_types(self):
        with self.assertRaises(AnalyticsBoundaryError):
            pins([81,str(uuid4())],[CAT1,CAT2])

    def test_modern_pins_reject_synthetic_string_identifiers(self):
        with self.assertRaises(AnalyticsBoundaryError):
            pins(["not-a-uuid"],[CAT1])

    def test_modern_human_supersession_denied(self):
        cat=CAT1
        data={
            "category_key":cat,
            "category_review_status":"approved",
            "result_id":str(uuid4()),
            "review_id":str(uuid4()),
            "ready_event_id":str(uuid4()),
            "event_sha256":HASH,
            "verified_chunk_sha256":HASH,
            "m36_evidence_sha256":HASH,
            "model_decision":"match",
            "verified_model_decision":"match",
            "human_decision":"match",
            "latest_human_decision":"match",
        }
        data["latest_review_id"]=data["review_id"]
        digest=canonical_hash({"effective_decisions":[[cat,"match"]]})
        failed,observed=_effective_modern_decisions(
            [data],[cat],(cat,),datetime.now(timezone.utc),digest)
        self.assertIsNone(failed)
        self.assertEqual(observed,digest)
        changed={**data,"latest_review_id":str(uuid4())}
        failed,_=_effective_modern_decisions(
            [changed],[cat],(cat,),datetime.now(timezone.utc),digest)
        self.assertEqual(failed,"live_source_or_human_review_superseded")

    def test_modern_relevance_never_promotes_uncertain_to_match(self):
        cat=CAT1
        data={
            "category_key":cat,
            "category_review_status":"approved",
            "result_id":str(uuid4()),"review_id":str(uuid4()),
            "ready_event_id":str(uuid4()),"event_sha256":HASH,
            "verified_chunk_sha256":HASH,"m36_evidence_sha256":HASH,
            "model_decision":"match","verified_model_decision":"match",
            "human_decision":"uncertain","latest_human_decision":"uncertain",
        }
        data["latest_review_id"]=data["review_id"]
        failure,_=_effective_modern_decisions(
            [data],[cat],(cat,),datetime.now(timezone.utc),HASH)
        self.assertEqual(failure,"uncertain_live_verdict")


if __name__ == "__main__":
    unittest.main()
