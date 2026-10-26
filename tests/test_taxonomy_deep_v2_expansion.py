"""Offline contract for explicit Deep V2 expansion prompts, no DB or LLM."""
from __future__ import annotations

from copy import deepcopy
import json
import unittest

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.taxonomy_builder.deep_v2_transport import (
    MAX_H1_BYTES, v2_messages, v2_request,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from test_taxonomy_deep_v2_validation import sample
from test_taxonomy_deep_v2_lifecycle import model


class ExpansionPromptTests(unittest.TestCase):
    def setUp(self):
        self.source, self.review = sample()
        self.quality = {
            "passed": False,
            "gaps": [{"reason": "Need a more specific test"}],
            "h1_is_locked": True,
            "last_tree_is_locked": True,
            "explicit_expansion_number": 1,
            "rejected_pass1_node_ids": [],
        }

    def test_expansion_prompt_carries_prior_tree_and_original_approved_h1(self):
        stage, payload = v2_request(
            self.source, model(), role="deep_expand",
            review=self.review, coverage=self.quality,
            expansion_base=self.review,
        )
        self.assertEqual(stage["role"], "deep_expand")
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertEqual(payload["provider"]["allow_fallbacks"], False)
        self.assertFalse(payload["stream"])
        user = json.loads(payload["messages"][-1]["content"])
        self.assertEqual(user["review1"], self.review)
        self.assertEqual(user["last_approved_or_generated_tree"], self.review)
        self.assertEqual(user["coverage"], self.quality)
        self.assertEqual(user["research_input_sha256"],
                         self.source["research_input_sha256"])
        self.assertEqual(payload, v2_request(
            self.source, model(), role="deep_expand", review=self.review,
            coverage=self.quality, expansion_base=self.review)[1])

    def test_expansion_base_required_and_not_allowed_in_other_passes(self):
        with self.assertRaisesRegex(PipelineError, "expansion_base_required"):
            v2_messages(self.source, role="deep_expand",
                        review=self.review, coverage=self.quality)
        for role in ("deep_first", "deep_second"):
            with self.subTest(role=role), self.assertRaises(PipelineError):
                v2_messages(self.source, role=role, review=self.review,
                            coverage=self.quality, expansion_base=self.review)

    def test_context_limit_fails_closed_without_truncation(self):
        excessive = deepcopy(self.review)
        excessive[0]["definition"] = "x" * (MAX_H1_BYTES + 1)
        with self.assertRaisesRegex(PipelineError, "expansion_tree_too_large"):
            v2_messages(self.source, role="deep_expand", review=self.review,
                        coverage=self.quality, expansion_base=excessive)

    def test_expansion_request_identity_changes_with_prior_approved_tree(self):
        _, a = v2_request(
            self.source, model(), role="deep_expand",
            review=self.review, coverage=self.quality,
            expansion_base=self.review)
        newer = deepcopy(self.review)
        newer[1]["search_signals"] = ["New unique testable format"]
        _, b = v2_request(
            self.source, model(), role="deep_expand",
            review=self.review, coverage=self.quality, expansion_base=newer)
        self.assertNotEqual(canonical_sha(a), canonical_sha(b))
        self.assertNotEqual(canonical_sha(a["messages"]),
                            canonical_sha(b["messages"]))


if __name__ == "__main__":
    unittest.main()
