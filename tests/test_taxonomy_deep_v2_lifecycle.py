"""RTB-FIX-02 safety preflight tests. No requests, no SQL or paid API calls."""
from __future__ import annotations

from copy import deepcopy
import unittest

from intelligence.taxonomy_builder.deep_v2_lifecycle import (
    BILLABLE_STAGES, MAX_EXPANSIONS, MODEL_ID, PLAN_VERSION,
    prepare_deep_v2_preflight, stage_permission, validate_frozen_input,
    validate_opus_catalog,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.project_sources.context_builder import sha as canonical_sha
from test_taxonomy_research_input import source_fixture, build


def model():
    return {
        "model_id": MODEL_ID,
        "catalog_version_id": 4,
        "catalog_sha256": "b" * 64,
        "capabilities": {
            "model_id": MODEL_ID,
            "text_output": True,
            "supports_structured_outputs": True,
            "max_output_tokens": 16384,
            "context_length": 180000,
        },
        "pricing": {
            "prompt": "0.000005", "completion": "0.000025",
            "request": "0.001", "internal_reasoning": "0.000002",
        },
    }


def source():
    return build(*source_fixture())


class DeepV2PreflightTests(unittest.TestCase):
    def test_pins_full_research_sha_and_model_with_no_authorization(self):
        inp = source()
        p = prepare_deep_v2_preflight(inp, model())
        self.assertEqual(p["schema_version"], PLAN_VERSION)
        self.assertEqual(p["research_input_sha256"], inp["research_input_sha256"])
        self.assertEqual(p["model_id"], MODEL_ID)
        self.assertEqual(p["planned_required_passes"], list(BILLABLE_STAGES[:2]))
        self.assertEqual(p["possible_optional_expansions"], list(BILLABLE_STAGES[2:]))
        self.assertEqual(p["human_reviews"], ["review1", "review2"])
        self.assertFalse(p["quote_created"])
        self.assertFalse(p["user_budget_consent_granted"])
        self.assertFalse(p["provider_dispatch_allowed"])
        self.assertEqual(p["provider_calls"], 0)
        self.assertEqual(p["preflight_sha256"], canonical_sha(
            {key: value for key, value in p.items() if key != "preflight_sha256"}))
        self.assertEqual(p, prepare_deep_v2_preflight(inp, model()))

    def test_source_tampering_rejected(self):
        inp = source()
        for tamper in (
            lambda x: x["c1_approved_facts"][0].update({"reviewed_text": "forged"}),
            lambda x: x.update({"research_input_sha256": "f" * 64}),
            lambda x: x.update({"paid_execution_authorized": True}),
            lambda x: x["c2_original_video_observations"].pop(),
        ):
            with self.subTest(tamper=str(tamper)):
                changed = deepcopy(inp)
                tamper(changed)
                with self.assertRaises(PipelineError):
                    validate_frozen_input(changed)

    def test_source_is_not_valid_without_signed_hash(self):
        inp = source()
        del inp["research_input_sha256"]
        with self.assertRaises(PipelineError):
            prepare_deep_v2_preflight(inp, model())

    def test_only_pinned_opus_55_not_generic_models(self):
        inp = source()
        for name in ("anthropic/claude-sonnet-4.6", "anthropic/claude-opus-4.6", ""):
            m = model()
            m["model_id"] = name
            with self.assertRaisesRegex(PipelineError, "requires_opus_55"):
                prepare_deep_v2_preflight(inp, m)

    def test_missing_catalog_and_structured_output_rejected(self):
        for change in (
            lambda m: m.update({"catalog_version_id": 0}),
            lambda m: m.update({"catalog_sha256": "not-a-sha"}),
            lambda m: m["capabilities"].update({"supports_structured_outputs": False}),
            lambda m: m["capabilities"].update({"text_output": False}),
            lambda m: m["capabilities"].update({"context_length": 1}),
        ):
            with self.subTest(change=str(change)):
                m = model()
                change(m)
                with self.assertRaises(PipelineError):
                    validate_opus_catalog(m, max_output_tokens=8192)

    def test_max_tokens_are_bounded_and_bool_rejected(self):
        for value in (True, None, 0, 4095, 16001, 8192.0):
            with self.subTest(value=value):
                with self.assertRaises(PipelineError):
                    prepare_deep_v2_preflight(source(), model(), max_output_tokens=value)

    def test_stale_or_conditional_pricing_fails_closed(self):
        for corrupted in (
            {"prompt": "bad", "completion": "0.01"},
            {"prompt": "0", "completion": "0", "request": "0"},
            {"prompt": "0.000005", "completion": "0.000025",
             "overrides": {"long_context": "custom"}},
        ):
            with self.subTest(price=corrupted):
                m = model()
                m["pricing"] = corrupted
                with self.assertRaises(PipelineError):
                    prepare_deep_v2_preflight(source(), m)

    def test_context_capacity_is_conservatively_checked(self):
        m = model()
        m["capabilities"]["context_length"] = 8200
        with self.assertRaisesRegex(PipelineError, "context_too_small"):
            prepare_deep_v2_preflight(source(), m)

    def test_user_interaction_required_for_every_paid_pass(self):
        stages = [
            ("pass1_pending", 0, "deep_first"),
            ("pass2_pending", 0, "deep_second"),
            ("expand_pending", 0, "deep_expand_1"),
            ("expand_pending", 1, "deep_expand_2"),
        ]
        for status, count, stage in stages:
            with self.subTest(status=status, count=count):
                permission = stage_permission(status, count, consent_active=True)
                self.assertEqual(permission["next_action"], stage)
                self.assertFalse(permission["paid_dispatch_allowed"])
                self.assertTrue(permission["requires_fresh_user_action"])
        self.assertEqual(MAX_EXPANSIONS, 2)
        with self.assertRaisesRegex(PipelineError, "expansion_limit_reached"):
            stage_permission("expand_pending", 2, consent_active=True)

    def test_human_review_cannot_trigger_paid_provider(self):
        for state, expected in (("review1", "human_review_1"),
                                ("review2", "human_review_2")):
            p = stage_permission(state, 0, consent_active=True)
            self.assertEqual(p["next_action"], expected)
            self.assertFalse(p["paid_dispatch_allowed"])

    def test_no_consent_and_uncertain_spend_disable_dispatch(self):
        for status in ("pass1_pending", "pass2_pending", "expand_pending", "review1"):
            p = stage_permission(status, 0, consent_active=False)
            self.assertEqual(p["next_action"], "new_explicit_consent")
            self.assertFalse(p["paid_dispatch_allowed"])
        for state in ("approved", "failed", "uncertain", "blocked_quality", "cancelled"):
            p = stage_permission(state, 0, consent_active=True)
            self.assertTrue(p["terminal"])
            self.assertIsNone(p["next_action"])

    def test_unknown_state_and_bad_expansion_count_are_blocked(self):
        for name, count in (("pass1_pending", -1), ("pass1_pending", 3),
                            ("pass1_pending", True), ("automatic_retry", 0)):
            with self.assertRaises(PipelineError):
                stage_permission(name, count, consent_active=True)


if __name__ == "__main__":
    unittest.main()
