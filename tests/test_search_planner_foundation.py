"""Search Planner foundation — zero-cost unit tests, no PostgreSQL required."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest import mock

from intelligence.search_planner import (
    build_hypothesis_contexts, load_approved_publication,
)
from intelligence.search_planner.contracts import (
    NODE_FIELDS, validate_published_snapshot, validate_search_policy,
)
from intelligence.search_planner.input_loader import read_published_snapshot
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

PID = "a" * 32
RID = "11111111-1111-4111-8111-111111111111"
PUB = "22222222-2222-4222-8222-222222222222"
FUNCTION = "33333333-3333-4333-8333-333333333333"
PROBLEM = "44444444-4444-4444-8444-444444444444"


def node(ident, kind, parent, relation, fact_ids, name):
    return {
        "id": ident, "kind": kind, "parent_id": parent,
        "relation": relation, "source_fact_ids": fact_ids,
        "name": name,
        "rationale": "Основано на утверждённом контексте RTB",
        "definition": "Полное определение темы: не перефразировать и не сокращать",
        "include": "Подходящие короткие видео из утверждённых границ",
        "exclude": "Неподходящие сторонние темы; без ложных обещаний",
        "examples": ["Пример ритуала №1", "Пример ритуала №2"],
    }


FACTS = [
    {
        "id": FUNCTION, "kind": "function",
        "text": "Продукт помогает создавать домашние тренировки",
        "source_quote": "Создаёт домашние тренировки",
        "origin": "explicit",
    },
    {
        "id": PROBLEM, "kind": "problem",
        "text": "Недостаточно времени для занятий",
        # Historical approved facts can lack provenance fields.
    },
]
TREE = [
    node("home", "direction", None, "direct", [FUNCTION], "Домашние занятия"),
    node("time", "direction", None, "problem_related", [PROBLEM], "Нехватка времени"),
    node("bodyweight", "branch", "home", "direct", [FUNCTION], "Без оборудования"),
    node("short", "hypothesis", "bodyweight", "problem_related", [PROBLEM], "Короткие занятия"),
    node("morning", "hypothesis", "bodyweight", "direct", [FUNCTION], "Утренние ритуалы"),
    node("time_hyp", "hypothesis", "time", "problem_related", [PROBLEM], "Быстро заниматься"),
]
PROFILE = {
    "product_type": "fitness_app",
    "product_functions": "Создаёт домашние тренировки",
    "audience_problems": "Недостаточно времени",
    "target_audience": "Занятые взрослые",
    "research_goal": "Исследовать короткий видеоконтент",
    "other_private_profile_field": "This must not be copied into AI Query.",
}
POLICY = {
    "languages": ["ru", "en"],
    "markets": [],
    "allowed_modes": ["keyword", "hashtag"],
    "target_candidates_per_hypothesis": 10,
    "max_candidates_per_hypothesis": 16,
    "max_total_candidates": 480,
}


def snapshot():
    return {
        "project_id": PID,
        "rtb_run_id": RID,
        "taxonomy_publication_id": PUB,
        "taxonomy_version": 3,
        "tree": copy.deepcopy(TREE),
        "approved_facts": copy.deepcopy(FACTS),
        "profile_snapshot": copy.deepcopy(PROFILE),
        "run_status": "approved",
        "deep_status": "approved",
        "human_reviews": [1, 2],
    }


class FakeResult:
    def __init__(self, row):
        self.row = row
    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row):
        self.row = row
        self.queries = []
    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if sql.startswith("SET TRANSACTION"):
            return FakeResult(None)
        return FakeResult(self.row)
    def transaction(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


def db_row(s):
    return (
        s["project_id"], s["rtb_run_id"], s["taxonomy_publication_id"],
        s["taxonomy_version"], s["tree"], s["approved_facts"],
        s["profile_snapshot"], s["run_status"], s["deep_status"],
        s["human_reviews"],
    )


class SearchPlannerFoundationTests(unittest.TestCase):
    def test_full_contexts_with_optional_branch_and_preserved_fields(self):
        s = snapshot()
        before = copy.deepcopy(s)
        p = copy.deepcopy(POLICY)
        contexts = build_hypothesis_contexts(s, p)
        self.assertEqual(len(contexts), 3)
        self.assertEqual(s, before)
        self.assertEqual(p, POLICY)
        by_hyp = {c["hypothesis"]["id"]: c for c in contexts}
        short = by_hyp["short"]
        self.assertEqual(short["ancestry_path"], ["home", "bodyweight", "short"])
        self.assertEqual(short["branch"]["id"], "bodyweight")
        self.assertEqual(short["direction"]["id"], "home")
        self.assertEqual(short["neighbor_hypotheses"][0]["id"], "morning")
        self.assertEqual([x["id"] for x in short["approved_facts"]], sorted((FUNCTION, PROBLEM)))
        self.assertEqual(short["approved_facts"][0]["source_quote"], "Создаёт домашние тренировки")
        self.assertEqual(short["approved_facts"][1]["source_quote"], None)
        self.assertEqual(short["search_policy"], POLICY)
        self.assertEqual(short["derived_search_facets"], [])
        self.assertEqual(short["project_context"]["target_audience"], "Занятые взрослые")
        self.assertNotIn("other_private_profile_field", short["project_context"])
        for key in ("hypothesis", "branch", "direction"):
            obj = short[key]
            self.assertEqual(set(obj), set(NODE_FIELDS))
            original = next(n for n in TREE if n["id"] == obj["id"])
            for field in NODE_FIELDS:
                self.assertEqual(obj[field], original[field], field)
        direct = by_hyp["time_hyp"]
        self.assertIsNone(direct["branch"])
        self.assertEqual(direct["ancestry_path"], ["time", "time_hyp"])
        self.assertEqual([x["id"] for x in direct["approved_facts"]], [PROBLEM])

    def test_hash_and_order_deterministic(self):
        s = snapshot()
        a = build_hypothesis_contexts(s, POLICY)
        s["tree"] = list(reversed(s["tree"]))
        b = build_hypothesis_contexts(s, POLICY)
        self.assertEqual([x["hypothesis"]["id"] for x in a],
                         [x["hypothesis"]["id"] for x in b])
        # This digest is a checksum of the exact stored tree array; changing
        # its order changes the publication's canonical bytes by design.
        self.assertNotEqual(a[0]["taxonomy_sha256"], b[0]["taxonomy_sha256"])
        self.assertEqual(a[0]["taxonomy_sha256"],
                         build_hypothesis_contexts(snapshot(), POLICY)[0]["taxonomy_sha256"])

    def test_returns_deep_copies(self):
        result = build_hypothesis_contexts(snapshot(), POLICY)
        result[0]["direction"]["name"] = "tampered"
        self.assertEqual(TREE[0]["name"], "Домашние занятия")
        result[0]["search_policy"]["languages"].append("de")
        self.assertEqual(POLICY["languages"], ["ru", "en"])

    def test_rejects_unapproved_and_corrupted_publications(self):
        examples = [
            ("run_status", "topic_review"),
            ("deep_status", "review2"),
            ("human_reviews", [1]),
            ("human_reviews", [1, 1]),
            ("taxonomy_version", 0),
            ("profile_snapshot", None),
            ("approved_facts", []),
        ]
        for field, value in examples:
            with self.subTest(field=field, value=value):
                s = snapshot(); s[field] = value
                with self.assertRaises(PipelineError):
                    build_hypothesis_contexts(s, POLICY)
        s = snapshot(); s["tree"][3]["source_fact_ids"] = ["unapproved"]
        with self.assertRaises(PipelineError):
            build_hypothesis_contexts(s, POLICY)
        s = snapshot(); s["tree"][2]["parent_id"] = "short"
        with self.assertRaises(PipelineError):
            build_hypothesis_contexts(s, POLICY)
        s = snapshot(); s["tree"][1]["kind"] = "hypothesis"
        with self.assertRaises(PipelineError):
            build_hypothesis_contexts(s, POLICY)
        s = snapshot(); s["tree"].append(node("unused", "direction", None, "adjacent", [], "Orphan root"))
        with self.assertRaisesRegex(PipelineError, "no hypothesis"):
            build_hypothesis_contexts(s, POLICY)

    def test_bad_project_and_publication_identity_rejected(self):
        s = snapshot()
        for key, value in (("project_id", "foreign"), ("rtb_run_id", "abc"),
                           ("taxonomy_publication_id", "not-a-uuid")):
            bad = copy.deepcopy(s); bad[key] = value
            with self.assertRaises(PipelineError):
                validate_published_snapshot(bad)
        with self.assertRaises(PipelineError):
            load_approved_publication("x", PUB, connect=lambda: None)

    def test_language_market_mode_limits_are_explicit(self):
        for key, value in (
            ("languages", []), ("languages", ["en", "en"]),
            ("languages", ["ENG-US"]),
            ("markets", ["US", "US"]), ("allowed_modes", ["video"]),
            ("target_candidates_per_hypothesis", 17),
            ("max_total_candidates", 481),
        ):
            with self.subTest(key=key):
                bad = copy.deepcopy(POLICY); bad[key] = value
                with self.assertRaises(PipelineError):
                    validate_search_policy(bad)
        bad = copy.deepcopy(POLICY)
        bad["target_candidates_per_hypothesis"] = 16
        bad["max_candidates_per_hypothesis"] = 8
        with self.assertRaises(PipelineError):
            validate_search_policy(bad)
        with self.assertRaises(PipelineError):
            validate_search_policy({})

    def test_snapshot_read_only_sql_and_project_scope(self):
        s = snapshot()
        conn = FakeConnection(db_row(s))
        result = load_approved_publication(PID, PUB, connect=lambda: conn)
        self.assertEqual(result["project_id"], PID)
        self.assertEqual(result["taxonomy_version"], 3)
        self.assertEqual(len(conn.queries), 2)
        self.assertIn("REPEATABLE READ READ ONLY", conn.queries[0][0])
        sql, params = conn.queries[1]
        self.assertIn("JOIN tb_taxonomy_publications", sql)
        self.assertIn("JOIN tb_deep_runs", sql)
        self.assertIn("JOIN tb_fact_review_submissions", sql)
        self.assertIn("tb_deep_reviews", sql)
        self.assertEqual(params, (PID, PUB))
        self.assertIn("d.project_id=%s", sql)

    def test_foreign_or_incomplete_db_row_is_not_accepted(self):
        s = snapshot()
        for field, value in (
            ("project_id", "b" * 32),
            ("taxonomy_publication_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            ("deep_status", "review2"), ("human_reviews", [1]),
        ):
            bad = copy.deepcopy(s); bad[field] = value
            with self.subTest(field=field):
                conn = FakeConnection(db_row(bad))
                # Fake driver deliberately returns a wrong row despite
                # the SQL WHERE clause. The Python layer must fail closed.
                with self.assertRaises(PipelineError):
                    read_published_snapshot(conn, PID, PUB)
        conn = FakeConnection(None)
        with self.assertRaisesRegex(PipelineError, "not found"):
            read_published_snapshot(conn, PID, PUB)

    def test_context_matches_committed_json_schema_when_dependency_available(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is optional for runtime")
        path = (Path(__file__).resolve().parents[1] / "docs"
                / "SEARCH_PLANNER_HYPOTHESIS_CONTEXT_V1.schema.json")
        schema = json.loads(path.read_text(encoding="utf-8"))
        for context in build_hypothesis_contexts(snapshot(), POLICY):
            jsonschema.Draft202012Validator(schema).validate(context)


if __name__ == "__main__":
    unittest.main()
