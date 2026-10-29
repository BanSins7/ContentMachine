"""M17 all-approved-leaf deterministic ranking unit QA; NO fake model inference.

Synthetic finite vectors below test ONLY pure coverage/ranking math. They are
never accepted by the production rank_live_run_item entrypoint, which requires
a SHA-verified local M16 ONNX CPU session + authoritative DB C9/D1 gates.
"""
from __future__ import annotations

from copy import deepcopy
import math
import subprocess
import sys
import unittest
from unittest.mock import patch

from intelligence.relevance.live_leaf_ranking import (
    EXCLUSION_PENALTY, RANK_VERSION,
    prepare_c9_leaf_features, prepare_d1_video_text,
    rank_all_leaf_vectors, rank_live_run_item,
)
from intelligence.relevance.ports.approved_c9 import load_approved_c9
from intelligence.relevance.v1_contracts import (
    SREContractError, fingerprint, validate_content_input,
)
from test_sre_taxonomy_adapter import FakeConnection, db_fixtures, PID, PUB

SHA="a"*64
MODEL="b"*64


class LeafRankingM17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle=load_approved_c9(
            PID,PUB,connect=lambda:FakeConnection(db_fixtures()),
        )
        cls.features=prepare_c9_leaf_features(cls.bundle)

    def _vectors(self,features=None):
        features=features or self.features
        pos={f["category_key"]:(1.0,0.0,0.0) for f in features}
        neg={f["category_key"]:(1.0,0.0,0.0) for f in features
             if f["exclusion_text"] is not None}
        return pos,neg

    def _rank(self,features=None,positive=None,negative=None,video=(1.0,0.0,0.0)):
        features=features or self.features
        pos,neg=self._vectors(features)
        return rank_all_leaf_vectors(
            features,video,positive if positive is not None else pos,
            negative if negative is not None else neg,
            expected_hypothesis_keys=self.bundle["hypothesis_keys"],
            model_identity_sha256=MODEL,content_sha256=SHA,
        )

    def test_three_approved_leaves_full_ancestry_and_separate_exclusions(self):
        self.assertEqual(len(self.features),3)
        self.assertEqual([f["category_key"] for f in self.features],
                         self.bundle["hypothesis_keys"])
        for feat in self.features:
            self.assertTrue(feat["positive_text"])
            self.assertEqual(feat["source_node_id"],feat["ancestry_path"][-1])
            self.assertEqual(feat["direction_id"],feat["ancestry_path"][0])
            self.assertTrue(feat["approved_fact_ids"])
            self.assertEqual(feat["context_sha256"],next(
                c["context_sha256"] for c in self.bundle["contexts"]
                if c["category_key"]==feat["category_key"]))
            node=next(c["hypothesis"] for c in self.bundle["contexts"]
                      if c["category_key"]==feat["category_key"])
            self.assertIn(node["name"],feat["positive_text"])
            if node["exclude"]:
                for exclusion in node["exclude"]:
                    self.assertIn(exclusion,feat["exclusion_text"])
        self.assertEqual(prepare_c9_leaf_features(deepcopy(self.bundle)),self.features)

    def test_all_three_even_negative_and_low_similarity_no_topk(self):
        pos,neg=self._vectors()
        keys=[f["category_key"] for f in self.features]
        pos[keys[0]]=(-1.0,0.0,0.0)
        pos[keys[1]]=(0.0,1.0,0.0)
        pos[keys[2]]=(1.0,0.0,0.0)
        x=self._rank(positive=pos,negative=neg)
        self.assertEqual(x["schema_version"],RANK_VERSION)
        self.assertEqual(x["candidate_count"],3)
        self.assertEqual(x["expected_leaf_count"],3)
        self.assertEqual(x["dropped_count"],0)
        self.assertTrue(x["ranking_only"])
        self.assertEqual(set(k["category_key"] for k in x["candidates"]),set(keys))
        self.assertTrue(all(k["is_candidate"] and
                            k["classification_state"]=="not_evaluated"
                            for k in x["candidates"]))
        self.assertEqual([k["rank"] for k in x["candidates"]],[1,2,3])
        self.assertTrue(any(k["positive_similarity"]==-1.0 for k in x["candidates"]))
        self.assertEqual(x,self._rank(positive=pos,negative=neg))
        self.assertEqual(x["ranking_sha256"],fingerprint({
            k:v for k,v in x.items() if k!="ranking_sha256"
        }))

    def test_48_approved_leaf_math_coverage_no_threshold_or_candidate_loss(self):
        # This expands only the PURE mathematical fixture; it is not DB C9
        # authority and never represents actual pretrained model inference.
        features=[]
        for i in range(48):
            key=f"00000000-0000-4000-8000-{i:012x}"
            features.append({
                "category_key":key,"source_node_id":f"node-{i}",
                "direction_id":"direction-1","branch_id":None,
                "ancestry_path":["direction-1",f"node-{i}"],
                "context_sha256":"a"*64,
                "exclusion_text":"negative" if i%2==0 else None,
            })
        all_keys=[f["category_key"] for f in features]
        positives={k:(-1.0,0.0,0.0) for k in all_keys}
        exclusions={k:(1.0,0.0,0.0) for i,k in enumerate(all_keys) if i%2==0}
        result=rank_all_leaf_vectors(
            features,(1.0,0.0,0.0),positives,exclusions,
            expected_hypothesis_keys=all_keys,
            model_identity_sha256=MODEL,content_sha256=SHA,
        )
        self.assertEqual(result["candidate_count"],48)
        self.assertEqual(result["dropped_count"],0)
        self.assertEqual(len({x["category_key"] for x in result["candidates"]}),48)
        self.assertTrue(all(x["positive_similarity"]==-1 for x in result["candidates"]))
        self.assertTrue(all(x["is_candidate"] for x in result["candidates"]))
        self.assertEqual([x["rank"] for x in result["candidates"]],list(range(1,49)))
        self.assertEqual(result,rank_all_leaf_vectors(
            features,(1.0,0.0,0.0),positives,exclusions,
            expected_hypothesis_keys=all_keys,
            model_identity_sha256=MODEL,content_sha256=SHA,
        ))

    def test_separate_exclusion_is_penalty_not_disqualifier(self):
        features=deepcopy(self.features)
        for f in features:
            f["exclusion_text"]="Not this case."
        p={f["category_key"]:(1.0,0.0) for f in features}
        neg={f["category_key"]:(1.0,0.0) for f in features}
        result=self._rank(features=features,positive=p,negative=neg,video=(1.0,0.0))
        self.assertEqual(len(result["candidates"]),len(features))
        for row in result["candidates"]:
            self.assertEqual(row["positive_similarity"],1.0)
            self.assertEqual(row["exclusion_similarity"],1.0)
            self.assertAlmostEqual(row["exclusion_penalty"],EXCLUSION_PENALTY)
            self.assertAlmostEqual(row["rank_score"],1.0-EXCLUSION_PENALTY)
        neg={f["category_key"]:(-1.0,0.0) for f in features}
        result=self._rank(features=features,positive=p,negative=neg,video=(1.0,0.0))
        self.assertTrue(all(row["exclusion_penalty"]==0 for row in result["candidates"]))
        self.assertTrue(all(row["rank_score"]==1 for row in result["candidates"]))

    def test_stable_tie_break_by_approved_leaf_key(self):
        out=self._rank()
        self.assertEqual([r["category_key"] for r in out["candidates"]],
                         self.bundle["hypothesis_keys"])
        self.assertEqual(out,self._rank())
        self.assertEqual(out["candidate_count"],len(self.features))

    def test_forged_c9_leaf_h1_h2_exclusions_and_omission_blocked(self):
        mutations=[
            lambda c:c["contexts"].pop(),
            lambda c:c["hypothesis_keys"].pop(),
            lambda c:c["contexts"].reverse(),
            lambda c:c["contexts"][0]["hypothesis"]["exclude"].append("Invented exclusion"),
            lambda c:c["contexts"][0]["hypothesis"].update(name="Invented hypothesis"),
            lambda c:c["approved_snapshot"].update(human_reviews=[1]),
            lambda c:c.update(project_id="b"*32),
        ]
        for i,change in enumerate(mutations):
            with self.subTest(i=i),self.assertRaises(SREContractError):
                fake=deepcopy(self.bundle)
                change(fake)
                prepare_c9_leaf_features(fake)

    def test_missing_duplicate_extra_unknown_and_nonfinite_vector_are_denied(self):
        valid,ex=self._vectors()
        keys=[f["category_key"] for f in self.features]
        for positive,negative in (
            ({keys[0]:valid[keys[0]]},ex),
            ({**valid,"55555555-5555-4555-8555-555555555555":(1,0,0)},ex),
            (valid,{**ex,"55555555-5555-4555-8555-555555555555":(1,0,0)}),
        ):
            with self.assertRaises(SREContractError):
                self._rank(positive=positive,negative=negative)
        for changed in ((float("nan"),0,0),(float("inf"),0,0),(True,0,0),(0,0,0)):
            pos=dict(valid);pos[keys[0]]=changed
            with self.assertRaises(SREContractError):
                self._rank(positive=pos,negative=ex)
        with self.assertRaises(SREContractError):
            self._rank(video=(0,0,0))
        feat=deepcopy(self.features);feat.pop()
        with self.assertRaises(SREContractError):
            self._rank(features=feat,positive=valid,negative=ex)
        feat=deepcopy(self.features);feat[0]["category_key"]=feat[1]["category_key"]
        with self.assertRaises(SREContractError):
            self._rank(features=feat)

    def test_validated_d1_text_uses_only_real_observations_no_keyword_fallback(self):
        data={
            "schema_version":"sre-content-input/v1","project_id":PID,
            "video_id":1,"content_version_id":3,"content_sha256":SHA,
            "source_id":"youtube","source_policy_version_id":4,
            "completeness":"metadata_only","media_asset_id":None,"expires_at":None,
            "rights":{"can_store_metadata":True,"can_semantic_classify":True,
                      "can_external_llm":False,"can_analyze_media":False},
            "provenance":{"discovery_id":5,"search_query_id":None,"source_request_id":None},
            "data":{"title":"Бег в парке","description":"Training for 5 km",
                    "hashtags":["#running"],"transcript":None,"scenes":None},
        }
        text=prepare_d1_video_text(data)
        self.assertIn("Бег",text)
        self.assertIn("#running",text)
        self.assertNotIn("search_query_id",text)
        data["data"]["title"]=None
        data["data"]["description"]=None
        data["data"]["hashtags"]=[]
        with self.assertRaises(SREContractError):
            prepare_d1_video_text(data)
        data["data"]["description"]="a"*8200
        with self.assertRaises(SREContractError):
            prepare_d1_video_text(data)

    def test_production_entry_denies_foreign_before_loading_any_model(self):
        class Result:
            def fetchone(self):return None
        class Connection:
            def __init__(self):self.queries=[]
            def execute(self,sql,params):
                self.queries.append((sql,params));return Result()
        db=Connection()
        with patch("intelligence.relevance.live_leaf_ranking.load_local_embedding_backend",
                   side_effect=AssertionError("model must not load")) as load_model:
            with self.assertRaises(SREContractError) as e:
                rank_live_run_item(db,PID,"11111111-1111-4111-8111-111111111111",
                                   4,model_directory="missing",model_pins={})
        self.assertEqual(e.exception.code,"rights_denied")
        self.assertEqual(db.queries[0][1][0],PID)
        load_model.assert_not_called()

    def test_import_is_pure_no_cloud_onnx_or_web(self):
        script=(
            "import sys;import intelligence.relevance.live_leaf_ranking;"
            "assert 'onnxruntime' not in sys.modules;"
            "assert 'tokenizers' not in sys.modules;"
            "assert 'openai' not in sys.modules;"
            "assert 'transformers' not in sys.modules;"
        )
        proc=subprocess.run([sys.executable,"-c",script],
                            capture_output=True,text=True,timeout=15)
        self.assertEqual(proc.returncode,0,proc.stderr)


if __name__=="__main__":
    unittest.main()
