"""SRE S1 contract tests: pure offline fixtures, no PostgreSQL, LLM or network."""
from __future__ import annotations

import copy
import math
import unittest
from datetime import datetime, timezone
from uuid import UUID, uuid4, uuid5

from intelligence.relevance.presets import strong_only
from intelligence.relevance.v1_contracts import (
    CONTRACT_VERSIONS, SREContractError, authorize_content_usage,
    canonical_json, fingerprint, validate_content_input, validate_model_output,
    validate_retrieval_scores, validate_transition,
)
from intelligence.relevance.v1_runtime import (
    validate_analytics_event, validate_cost_quote, validate_pipeline_snapshot,
    validate_run_plan, validate_spending_consent, validate_spending_position,
)
from intelligence.relevance.v1_taxonomy import validate_taxonomy_snapshot


H = "a" * 64
HH = "b" * 64
PID = "0123456789abcdef0123456789abcdef"
RID = "11111111-1111-4111-8111-111111111111"
PUB = "22222222-2222-4222-8222-222222222222"
QID = "33333333-3333-4333-8333-333333333333"
CID = "44444444-4444-4444-8444-444444444444"
K1 = str(uuid5(UUID(RID), "h1"))
K2 = str(uuid5(UUID(RID), "h2"))


def content():
    return {
        "schema_version": CONTRACT_VERSIONS["content"],
        "project_id": PID,
        "video_id": 29,
        "source_id": "collector_tiktok",
        "source_policy_version_id": 10,
        "content_version_id": 55,
        "content_sha256": H,
        "completeness": "metadata_only",
        "rights": {
            "can_store_metadata": True,
            "can_semantic_classify": True,
            "can_external_llm": False,
            "can_analyze_media": False,
        },
        "data": {"title": "Калории в кофе", "description": "Разбор напитков",
                 "hashtags": ["coffee"], "transcript": None, "scenes": None},
        "provenance": {"discovery_id": 1, "search_query_id": None,
                       "source_request_id": 4},
        "media_asset_id": None,
        "expires_at": "2026-11-23T00:00:00Z",
    }


def nodes_and_facts():
    f1, f2 = str(uuid4()), str(uuid4())
    facts = [{"id": f1, "kind": "function", "text": "Счётчик калорий"},
             {"id": f2, "kind": "problem", "text": "Скрытые калории"}]
    def node(name, key, parent, kind, relation, facts):
        return {"id": key, "parent_id": parent, "kind": kind,
                "relation": relation, "source_fact_ids": facts,
                "name": name, "rationale": "Модель исследования темы",
                "definition": "Практический обучающий контент", "include": "Разбор темы",
                "exclude": "Посторонние видео", "examples": ["Практический пример"]}
    tree = [
        node("Питание", "dir_a", None, "direction", "direct", [f1]),
        node("Калории кофе", "h1", "dir_a", "hypothesis", "direct", [f1]),
        node("Проблемы питания", "dir_b", None, "direction", "problem_related", [f2]),
        node("Неверная оценка еды", "h2", "dir_b", "hypothesis", "problem_related", [f2]),
    ]
    return tree, facts


def taxonomy():
    tree, facts = nodes_and_facts()
    return {"schema_version": CONTRACT_VERSIONS["taxonomy"],
            "project_id": PID, "publication_id": PUB, "rtb_run_id": RID,
            "taxonomy_version": 3, "sre_taxonomy_version_id": 14,
            "tree_sha256": fingerprint(tree), "human_reviews": [1, 2],
            "approved_facts": facts, "nodes": tree,
            "hypothesis_links": [{"source_node_id": "h1", "category_key": K1},
                                 {"source_node_id": "h2", "category_key": K2}]}


def model():
    return {"provider": "openrouter", "model_id": "example/model",
            "model_version": "pinned-1", "catalog_snapshot_id": 9,
            "catalog_sha256": H, "prompt_sha256": HH,
            "output_schema_sha256": H, "input_usd_per_million": "1.50",
            "output_usd_per_million": "3.25", "capability": "llm_classifier"}


def pipeline():
    return {"schema_version": CONTRACT_VERSIONS["pipeline"],
            "project_id": PID, "pipeline_config_id": 7, "taxonomy_version_id": 14,
            "config": strong_only(), "models": {"strong_llm": model()}}


