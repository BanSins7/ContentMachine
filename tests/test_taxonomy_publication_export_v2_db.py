"""RTB FIX-08 PG17: publication-ID read-only export via real signed B9/A8.

Fixture reuses mocked Opus chain and SQL059 publication. Neither export path
nor consumer context building can execute LLMs or issue any paid request.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4
import unittest

from test_ke_stage02_intake_db import DSN
from test_taxonomy_deep_v2_publication_db import DeepV2PublicationPG
from intelligence.taxonomy_builder.publication_export_v2 import (
    SCHEMA_V1, SCHEMA_V2, RTBDeepV2PublicationExportRepository,
    to_search_planner_v1_snapshot, validate_publication_export,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.search_planner.contracts import validate_published_snapshot
from intelligence.search_planner.context_builder import build_hypothesis_contexts


@unittest.skipUnless(DSN, "PostgreSQL17 required for RTB published export")
class DeepV2ExportPG(unittest.TestCase):
    def setUp(self):
        self.fx = DeepV2PublicationPG(methodName="runTest")
        self.fx.setUp()
        self.factory = self.fx.factory
        self.pid,self.rid,self.actor = (
            self.fx.pid,self.fx.rid,self.fx.actor,
        )
        self.published = self.fx.repo.publish(self.pid,self.rid,self.actor)
        self.pub_id = self.published["publication_id"]
        self.repo = RTBDeepV2PublicationExportRepository(
            connection_factory=self.factory)

    def tearDown(self):
        self.fx.tearDown()

    def _counts(self):
        with self.factory() as conn:
            return conn.execute(
                """SELECT
                  (SELECT count(*) FROM tb_deep_v2_publications
                   WHERE project_id=%s),
                  (SELECT count(*) FROM tb_deep_v2_sre_links
                   WHERE project_id=%s),
                  (SELECT count(*) FROM sre_taxonomy_versions
                   WHERE project_id=%s),
                  (SELECT count(*) FROM tb_deep_v2_attempts
                   WHERE project_id=%s AND run_id=%s)""",
                (self.pid,self.pid,self.pid,self.pid,self.rid),
            ).fetchone()

    def test_by_publication_id_v1_exact_11_and_v2_exact_14(self):
        one = self.repo.export(
            self.pid,self.pub_id,self.actor,accepted_versions=[SCHEMA_V1])
        two = self.repo.export(
            self.pid,self.pub_id,self.actor,accepted_versions=[SCHEMA_V2])
        self.assertEqual(one["schema_version"],SCHEMA_V1)
        self.assertEqual(two["schema_version"],SCHEMA_V2)
        self.assertTrue(all(len(n)==11 for n in one["nodes"]))
        self.assertTrue(all(len(n)==14 for n in two["nodes"]))
        self.assertEqual(one["v1_core_sha256"],two["v1_core_sha256"])
        self.assertEqual(one["publication_id"],self.pub_id)
        self.assertEqual(two["publication_id"],self.pub_id)
        self.assertEqual(two["nodes"],self.fx.signed_h2["approved_nodes"])
        self.assertEqual(one["provider_calls"],0)
        self.assertEqual(validate_publication_export(two),two)

    def test_search_planner_v1_validator_and_hypothesis_context(self):
        dto=self.repo.export(
            self.pid,self.pub_id,self.actor,accepted_versions=[SCHEMA_V1])
        legacy=to_search_planner_v1_snapshot(dto)
        approved=validate_published_snapshot(legacy)
        self.assertEqual(approved["tree"],dto["nodes"])
        result=build_hypothesis_contexts(legacy,{
            "languages":["en"],"markets":[],
            "allowed_modes":["keyword"],
            "target_candidates_per_hypothesis":1,
            "max_candidates_per_hypothesis":3,
            "max_total_candidates":30,
        })
        self.assertGreaterEqual(len(result),2)
        self.assertEqual(result[0]["taxonomy_publication_id"],self.pub_id)

    def test_foreign_project_or_missing_publication_fails_closed(self):
        with self.assertRaises(PipelineError):
            self.repo.export("f"*32,self.pub_id,self.actor,
                             accepted_versions=[SCHEMA_V1])
        with self.assertRaises(PipelineError):
            self.repo.export(self.pid,str(uuid4()),self.actor,
                             accepted_versions=[SCHEMA_V2])
        with self.assertRaises(PipelineError):
            self.repo.export(self.pid,self.rid,self.actor,
                             accepted_versions=[SCHEMA_V2])

    def test_unauthorized_reviewer_and_unsupported_version_refused(self):
        with self.assertRaises((ValueError,PipelineError)):
            self.repo.export(
                self.pid,self.pub_id,"not-authorized",
                accepted_versions=[SCHEMA_V1])
        with self.assertRaisesRegex(PipelineError,"version"):
            self.repo.export(
                self.pid,self.pub_id,self.actor,
                accepted_versions=["rtb-publication-export/v900"])

    def test_no_db_writes_or_provider_calls_in_repeated_reader(self):
        before=self._counts()
        for _ in range(4):
            for version in (SCHEMA_V1,SCHEMA_V2):
                output=self.repo.export(
                    self.pid,self.pub_id,self.actor,
                    accepted_versions=[version])
                self.assertEqual(output["provider_calls"],0)
        self.assertEqual(self._counts(),before)

    def test_concurrent_consumer_reads_are_identical(self):
        def read(_):
            return self.repo.export(
                self.pid,self.pub_id,self.actor,
                accepted_versions=[SCHEMA_V2])
        with ThreadPoolExecutor(max_workers=3) as pool:
            outs=list(pool.map(read, range(3)))
        self.assertTrue(all(out==outs[0] for out in outs))
        self.assertEqual(self._counts()[0:2],(1,1))

    def test_missing_original_b9_a_binding_is_rejected_without_fallback(self):
        original=self.repo.bindings.get
        def corrupt(pid,bid,actor):
            record=deepcopy(original(pid,bid,actor))
            record["research_input_sha256"]="f"*64
            return record
        with patch.object(self.repo.bindings,"get",side_effect=corrupt):
            with self.assertRaisesRegex(PipelineError,"binding_replay_drift"):
                self.repo.export(
                    self.pid,self.pub_id,self.actor,
                    accepted_versions=[SCHEMA_V2])

    def test_legacy_adapter_never_mutates_source_or_invents_policy(self):
        dto=self.repo.export(
            self.pid,self.pub_id,self.actor,accepted_versions=[SCHEMA_V1])
        baseline=deepcopy(dto)
        wrapped=to_search_planner_v1_snapshot(dto)
        wrapped["tree"][0]["name"]="mutable client copy"
        self.assertEqual(dto,baseline)
        self.assertNotIn("languages",dto["profile_snapshot"])
        self.assertIsNone(dto["profile_snapshot"]["research_goal"])
        self.assertTrue(dto["source_lineage"]
                        ["observations_are_original_a_not_approved_product_facts"])

if __name__ == "__main__":
    unittest.main()
