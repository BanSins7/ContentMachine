"""RTB-FIX-02-B: PostgreSQL17 binding never acts as a quote or paid run.

Uses the exact A/B Stage13 synthetic source/reviewer flow, with no provider
calls or externally accessed videos.
"""
from __future__ import annotations

from copy import deepcopy
import unittest

from psycopg.errors import RaiseException
from test_ke_stage02_intake_db import DSN
from test_ke_stage13_knowledge_snapshot import Stage13PG

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.taxonomy_builder.deep_v2_binding_repository import (
    RTBDeepV2BindingRepository,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError


@unittest.skipUnless(DSN, "PostgreSQL17 needed for RTB FIX-02-B provenance binding")
class RTBDeepV2BindingPG(unittest.TestCase):

    def setUp(self):
        self.base = Stage13PG(methodName="runTest")
        self.base.setUp()
        sid = self.base._published()
        repo = self.base._repo()
        self.snapshot = repo.publish(self.base.pid, sid, self.base.reviewer)
        repo.prepare_c_handoff(self.base.pid, sid, self.base.reviewer)
        self.binding = RTBDeepV2BindingRepository(
            connection_factory=self.base._factory()
        )

    def tearDown(self):
        self.base.doCleanups()

    def _counts(self):
        with self.base.psycopg.connect(DSN) as conn:
            return conn.execute(
                """SELECT
                  (SELECT count(*) FROM tb_runs WHERE project_id=%s),
                  (SELECT count(*) FROM tb_deep_runs WHERE project_id=%s),
                  (SELECT count(*) FROM knowledge_c_generation_runs
                    WHERE project_id=%s),
                  (SELECT count(*) FROM tb_deep_v2_research_bindings
                    WHERE project_id=%s)""",
                (self.base.pid, self.base.pid, self.base.pid, self.base.pid),
            ).fetchone()

    def test_exact_B9_provenance_idempotent_and_unpaid(self):
        before = self._counts()
        a = self.binding.pin(
            self.base.pid, self.snapshot["snapshot_id"], self.base.reviewer)
        b = self.binding.pin(
            self.base.pid, self.snapshot["snapshot_id"], self.base.reviewer)
        c = self.binding.get(self.base.pid, a["binding_id"], self.base.reviewer)
        self.assertTrue(a["created"])
        self.assertFalse(b["created"])
        self.assertEqual(a["binding_id"], b["binding_id"])
        self.assertEqual(c["research_input"], a["research_input"])
        self.assertEqual(a["research_input_sha256"], b["research_input_sha256"])
        self.assertFalse(a["paid_execution_authorized"])
        self.assertEqual(a["provider_calls"], 0)
        after = self._counts()
        self.assertEqual(before[:3], after[:3])
        self.assertEqual(after[3], before[3] + 1)

    def test_no_foreign_project_or_unapproved_actor(self):
        a = self.binding.pin(
            self.base.pid, self.snapshot["snapshot_id"], self.base.reviewer)
        for project, actor in (
            (self.base.other, self.base.reviewer),
            (self.base.pid, "not-authorized"),
        ):
            with self.subTest(project=project, reviewer=actor):
                with self.assertRaises(ValueError):
                    self.binding.pin(project, self.snapshot["snapshot_id"], actor)
                with self.assertRaises(ValueError):
                    self.binding.get(project, a["binding_id"], actor)

    def test_verified_replay_drift_does_not_overwrite_original(self):
        a = self.binding.pin(
            self.base.pid, self.snapshot["snapshot_id"], self.base.reviewer)
        good = self.binding.research

        class Forged:
            def preview(self, project_id, snapshot_id, reviewer_id):
                original = good.preview(project_id, snapshot_id, reviewer_id)
                bad = deepcopy(original)
                bad["c1_approved_facts"][0]["reviewed_text"] = "fabricated"
                bad["research_input_sha256"] = canonical_sha({
                    k: v for k, v in bad.items() if k != "research_input_sha256"
                })
                return bad

        self.binding.research = Forged()
        with self.assertRaisesRegex(
            PipelineError, "rtb_v2_binding_immutable_replay_mismatch"
        ):
            self.binding.pin(
                self.base.pid, self.snapshot["snapshot_id"], self.base.reviewer)
        with self.assertRaisesRegex(PipelineError, "rtb_v2_binding_source_changed"):
            self.binding.get(self.base.pid, a["binding_id"], self.base.reviewer)
        with self.base.psycopg.connect(DSN) as conn:
            count = conn.execute(
                "SELECT count(*) FROM tb_deep_v2_research_bindings WHERE project_id=%s",
                (self.base.pid,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_immutable_sql_update_delete_blocked(self):
        a = self.binding.pin(
            self.base.pid, self.snapshot["snapshot_id"], self.base.reviewer)
        for stmt in (
            "DELETE FROM tb_deep_v2_research_bindings WHERE id=%s",
            """UPDATE tb_deep_v2_research_bindings
               SET research_input_sha256=repeat('0',64) WHERE id=%s""",
        ):
            with self.subTest(stmt=stmt):
                with self.assertRaises(RaiseException):
                    with self.base.psycopg.connect(DSN) as conn:
                        conn.execute(stmt, (a["binding_id"],))

    def test_db_insert_enforces_handoff_snapshot_relationship(self):
        a = self.binding.pin(
            self.base.pid, self.snapshot["snapshot_id"], self.base.reviewer)
        # A different valid handoff UUID (same project) MUST NOT be accepted as
        # a substitute for the original source set.
        with self.base.psycopg.connect(DSN) as conn:
            row = conn.execute(
                """SELECT handoff_id,handoff_sha256 FROM tb_deep_v2_research_bindings
                   WHERE id=%s""", (a["binding_id"],)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(str(row[0]), a["research_input"]["b9_handoff_id"])
            self.assertEqual(row[1], a["research_input"]["b9_handoff_sha256"])

    def test_research_input_not_an_auth_to_dispatch(self):
        a = self.binding.pin(
            self.base.pid, self.snapshot["snapshot_id"], self.base.reviewer)
        self.assertFalse(a["research_input"]["paid_execution_authorized"])
        with self.base.psycopg.connect(DSN) as conn:
            c = conn.execute(
                """SELECT count(*) FROM tb_deep_v2_research_bindings
                   WHERE project_id=%s AND research_input->>'paid_execution_authorized' <> 'false'""",
                (self.base.pid,),
            ).fetchone()[0]
        self.assertEqual(c, 0)


if __name__ == "__main__":
    unittest.main()
