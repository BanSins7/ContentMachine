"""RTB FIX-09-C3 PostgreSQL17 route-to-repository integration without Opus.

The fixture publishes a B9/A8/H1/H2 approved taxonomy using mocked provider.
These tests exercise the actual HTTP dispatch adapter with a real PG17
connection_factory, not live sockets or real paid provider requests.
"""
from __future__ import annotations

import unittest
from uuid import uuid4

from test_ke_stage02_intake_db import DSN
from test_taxonomy_deep_v2_publication_db import DeepV2PublicationPG

from intelligence.taxonomy_builder import deep_v2_http as api
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError


@unittest.skipUnless(DSN, "PostgreSQL17 needed for RTB Deep V2 HTTP adapter")
class DeepV2HTTPPostgres(unittest.TestCase):
    def setUp(self):
        self.fx=DeepV2PublicationPG(methodName="runTest")
        self.fx.setUp()
        self.pid,self.rid,self.actor=(
            self.fx.pid,self.fx.rid,self.fx.actor)
        self.factory=self.fx.factory
        self.pub=self.fx.repo.publish(self.pid,self.rid,self.actor)
        self.pubid=self.pub["publication_id"]

    def tearDown(self):
        self.fx.tearDown()

    def _get(self,endpoint,query=""):
        return api.dispatch_get(
            self.pid,endpoint,self.actor,query,factory=self.factory)

    def _post(self,endpoint,body,origin="http://127.0.0.1:8765"):
        return api.dispatch_post(
            self.pid,endpoint,body,self.actor,
            origin=origin,factory=self.factory)

    def _counts(self):
        with self.factory() as conn:
            return conn.execute(
                """SELECT
                   (SELECT count(*) FROM tb_deep_v2_attempts
                     WHERE project_id=%s AND run_id=%s),
                   (SELECT count(*) FROM tb_deep_v2_publications
                     WHERE project_id=%s AND run_id=%s),
                   (SELECT count(*) FROM tb_deep_v2_sre_links
                     WHERE project_id=%s AND run_id=%s),
                   (SELECT count(*) FROM tb_deep_v2_reviews
                     WHERE project_id=%s AND run_id=%s)""",
                (self.pid,self.rid,self.pid,self.rid,
                 self.pid,self.rid,self.pid,self.rid),
            ).fetchone()

    def test_get_status_review1_review2_and_publication_are_read_only(self):
        before=self._counts()
        status=self._get(f"runs/{self.rid}/status")
        self.assertEqual(status["status"],"published")
        self.assertIsInstance(status["expansion_count"],int)
        first=self._get(f"runs/{self.rid}/review1")
        second=self._get(f"runs/{self.rid}/review2")
        out=self._get(f"runs/{self.rid}/publication")
        self.assertEqual(out["publication_id"],self.pubid)
        self.assertEqual(first["approved_nodes_sha256"],second["readiness"]["h1_sha256"])
        self.assertEqual(second["approved_sha256"],out["v2_tree_sha256"])
        self.assertEqual(self._counts(),before)

    def test_export_requires_version_and_keeps_11_14_evidence_boundary(self):
        before=self._counts()
        v1=self._get(f"exports/{self.pubid}",
                     "version=rtb-publication-export/v1")
        v2=self._get(f"exports/{self.pubid}",
                     "version=rtb-publication-export/v2")
        self.assertTrue(all(len(n)==11 for n in v1["nodes"]))
        self.assertTrue(all(len(n)==14 for n in v2["nodes"]))
        self.assertNotIn("original_a_observations",v1)
        self.assertIn("original_a_observations",v2)
        self.assertEqual(v1["publication_id"],self.pubid)
        self.assertEqual(v1["source_lineage"]["research_input_sha256"],
                         v2["source_lineage"]["research_input_sha256"])
        self.assertEqual(self._counts(),before)

    def test_post_published_replay_does_not_create_second_sre_version(self):
        before=self._counts()
        again=self._post(
            f"runs/{self.rid}/publish",{"confirm_publication":True})
        self.assertFalse(again["created"])
        self.assertEqual(again["publication_id"],self.pubid)
        self.assertEqual(again["provider_calls"],0)
        self.assertEqual(self._counts(),before)

    def test_client_cannot_bypass_b9_or_publish_wrong_project(self):
        before=self._counts()
        with self.assertRaises(Exception):
            api.dispatch_get(
                "f"*32,f"runs/{self.rid}/publication",
                self.actor,factory=self.factory)
        with self.assertRaises(Exception):
            api.dispatch_get(
                self.pid,f"runs/{self.rid}/review1",
                "unknown-and-disabled-reviewer",factory=self.factory)
        with self.assertRaises(PipelineError):
            self._get(f"exports/{uuid4()}", "version=rtb-publication-export/v2")
        self.assertEqual(self._counts(),before)

    def test_no_state_change_for_forbidden_origin_or_paid_missing_intent(self):
        before=self._counts()
        with self.assertRaisesRegex(PipelineError,"same_origin_required"):
            self._post(
                f"runs/{self.rid}/publish",{"confirm_publication":True},
                origin="http://evil.example")
        with self.assertRaisesRegex(PipelineError,"explicit_action_required"):
            self._post(
                f"runs/{self.rid}/advance-pass1",
                {"authorize_paid_call":False})
        with self.assertRaisesRegex(PipelineError,"explicit_version_required"):
            self._get(f"exports/{self.pubid}")
        self.assertEqual(self._counts(),before)


if __name__=="__main__":
    unittest.main()
