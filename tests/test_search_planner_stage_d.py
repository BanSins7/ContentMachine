"""Stage D QA: schema compatibility, pins, method trace, fake HTTP contract.

ALL tests offline, no Postgres or paid API key. PostgreSQL E2E is in
test_taxonomy_deep_db, checked by Windows CI on disposable database.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_search_planner_foundation import snapshot, POLICY
from test_search_planner_engine_contracts import config
from intelligence.search_planner import build_hypothesis_contexts
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.search_planner.generation_engine.provider_port import (
    FakeProviderPort, BatchRequest, PossiblyBilled,
)
from intelligence.search_planner.ai_query.prompts import (
    raw_schema, stage_d_fingerprints, build_completion_payload, _provider_subset,
    payload_sha256, conservative_input_upper, enforce_pins,
)
from intelligence.search_planner.ai_query.quality import audit_raw_trace
from intelligence.search_planner.ai_query.experiments import trace_metrics, compare_ablations
from intelligence.search_planner.ai_query.transport import (
    OpenRouterQueryTransport, parse_completion,
)


def contexts():
    return build_hypothesis_contexts(snapshot(), POLICY)


def pinned_model():
    m = config()["model"]
    m.update(stage_d_fingerprints())
    return m


def fake_raw(ctxs):
    req = BatchRequest("g", "b", "model", tuple(ctxs), "a"*64, 8000)
    return FakeProviderPort("success").invoke_once(req).raw_json


def check_provider_objects(node):
    if type(node) is list:
        for child in node:
            check_provider_objects(child)
    elif type(node) is dict:
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node["required"]) == set(node["properties"])
        for child in node.values():
            check_provider_objects(child)


class StageDTests(unittest.TestCase):
    def test_prompts_are_pinned_and_include_unabridged_context(self):
        ctxs = contexts()
        original = copy.deepcopy(ctxs)
        p = build_completion_payload(ctxs, pinned_model())
        self.assertEqual(ctxs, original)
        self.assertEqual(p["model"], pinned_model()["model_id"])
        self.assertEqual(p["response_format"]["type"], "json_schema")
        self.assertTrue(p["response_format"]["json_schema"]["strict"])
        self.assertEqual(p["provider"], {
            "require_parameters": True, "allow_fallbacks": False,
        })
        self.assertFalse(p["stream"])
        self.assertEqual(json.loads(p["messages"][1]["content"])["hypothesis_contexts"], ctxs)
        self.assertIn("THINKQE-INSPIRED", p["messages"][0]["content"])
        self.assertIn("MUGI-INSPIRED", p["messages"][0]["content"])
        self.assertIn("JINA-INSPIRED", p["messages"][0]["content"])
        self.assertEqual(payload_sha256(p), payload_sha256(build_completion_payload(ctxs, pinned_model())))
        self.assertGreater(conservative_input_upper(p), len(p["messages"][1]["content"]))
        check_provider_objects(p["response_format"]["json_schema"]["schema"])

    def test_pin_tampering_blocks_before_paid_dispatch(self):
        for field in ("prompt_sha256", "raw_schema_sha256", "strategy_version", "prompt_version"):
            with self.subTest(field=field):
                bad = pinned_model()
                bad[field] = "wrong"
                with self.assertRaises(PipelineError):
                    build_completion_payload(contexts(), bad)

    def test_structured_output_has_optional_null_compatibility(self):
        schema = _provider_subset(raw_schema())
        self.assertEqual(schema["properties"]["hypotheses"]["type"], "array")
        hyp = schema["properties"]["hypotheses"]["items"]
        # These schema hints are provider-side. Strict bounds still enforced
        # by raw_validation and are never dropped locally.
        self.assertEqual(hyp["properties"]["no_viable_reason"]["type"], ["string", "null"])
        self.assertEqual(set(hyp["properties"]), {
            "hypothesis_id", "semantic_profile", "facets", "queries", "no_viable_reason"
        })
        query = hyp["properties"]["queries"]["items"]
        self.assertEqual(set(query["properties"]), {
            "facet_key", "text", "query_mode", "angle", "language",
            "research_stance", "rationale"
        })
        self.assertEqual(set(query["required"]), set(query["properties"]))
        self.assertEqual(
            set(hyp["properties"]["facets"]["items"]["properties"]),
            {"facet_key", "angle", "intent", "anchor_terms", "sketches", "lexical_hints"}
        )

    def test_diagnostics_are_auditable_and_not_fake_retrieval(self):
        ctxs = contexts()
        raw = fake_raw(ctxs)
        diagnostics = audit_raw_trace(raw, ctxs, POLICY)
        self.assertEqual(diagnostics["hypotheses"], len(ctxs))
        self.assertEqual(diagnostics["candidates"], len(ctxs))
        self.assertEqual(diagnostics["facets"], len(ctxs))
        self.assertEqual(diagnostics["sketches"], len(ctxs))
        self.assertEqual(diagnostics["lexical_hints"], len(ctxs))
        self.assertTrue(trace_metrics(raw, ctxs, POLICY)["not_a_retrieval_quality_metric"])
        self.assertIn("A4_full", compare_ablations({"A4_full": (raw,ctxs,POLICY)}))

    def test_malformed_lexical_traces_fail_closed(self):
        ctxs = contexts()
        bad = fake_raw(ctxs)
        bad["hypotheses"][0]["queries"][0]["text"] = "fyp"
        with self.assertRaises(PipelineError):
            audit_raw_trace(bad,ctxs,POLICY)
        bad = fake_raw(ctxs)
        bad["hypotheses"][0]["queries"][0]["query_mode"] = "hashtag"
        bad["hypotheses"][0]["queries"][0]["text"] = "#two words"
        with self.assertRaises(PipelineError):
            audit_raw_trace(bad,ctxs,POLICY)
        bad = fake_raw(ctxs)
        bad["hypotheses"][0]["facets"][0]["sketches"].append("y"*161)
        with self.assertRaises(PipelineError):
            audit_raw_trace(bad,ctxs,POLICY)

    def test_rejects_fabricated_engagement_and_repeated_jina_hints(self):
        ctxs = contexts()
        fake = fake_raw(ctxs)
        fake["hypotheses"][0]["facets"][0]["sketches"] = [
            "Creator got 1m views showing this product"
        ]
        with self.assertRaises(PipelineError):
            audit_raw_trace(fake, ctxs, POLICY)
        fake = fake_raw(ctxs)
        fake["hypotheses"][0]["facets"][0]["lexical_hints"] = ["workout", "workout"]
        with self.assertRaises(PipelineError):
            audit_raw_trace(fake, ctxs, POLICY)
        fake = fake_raw(ctxs)
        facet = copy.deepcopy(fake["hypotheses"][0]["facets"][0])
        facet["facet_key"] = "also_direct"
        fake["hypotheses"][0]["facets"].append(facet)
        with self.assertRaises(PipelineError):
            audit_raw_trace(fake, ctxs, POLICY)

    def test_clean_mock_openrouter_http(self):
        calls=[]
        ctxs=contexts()
        payload=build_completion_payload(ctxs,pinned_model())
        raw=fake_raw(ctxs)
        def sender(p):
            calls.append(p)
            return {"id":"mock_gen","model":p["model"],
                "choices":[{"finish_reason":"stop",
                            "message":{"content":json.dumps(raw,ensure_ascii=False)}}],
                "usage":{"prompt_tokens":355,"completion_tokens":171,
                         "cost":"0.00000001"}}
        transport=OpenRouterQueryTransport(sender=sender)
        reply=transport.invoke_once(payload)
        self.assertEqual(len(calls),1)
        self.assertEqual(reply.model_id,payload["model"])
        self.assertEqual(reply.usage["cost"],reply.charged_usd)
        audit_raw_trace(reply.raw_json,ctxs,POLICY)

    def test_cost_may_be_unknown_after_partial_http(self):
        with self.assertRaises(PossiblyBilled):
            parse_completion({"choices":[]}, "model")
        with self.assertRaises(PossiblyBilled):
            OpenRouterQueryTransport(sender=lambda p: {"usage":None}).invoke_once({
                "provider":{"require_parameters":True,"allow_fallbacks":False},
                "model":"model","stream":False,
            })
        with self.assertRaises(PipelineError):
            OpenRouterQueryTransport(sender=lambda p: {}).invoke_once({
                "provider":{"require_parameters":False,"allow_fallbacks":True},
                "model":"model","stream":False,
            })

    def test_known_billed_but_invalid_json_preserves_cost(self):
        body={"id":"mock","model":"model",
              "choices":[{"finish_reason":"stop",
                          "message":{"content":"{not json}"}}],
              "usage":{"prompt_tokens":20,"completion_tokens":30,"cost":"0.12"}}
        parsed=parse_completion(body,"model")
        self.assertIsNone(parsed.raw_json)
        self.assertEqual(parsed.usage["cost"],"0.12000000")
        for bad in ("NaN","-1","Infinity"):
            with self.subTest(bad=bad):
                cp=copy.deepcopy(body)
                cp["usage"]["cost"]=bad
                with self.assertRaises(PossiblyBilled):
                    parse_completion(cp,"model")

    def test_openrouter_numeric_usage_cost_is_decimal_and_rounded_up(self):
        from decimal import Decimal
        body = {
            "id": "real_shape_mock", "model": pinned_model()["model_id"],
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps(fake_raw(contexts()))},
            }],
            "usage": {"prompt_tokens": 125, "completion_tokens": 98,
                      "cost": 0.000000001},
        }
        parsed = parse_completion(body, pinned_model()["model_id"])
        self.assertEqual(parsed.charged_usd, "0.00000001")
        self.assertEqual(parsed.usage["cost"], "0.00000001")
        body["usage"]["cost"] = Decimal("0.123456789")
        self.assertEqual(parse_completion(body, pinned_model()["model_id"])
                         .charged_usd, "0.12345679")
        body["usage"]["cost"] = float("nan")
        with self.assertRaises(PossiblyBilled):
            parse_completion(body, pinned_model()["model_id"])

    def test_never_import_video_collectors(self):
        import inspect
        from intelligence.search_planner.ai_query import transport, prompts, live_worker
        code = inspect.getsource(transport)+inspect.getsource(prompts)+inspect.getsource(live_worker)
        for forbidden in ("apify", "brightdata", "dtk_tiktok"):
            self.assertNotIn(forbidden, code.casefold())
        self.assertNotIn("for attempt in range", code)


if __name__ == "__main__":
    unittest.main()
