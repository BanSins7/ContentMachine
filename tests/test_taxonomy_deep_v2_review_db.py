"""PostgreSQL17 Human Review1 DB: approved immutable H1 and Pass2 pending.

No OpenRouter calls. Pass1 is always a synthetic mocked sender with
known charge, not a real provider request.
"""
from __future__ import annotations

from copy import deepcopy
import unittest

from psycopg.errors import RaiseException, UniqueViolation
from test_ke_stage02_intake_db import DSN
from test_taxonomy_deep_v2_pass1_db import DeepV2Pass1PG

from intelligence.taxonomy_builder.deep_v2_review_repository import (
    RTBDeepV2HumanReviewRepository,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError


@unittest.skipUnless(DSN, "PostgreSQL17 needed for RTB Human Review1")
class RTBDeepV2Review1PG(unittest.TestCase):
    def setUp(self):
        self.fx = DeepV2Pass1PG(methodName="runTest")
        self.fx.setUp()
        result = self.fx._advance(self.fx._sender())
        self.assertEqual(result["status"], "review1")
        self.repo = RTBDeepV2HumanReviewRepository(
            connection_factory=self.fx.fx.factory)
        self.pid = self.fx.fx.pid
        self.run_id = self.fx.started["run_id"]
        self.actor = self.fx.fx.reviewer
        self.proposal = self.fx._nodes()

    def tearDown(self):
        self.fx.tearDown()

    def _approve(self):
        return [{"action": "approve", "id": n["id"]}
                for n in self.proposal]

    def test_approve_all_pins_sha_and_advances_to_pass2_pending_without_payment(self):
        before = self.repo.inspect_pass1(self.pid, self.run_id, self.actor)
        result = self.repo.submit_review1(self.pid, self.run_id,
                                          self.actor, self._approve())
        self.assertEqual(result["status"], "pass2_pending")
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["approved_nodes"], self.proposal)
        persisted = self.repo.get_review1(self.pid, self.run_id, self.actor)
        self.assertEqual(persisted["approved_nodes_sha256"],
                         result["approved_nodes_sha256"])
        self.assertEqual(persisted["approved_nodes"], self.proposal)
        after = self.repo.inspect_pass1(self.pid, self.run_id, self.actor)
        self.assertEqual(after["status"], "pass2_pending")
        self.assertEqual(after["spent_usd"], before["spent_usd"])
        self.assertEqual(after["reserved_usd"], before["reserved_usd"])

    def test_cannot_reapprove_or_edit_immutable_review(self):
        self.repo.submit_review1(self.pid, self.run_id,
                                 self.actor, self._approve())
        with self.assertRaisesRegex(PipelineError, "review1_not_pending"):
            self.repo.submit_review1(self.pid, self.run_id,
                                     self.actor, self._approve())
        with self.assertRaises(RaiseException):
            with self.fx.fx.factory() as conn:
                conn.execute(
                    """DELETE FROM tb_deep_v2_reviews
                       WHERE project_id=%s AND run_id=%s""",
                    (self.pid, self.run_id),
                )

    def test_incomplete_decisions_roll_back_state(self):
        with self.assertRaisesRegex(PipelineError, "review_incomplete"):
            self.repo.submit_review1(
                self.pid, self.run_id, self.actor, self._approve()[:-1])
        self.assertEqual(
            self.repo.inspect_pass1(self.pid, self.run_id, self.actor)["status"],
            "review1")
        with self.fx.fx.factory() as conn:
            count = conn.execute(
                """SELECT count(*) FROM tb_deep_v2_reviews
                   WHERE project_id=%s AND run_id=%s""",
                (self.pid,self.run_id),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_reject_and_reparent_are_not_silently_permitted(self):
        decisions = self._approve()
        decisions[0] = {"action": "reject", "id": self.proposal[0]["id"]}
        with self.assertRaises(PipelineError):
            self.repo.submit_review1(self.pid, self.run_id, self.actor, decisions)
        self.assertEqual(
            self.repo.inspect_pass1(self.pid, self.run_id, self.actor)["status"],
            "review1")

    def test_human_approved_text_can_change_not_provenance(self):
        decisions = self._approve()
        edited = deepcopy(self.proposal[1])
        edited["search_signals"] = ["New manually validated comparison hook"]
        decisions[1] = {"action": "edit", "id": edited["id"], "node": edited}
        result = self.repo.submit_review1(self.pid, self.run_id, self.actor, decisions)
        self.assertEqual(result["approved_nodes"][1]["search_signals"],
                         edited["search_signals"])
        self.assertEqual(result["status"], "pass2_pending")

    def test_foreign_project_and_unauthorized_reviewer_cannot_read_or_submit(self):
        with self.assertRaises((ValueError,PipelineError)):
            self.repo.submit_review1(
                self.fx.fx.base.other, self.run_id, self.actor, self._approve())
        with self.assertRaises((ValueError,PipelineError)):
            self.repo.get_review1(self.pid, self.run_id, "not-authorized")
        self.assertEqual(
            self.repo.inspect_pass1(self.pid,self.run_id,self.actor)["status"],
            "review1")


if __name__ == "__main__":
    unittest.main()
