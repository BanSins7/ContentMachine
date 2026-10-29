"""Offline QA of remote Jina A/B wiring; all provider calls are mocked.

These tests assert the exact Jina model/task fields and real query text
concatenation, without claiming that mock vectors measure retrieval quality.
"""
import copy
import hashlib
import json
import unittest

from test_search_planner_query_validation import sample
from intelligence.search_planner.query_validation.contracts import DEFAULT_POLICY
from intelligence.search_planner.query_validation.jina_remote import (
    JinaLLMQueryExpander, JinaV3RemoteEmbeddings,
)
from intelligence.search_planner.query_validation.jina_ab import compare_jina_bundle
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError


def vectors_for(payload):
    assert payload["model"] == "jina-embeddings-v3"
    assert payload["task"] in ("retrieval.query", "retrieval.passage")
    assert payload["normalized"] is True
    data = []
    for index, text in enumerate(payload["input"]):
        # QA-only hashing: never reaches live Jina A/B or production QV.
        raw = hashlib.sha256((payload["task"] + text).encode("utf-8")).digest()
        vec = [0.0] * 1024
        for offset in range(0, 32, 4):
            vec[int.from_bytes(raw[offset:offset+4], "big") % 1024] += 1.0
        data.append({"index": index, "embedding": vec})
    return {"data": data}


class JinaABTests(unittest.TestCase):
    def test_same_source_candidates_different_real_embedding_inputs(self):
        bundle, contexts = sample()
        source = copy.deepcopy(bundle)
        sent = []
        def llm(payload):
            self.assertEqual(payload["stream"], False)
            self.assertIn("additional search keywords and phrases",
                          payload["messages"][1]["content"])
            # The real OpenRouter API is the only live source of expansions.
            # This sender is deterministic only for QA of protocol and mapping.
            queries = [
                {"qid": c["candidate_id"], "additional_info": "extra related terminology"}
                for c in bundle["candidates"]
            ]
            return {"choices": [{"finish_reason": "stop",
                                 "message": {"content": json.dumps(queries)}}]}
        def embed(payload):
            sent.append((payload["task"], list(payload["input"])))
            return vectors_for(payload)
        out = compare_jina_bundle(
            bundle, contexts, DEFAULT_POLICY,
            expander=JinaLLMQueryExpander("google/gemini-2.5-flash", sender=llm),
            query_embeddings=JinaV3RemoteEmbeddings("retrieval.query", sender=embed),
            passage_embeddings=JinaV3RemoteEmbeddings("retrieval.passage", sender=embed),
            candidate_limit=20,
        )
        self.assertEqual(bundle, source)
        self.assertEqual(out["schema_version"], "jina-query-expansion-ab/v1")
        self.assertEqual(out["compared_candidate_count"], len(bundle["candidates"]))
        self.assertFalse(out["production_qv_mutated"])
        self.assertFalse(out["precision_or_video_recall_measured"])
        self.assertEqual(out["collector_queries_dispatched"], 0)
        self.assertEqual(len(out["per_query"]), len(bundle["candidates"]))
        for item in out["per_query"]:
            self.assertEqual(item["expanded_text"],
                             item["original_query"] + " " + item["additional_info"])
            self.assertGreater(item["semantic_drift_one_minus_cosine"], 0)
        self.assertTrue(any(task == "retrieval.query" for task, _ in sent))
        self.assertTrue(any(task == "retrieval.passage" for task, _ in sent))
        self.assertTrue(any("extra related terminology" in t for _, items in sent for t in items))

    def test_llm_rejects_incomplete_or_fake_expansion(self):
        queries = {"one": "posture exercises", "two": "upper back training"}
        incomplete = lambda _: {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps([{"qid": "one", "additional_info": "exercise routines"}])
        }}]}
        expander = JinaLLMQueryExpander("google/gemini-2.5-flash", sender=incomplete)
        with self.assertRaises(PipelineError):
            expander.expand(queries)

    def test_non_jina_vectors_are_rejected(self):
        bundle, contexts = sample()
        expander = JinaLLMQueryExpander(
            "google/gemini-2.5-flash", sender=lambda _: {})
        from intelligence.search_planner.query_validation.embedding import HashingTestEmbeddings
        with self.assertRaises(PipelineError):
            compare_jina_bundle(
                bundle, contexts, DEFAULT_POLICY,
                expander=expander,
                query_embeddings=HashingTestEmbeddings(),
                passage_embeddings=JinaV3RemoteEmbeddings(
                    "retrieval.passage", sender=vectors_for),
            )

    def test_external_model_id_validation(self):
        JinaLLMQueryExpander("google/gemini-2.5-flash")
        with self.assertRaises(PipelineError):
            JinaLLMQueryExpander("bad model identifier")
