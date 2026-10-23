"""Deterministic, zero-cost Generation Engine tests; no PostgreSQL required."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intelligence.search_planner.generation_engine import (
    make_batches, RunState, BatchState, AttemptState, require_transition,
    validate_generation_config,
)
from intelligence.search_planner.generation_engine.contracts import (
    RUN_TRANSITIONS, BATCH_TRANSITIONS, ATTEMPT_TRANSITIONS,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.search_planner import build_hypothesis_contexts

from test_search_planner_foundation import snapshot, POLICY


def config():
    return {
        "schema_version": "search-generation-config/v1",
        "search_policy": copy.deepcopy(POLICY),
        "model": {
            "provider": "openrouter",
            "model_id": "anthropic/claude-opus-5.5",
            "catalog_snapshot_id": 1,
            "model_catalog_sha256": "a" * 64,
            "prompt_version": "ai-query-v1",
            "prompt_sha256": "b" * 64,
            "raw_schema_version": "ai-query-raw/v1",
            "raw_schema_sha256": "c" * 64,
            "strategy_version": "facets-v1",
            "max_output_tokens": 8000,
        },
        "execution": {
            "target_hypotheses_per_batch": 4,
            "hard_max_hypotheses_per_batch": 5,
            "max_input_bytes_per_batch": 200000,
            "max_concurrent_provider_calls": 1,
            "automatic_paid_retries": 0,
            "samples_per_batch": 1,
        },
    }


class EngineContractTests(unittest.TestCase):
    def test_structural_config_valid_and_immutable_result(self):
        inp = config()
        actual = validate_generation_config(inp)
        self.assertEqual(actual, inp)
        actual["search_policy"]["languages"].append("de")
        self.assertEqual(inp["search_policy"]["languages"], ["ru", "en"])

    def test_bad_model_or_execution_rejected(self):
        cases = (
            ("model", "provider", "other"),
            ("model", "catalog_snapshot_id", "uuid-not-integer"),
            ("model", "prompt_sha256", "xyz"),
            ("model", "max_output_tokens", 0),
            ("execution", "max_concurrent_provider_calls", 2),
            ("execution", "automatic_paid_retries", 1),
            ("execution", "samples_per_batch", 2),
            ("execution", "max_input_bytes_per_batch", 0),
        )
        for section, field, value in cases:
            with self.subTest(section=section, field=field):
                bad = config()
                bad[section][field] = value
                with self.assertRaises(PipelineError):
                    validate_generation_config(bad)
        bad = config(); bad["extra"] = "not in contract"
        with self.assertRaises(PipelineError):
            validate_generation_config(bad)

    def test_transition_graphs_fail_closed(self):
        require_transition("created", "awaiting_authorization", RUN_TRANSITIONS)
        require_transition("pending", "reserved", BATCH_TRANSITIONS)
        require_transition("reserved", "dispatch_intent", ATTEMPT_TRANSITIONS)
        for graph, src, dst in (
            (RUN_TRANSITIONS, "completed", "running"),
            (RUN_TRANSITIONS, "bogus", "queued"),
            (RUN_TRANSITIONS, "awaiting_authorization", "completed"),
            (BATCH_TRANSITIONS, "pending", "completed"),
            (ATTEMPT_TRANSITIONS, "uncertain", "dispatch_intent"),
        ):
            with self.subTest(src=src, dst=dst):
                with self.assertRaises(PipelineError):
                    require_transition(src, dst, graph)

    def test_batches_preserve_context_and_direction(self):
        contexts = build_hypothesis_contexts(snapshot(), POLICY)
        before = copy.deepcopy(contexts)
        planned = make_batches(contexts, config())
        self.assertEqual(len(planned), 2)
        self.assertEqual(sum(len(b["hypothesis_ids"]) for b in planned), 3)
        self.assertEqual([b["batch_index"] for b in planned], [0, 1])
        self.assertEqual(contexts, before)
        for b in planned:
            self.assertEqual(len(set(b["hypothesis_ids"])), len(b["hypothesis_ids"]))
            self.assertEqual(b["output_token_cap"], 8000)
            self.assertEqual(len(b["input_sha256"]), 64)
        self.assertEqual(planned, make_batches(list(reversed(contexts)), config()))

    def test_batch_splits_by_count_for_24_hypotheses(self):
        seed = build_hypothesis_contexts(snapshot(), POLICY)[0]
        examples = []
        for i in range(24):
            ctx = copy.deepcopy(seed)
            hyp_id = "hyp_%02d" % i
            ctx["hypothesis"]["id"] = hyp_id
            ctx["ancestry_path"][-1] = hyp_id
            examples.append(ctx)
        batches = make_batches(examples, config())
        self.assertEqual(len(batches), 6)
        self.assertEqual([len(b["hypothesis_ids"]) for b in batches], [4] * 6)

    def test_no_duplicate_id_or_truncation(self):
        seed = build_hypothesis_contexts(snapshot(), POLICY)[0]
        with self.assertRaisesRegex(PipelineError, "Duplicate"):
            make_batches([seed, copy.deepcopy(seed)], config())
        bad = config()
        bad["execution"]["max_input_bytes_per_batch"] = 4500
        with self.assertRaisesRegex(PipelineError, "context_too_large"):
            make_batches([seed], bad)
        self.assertGreater(len(seed["hypothesis"]["definition"]), 0)

    def test_changed_config_changes_batch_fingerprint(self):
        contexts = build_hypothesis_contexts(snapshot(), POLICY)
        original = make_batches(contexts, config())
        different = config()
        different["model"]["prompt_sha256"] = "d" * 64
        changed = make_batches(contexts, different)
        self.assertNotEqual(original[0]["input_sha256"], changed[0]["input_sha256"])

    def test_no_provider_dependency_in_planner(self):
        import inspect
        from intelligence.search_planner.generation_engine import run_manager
        from intelligence.search_planner.generation_engine import batch_manager
        source = inspect.getsource(run_manager) + inspect.getsource(batch_manager)
        self.assertNotIn("requests.post", source)
        self.assertNotIn("openrouter.invoke", source)
        self.assertNotIn("apify", source.lower())
        self.assertNotIn("brightdata", source.lower())


if __name__ == "__main__":
    unittest.main()
