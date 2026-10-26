"""VC-MOD-05: technical registry/planner over a sealed QV source (offline)."""
from __future__ import annotations

from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from intelligence.taxonomy_builder.pipeline_contracts import canonical, sha256
from intelligence.collector.contracts import CollectorError
from intelligence.collector.manifest_planner import build_manifest_technical_preview
from intelligence.collector.registry import list_sources, match_source
from test_video_collector_qv_publisher_v1 import make_source


def with_hashtag(source):
    rows = source.selected
    rows[0]["query_mode"] = "hashtag"
    new_json = canonical(rows)
    digest = sha256({
        "project_id": source.project_id, "outbox_id": source.outbox_id,
        "validation_run_id": source.validation_run_id,
        "generation_id": source.generation_id,
        "publication_id": source.publication_id,
        "taxonomy_version": source.taxonomy_version,
        "taxonomy_sha256": source.taxonomy_sha256,
        "config_sha256": source.config_sha256,
        "source_bundle_sha256": source.source_bundle_sha256,
        "embedding_model_key": source.embedding_model_key,
        "source_candidate_count": source.source_candidate_count,
        "queries": rows, "coverage": source.coverage,
        "decisions": source.decisions,
    })
    return replace(source, _queries_json=new_json, bundle_sha256=digest)


class ManifestPlannerTests(TestCase):
    def test_registry_rejects_unimplemented_capabilities_without_fallback(self):
        src, op = match_source("dtk_tiktok", "keyword")
        self.assertEqual(op, "search_posts")
        self.assertEqual(src.platform, "tiktok")
        _, unsupported = match_source("dtk_tiktok", "hashtag")
        self.assertIsNone(unsupported)
        self.assertFalse(src.operationally_verified)
        self.assertFalse(src.source_policy_verified)
        names = {x["source_id"] for x in list_sources()}
        self.assertIn("dtk_tiktok", names)
        with self.assertRaises(CollectorError):
            match_source("not_registered", "keyword")

    def test_one_shard_one_keyword_no_network(self):
        source = make_source(1)
        with patch("intelligence.connectors.dtk.submit") as provider:
            preview = build_manifest_technical_preview(source)
        provider.assert_not_called()
        self.assertEqual(preview["schema_version"], "collector.manifest_technical_plan/v1")
        self.assertEqual(preview["task_count"], 1)
        self.assertEqual(preview["query_covered_count"], 1)
        self.assertEqual(preview["shard_count"], 1)
        self.assertFalse(preview["execution_authorized"])
        self.assertEqual(preview["external_calls"], 0)
        self.assertEqual(preview["shards"][0]["tasks"][0]["operation"], "search_posts")
        self.assertIn("source_policy_not_verified", preview["blocks"])
        self.assertIn("provider_connection_not_verified", preview["blocks"])

    def test_480_preserved_across_shards_but_global_quota_blocks(self):
        source = make_source(480)
        data = build_manifest_technical_preview(source)
        self.assertEqual(data["selected_count"], 480)
        self.assertEqual(data["shard_count"], 24)
        self.assertEqual(data["query_covered_count"], 480)
        self.assertEqual(data["query_rejected_count"], 0)
        self.assertEqual(data["task_count"], 480)
        self.assertEqual(data["max_external_requests_upper_bound"], 480)
        self.assertIn("manifest_request_cap_exceeded", data["blocks"])
        self.assertEqual(sum(x["selected_count"] for x in data["shards"]), 480)
        ids = [qid for shard in data["shards"] for task in shard["tasks"]
               for qid in task["query_ids"]]
        self.assertEqual(len(set(ids)), 480)

    def test_shard_boundaries_20_21_and_50(self):
        s = make_source(21)
        a = build_manifest_technical_preview(s)
        self.assertEqual([x["selected_count"] for x in a["shards"]], [20, 1])
        b = build_manifest_technical_preview(s, shard_size=50)
        self.assertEqual([x["selected_count"] for x in b["shards"]], [21])
        self.assertNotEqual(a["manifest_sha256"], b["manifest_sha256"])

    def test_empty_selected_source_has_no_task_or_provider_request(self):
        data = build_manifest_technical_preview(make_source(0))
        self.assertEqual(data["shards"], [])
        self.assertEqual(data["task_count"], 0)
        self.assertEqual(data["status"], "no_queries")
        self.assertIn("no_queries", data["blocks"])
        self.assertEqual(data["external_calls"], 0)

    def test_hashtag_is_explicitly_rejected_not_converted_to_keyword(self):
        source = with_hashtag(make_source(1))
        result = build_manifest_technical_preview(source)
        self.assertEqual(result["query_covered_count"], 0)
        self.assertEqual(result["query_rejected_count"], 1)
        self.assertEqual(result["task_count"], 0)
        self.assertEqual(result["shards"][0]["rejected"][0]["reason"],
                         "unsupported_capability")
        self.assertIn("partial_query_coverage", result["blocks"])

    def test_future_providers_do_not_fake_capabilities(self):
        for provider in ("youtube_official", "instagram_official", "apify_tiktok"):
            with self.subTest(provider=provider):
                data = build_manifest_technical_preview(make_source(1),
                                                        source_id=provider)
                self.assertEqual(data["task_count"], 0)
                self.assertEqual(data["query_rejected_count"], 1)
                self.assertIn("partial_query_coverage", data["blocks"])

    def test_caps_and_forged_source_fail_closed(self):
        source = make_source(1)
        with self.assertRaises(CollectorError):
            build_manifest_technical_preview({"selected": source.selected})
        for n in (0, 51, True):
            with self.subTest(shard_size=n), self.assertRaises(CollectorError):
                build_manifest_technical_preview(source, shard_size=n)
        for n in (0, 251, True):
            with self.subTest(limit=n), self.assertRaises(CollectorError):
                build_manifest_technical_preview(source, total_request_cap=n)

    def test_output_determinism(self):
        source = make_source(21)
        left = build_manifest_technical_preview(source)
        right = build_manifest_technical_preview(source)
        self.assertEqual(left["plan_hash"], right["plan_hash"])
        self.assertEqual(left["manifest_sha256"], right["manifest_sha256"])
        self.assertEqual(left["shards"], right["shards"])

    def test_language_and_uuid_query_provenance_kept(self):
        source = make_source(21)
        p = build_manifest_technical_preview(source)
        ids = set()
        for item in p["shards"]:
            for task in item["tasks"]:
                self.assertEqual(task["language"], "en")
                self.assertEqual(task["platform"], "tiktok")
                ids.update(task["query_ids"])
        self.assertEqual(ids, {q["candidate_id"] for q in source.selected})
