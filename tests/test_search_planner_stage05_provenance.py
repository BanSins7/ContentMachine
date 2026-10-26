"""SP-05 no-network AI Query and Output Manager provenance regression.

These checks are offline and cannot prove correctness of a real OpenRouter
bill or of actual source-video retrieval. They enforce consistency at the
PostgreSQL settlement and read-to-QV boundaries.
"""
from __future__ import annotations

import copy
import unittest
from unittest import mock

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.search_planner.ai_query.provenance import (
    verify_candidates_bundle, candidate_manifest_sha256,
)
from intelligence.search_planner.generation_engine.output_manager import (
    _load_contexts, _validate_provider_reply,
)
from intelligence.search_planner.generation_engine.provider_port import (
    ProviderResponse,
)
from test_search_planner_stage_d import contexts, pinned_model, fake_raw
from test_search_planner_foundation import POLICY
from test_search_planner_engine_contracts import config
from intelligence.taxonomy_builder.pipeline_contracts import sha256

GEN = "55555555-5555-4555-8555-555555555555"
PUB = "22222222-2222-4222-8222-222222222222"
CAND = "77777777-7777-4777-8777-777777777777"
PID = "a" * 32


def bundle():
    return {
        "schema_version": "query_candidates/v1", "project_id": PID,
        "generation_id": GEN, "taxonomy_publication_id": PUB,
        "taxonomy_version": 3, "input_sha256": "a" * 64,
        "prompt_version": "ai-query-v1", "model_id": "anthropic/claude-opus-5.5",
        "status": "completed",
        "candidates": [{
            "candidate_id": CAND, "hypothesis_id": "short",
            "direction_id": "home", "branch_id": "bodyweight",
            "source_fact_ids": ["fact_1", "fact_2"],
            "text": "домашние упражнения",
            "query_mode": "keyword", "angle": "direct", "language": "ru",
            "research_stance": "neutral", "rationale": "проверка гипотезы",
        }],
        "hypothesis_coverage": [
            {"hypothesis_id": "short", "state": "generated",
             "candidate_count": 1, "reason": None},
            {"hypothesis_id": "morning", "state": "no_viable_queries",
             "candidate_count": 0, "reason": "недостаточно конкретного контекста"},
        ],
    }


class DBRows:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []
    def execute(self, stmt, args):
        self.sql.append((stmt, args))
        return self
    def fetchall(self):
        return self.rows