def run():
    return {"schema_version": CONTRACT_VERSIONS["run"], "project_id": PID,
            "run_id": RID, "publication_id": PUB, "taxonomy_version_id": 14,
            "taxonomy_sha256": H, "pipeline_config_id": 7,
            "pipeline_sha256": HH, "idempotency_key": "sre-batch-29",
            "state": "awaiting_authorization",
            "items": [{"video_id": 29, "content_version_id": 55,
                       "content_sha256": H, "hypothesis_keys": [K1, K2]}]}


def quote():
    return {"schema_version": CONTRACT_VERSIONS["quote"], "project_id": PID,
            "run_id": RID, "quote_id": QID, "model_catalog_sha256": H,
            "request_sha256": HH, "issued_at": "2026-10-23T10:00:00Z",
            "expires_at": "2026-10-23T11:00:00Z",
            "low_usd": "0.01", "expected_usd": "0.05", "high_usd": "0.10"}


def consent():
    return {"schema_version": CONTRACT_VERSIONS["consent"],
            "project_id": PID, "run_id": RID, "quote_id": QID,
            "consent_id": CID, "authorized_usd": "0.10",
            "approved_at": "2026-10-23T10:05:00Z",
            "expires_at": "2026-10-24T10:05:00Z", "revoked_at": None}


def event():
    return {"schema_version": CONTRACT_VERSIONS["event"],
            "event_id": CID, "event_type": "sre.classification_item_completed",
            "project_id": PID, "run_id": RID, "publication_id": PUB,
            "taxonomy_version_id": 14, "occurred_at": "2026-10-23T10:10:00Z",
            "video_id": 29, "content_version_id": 55,
            "source_policy_version_id": 10, "result_sha256": H,
            "decisions": {"match": [K1], "no_match": [K2],
                          "uncertain": [], "not_evaluated": []}}


def invalid(call, payload):
    with unittest.TestCase().assertRaises(SREContractError):
        call(payload)


