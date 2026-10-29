"""M17 actual local ONNX all-leaf acceptance (no cloud, no paid calls).

This is intentionally NOT part of the fast unit matrix. When the real M16
pretrained model is not provisioned, the test skips with explicit BLOCKED
message and MUST NOT be counted toward M17 production acceptance.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

from intelligence.relevance.live_leaf_ranking import (
    _rank_verified, prepare_c9_leaf_features,
)
from intelligence.relevance.local_embedding import load_local_embedding_backend
from intelligence.relevance.ports.approved_c9 import load_approved_c9
from test_sre_taxonomy_adapter import FakeConnection,db_fixtures,PID,PUB

class GenuineLeafRankingM17(unittest.TestCase):
    def test_real_cpu_model_ranks_every_approved_leaf_without_candidate_drop(self):
        if os.getenv("CM_SRE_M16_REAL_MODEL_REQUIRED") != "1":
            self.skipTest("M17 REAL QA BLOCKED: pretrained pinned ONNX model absent; fast math fixtures are not real inference")
        model_path=os.environ.get("CM_SRE_M16_MODEL_DIR")
        pin_path=os.environ.get("CM_SRE_M16_PINS_FILE")
        self.assertTrue(model_path and pin_path,"M17 real QA requires actual model and trusted pins")
        with Path(pin_path).open("r",encoding="utf-8") as fd:
            pins=json.load(fd)
        real=load_local_embedding_backend(model_path,pins)
        c9=load_approved_c9(PID,PUB,connect=lambda:FakeConnection(db_fixtures()))
        self.assertEqual(len(prepare_c9_leaf_features(c9)),len(c9["hypothesis_keys"]))
        # Synthetic D1 content is ONLY a local test text for this actual
        # embedding-inference acceptance; real DB authority is separately
        # exercised by test_sre_offline_pg.py.
        content={
            "schema_version":"sre-content-input/v1",
            "project_id":PID,"video_id":1,"content_version_id":4,
            "source_id":"local_test","source_policy_version_id":3,
            "content_sha256":"a"*64,"completeness":"metadata_only",
            "media_asset_id":None,"expires_at":None,
            "rights":{"can_store_metadata":True,"can_semantic_classify":True,
                      "can_external_llm":False,"can_analyze_media":False},
            "provenance":{"discovery_id":2,"search_query_id":None,
                          "source_request_id":None},
            "data":{"title":"Training in the park",
                    "description":"Outdoor jogging and fitness exercises",
                    "hashtags":["#running"],"transcript":None,"scenes":None},
        }
        ranked=_rank_verified(real,c9,content)
        rerun=_rank_verified(real,c9,content)
        self.assertTrue(ranked["ranking_only"])
        self.assertEqual(ranked["ranking_sha256"],rerun["ranking_sha256"])
        self.assertEqual(ranked["candidate_count"],len(c9["hypothesis_keys"]))
        self.assertEqual(ranked["dropped_count"],0)
        self.assertEqual(
            {x["category_key"] for x in ranked["candidates"]},
            set(c9["hypothesis_keys"]),
        )
        self.assertEqual({x["classification_state"] for x in ranked["candidates"]},
                         {"not_evaluated"})
        self.assertTrue(all(x["is_candidate"] for x in ranked["candidates"]))
        self.assertEqual([x["rank"] for x in ranked["candidates"]],
                         list(range(1,len(c9["hypothesis_keys"])+1)))
        self.assertEqual(ranked["paid_calls"],0)
        self.assertFalse(ranked["dispatch_allowed"])


if __name__=="__main__":
    unittest.main()