class Stage05Integrity(unittest.TestCase):
    def test_valid_completed_bundle_round_trip_and_no_mutation(self):
        raw = bundle()
        previous = copy.deepcopy(raw)
        result = verify_candidates_bundle(raw)
        self.assertEqual(raw, previous)
        self.assertEqual(result, previous)
        result["candidates"][0]["text"] = "tampered"
        self.assertEqual(raw, previous)
        self.assertEqual(candidate_manifest_sha256(raw),
                         candidate_manifest_sha256(copy.deepcopy(raw)))

    def test_all_hypotheses_no_viable_is_valid_zero_work(self):
        original = bundle()
        original["candidates"].clear()
        original["hypothesis_coverage"][0] = {
            "hypothesis_id": "short", "state": "no_viable_queries",
            "candidate_count": 0, "reason": "не подтверждено",}
        self.assertEqual(verify_candidates_bundle(original)["candidates"], [])

    def test_each_corrupt_source_count_fails_closed(self):
        base = bundle()
        variants = []
        def change(f):
            raw = copy.deepcopy(base)
            f(raw)
            variants.append(raw)
        change(lambda b: b["hypothesis_coverage"][0].__setitem__("candidate_count", 2))
        change(lambda b: b["hypothesis_coverage"].pop(0))
        change(lambda b: b["hypothesis_coverage"].append(copy.deepcopy(b["hypothesis_coverage"][0])))
        change(lambda b: b["candidates"][0].__setitem__("hypothesis_id", "foreign_hypothesis"))
        change(lambda b: b["candidates"].append(copy.deepcopy(b["candidates"][0])))
        change(lambda b: b["hypothesis_coverage"][0].__setitem__("state", "no_viable_queries"))
        change(lambda b: b["hypothesis_coverage"][1].__setitem__("reason", ""))
        for data in variants:
            with self.subTest(data=data), self.assertRaises(PipelineError):
                verify_candidates_bundle(data)

    def test_forbidden_status_schema_and_pins(self):
        base = bundle()
        for field, value in (
            ("schema_version", "validated_query_set/v1"),
            ("status", "running"),
            ("taxonomy_version", 0),
            ("generation_id", "fake"),
            ("taxonomy_publication_id", "fake"),
            ("input_sha256", "NaN"),
            ("model_id", ""),
            ("prompt_version", None),
            ("project_id", ""),
        ):
            raw = copy.deepcopy(base)
            raw[field] = value
            with self.subTest(field=field), self.assertRaises(PipelineError):
                verify_candidates_bundle(raw)

    def test_no_duplicate_candidate_uuids_or_fake_fact_lineage(self):
        for field, value in (
            ("candidate_id", "not-uuid"),
            ("query_mode", "search_posts"),
            ("text", " "),
            ("rationale", ""),
            ("source_fact_ids", ["fact_1", "fact_1"]),
            ("source_fact_ids", "fact_1"),
        ):
            raw = bundle()
            raw["candidates"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(PipelineError):
                verify_candidates_bundle(raw)

    def test_complete_contexts_checksum_verified_before_settlement(self):
        ctx = contexts()[0]
        db = DBRows([(ctx["hypothesis"]["id"], ctx, sha256(ctx))])
        result = _load_contexts(db, GEN, [ctx["hypothesis"]["id"]])
        self.assertEqual(result, [ctx])
        self.assertEqual(db.sql[0][1], (GEN,))

    def test_corrupt_or_duplicate_stored_context_fails_closed(self):
        ctx = contexts()[0]
        hid = ctx["hypothesis"]["id"]
        with self.assertRaisesRegex(PipelineError, "checksum"):
            _load_contexts(DBRows([(hid, ctx, "0" * 64)]), GEN, [hid])
        with self.assertRaisesRegex(PipelineError, "checksum"):
            _load_contexts(DBRows([(hid, ctx, sha256(ctx)),
                                   (hid, ctx, sha256(ctx))]), GEN, [hid])

    def test_pinned_stage_d_trace_audited_at_settlement(self):
        ctxs = contexts()
        model = pinned_model()
        cfg = config()
        cfg["model"] = model
        reply = ProviderResponse(
            raw_json=fake_raw(ctxs), charged_usd="0.00000001",
            model_id=model["model_id"], provider_response_id="mock-123",
            finish_reason="stop",
            usage={"prompt_tokens": 100, "completion_tokens": 120,
                   "cost": "0.00000001"},
        )
        self.assertEqual(str(_validate_provider_reply(reply, cfg, ctxs)),
                         "1E-8")
        invalid = copy.deepcopy(reply.raw_json)
        invalid["hypotheses"][0]["queries"][0]["text"] = "fyp"
        altered = ProviderResponse(
            raw_json=invalid, charged_usd=reply.charged_usd,
            model_id=reply.model_id, provider_response_id=reply.provider_response_id,
            finish_reason="stop", usage=reply.usage,
        )
        with self.assertRaises(PipelineError):
            _validate_provider_reply(altered, cfg, ctxs)

    def test_pin_tampering_blocks_settlement_before_publishing(self):
        ctxs = contexts()
        cfg = config()
        cfg["model"] = pinned_model()
        cfg["model"]["raw_schema_sha256"] = "c" * 64
        response = ProviderResponse(
            raw_json=fake_raw(ctxs), charged_usd="0.00000001",
            model_id=cfg["model"]["model_id"], provider_response_id="mock",
            usage={"prompt_tokens": 1, "completion_tokens": 1,
                   "cost": "0.00000001"},
        )
        with self.assertRaises(PipelineError):
            _validate_provider_reply(response, cfg, ctxs)

    def test_raw_model_coverage_refuses_missing_hypothesis(self):
        ctxs = contexts()
        cfg = config()
        cfg["model"] = pinned_model()
        raw = fake_raw(ctxs)
        raw["hypotheses"].pop()
        response = ProviderResponse(
            raw_json=raw, charged_usd="0.00000001",
            model_id=cfg["model"]["model_id"], provider_response_id="mock",
            usage={"prompt_tokens": 1, "completion_tokens": 1,
                   "cost": "0.00000001"},
        )
        with self.assertRaises(PipelineError):
            _validate_provider_reply(response, cfg, ctxs)


if __name__ == "__main__":
    unittest.main()