class ContentContractTests(unittest.TestCase):
    def test_content_normalized_metadata_only(self):
        a = validate_content_input(content())
        self.assertEqual(a["data"]["title"], "Калории в кофе")
        self.assertEqual(a["content_version_id"], 55)
        self.assertEqual(a["provenance"]["source_request_id"], 4)

    def test_missing_unexpected_and_wrong_id_fail_closed(self):
        for modified in (
            lambda a: a.pop("rights"),
            lambda a: a.update(project_id="bad"),
            lambda a: a.update(video_id=True),
            lambda a: a.update(source_policy_version_id=0),
            lambda a: a.update(schema_version="sre-content-input/v2"),
            lambda a: a.update(unknown_field="value"),
            lambda a: a.update(content_sha256="A" * 64),
        ):
            a = content()
            modified(a)
            with self.subTest(a=a), self.assertRaises(SREContractError):
                validate_content_input(a)

    def test_rights_must_be_explicit_bools_and_no_grant_in_shape_validator(self):
        a = content()
        a["rights"]["can_semantic_classify"] = "true"
        invalid(validate_content_input, a)
        self.assertEqual(validate_content_input(content())["rights"]["can_external_llm"], False)
        with self.assertRaises(SREContractError) as err:
            authorize_content_usage(content(), external_llm=True,
                                    at_time=datetime(2026, 10, 23, tzinfo=timezone.utc))
        self.assertEqual(err.exception.code, "rights_denied")
        a = content()
        a["rights"]["can_semantic_classify"] = False
        invalid(authorize_content_usage, a)

    def test_expired_rights_and_naive_time(self):
        with self.assertRaises(SREContractError) as err:
            authorize_content_usage(content(), at_time=datetime(2026, 12, 1, tzinfo=timezone.utc))
        self.assertEqual(err.exception.code, "rights_expired")
        with self.assertRaises(SREContractError):
            authorize_content_usage(content(), at_time=datetime(2026, 10, 23))

    def test_multimodal_requires_media_asset_and_rights(self):
        a = content()
        a["data"]["transcript"] = [{"text": "Калории кофе", "start_ms": 0, "end_ms": 1200}]
        a["completeness"] = "partial"
        invalid(validate_content_input, a)
        a["media_asset_id"] = 12
        invalid(validate_content_input, a)
        a["rights"]["can_analyze_media"] = True
        self.assertEqual(validate_content_input(a)["data"]["transcript"][0]["end_ms"], 1200)
        a["completeness"] = "metadata_only"
        invalid(validate_content_input, a)
        a["completeness"] = "partial"
        a["data"]["scenes"] = [{"text": "test", "start_ms": 500, "end_ms": 300}]
        invalid(validate_content_input, a)

    def test_provider_output_grounding_and_multi_label(self):
        good = {"schema_version": CONTRACT_VERSIONS["labels"], "labels": [
            {"category_key": K1, "decision": "match", "reason": "Title explains calories",
             "evidence": [{"source": "title", "quote": "Калории в кофе"}]},
            {"category_key": K2, "decision": "no_match", "reason": "No food examples",
             "evidence": []}]}
        self.assertEqual(validate_model_output(good, [K1, K2], content())["labels"][0]["decision"], "match")
        good["labels"][0]["evidence"][0]["quote"] = "Fabricated"
        invalid(lambda v: validate_model_output(v, [K1, K2], content()), good)

    def test_missing_extra_or_system_verdict_are_not_semantic_uncertain(self):
        base = {"schema_version": CONTRACT_VERSIONS["labels"], "labels": [
            {"category_key": K1, "decision": "uncertain", "reason": "Insufficient data",
             "evidence": []}]}
        invalid(lambda v: validate_model_output(v, [K1, K2], content()), base)
        base["labels"][0]["decision"] = "not_evaluated"
        invalid(lambda v: validate_model_output(v, [K1], content()), base)
        base["labels"][0]["decision"] = "no_match"
        base["labels"][0]["reason"] = "Reason"
        base["labels"].append(copy.deepcopy(base["labels"][0]))
        invalid(lambda v: validate_model_output(v, [K1], content()), base)


    def test_output_evidence_unknown_keys_and_overlong_quote(self):
        out = {"schema_version": CONTRACT_VERSIONS["labels"], "labels": [
            {"category_key": K1, "decision": "match", "reason": "Grounded",
             "evidence": [{"source": "title", "quote": "Калории", "injected": True}]}]}
        invalid(lambda v: validate_model_output(v, [K1], content()), out)
        out["labels"][0]["evidence"] = [{"source": "title", "quote": "Калории" * 500}]
        invalid(lambda v: validate_model_output(v, [K1], content()), out)

    def test_content_size_and_empty_embedding_set_rejected(self):
        a = content()
        a["completeness"] = "partial"
        a["media_asset_id"] = 7
        a["rights"]["can_analyze_media"] = True
        a["data"]["scenes"] = [
            {"text": "Б" * 1000, "start_ms": i * 1000,
             "end_ms": (i + 1) * 1000} for i in range(600)
        ]
        invalid(validate_content_input, a)
        invalid(lambda v: validate_retrieval_scores(v, []), {})

    def test_retrieval_is_deterministic_and_rejects_bools_nan(self):
        self.assertEqual(validate_retrieval_scores({K1: 0.5, K2: 0.5}, [K1, K2]),
                         sorted([K1, K2]))
        for wrong in ({K1: True, K2: 0.1}, {K1: math.nan, K2: 0.1},
                      {K1: 1.1, K2: 0.1}, {K1: 0.1}):
            invalid(lambda v: validate_retrieval_scores(v, [K1, K2]), wrong)


class TaxonomyContractTests(unittest.TestCase):
    def test_human_approved_tree_preserved(self):
        original = taxonomy()
        result = validate_taxonomy_snapshot(original)
        self.assertEqual(result["nodes"], original["nodes"])
        self.assertEqual(result["hypothesis_links"][0]["category_key"], K1)

    def test_missing_review_mismatch_hash_and_unexpected_field(self):
        for modify in (
            lambda t: t.update(human_reviews=[1]),
            lambda t: t.update(tree_sha256=HH),
            lambda t: t["hypothesis_links"][0].update(category_key=K2),
            lambda t: t["nodes"][1].pop("exclude"),
            lambda t: t["nodes"][1].update(unknown="spoof"),
            lambda t: t.update(project_id="F" * 32),
        ):
            t = taxonomy()
            modify(t)
            with self.subTest(modify=str(modify)), self.assertRaises(SREContractError):
                validate_taxonomy_snapshot(t)

    def test_missing_or_invalid_hypothesis_links(self):
        t = taxonomy()
        t["hypothesis_links"].pop()
        invalid(validate_taxonomy_snapshot, t)
        t = taxonomy()
        t["hypothesis_links"][0]["source_node_id"] = "dir_a"
        invalid(validate_taxonomy_snapshot, t)

    def test_disconnected_and_cyclic_tree(self):
        t = taxonomy()
        t["nodes"][1]["parent_id"] = "unknown"
        t["tree_sha256"] = fingerprint(t["nodes"])
        invalid(validate_taxonomy_snapshot, t)
        t = taxonomy()
        t["nodes"][0]["parent_id"] = "h1"
        t["tree_sha256"] = fingerprint(t["nodes"])
        invalid(validate_taxonomy_snapshot, t)


