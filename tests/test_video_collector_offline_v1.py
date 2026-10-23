"""Zero-network Video Collector v1 contracts, registry and preview regression."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import socket
import unittest
from unittest.mock import patch

from intelligence.collector import (
    CollectorError, build_preview, list_sources,
    match_source, parse_validated_query_set,
)
from intelligence.collector.contracts import (
    CandidateEnvelope, ProviderPage, QUERY_SET_SCHEMA,
)
from intelligence.collector.preview_cli import preview_document

PID = "a" * 32
PUB = "11111111-1111-4111-8111-111111111111"
GEN = "22222222-2222-4222-8222-222222222222"


def query(query_id="q_workout", text="10 minute home workout",
          mode="keyword", hypothesis_id="hyp_workout", language="en"):
    return {
        "query_id": query_id, "hypothesis_id": hypothesis_id,
        "direction_id": "dir_fitness", "mode": mode, "text": text,
        "language": language, "validation_state": "accepted",
    }


def fixture(queries=None):
    return {
        "schema_version": QUERY_SET_SCHEMA, "project_id": PID,
        "taxonomy_publication_id": PUB, "taxonomy_version": 3,
        "taxonomy_sha256": "b" * 64, "generation_id": GEN,
        "validation_version": "query-validation/v1",
        "queries": [query()] if queries is None else queries,
    }


class QueryIntakeTests(unittest.TestCase):
    def test_valid_snapshot_parses_with_strict_types(self):
        item = parse_validated_query_set(fixture(), expected_project_id=PID,
                                         expected_publication_id=PUB)
        self.assertEqual(item.queries[0].text, "10 minute home workout")
        self.assertEqual(item.fingerprint, parse_validated_query_set(fixture()).fingerprint)

    def test_input_is_copied_not_mutated(self):
        raw = fixture()
        original = deepcopy(raw)
        item = parse_validated_query_set(raw)
        raw["queries"][0]["text"] = "malicious mutation"
        self.assertEqual(item.queries[0].text, original["queries"][0]["text"])

    def test_project_and_publication_scopes(self):
        with self.assertRaises(CollectorError) as ctx:
            parse_validated_query_set(fixture(), expected_project_id="c"*32)
        self.assertEqual(ctx.exception.code, "project_scope_mismatch")
        with self.assertRaises(CollectorError) as ctx:
            parse_validated_query_set(fixture(), expected_publication_id=GEN)
        self.assertEqual(ctx.exception.code, "publication_scope_mismatch")

    def test_exact_fields_duplicates_and_validation_state(self):
        bad = fixture()
        bad["untrusted_is_approved"] = True
        with self.assertRaises(CollectorError):
            parse_validated_query_set(bad)
        bad = fixture([query(), query()])
        with self.assertRaises(CollectorError) as ctx:
            parse_validated_query_set(bad)
        self.assertEqual(ctx.exception.code, "duplicate_query_id")
        bad = fixture()
        bad["queries"][0]["validation_state"] = "draft"
        with self.assertRaises(CollectorError):
            parse_validated_query_set(bad)
        bad = fixture()
        bad["queries"][0]["provider_key"] = "secret"
        with self.assertRaises(CollectorError):
            parse_validated_query_set(bad)

    def test_invalid_types_limits_and_text(self):
        for change in (
            lambda p: p.update(taxonomy_version=True),
            lambda p: p.update(project_id="abc"),
            lambda p: p.update(taxonomy_sha256="bad"),
            lambda p: p.update(queries=[]),
            lambda p: p["queries"][0].update(text="abc\x00x"),
            lambda p: p["queries"][0].update(text="x"*161),
            lambda p: p["queries"][0].update(language="invalid language"),
            lambda p: p["queries"][0].update(mode="auto_scrape"),
            lambda p: p["queries"][0].update(hypothesis_id="dir_fitness"),
        ):
            raw = fixture()
            change(raw)
            with self.subTest(change=repr(change)), self.assertRaises(CollectorError):
                parse_validated_query_set(raw)


class RegistryTests(unittest.TestCase):
    def test_dtk_declared_not_enabled_or_authorized(self):
        source, operation = match_source("dtk_tiktok", "keyword")
        self.assertEqual(operation, "search_posts")
        self.assertFalse(source.operationally_verified)
        self.assertFalse(source.source_policy_verified)
        self.assertEqual(match_source("dtk_tiktok", "hashtag")[1], None)
        with self.assertRaises(CollectorError):
            match_source("unknown_scraper", "keyword")
        for record in list_sources():
            self.assertFalse(record["operationally_verified"])
            self.assertFalse(record["source_policy_verified"])


class TaskPlannerTests(unittest.TestCase):
    def test_single_query_plan_is_always_offline(self):
        plan = build_preview(fixture())
        self.assertEqual(plan["status"], "preview_only")
        self.assertFalse(plan["execution_enabled"])
        self.assertFalse(plan["query_provenance_verified"])
        self.assertIn("source_policy_not_verified", plan["blocks"])
        self.assertIn("query_set_not_server_verified", plan["blocks"])
        self.assertEqual(len(plan["tasks"]), 1)
        self.assertEqual(plan["tasks"][0]["operation"], "search_posts")
        self.assertEqual(plan["tasks"][0]["params"], {"keyword": "10 minute home workout"})
        self.assertEqual(plan["summary"]["max_external_requests_upper_bound"], 1)
        self.assertEqual(plan["summary"]["requested_result_upper_bound"], 20)
        self.assertIsNone(plan["summary"]["estimated_cost_usd"])

    def test_deterministic_when_input_order_changes(self):
        queries = [query("q_two", "  Yoga   At Home  ", hypothesis_id="hyp_yoga"),
                   query("q_one", "yoga at home", hypothesis_id="hyp_alt"),
                   query("q_three", "Workout at home", hypothesis_id="hyp_workout")]
        a = build_preview(fixture(queries))
        b = build_preview(fixture(list(reversed(queries))))
        self.assertEqual(a, b)
        self.assertEqual(a["summary"]["planned_task_count"], 2)
        self.assertEqual(a["tasks"][1]["query_ids"], ["q_one", "q_two"])
        self.assertEqual(a["tasks"][1]["hypothesis_ids"], ["hyp_alt", "hyp_yoga"])
        self.assertEqual(a["summary"]["input_query_count"], 3)

    def test_casefold_nfkc_dedup_but_language_separate(self):
        qs = [query("q_first", "Ｆｉｔｎｅｓｓ", hypothesis_id="hyp_one"),
              query("q_second", "fitness", hypothesis_id="hyp_two"),
              query("q_third", "fitness", hypothesis_id="hyp_three", language="ru")]
        plan = build_preview(fixture(qs))
        self.assertEqual(len(plan["tasks"]), 2)
        self.assertEqual(set(plan["summary"]["covered_hypothesis_ids"]),
                         {"hyp_one", "hyp_two", "hyp_three"})

    def test_unsupported_mode_is_not_silently_converted(self):
        plan = build_preview(fixture([query("q_keyword"), query(
            "q_hashtag", "#homeworkout", mode="hashtag",
            hypothesis_id="hyp_hashtag")]))
        self.assertEqual(plan["summary"]["planned_task_count"], 1)
        self.assertEqual(plan["rejected_queries"][0]["reason"], "unsupported_capability")
        self.assertIn("partial_query_coverage", plan["blocks"])
        self.assertEqual(plan["summary"]["uncovered_hypothesis_ids"], ["hyp_hashtag"])
        none = build_preview(fixture([query(mode="hashtag")]))
        self.assertEqual(none["summary"]["planned_task_count"], 0)
        self.assertIn("no_supported_tasks", none["blocks"])

    def test_cannot_run_unsupported_future_adapter(self):
        plan = build_preview(fixture(), source_id="apify_tiktok")
        self.assertEqual(plan["tasks"], [])
        self.assertFalse(plan["execution_enabled"])
        with self.assertRaises(CollectorError):
            build_preview(fixture(), source_id="unregistered")

    def test_over_limit_plan_is_blocked_not_truncated(self):
        items = [query("q_%02d" % n, "fitness routine %d" % n) for n in range(3)]
        limits = {
            "max_tasks": 1, "max_pages_per_task": 3,
            "max_results_per_page": 7, "max_total_requests": 2,
            "max_total_candidates": 9,
        }
        preview = build_preview(fixture(items), limits=limits)
        self.assertEqual(preview["summary"]["planned_task_count"], 3)
        self.assertIn("max_tasks_exceeded", preview["blocks"])
        self.assertIn("max_total_requests_exceeded", preview["blocks"])
        self.assertIn("pagination_capability_not_verified", preview["blocks"])
        self.assertEqual(preview["summary"]["max_external_requests_upper_bound"], 9)
        self.assertEqual(preview["summary"]["requested_result_upper_bound"], 9)

    def test_bad_limit_rejected_not_bool_accepted(self):
        with self.assertRaises(CollectorError):
            build_preview(fixture(), limits={"max_tasks": True})
        with self.assertRaises(CollectorError):
            build_preview(fixture(), limits={
                "max_tasks": 1, "max_pages_per_task": 1,
                "max_results_per_page": 10, "max_total_requests": 1,
                "max_total_candidates": 10, "unapproved": 1,
            })

    def test_no_network_or_provider_calls_even_with_preview(self):
        with patch("socket.create_connection", side_effect=AssertionError("network called")), patch(
            "urllib.request.urlopen", side_effect=AssertionError("network called")
        ):
            result = build_preview(fixture())
            self.assertFalse(result["execution_enabled"])

    def test_cli_document_is_strict_no_fake_trust_flags(self):
        input_document = {
            "query_set": fixture(), "source_id": "dtk_tiktok", "limits": None,
        }
        output = preview_document(input_document)
        self.assertFalse(output["query_provenance_verified"])
        input_document["trust_me"] = True
        with self.assertRaises(CollectorError):
            preview_document(input_document)

    def test_serializable_with_complete_provenance(self):
        preview = build_preview(fixture())
        restored = json.loads(json.dumps(preview, ensure_ascii=False))
        self.assertEqual(restored["taxonomy_publication_id"], PUB)
        self.assertEqual(restored["generation_id"], GEN)
        self.assertEqual(restored["tasks"][0]["hypothesis_ids"], ["hyp_workout"])


class DownstreamContractTests(unittest.TestCase):
    def test_candidate_identity_and_page_validation(self):
        now = datetime.now(timezone.utc)
        item = CandidateEnvelope(
            project_id=PID,
            taxonomy_publication_id=PUB,
            source_id="dtk_tiktok",
            provider_task_id="task1",
            query_ids=("q_one",),
            hypothesis_ids=("hyp_one",),
            platform="tiktok",
            platform_video_id="7250000000000000000",
            canonical_url="https://www.tiktok.com/@author/video/7250000000000000000",
            fetched_at=now,
        )
        page = ProviderPage(
            source_id="dtk_tiktok", provider_task_id="task1", items=(item,), fetched_at=now,
        )
        self.assertEqual(len(page.items), 1)
        with self.assertRaises(CollectorError):
            CandidateEnvelope(
                project_id=PID, taxonomy_publication_id=PUB,
                source_id="dtk_tiktok", provider_task_id="task1",
                query_ids=("q_one",), hypothesis_ids=("hyp_one",),
                platform="tiktok", platform_video_id="99999",
                canonical_url="https://www.tiktok.com/@author/video/7250000000000000000",
                fetched_at=now,
            )
        with self.assertRaises(CollectorError):
            ProviderPage(
                source_id="dtk_tiktok", provider_task_id="task1",
                items=(), fetched_at=now, has_more="yes",
            )


if __name__ == "__main__":
    unittest.main()
