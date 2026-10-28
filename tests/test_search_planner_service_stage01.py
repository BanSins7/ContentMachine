"""SP-01 standalone service, strict identities and injected-port QA.

No database, provider key, model download, network request or Collector module
is required. Existing SP1/SP2 fixtures exercise actual current C9 V1 shape.
"""
from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from intelligence.search_planner import (
    SearchPlannerService, SearchPlannerOptions, SERVICE_CONTRACT_VERSION,
    load_approved_publication, build_hypothesis_contexts,
    ContextPreview, GenerationStatus, ValidatedQueriesView,
)
from intelligence.search_planner.api_contracts import (
    CONTEXT_VIEW_SCHEMA, GENERATION_VIEW_SCHEMA, VALIDATED_VIEW_SCHEMA,
)
from intelligence.search_planner.configuration import MAX_C9_HYPOTHESES
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from test_search_planner_foundation import (
    PID, PUB, RID, POLICY, snapshot,
)

GEN = "55555555-5555-4555-8555-555555555555"
QV = "66666666-6666-4666-8666-666666666666"
CAND = "77777777-7777-4777-8777-777777777777"


def service_with_fixture(**kwargs):
    return SearchPlannerService(
        publication_reader=lambda pid, pub: snapshot(),
        **kwargs,
    )


def valid_bundle():
    return {
        "schema_version": "validated_queries/v1",
        "validation_run_id": QV,
        "generation_id": GEN,
        "project_id": PID,
        "embedding_mode": "semantic_local",
        "embedding_model_key": "pinned-multilingual-model",
        "config_sha256": "a" * 64,
        "source_bundle_sha256": "b" * 64,
        "status": "completed",
        "queries": [{
            "candidate_id": CAND, "hypothesis_id": "short",
            "direction_id": "home", "branch_id": "bodyweight",
            "source_fact_ids": [], "text": "домашняя тренировка",
            "query_mode": "keyword", "angle": "exercise",
            "language": "ru", "research_stance": "discovery",
            "rationale": "test", "relevance_score": 0.8,
            "negative_score": 0.1, "mmr_score": 0.6,
            "rank_in_hypothesis": 1,
        }],
        "coverage": [{"hypothesis_id": "short", "source_count": 1,
                      "selected_count": 1, "outcome": "selected", "reason": None}],
        "decisions": [{"candidate_id": CAND, "decision": "selected",
                       "reason_code": "selected", "duplicate_of": None,
                       "positive_similarity": 0.8, "negative_similarity": 0.1,
                       "score_margin": 0.7, "mmr_score": 0.6}],
    }


