"""Pure Stage C contract tests: fake provider, strict raw schema, no DB/HTTP."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.search_planner import build_hypothesis_contexts
from intelligence.search_planner.generation_engine.raw_validation import validate_raw_batch
from intelligence.search_planner.generation_engine.provider_port import (
    FakeProviderPort, BatchRequest, PossiblyBilled, KnownProviderFailure,
)
from test_search_planner_foundation import POLICY, snapshot


def _contexts():
    return build_hypothesis_contexts(snapshot(), POLICY)


def _request(contexts):
    return BatchRequest(
        generation_id="gen_fake", batch_id="batch_fake", model_id="fake/model",
        contexts=tuple(contexts),
        request_sha256="a" * 64, max_output_tokens=1000,
    )


class StageCContracts(unittest.TestCase):
    def test_fake_success_and_no_viable(self):
        contexts = _contexts()
        req = _request(contexts)
        for mode in ("success", "no_viable"):
            fake = FakeProviderPort(mode)
            response = fake.invoke_once(req)
            self.assertEqual(response.model_id, "fake/model")
            validated = validate_raw_batch(response.raw_json, contexts, POLICY)
            self.assertEqual(len(validated["hypotheses"]), len(contexts))
            self.assertEqual(len(fake.calls), 1)
            for hyp in validated["hypotheses"]:
                self.assertEqual(bool(hyp["queries"]), mode == "success")
                self.assertEqual(bool(hyp["no_viable_reason"]), mode == "no_viable")

    def test_rejects_wrong_facet_language_incomplete_and_extra_fields(self):
        contexts = _contexts()
        raw = FakeProviderPort().invoke_once(_request(contexts)).raw_json
        invalid = copy.deepcopy(raw)
        invalid["hypotheses"][0]["queries"][0]["facet_key"] = "not_a_facet"
        with self.assertRaises(PipelineError):
            validate_raw_batch(invalid, contexts, POLICY)
        invalid = copy.deepcopy(raw)
        invalid["hypotheses"][0]["queries"][0]["language"] = "ja"
        with self.assertRaises(PipelineError):
            validate_raw_batch(invalid, contexts, POLICY)
        invalid = copy.deepcopy(raw)
        invalid["hypotheses"] = invalid["hypotheses"][:-1]
        with self.assertRaises(PipelineError):
            validate_raw_batch(invalid, contexts, POLICY)
        invalid = copy.deepcopy(raw)
        invalid["hypotheses"][0]["invented_project_id"] = "other"
        with self.assertRaises(PipelineError):
            validate_raw_batch(invalid, contexts, POLICY)
        invalid = copy.deepcopy(raw)
        invalid["hypotheses"][0]["queries"][0]["text"] = " "
        with self.assertRaises(PipelineError):
            validate_raw_batch(invalid, contexts, POLICY)

    def test_provider_failure_modes_are_explicit(self):
        req = _request(_contexts())
        with self.assertRaises(PossiblyBilled):
            FakeProviderPort("uncertain").invoke_once(req)
        with self.assertRaises(KnownProviderFailure):
            FakeProviderPort("known_failure").invoke_once(req)
        with self.assertRaises(PipelineError):
            validate_raw_batch(
                FakeProviderPort("invalid_json").invoke_once(req).raw_json,
                _contexts(), POLICY,
            )

    def test_transport_is_explicit_fake_only(self):
        import inspect
        from intelligence.search_planner.generation_engine import worker, provider_port
        source = inspect.getsource(worker) + inspect.getsource(provider_port)
        for forbidden in ("requests.post(", "httpx.post(", "urlopen(", "urllib.request("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
