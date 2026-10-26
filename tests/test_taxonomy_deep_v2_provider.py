"""RTB Deep V2 one-call provider adversarial tests; all offline and unpaid."""
from __future__ import annotations

from copy import deepcopy
import json
import unittest

from intelligence.taxonomy_builder.deep_v2_provider import single_v2_request
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.provider import ProviderUncertain
from intelligence.taxonomy_builder.deep_v2_transport import v2_request
from test_taxonomy_research_input import source_fixture, build
from test_taxonomy_deep_v2_lifecycle import model


def request():
    return v2_request(build(*source_fixture()), model())[1]


def success(body):
    return {
        "id": "mock-usage-1",
        "model": body["model"],
        "usage": {"cost": "0.025", "prompt_tokens": 1200, "completion_tokens": 300},
        "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"nodes": []})}}],
    }


class DeepV2ProviderTests(unittest.TestCase):

    def test_exact_pinned_payload_and_known_usage(self):
        payload = request()
        calls = []
        def sender(body):
            calls.append(deepcopy(body))
            return success(body)
        out = single_v2_request(payload, sender=sender)
        self.assertEqual(calls, [payload])
        self.assertEqual(str(out["charged_usd"]), "0.025")
        self.assertEqual(out["usage"]["prompt_tokens"], 1200)
        self.assertEqual(out["raw_json"], {"nodes": []})
        self.assertEqual(out["finish_reason"], "stop")

    def test_no_fallback_or_unstructured_mode(self):
        for key, replacement in (
            ("stream", True),
            ("response_format", {"type": "json_object"}),
            ("provider", {"allow_fallbacks": True}),
            ("usage", {"include": False}),
        ):
            with self.subTest(key=key):
                bad = request()
                bad[key] = replacement
                seen = []
                with self.assertRaises(PipelineError):
                    single_v2_request(bad, sender=lambda body: seen.append(body))
                self.assertFalse(seen)

    def test_unknown_cost_blocks_and_never_retries(self):
        called = []
        def sender(body):
            called.append(body)
            r = success(body)
            del r["usage"]["cost"]
            return r
        with self.assertRaises(ProviderUncertain):
            single_v2_request(request(), sender=sender)
        self.assertEqual(len(called), 1)

    def test_wrong_token_type_or_missing_tokens_unknown(self):
        for field, value in (("prompt_tokens", True),
                             ("completion_tokens", "-3"),
                             ("prompt_tokens", None)):
            with self.subTest(field=field, value=value):
                def sender(body):
                    r = success(body)
                    r["usage"][field] = value
                    return r
                with self.assertRaises(ProviderUncertain):
                    single_v2_request(request(), sender=sender)

    def test_single_provider_exception_is_uncertain(self):
        calls = []
        def sender(body):
            calls.append(body)
            raise TimeoutError("transport timed out after provider may have charged")
        with self.assertRaises(ProviderUncertain):
            single_v2_request(request(), sender=sender)
        self.assertEqual(len(calls), 1)

    def test_malformed_paid_completion_retains_known_charge(self):
        def sender(body):
            r = success(body)
            r["choices"][0]["message"]["content"] = "not valid json"
            return r
        result = single_v2_request(request(), sender=sender)
        self.assertEqual(str(result["charged_usd"]), "0.025")
        self.assertIsNone(result["raw_json"])

    def test_known_cost_with_missing_choices_is_settled_not_retried(self):
        def sender(body):
            r = success(body)
            r["choices"] = []
            return r
        out = single_v2_request(request(), sender=sender)
        self.assertIsNone(out["raw_json"])
        self.assertEqual(str(out["charged_usd"]), "0.025")


if __name__ == "__main__":
    unittest.main()