class SearchPlannerServiceStage01(unittest.TestCase):
    def test_historical_exports_unchanged(self):
        self.assertTrue(callable(load_approved_publication))
        self.assertTrue(callable(build_hypothesis_contexts))
        self.assertEqual(SERVICE_CONTRACT_VERSION, "search-planner-service/v1")

    def test_facade_preview_full_c9_v1_and_determinism(self):
        original = snapshot()
        calls = []
        def read(pid, pub):
            calls.append((pid, pub))
            return copy.deepcopy(original)

        svc = SearchPlannerService(publication_reader=read)
        policy = copy.deepcopy(POLICY)
        view = svc.preview_contexts(PID, PUB, policy)
        self.assertIsInstance(view, ContextPreview)
        out = view.to_dict()
        self.assertEqual(out["schema_version"], CONTEXT_VIEW_SCHEMA)
        self.assertEqual(out["context_count"], 3)
        self.assertEqual(out["contexts_sha256"], sha256(out["contexts"]))
        self.assertEqual(out["taxonomy_sha256"], out["contexts"][0]["taxonomy_sha256"])
        self.assertEqual({c["hypothesis"]["id"] for c in out["contexts"]},
                         {"short", "morning", "time_hyp"})
        self.assertEqual(svc.preview_contexts(PID, PUB, POLICY).contexts_sha256,
                         view.contexts_sha256)
        self.assertEqual(calls, [(PID, PUB), (PID, PUB)])
        self.assertEqual(policy, POLICY)
        self.assertEqual(original, snapshot())
        self.assertEqual(out["provider_requests"], 0)
        self.assertFalse(out["spending_authorized"])

    def test_context_view_is_frozen_and_output_isolation(self):
        view = service_with_fixture().preview_contexts(PID, PUB, POLICY)
        a = view.to_dict()
        a["contexts"][0]["hypothesis"]["id"] = "forged"
        a["contexts"].clear()
        b = view.to_dict()
        self.assertEqual(b["context_count"], 3)
        self.assertNotEqual(b["contexts"][0]["hypothesis"]["id"], "forged")
        with self.assertRaises(FrozenInstanceError):
            view.context_count = 1

    def test_preview_rejects_foreign_unapproved_or_forged_reader(self):
        bad = snapshot()
        bad["project_id"] = "b" * 32
        with self.assertRaises(PipelineError):
            SearchPlannerService(publication_reader=lambda *_: bad).preview_contexts(
                PID, PUB, POLICY)
        bad = snapshot()
        bad["human_reviews"] = [1]
        with self.assertRaises(PipelineError):
            SearchPlannerService(publication_reader=lambda *_: bad).preview_contexts(
                PID, PUB, POLICY)
        bad = snapshot()
        bad["taxonomy_publication_id"] = RID
        with self.assertRaises(PipelineError):
            SearchPlannerService(publication_reader=lambda *_: bad).preview_contexts(
                PID, PUB, POLICY)

    def test_context_builder_wrong_project_hash_or_duplicate_rejected(self):
        contexts = build_hypothesis_contexts(snapshot(), POLICY)
        for modify in (
            lambda x: x[0].__setitem__("project_id", "b" * 32),
            lambda x: x[0].__setitem__("taxonomy_sha256", "f" * 64),
            lambda x: x[1]["hypothesis"].__setitem__("id", x[0]["hypothesis"]["id"]),
        ):
            forged = copy.deepcopy(contexts)
            modify(forged)
            svc = service_with_fixture(context_builder=lambda *_: forged)
            with self.assertRaises(PipelineError):
                svc.preview_contexts(PID, PUB, POLICY)

    def test_invalid_input_aborts_before_injected_read(self):
        events = []
        svc = SearchPlannerService(publication_reader=lambda *_: events.append(1))
        with self.assertRaises(PipelineError):
            svc.preview_contexts(PID, "incorrect-uuid", POLICY)
        with self.assertRaises(PipelineError):
            svc.preview_contexts("unknown-project", PUB, POLICY)
        with self.assertRaises(PipelineError):
            svc.preview_contexts(PID, PUB, {"languages": ["ru"]})
        self.assertEqual(events, [])

    def test_preview_bound_and_runtime_options(self):
        self.assertEqual(SearchPlannerOptions().max_preview_hypotheses,
                         MAX_C9_HYPOTHESES)
        with self.assertRaises(PipelineError):
            SearchPlannerOptions(max_preview_hypotheses=True)
        with self.assertRaises(PipelineError):
            SearchPlannerOptions(max_preview_hypotheses=49)
        with self.assertRaises(PipelineError):
            SearchPlannerOptions.from_mapping({"collector_enabled": True})
        with self.assertRaises(PipelineError):
            SearchPlannerService(options={})
        svc = service_with_fixture(options=SearchPlannerOptions(2))
        with self.assertRaisesRegex(PipelineError, "count"):
            svc.preview_contexts(PID, PUB, POLICY)

    def test_falsy_reader_is_not_silently_replaced_by_database_reader(self):
        class FalsyReader:
            def __bool__(self):
                return False
            def __call__(self, pid, pub):
                return snapshot()
        svc = SearchPlannerService(publication_reader=FalsyReader())
        self.assertEqual(svc.preview_contexts(PID, PUB, POLICY).context_count, 3)
        with self.assertRaises(PipelineError):
            SearchPlannerService(publication_reader=False)

    def test_generation_status_has_identity_and_no_mutation(self):
        captured = []
        def read(pid, gid):
            captured.append((pid, gid))
            return {
                "generation_id": gid, "taxonomy_publication_id": PUB,
                "input_sha256": "c" * 64, "status": "awaiting_authorization",
                "hypothesis_count": 3, "batch_count": 2,
            }
        svc = SearchPlannerService(generation_reader=read)
        data = svc.get_generation(PID, GEN).to_dict()
        self.assertEqual(data["schema_version"], GENERATION_VIEW_SCHEMA)
        self.assertEqual(data["project_id"], PID)
        self.assertEqual(data["status"], "awaiting_authorization")
        self.assertEqual(captured, [(PID, GEN)])
        with self.assertRaises(PipelineError):
            SearchPlannerService(generation_reader=lambda *_: {
                **read(PID, GEN), "generation_id": QV,
            }).get_generation(PID, GEN)
        with self.assertRaises(PipelineError):
            SearchPlannerService(generation_reader=lambda *_: {
                **read(PID, GEN), "batch_count": True,
            }).get_generation(PID, GEN)

    def test_validated_view_requires_db_reader_and_real_semantic(self):
        captured = []
        def read(pid, vid, *, include_test=False):
            captured.append((pid, vid, include_test))
            return valid_bundle()
        svc = SearchPlannerService(validated_bundle_reader=read)
        view = svc.get_validated_queries(PID, QV)
        self.assertIsInstance(view, ValidatedQueriesView)
        data = view.to_dict()
        self.assertEqual(data["schema_version"], VALIDATED_VIEW_SCHEMA)
        self.assertEqual(data["selected_count"], 1)
        self.assertEqual(data["bundle_sha256"], sha256(data["bundle"]))
        self.assertFalse(data["external_search_dispatched_by_this_read"])
        self.assertEqual(captured, [(PID, QV, False)])
        data["bundle"]["queries"].clear()
        self.assertEqual(view.to_dict()["selected_count"], 1)
        for field, value in (
            ("embedding_mode", "test_fixture"),
            ("status", "pending"),
            ("project_id", "b" * 32),
            ("validation_run_id", GEN),
            ("schema_version", "validated_query_set/v1"),
        ):
            bad = valid_bundle()
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(PipelineError):
                SearchPlannerService(validated_bundle_reader=lambda *a, **k: bad).get_validated_queries(PID, QV)

    def test_validated_view_duplicate_candidate_fails(self):
        bad = valid_bundle()
        bad["queries"].append(copy.deepcopy(bad["queries"][0]))
        with self.assertRaises(PipelineError):
            SearchPlannerService(validated_bundle_reader=lambda *a, **k: bad).get_validated_queries(PID, QV)

    def test_unavailable_collector_status_has_no_side_effects(self):
        events = []
        svc = SearchPlannerService(
            publication_reader=lambda *_: events.append("publication"),
            generation_reader=lambda *_: events.append("generation"),
            validated_bundle_reader=lambda *a, **k: events.append("validation"),
        )
        status = svc.status().to_dict()
        self.assertEqual(status["collector"]["state"], "unavailable")
        self.assertFalse(status["collector"]["external_search_ready"])
        self.assertEqual(status["paid_calls_in_this_operation"], 0)
        self.assertEqual(events, [])
        self.assertEqual(json.loads(json.dumps(status)), status)

    def test_new_public_package_import_does_not_require_collector(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = """
import builtins, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'intelligence.collector' or name.startswith('intelligence.collector.'):
        raise AssertionError('Unexpected Collector runtime import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from intelligence.search_planner import SearchPlannerService, SearchPlannerOptions
service = SearchPlannerService()
assert service.status().to_dict()['collector']['state'] == 'unavailable'
assert SearchPlannerOptions().max_preview_hypotheses == 48
assert 'intelligence.collector' not in sys.modules
"""
        p = subprocess.run(
            [sys.executable, "-c", script], cwd=repo_root,
            check=False, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(p.returncode, 0, p.stdout + "\n" + p.stderr)


if __name__ == "__main__":
    unittest.main()
