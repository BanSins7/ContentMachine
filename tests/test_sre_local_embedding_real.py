"""M16 REAL ONNX CPU inference acceptance, not a fast fake fixture.

Set CM_SRE_M16_REAL_MODEL_REQUIRED=1 plus local offline pins JSON/model dir.
A normal CI skip is NEVER an M16 DoD PASS. An explicitly required run fails
if any asset is missing, dependency unavailable, checksum different, or model
cannot produce repeatable, semantic, finite nonzero vectors.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

from intelligence.relevance.local_embedding import (
    cosine_similarity,load_local_embedding_backend,probe_local_embedding_model,
)


class RealLocalEmbeddingInference(unittest.TestCase):
    def test_actual_pinned_onnx_cpu_semantic_vectors(self):
        if os.environ.get("CM_SRE_M16_REAL_MODEL_REQUIRED") != "1":
            self.skipTest("M16 REAL QA BLOCKED: no controlled local model environment; skip != PASS")
        directory=os.environ.get("CM_SRE_M16_MODEL_DIR")
        pins_path=os.environ.get("CM_SRE_M16_PINS_FILE")
        self.assertTrue(directory and pins_path, "real M16 QA requires both absolute paths")
        with Path(pins_path).open("r",encoding="utf-8") as fd:
            pins=json.load(fd)
        report=probe_local_embedding_model(directory,pins)
        self.assertEqual(report["status"],"ready",report)
        self.assertTrue(report["real_inference_verified"])
        backend=load_local_embedding_backend(directory,pins)
        examples=[
            "A person is running in a city park.",
            "Someone is jogging outdoors in the park.",
            "The bank printed its annual financial report.",
        ]
        one=backend.embed(examples)
        two=backend.embed(examples)
        self.assertTrue(one["real_model"])
        self.assertEqual(one["backend"],"onnxruntime-cpu")
        self.assertEqual(one["model_identity_sha256"],
                         report["model_identity_sha256"])
        self.assertEqual(one["vector_sha256"],two["vector_sha256"])
        self.assertEqual(len(one["vectors"]),3)
        self.assertEqual(len(one["vectors"][0]),backend.pins.dimensions)
        self.assertEqual(one["source"],"local_cpu_onnx")
        similar=cosine_similarity(one["vectors"][0],one["vectors"][1])
        different=cosine_similarity(one["vectors"][0],one["vectors"][2])
        self.assertGreater(similar,different+0.025,
                           f"Actual local semantic model has no discriminative ranking: {similar},{different}")
        self.assertLessEqual(abs(similar),1.0)
        self.assertLessEqual(abs(different),1.0)


if __name__=="__main__":
    unittest.main()