class FinancialAndRunTests(unittest.TestCase):
    def test_pipeline_snapshot_validates_models_and_stages(self):
        self.assertEqual(validate_pipeline_snapshot(pipeline())["models"]["strong_llm"]["catalog_snapshot_id"], 9)
        x = pipeline()
        x["models"]["strong_llm"]["capability"] = "embedding_retrieval"
        invalid(validate_pipeline_snapshot, x)
        x = pipeline()
        x["models"]["strong_llm"]["input_usd_per_million"] = float("nan")
        invalid(validate_pipeline_snapshot, x)
        x = pipeline()
        x["models"]["extra"] = model()
        invalid(validate_pipeline_snapshot, x)

    def test_run_plan_rejects_duplicate_video_and_unauthorized_state(self):
        self.assertEqual(validate_run_plan(run())["items"][0]["hypothesis_keys"], [K1, K2])
        x = run()
        x["state"] = "running"
        invalid(validate_run_plan, x)
        x = run()
        x["items"].append(copy.deepcopy(x["items"][0]))
        invalid(validate_run_plan, x)
        x = run()
        x["idempotency_key"] = "✖"
        invalid(validate_run_plan, x)

    def test_quote_expiry_positive_and_decimal_precision(self):
        self.assertEqual(validate_cost_quote(quote())["high_usd"], "0.10")
        for edit in (
            lambda q: q.update(low_usd="0.12"),
            lambda q: q.update(expires_at="2026-10-23T09:59:00Z"),
            lambda q: q.update(high_usd="0.100000001"),
            lambda q: q.update(high_usd="-1"),
            lambda q: q.update(high_usd=float("nan")),
        ):
            q = quote()
            edit(q)
            invalid(validate_cost_quote, q)

    def test_consent_is_bound_to_exact_quote_and_cap(self):
        self.assertEqual(validate_spending_consent(consent(), quote())["authorized_usd"], "0.10")
        for edit in (
            lambda c: c.update(quote_id=str(uuid4())),
            lambda c: c.update(project_id="fedcba9876543210fedcba9876543210"),
            lambda c: c.update(authorized_usd="0.09"),
            lambda c: c.update(approved_at="2026-10-23T12:00:00Z"),
            lambda c: c.update(revoked_at="2026-10-23T09:00:00Z"),
        ):
            c = consent()
            edit(c)
            invalid(lambda v: validate_spending_consent(v, quote()), c)

    def test_budget_invariant(self):
        self.assertEqual(validate_spending_position("1.00000000", "0.40", "0.60")["spent_usd"], "0.40")
        with self.assertRaises(SREContractError) as err:
            validate_spending_position("1.00", "0.60", "0.50")
        self.assertEqual(err.exception.code, "budget_exceeded")

    def test_state_transitions_are_strict(self):
        self.assertEqual(validate_transition("attempt", "reserved", "dispatch_intent"), "dispatch_intent")
        self.assertEqual(validate_transition("run", "running", "completed"), "completed")
        for kind, before, after in (
            ("attempt", "reserved", "completed"),
            ("attempt", "uncertain", "reserved"),
            ("run", "completed", "running"),
            ("other", "reserved", "running"),
        ):
            with self.assertRaises(SREContractError):
                validate_transition(kind, before, after)

    def test_valid_analytics_event_and_no_duplicate_labels(self):
        self.assertEqual(validate_analytics_event(event())["decisions"]["match"], [K1])
        x = event()
        x["decisions"]["no_match"] = [K1]
        invalid(validate_analytics_event, x)
        x = event()
        x.pop("content_version_id")
        invalid(validate_analytics_event, x)
        x = event()
        x["event_type"] = "sre.classification_run_completed"
        invalid(validate_analytics_event, x)
        x = event()
        x["event_type"] = "sre.rights_revoked"
        x["reason"] = "source policy revoked"
        x.pop("decisions")
        self.assertEqual(validate_analytics_event(x)["reason"], "source policy revoked")

    def test_fingerprint_rejects_nan_and_is_order_independent(self):
        self.assertEqual(fingerprint({"x": 1, "y": 2}), fingerprint({"y": 2, "x": 1}))
        with self.assertRaises(SREContractError):
            canonical_json({"x": math.nan})


if __name__ == "__main__":
    unittest.main()
