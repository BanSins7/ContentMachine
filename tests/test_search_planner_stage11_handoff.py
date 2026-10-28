"""SP-11 read-only, DB-trusted QV to Collector V1 compatibility projection.

The Collector branch is intentionally NOT imported. All offline fixtures
exercise format/provenance, not real Collector ACK or any provider operation.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from intelligence.search_planner.handoff import (
    HANDOFF_SCHEMA, MAX_CHUNK_SIZE,
    project_verified_query_handoff, verify_handoff_projection,
    VerifiedHandoffProjector,
)
from intelligence.search_planner.query_validation.linkage import build_selected_query_links
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from test_search_planner_stage10_task_planner import planning_input, PID, QV, GEN, PUB

UUID_TEMPLATE = "00000000-0000-4000-8000-{:012x}"


def synthetic_trusted_source(n):
    """Deterministic in-memory DB-equivalent fixture, NOT real PostgreSQL."""
    assert 0 <= n <= 480
    src = planning_input(no_work=True)
    bundle = src["validated_bundle"]
    bundle["queries"] = []
    bundle["coverage"] = []
    bundle["decisions"] = []
    counts = {}
    for i in range(n):
        cid = UUID_TEMPLATE.format(i + 1)
        hid = "hyp{:02d}".format(i // 16)
        rank = (i % 16) + 1
        counts[hid] = counts.get(hid, 0) + 1
        bundle["queries"].append({
            "candidate_id": cid,
            "hypothesis_id": hid,
            "direction_id": "direction01",
            "branch_id": "branch01",
            "source_fact_ids": ["fact_{}".format(i % 5)],
            "text": "тренировка вариант {}".format(i),
            "query_mode": "keyword",
            "angle": "direct",
            "language": "ru",
            "research_stance": "neutral",
            "rationale": "Synthetic QA only",
            "relevance_score": 0.81,
            "negative_score": 0.15,
            "mmr_score": 0.51,
            "rank_in_hypothesis": rank,
        })
        bundle["decisions"].append({
            "candidate_id": cid, "decision": "selected",
            "reason_code": "selected", "duplicate_of": None,
            "positive_similarity": 0.81, "negative_similarity": 0.15,
            "score_margin": 0.66, "mmr_score": 0.51,
        })
    for hid in sorted(counts):
        bundle["coverage"].append({
            "hypothesis_id": hid, "source_count": counts[hid],
            "selected_count": counts[hid], "outcome": "selected", "reason": None,
        })
    if n == 0:
        bundle["coverage"] = [{
            "hypothesis_id": "hyp00", "source_count": 0,
            "selected_count": 0, "outcome": "no_source_candidates",
            "reason": "No SQG candidates",
        }]
    links_rows = [
        (q["candidate_id"], q["hypothesis_id"], "selected", "selected",
         None, list(q["source_fact_ids"]), q["query_mode"], q["language"],
         q["text"])
        for q in bundle["queries"]
    ]
    src["shared_links"] = build_selected_query_links(
        links_rows, project_id=PID, validation_run_id=QV, selected_count=n
    )
    return src


class SP11Projection(unittest.TestCase):
    def test_versioned_schema_is_valid_json_and_rejects_execution(self):
        file = (Path(__file__).resolve().parents[1] /
                "docs" / "SEARCH_PLANNER_HANDOFF_V1.schema.json")
        schema = json.loads(file.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"],
                         "https://json-schema.org/draft/2020-12/schema")
        fields = schema["properties"]
        self.assertEqual(fields["schema_version"]["const"], HANDOFF_SCHEMA)
        self.assertIs(fields["external_dispatch_authorized"]["const"], False)
        self.assertEqual(fields["provider_calls_in_this_projection"]["const"], 0)
        self.assertEqual(fields["chunk_capacity"]["maximum"], 50)
        collector = fields["chunks"]["items"]["properties"]["collector_query_set"]
        self.assertEqual(collector["properties"]["schema_version"]["const"],
                         "validated_query_set/v1")
        self.assertEqual(collector["properties"]["queries"]["maxItems"], 50)

    def test_117_becomes_exact_50_50_17_and_not_silently_truncated(self):
        source = synthetic_trusted_source(117)
        original = deepcopy(source)
        result = project_verified_query_handoff(source)
        self.assertEqual(result["schema_version"], HANDOFF_SCHEMA)
        self.assertEqual(result["selected_query_count"], 117)
        self.assertEqual(result["chunk_count"], 3)
        self.assertEqual([q["chunk_size"] for q in result["chunks"]], [50, 50, 17])
        self.assertEqual([len(q["collector_query_set"]["queries"])
                          for q in result["chunks"]], [50, 50, 17])
        self.assertEqual(len(set(result["selected_query_ids"])), 117)
        self.assertEqual(
            set(result["selected_query_ids"]),
            {row["candidate_id"] for row in source["validated_bundle"]["queries"]},
        )
        self.assertEqual(
            [cid for ch in result["chunks"] for cid in ch["query_ids"]],
            result["selected_query_ids"],
        )
        self.assertEqual(source, original)
        self.assertEqual(result, project_verified_query_handoff(source))
        self.assertEqual(verify_handoff_projection(result, trusted_source=source), result)
        self.assertFalse(result["qv_outbox_acknowledged_by_this_projection"])
        self.assertFalse(result["external_dispatch_authorized"])
        self.assertIsNone(result["collector_receipt"])
        self.assertEqual(result["provider_calls_in_this_projection"], 0)

    def test_exact_collector_v1_keys_and_unchanged_uuid_mapping(self):
        source = synthetic_trusted_source(3)
        projected = project_verified_query_handoff(source)
        col = projected["chunks"][0]["collector_query_set"]
        self.assertEqual(set(col), {
            "schema_version", "project_id", "taxonomy_publication_id",
            "taxonomy_version", "taxonomy_sha256", "generation_id",
            "validation_version", "queries",
        })
        self.assertEqual(col["schema_version"], "validated_query_set/v1")
        self.assertEqual(col["project_id"], PID)
        self.assertEqual(col["taxonomy_publication_id"], PUB)
        self.assertEqual(col["generation_id"], GEN)
        self.assertEqual(col["validation_version"], "sp-qv-verified/v1")
        for raw, projected_q in zip(source["validated_bundle"]["queries"],
                                    col["queries"]):
            self.assertEqual(set(projected_q), {
                "query_id", "hypothesis_id", "direction_id", "mode",
                "text", "language", "validation_state",
            })
            self.assertEqual(projected_q["query_id"], raw["candidate_id"])
            self.assertEqual(projected_q["mode"], raw["query_mode"])
            self.assertEqual(projected_q["text"], raw["text"])
            self.assertEqual(projected_q["validation_state"], "accepted")

    def test_zero_selected_is_a_true_no_work_not_illegal_empty_collector_set(self):
        source = synthetic_trusted_source(0)
        result = project_verified_query_handoff(source)
        self.assertTrue(result["no_work_reason"])
        self.assertEqual(result["status"], "no_work")
        self.assertEqual(result["selected_query_count"], 0)
        self.assertEqual(result["chunk_count"], 0)
        self.assertEqual(result["chunks"], [])
        self.assertEqual(result["coverage"], source["validated_bundle"]["coverage"])
        self.assertEqual(verify_handoff_projection(result, trusted_source=source), result)
        self.assertIsNone(result["collector_receipt"])

    def test_boundaries_1_49_50_51_480_and_explicit_chunk_size(self):
        for n, lengths in (
            (1, [1]), (49, [49]), (50, [50]), (51, [50, 1]),
            (480, [50] * 9 + [30]),
        ):
            with self.subTest(n=n):
                result = project_verified_query_handoff(synthetic_trusted_source(n))
                self.assertEqual([x["chunk_size"] for x in result["chunks"]], lengths)
                self.assertEqual(sum(lengths), n)
        custom = project_verified_query_handoff(synthetic_trusted_source(117),
                                                 chunk_size=40)
        self.assertEqual([q["chunk_size"] for q in custom["chunks"]], [40, 40, 37])
        self.assertEqual(verify_handoff_projection(custom), custom)
        for bad in (0, -1, 51, True, "50"):
            with self.subTest(chunk=bad), self.assertRaises(PipelineError):
                project_verified_query_handoff(synthetic_trusted_source(1), chunk_size=bad)

    def test_exact_hypothesis_rank_uuid_order_reproducible(self):
        source = synthetic_trusted_source(37)
        original = project_verified_query_handoff(source)
        source["validated_bundle"]["queries"].reverse()
        source["validated_bundle"]["decisions"].reverse()
        source["shared_links"]["links"].reverse()
        source["shared_links"]["links_sha256"] = sha256(source["shared_links"]["links"])
        result = project_verified_query_handoff(source)
        self.assertEqual(result["selected_query_ids"], original["selected_query_ids"])
        self.assertEqual(
            [q["collector_query_set"] for q in result["chunks"]],
            [q["collector_query_set"] for q in original["chunks"]],
        )

    def test_full_lineage_keeps_rejected_alias_fact_and_qv_decision(self):
        source = synthetic_trusted_source(1)
        alias_id = UUID_TEMPLATE.format(180)
        source["validated_bundle"]["decisions"].append({
            "candidate_id": alias_id, "decision": "rejected",
            "reason_code": "exact_duplicate",
            "duplicate_of": source["validated_bundle"]["queries"][0]["candidate_id"],
            "positive_similarity": 0.75, "negative_similarity": 0.12,
            "score_margin": 0.63, "mmr_score": None,
        })
        source["validated_bundle"]["coverage"].append({
            "hypothesis_id": "hyp01", "source_count": 1,
            "selected_count": 0, "outcome": "all_rejected",
            "reason": "Exact equivalent selected under hyp00",
        })
        source["shared_links"]["links"][0]["linked_sources"].append({
            "candidate_id": alias_id,
            "hypothesis_id": "hyp01",
            "source_fact_ids": ["fact_approved_other_hypothesis"],
        })
        source["shared_links"]["links_sha256"] = sha256(source["shared_links"]["links"])
        result = project_verified_query_handoff(source)
        evidence = result["chunks"][0]["source_provenance"][0]
        self.assertEqual(len(evidence["linked_sources"]), 2)
        self.assertEqual(len(evidence["linked_qv_decisions"]), 2)
        self.assertEqual({r["hypothesis_id"] for r in evidence["linked_sources"]},
                         {"hyp00", "hyp01"})
        self.assertEqual({f for r in evidence["linked_sources"]
                          for f in r["source_fact_ids"]},
                         {"fact_0", "fact_approved_other_hypothesis"})
        self.assertEqual(result["selected_query_count"], 1)
        self.assertEqual(result["source_candidate_decision_count"], 2)

    def test_alias_to_an_unselected_or_foreign_target_rejected(self):
        source = synthetic_trusted_source(1)
        alias_id = UUID_TEMPLATE.format(230)
        source["validated_bundle"]["decisions"].append({
            "candidate_id": alias_id, "decision": "rejected",
            "reason_code": "exact_duplicate", "duplicate_of": UUID_TEMPLATE.format(240),
            "positive_similarity": 0.7, "negative_similarity": 0.1,
            "score_margin": 0.6, "mmr_score": None,
        })
        source["validated_bundle"]["coverage"][0]["source_count"] = 2
        source["shared_links"]["links"][0]["linked_sources"].append({
            "candidate_id": alias_id, "hypothesis_id": "hyp01",
            "source_fact_ids": [],
        })
        source["shared_links"]["links_sha256"] = sha256(source["shared_links"]["links"])
        with self.assertRaises(PipelineError):
            project_verified_query_handoff(source)

    def test_forged_accepted_json_does_not_override_qv_rejected_decision(self):
        src = synthetic_trusted_source(1)
        src["validated_bundle"]["decisions"][0]["decision"] = "rejected"
        src["validated_bundle"]["decisions"][0]["reason_code"] = "quota"
        src["validated_bundle"]["queries"][0]["validation_state"] = "accepted"
        with self.assertRaises(PipelineError):
            project_verified_query_handoff(src)

    def test_malformed_locale_text_hypothesis_or_direction_fails_closed(self):
        for mutate in (
            lambda s:s["validated_bundle"]["queries"][0].__setitem__("text", " two  spaces "),
            lambda s:s["validated_bundle"]["queries"][0].__setitem__("hypothesis_id", "0wrong"),
            lambda s:s["validated_bundle"]["queries"][0].__setitem__("direction_id", "hyp00"),
            lambda s:s["validated_bundle"]["queries"][0].__setitem__("language", "xx-YY-ZZ"),
            lambda s:s["validated_bundle"]["queries"][0].__setitem__("query_mode", "invalid"),
        ):
            src = synthetic_trusted_source(1)
            mutate(src)
            with self.subTest(mutation=str(mutate)), self.assertRaises(PipelineError):
                project_verified_query_handoff(src)

    def test_per_chunk_tampering_reorder_duplicate_and_missing_last_chunk(self):
        source = synthetic_trusted_source(117)
        original = project_verified_query_handoff(source)
        transforms = [
            lambda p:p["chunks"].pop(),
            lambda p:p["chunks"].reverse(),
            lambda p:p["chunks"][0]["query_ids"].__setitem__(
                0,p["chunks"][0]["query_ids"][1]),
            lambda p:p["chunks"][2].__setitem__("chunk_size", 16),
            lambda p:p["chunks"][1]["collector_query_set"]["queries"][0].__setitem__(
                "query_id", UUID_TEMPLATE.format(440)),
        ]
        for modify in transforms:
            bad = deepcopy(original)
            modify(bad)
            with self.subTest(case=str(modify)), self.assertRaises(PipelineError):
                verify_handoff_projection(bad)

    def test_rehashed_forged_payload_still_rejected_against_db_source(self):
        source = synthetic_trusted_source(1)
        p = project_verified_query_handoff(source)
        p["chunks"][0]["collector_query_set"]["queries"][0]["text"] = "forged query"
        c = p["chunks"][0]
        c["collector_query_set_sha256"] = sha256(c["collector_query_set"])
        c["chunk_sha256"] = sha256({k:v for k,v in c.items() if k != "chunk_sha256"})
        p["projection_sha256"] = sha256({k:v for k,v in p.items()
                                       if k != "projection_sha256"})
        with self.assertRaises(PipelineError):
            verify_handoff_projection(p, trusted_source=source)

    def test_handoff_public_projector_reads_only_verified_database_input(self):
        source = synthetic_trusted_source(117)
        requested = []
        def reader(pid, rid):
            requested.append((pid,rid))
            return deepcopy(source)
        projector = VerifiedHandoffProjector(planning_reader=reader)
        result = projector.preview(PID, QV)
        self.assertEqual(result["selected_query_count"], 117)
        self.assertEqual(requested, [(PID, QV)])
        with self.assertRaises(PipelineError):
            projector.preview("wrong-project", QV)
        with self.assertRaises(PipelineError):
            projector.preview(PID, "accepted-by-browser")
        self.assertEqual(requested, [(PID, QV)])

    def test_search_planner_facade_exposes_read_only_projection_only(self):
        from intelligence.search_planner import SearchPlannerService
        src = synthetic_trusted_source(51)
        calls = []
        projector = VerifiedHandoffProjector(
            planning_reader=lambda pid, vid: (
                calls.append((pid, vid)) or deepcopy(src)
            )
        )
        svc = SearchPlannerService(handoff_projector=projector)
        result = svc.preview_verified_handoff(PID, QV)
        self.assertEqual([c["chunk_size"] for c in result["chunks"]], [50, 1])
        self.assertFalse(result["qv_outbox_acknowledged_by_this_projection"])
        self.assertFalse(result["external_dispatch_authorized"])
        self.assertEqual(calls, [(PID, QV)])
        self.assertIn("preview_verified_handoff", svc.status().to_dict()["read_only_operations"])
        with self.assertRaises(PipelineError):
            SearchPlannerService(handoff_projector={})
        with self.assertRaises(PipelineError):
            svc.preview_verified_handoff(PID, QV, chunk_size=51)

    def test_fixture_embeddings_are_not_released_as_production(self):
        source = synthetic_trusted_source(1)
        source["validated_bundle"]["embedding_mode"] = "test_fixture"
        with self.assertRaises(PipelineError):
            project_verified_query_handoff(source)


if __name__ == "__main__":
    unittest.main()
