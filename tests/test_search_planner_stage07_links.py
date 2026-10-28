"""SP-07: lossless cross-hypothesis dedup, MMR quota, immutable QV link replay.

No real semantic model; fixed vectors only exercise control-flow invariants.
No Collector, OpenRouter, money or PostgreSQL installation needed.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace
import unittest

from intelligence.search_planner.query_validation.selection import (
    select_queries, normalized,
)
from intelligence.search_planner.query_validation.linkage import (
    get_selected_query_links, build_selected_query_links,
)
from intelligence.search_planner.query_validation.contracts import DEFAULT_POLICY
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from test_search_planner_query_validation import sample

PID = "a" * 32
QV = "66666666-6666-4666-8666-666666666666"


class ConstantVectors:
    """Deterministic unit fixture: NOT a production semantic model."""
    mode = "test_fixture"
    model_key = "test-stage07-constant-embedding-v1"
    def encode(self, texts):
        return [[1.0] + [0.0] * 63 for _ in texts]


def cross_hypothesis_bundle():
    bundle, contexts = sample()
    # Exactly one candidate in each of three hypotheses. Same normalized
    # query/mode/locale, distinct UUID and source-hypothesis provenance.
    candidates = []
    for item in bundle["hypothesis_coverage"]:
        hyp = item["hypothesis_id"]
        c = next(c for c in bundle["candidates"] if c["hypothesis_id"] == hyp)
        c["text"] = "универсальный план упражнений"
        candidates.append(c)
        item["candidate_count"] = 1
    bundle["candidates"] = candidates
    return bundle, contexts


def rows_from_selection(bundle, evaluation):
    by_id = {c["candidate_id"]: c for c in bundle["candidates"]}
    return [
        (d["candidate_id"], d["hypothesis_id"], d["decision"],
         d["reason_code"], d["duplicate_of"],
         list(by_id[d["candidate_id"]]["source_fact_ids"]),
         by_id[d["candidate_id"]]["query_mode"],
         by_id[d["candidate_id"]]["language"],
         by_id[d["candidate_id"]]["text"])
        for d in evaluation["decisions"]
    ]


class Result:
    def __init__(self, *, one=None, rows=()):
        self.one, self.rows = one, rows
    def fetchone(self):
        return self.one
    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, selected_count, rows):
        self.queries = []
        self._results = iter([
            Result(), Result(one=(selected_count, "semantic_local")),
            Result(rows=rows),
        ])
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def transaction(self):
        return self
    def execute(self, sql, args=None):
        self.queries.append((sql, args))
        return next(self._results)


class SharedLineageStage07(unittest.TestCase):
    def test_nfkc_casefold_exact_dedup_without_locale_mixing(self):
        self.assertEqual(normalized(" Ｆｕｌｌ Ｗｉｄｔｈ "),
                         normalized("full width"))
        self.assertEqual(normalized("＃ТЕСТ"), normalized("#тест"))
        self.assertNotEqual(normalized("русский"), normalized("english"))

    def test_cross_hyp_same_query_links_all_original_hypotheses(self):
        bundle, contexts = cross_hypothesis_bundle()
        original = copy.deepcopy(bundle)
        result = select_queries(bundle, contexts, DEFAULT_POLICY, ConstantVectors())
        self.assertEqual(bundle, original)
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(len(result["decisions"]), 3)
        d = {x["candidate_id"]: x for x in result["decisions"]}
        selected = [x for x in d.values() if x["decision"] == "selected"]
        self.assertEqual(len(selected), 1)
        primary = selected[0]["candidate_id"]
        for x in d.values():
            if x["candidate_id"] != primary:
                self.assertEqual(x["decision"], "rejected")
                self.assertEqual(x["reason_code"], "exact_duplicate")
                self.assertEqual(x["duplicate_of"], primary)
                self.assertIsNone(x["selected_rank"])
        links = result["shared_query_links"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["selected_candidate_id"], primary)
        self.assertEqual(
            {x["candidate_id"] for x in links[0]["linked_sources"]},
            {x["candidate_id"] for x in bundle["candidates"]},
        )
        self.assertEqual(
            {x["hypothesis_id"] for x in links[0]["linked_sources"]},
            {x["hypothesis_id"] for x in bundle["candidates"]},
        )
        self.assertEqual(sum(c["selected_count"] for c in result["coverage"]), 1)
        self.assertTrue(any("Equivalent query" in (c["reason"] or "")
                            for c in result["coverage"]))

    def test_existing_db_decisions_reconstruct_lossless_links(self):
        b, ctx = cross_hypothesis_bundle()
        outcome = select_queries(b, ctx, DEFAULT_POLICY, ConstantVectors())
        rows = rows_from_selection(b, outcome)
        projected = build_selected_query_links(rows, project_id=PID,
                                                validation_run_id=QV,
                                                selected_count=outcome["selected_count"])
        self.assertEqual(projected["schema_version"],
                         "search-planner-query-hypothesis-links/v1")
        self.assertEqual(projected["selected_count"], 1)
        self.assertFalse(projected["provider_tasks_dispatched"])
        self.assertEqual(
            projected["links"][0]["linked_sources"],
            outcome["shared_query_links"][0]["linked_sources"],
        )
        reverse = build_selected_query_links(list(reversed(rows)), project_id=PID,
                                             validation_run_id=QV,
                                             selected_count=outcome["selected_count"])
        self.assertEqual(reverse, projected)

    def test_local_dup_and_cross_hyp_dedup_remain_distinct(self):
        b, contexts = cross_hypothesis_bundle()
        duplicate = copy.deepcopy(b["candidates"][0])
        duplicate["candidate_id"] = "00000000-0000-4000-8000-000000000f55"
        duplicate["text"] = "УНИВЕРСАЛЬНЫЙ ПЛАН УПРАЖНЕНИЙ"
        b["candidates"].append(duplicate)
        for c in b["hypothesis_coverage"]:
            if c["hypothesis_id"] == duplicate["hypothesis_id"]:
                c["candidate_count"] += 1
        outcome = select_queries(b, contexts, DEFAULT_POLICY, ConstantVectors())
        self.assertEqual(outcome["selected_count"], 1)
        self.assertEqual(len(outcome["decisions"]), 4)
        self.assertEqual(
            {link["candidate_id"]
             for link in outcome["shared_query_links"][0]["linked_sources"]},
            {q["candidate_id"] for q in b["candidates"]},
        )

    def test_no_cross_hyp_merging_if_disabled(self):
        b, ctx = cross_hypothesis_bundle()
        policy = {**DEFAULT_POLICY, "cross_hypothesis_dedup": False}
        result = select_queries(b, ctx, policy, ConstantVectors())
        self.assertEqual(result["selected_count"], len(ctx))
        self.assertEqual(len(result["shared_query_links"]), len(ctx))
        self.assertTrue(all(len(link["linked_sources"]) == 1
                            for link in result["shared_query_links"]))

    def test_cross_mode_or_locale_not_merged(self):
        b, contexts = cross_hypothesis_bundle()
        b["candidates"][1]["query_mode"] = "hashtag"
        b["candidates"][1]["text"] = "#универсальный"
        for ctx in contexts:
            ctx["search_policy"]["allowed_modes"] = ["keyword", "hashtag"]
        result = select_queries(b, contexts, DEFAULT_POLICY, ConstantVectors())
        self.assertEqual(result["selected_count"], 2)

    def test_cross_hypothesis_candidate_not_suppressed_by_other_hypothesis_quota(self):
        # Regression: old global dedup before local MMR would discard the
        # second hypothesis query because it had matched an A candidate
        # which later lost its own MMR quota. Its valid query vanished.
        b, contexts = sample()
        a, other = (contexts[0]["hypothesis"]["id"],
                    contexts[1]["hypothesis"]["id"])
        a_candidates = [c for c in b["candidates"] if c["hypothesis_id"] == a]
        other_candidate = next(c for c in b["candidates"]
                               if c["hypothesis_id"] == other)
        a_candidates[0]["text"] = "первый вариант"
        a_candidates[1]["text"] = "второй вариант"
        other_candidate["text"] = "второй вариант"
        b["candidates"] = a_candidates + [other_candidate]
        for coverage in b["hypothesis_coverage"]:
            if coverage["hypothesis_id"] == a:
                coverage["candidate_count"] = 2
            elif coverage["hypothesis_id"] == other:
                coverage["candidate_count"] = 1
            else:
                coverage.update(state="no_viable_queries", candidate_count=0,
                                reason="нет подходящего источника")
        class DistinctVectors:
            mode = "test_fixture"
            model_key = "qa-sp07-quota-scope-test"
            def encode(self, texts):
                return [[0.8, 0.6] if t == "второй вариант"
                        else [1.0, 0.0] for t in texts]
        policy = {**DEFAULT_POLICY, "max_selected_per_hypothesis": 1}
        outcome = select_queries(b, contexts, policy, DistinctVectors())
        result = {d["candidate_id"]: d for d in outcome["decisions"]}
        self.assertEqual(result[a_candidates[0]["candidate_id"]]["decision"], "selected")
        self.assertEqual(result[a_candidates[1]["candidate_id"]]["reason_code"], "quota")
        self.assertEqual(result[other_candidate["candidate_id"]]["decision"], "selected")
        self.assertEqual(outcome["selected_count"], 2)

    def test_qv_immutable_sql_projection_includes_all_source_facts(self):
        b, ctx = cross_hypothesis_bundle()
        outcome = select_queries(b, ctx, DEFAULT_POLICY, ConstantVectors())
        rows = rows_from_selection(b, outcome)
        conn = FakeConn(outcome["selected_count"], rows)
        result = get_selected_query_links(PID, QV, connect=lambda: conn)
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(len(result["links"][0]["linked_sources"]), 3)
        self.assertIn("REPEATABLE READ READ ONLY", conn.queries[0][0])
        self.assertEqual(conn.queries[1][1], (QV, PID))
        self.assertEqual(conn.queries[2][1], (QV, PID))
        self.assertIn("JOIN sqg_candidates", conn.queries[2][0])
        self.assertNotIn("UPDATE ", conn.queries[2][0])

    def test_zero_selected_is_valid_no_work(self):
        out = build_selected_query_links([], project_id=PID,
                                         validation_run_id=QV, selected_count=0)
        self.assertEqual(out["links"], [])
        self.assertEqual(out["selected_count"], 0)

    def test_dangling_duplicate_to_quota_reject_is_not_an_executable_link(self):
        winner = ("00000000-0000-4000-8000-000000000001", "first",
                  "rejected", "quota", None, ["fact-a"], "keyword", "ru", "тема")
        alias = ("00000000-0000-4000-8000-000000000002", "second",
                 "rejected", "exact_duplicate", winner[0], ["fact-b"],
                 "keyword", "ru", "ТЕМА")
        result = build_selected_query_links([winner, alias], project_id=PID,
                                            validation_run_id=QV, selected_count=0)
        self.assertEqual(result["links"], [])

    def test_sql_view_rejects_fixture_mode(self):
        class FixtureConn(FakeConn):
            def __init__(self):
                self.queries = []
                self._results = iter([
                    Result(), Result(one=(0, "test_fixture")),
                ])
        with self.assertRaises(PipelineError):
            get_selected_query_links(PID, QV, connect=FixtureConn)

    def test_link_projection_rejects_corrupt_selected_count(self):
        rows = [("00000000-0000-4000-8000-000000000001", "a", "selected",
                 "selected", None, [], "keyword", "ru", "query")]
        with self.assertRaises(PipelineError):
            build_selected_query_links(rows, project_id=PID,
                                       validation_run_id=QV, selected_count=0)

    def test_link_projection_rejects_cross_language_duplicate(self):
        ids = ["00000000-0000-4000-8000-000000000001",
               "00000000-0000-4000-8000-000000000002"]
        rows = [
            (ids[0], "first", "selected", "selected", None,
             [], "keyword", "ru", "тема"),
            (ids[1], "other", "rejected", "exact_duplicate", ids[0],
             [], "keyword", "en", "тема"),
        ]
        with self.assertRaises(PipelineError):
            build_selected_query_links(rows, project_id=PID,
                                       validation_run_id=QV, selected_count=1)

    def test_link_projection_rejects_forged_exact_equivalence(self):
        a,b = ("00000000-0000-4000-8000-000000000001",
               "00000000-0000-4000-8000-000000000002")
        rows = [
            (a, "first", "selected", "selected", None, [],
             "keyword", "ru", "видеотренировки"),
            (b, "second", "rejected", "exact_duplicate", a, [],
             "keyword", "ru", "несвязанный запрос"),
        ]
        with self.assertRaises(PipelineError):
            build_selected_query_links(rows, project_id=PID,
                                       validation_run_id=QV, selected_count=1)

    def test_repeated_uuid_and_untrusted_facts_fail_closed(self):
        a = ("00000000-0000-4000-8000-000000000001", "first",
             "selected", "selected", None, ["f"], "keyword", "ru", "тема")
        for rows in ([a,a], [
            ("00000000-0000-4000-8000-000000000001", "first",
             "selected", "selected", None, ["f", "f"], "keyword", "ru", "тема"),
        ]):
            with self.subTest(rows=rows), self.assertRaises(PipelineError):
                build_selected_query_links(rows, project_id=PID,
                                           validation_run_id=QV, selected_count=1)


if __name__ == "__main__":
    unittest.main()
