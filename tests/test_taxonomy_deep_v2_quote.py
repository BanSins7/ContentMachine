"""Deep V2 exact Opus request and signed quote smoke tests (no DB/provider)."""
from __future__ import annotations

from copy import deepcopy
import unittest

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.taxonomy_builder.deep_v2_transport import (
    MAX_H1_BYTES, MAX_GATE_BYTES, ALL, v2_schema, v2_request, v2_messages,
)
from intelligence.taxonomy_builder.deep_v2_quote_repository import (
    compute_deep_v2_quote, _signed_quote,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.provider import reservation_cost
from test_taxonomy_research_input import source_fixture, build
from test_taxonomy_deep_v2_lifecycle import model


def verified_source():
    return build(*source_fixture())


class DeepV2QuoteContractTests(unittest.TestCase):

    def test_exact_14_fields_transport_strict_and_versioned(self):
        schema = v2_schema("deep_first")
        node = schema["schema"]["properties"]["nodes"]["items"]
        self.assertEqual(len(node["required"]), 14)
        self.assertEqual(set(node["required"]), set(ALL))
        self.assertFalse(node["additionalProperties"])
        self.assertTrue(schema["strict"])
        self.assertEqual(schema["name"], "research_taxonomy_deep-v2_1_deep_first")
        for key in ("source_observation_ids", "search_signals", "validation_focus"):
            self.assertIn(key, node["properties"])
        self.assertNotIn("duplicates", schema["schema"]["properties"])
        self.assertIn("duplicates", v2_schema("deep_second")["schema"]["properties"])

    def test_request_exact_pass1_and_quote_reproduce(self):
        inp = verified_source()
        m = model()
        stage, payload = v2_request(inp, m)
        quoted = compute_deep_v2_quote(inp, m)
        self.assertEqual(quoted["request"], payload)
        self.assertEqual(quoted["stage"], stage)
        self.assertEqual(quoted["pass1"], reservation_cost(payload, m))
        self.assertEqual(quoted["quote_body"]["request_sha256"], canonical_sha(payload))
        self.assertEqual(
            quoted["quote_body"]["prompt_sha256"], canonical_sha(payload["messages"]))
        self.assertTrue(quoted["total"] >= quoted["pass1"] + quoted["pass2"])
        self.assertGreater(quoted["pass2"], 0)
        self.assertEqual(
            payload["response_format"]["type"], "json_schema")
        self.assertEqual(payload["provider"]["allow_fallbacks"], False)
        self.assertEqual(payload["usage"], {"include": True})
        self.assertFalse(payload["stream"])
        self.assertEqual(quoted, compute_deep_v2_quote(inp, m))
        self.assertEqual(quoted["quote_body"]["authorized"], False)

    def test_signed_quote_hash_excludes_only_own_hash(self):
        priced = compute_deep_v2_quote(verified_source(), model())
        bid = "00000000-0000-4000-8000-000000000001"
        signed = _signed_quote(bid, priced)
        self.assertEqual(signed["binding_id"], bid)
        self.assertEqual(signed["quote_sha256"],
                         canonical_sha({k: v for k, v in signed.items()
                                        if k != "quote_sha256"}))
        self.assertEqual(signed, _signed_quote(bid, priced))

    def test_no_tampered_unapproved_source_or_catalog(self):
        inp = verified_source()
        inp["c1_approved_facts"][0]["reviewed_text"] = "invented"
        with self.assertRaises(PipelineError):
            compute_deep_v2_quote(inp, model())
        m = model()
        m["capabilities"]["supported_parameters"] = ["response_format"]
        with self.assertRaisesRegex(PipelineError, "strict_json_schema_not_supported"):
            compute_deep_v2_quote(verified_source(), m)

    def test_actual_video_evidence_passes_unchanged_as_untrusted_data(self):
        inp = verified_source()
        before = deepcopy(inp)
        messages = v2_messages(inp)
        self.assertEqual(inp, before)
        self.assertIn("UNTRUSTED", messages[0]["content"])
        self.assertIn("original_a_observations", messages[1]["content"])
        self.assertIn("approved_b9_facts", messages[1]["content"])
        self.assertIn(inp["research_input_sha256"], messages[1]["content"])

    def test_second_pass_requires_human_review_and_bounded_tree(self):
        inp = verified_source()
        with self.assertRaisesRegex(PipelineError, "h1_and_coverage_required"):
            v2_messages(inp, role="deep_second")
        review = [{"id": "one", "kind": "direction"}]
        coverage = {"uncovered": []}
        messages = v2_messages(inp, role="deep_second", review=review, coverage=coverage)
        self.assertIn('"review1"', messages[-1]["content"])
        self.assertIn('"coverage"', messages[-1]["content"])
        with self.assertRaisesRegex(PipelineError, "followup_review_too_large"):
            v2_messages(inp, role="deep_second",
                        review=[{"name": "x" * MAX_H1_BYTES}], coverage=coverage)
        with self.assertRaisesRegex(PipelineError, "followup_review_too_large"):
            v2_messages(inp, role="deep_second",
                        review=review, coverage={"str": "y" * MAX_GATE_BYTES})

    def test_no_budget_quote_if_followup_capacity_exceeds_model_context(self):
        m = model()
        m["capabilities"]["context_length"] = 30000
        with self.assertRaisesRegex(PipelineError, "pass2_context_reservation_too_small"):
            compute_deep_v2_quote(verified_source(), m)


if __name__ == "__main__":
    unittest.main()
