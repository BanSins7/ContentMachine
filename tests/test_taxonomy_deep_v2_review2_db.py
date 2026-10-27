"""PG17 Human Review2: true source-bound H1 + mock Pass2 + immutable readiness.

Never calls real OpenRouter. Review2 acceptance must be one SQL transaction
with gate report, H1 preservation, all source citations and no hidden spend.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from uuid import uuid4
import json
import unittest

from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb

from test_ke_stage02_intake_db import DSN
from test_taxonomy_deep_v2_review_db import RTBDeepV2Review1PG

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.taxonomy_builder.deep_v2_pass2 import RTBDeepV2Pass2Repository
from intelligence.taxonomy_builder.deep_v2_review2_repository import (
    RTBDeepV2Review2Repository,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError


@unittest.skipUnless(DSN, "PostgreSQL17 required for RTB Deep V2 Human Review2")
class DeepV2Review2PG(unittest.TestCase):
    def setUp(self):
        self.fx = RTBDeepV2Review1PG(methodName="runTest")
        self.fx.setUp()
        self.pid, self.rid, self.actor = (
            self.fx.pid, self.fx.run_id, self.fx.actor)
        self.factory = self.fx.repo.connection_factory
        self.repo = RTBDeepV2Review2Repository(connection_factory=self.factory)
        source = self.fx.fx.source
        # Stage13's real approved B9 snapshot may contain only a single
        # non-own-product function fact: it does NOT promise a problem fact.
        # Do not forge one or silently upgrade source rights for this test.
        facts = source["c1_approved_facts"]
        problem = next((f for f in facts if f["kind"] == "problem"), None)
        decisions = []
        for index, node in enumerate(self.fx.proposal):
            changed = deepcopy(node)
            changed["definition"] = (
                f"Distinct research intent scenario {index}: "
                + ("test real features and demonstrations" if index < 2 else
                   "investigate audience obstacles and objections")
            )
            changed["rationale"] = f"Specific grounded research format {index}"
            if index >= 2:
                changed["relation"] = (
                    "problem_related" if problem is not None else "adjacent"
                )
                changed["source_fact_ids"] = [
                    problem["rtb_fact_id"] if problem is not None
                    else facts[0]["rtb_fact_id"]
                ]
            if changed["kind"] == "hypothesis":
                changed["search_signals"] = [
                    "Comparison demonstrations for approved product functions"
                    if index == 1 else
                    "Audience problem and obstacle interviews"
                ]
                changed["validation_focus"] = [
                    "Confirm clip evidence for functional demonstrations"
                    if index == 1 else
                    "Check audience objection evidence against real footage"
                ]
            decisions.append({"action": "edit", "id": node["id"], "node": changed})
        self.h1 = self.fx.repo.submit_review1(
            self.pid, self.rid, self.actor, decisions)
        self.assertEqual(self.h1["status"], "pass2_pending")
        runner = RTBDeepV2Pass2Repository(connection_factory=self.factory)
        def mock(payload):
            return {
                "id": "mock-review2-pass2",
                "model": payload["model"],
                "choices": [{"finish_reason": "stop", "message": {
                    "content": json.dumps({
                        "nodes": self.h1["approved_nodes"], "duplicates": [],
                    }),
                }}],
                "usage": {
                    "prompt_tokens": 3400,
                    "completion_tokens": 1800,
                    "cost": "0.025",
                },
            }
        self.pass2 = runner.advance_pass2(
            self.pid, self.rid, self.actor, sender=mock)
        self.assertEqual(self.pass2["status"], "review2")
        self.before = runner.inspect_pass2(self.pid, self.rid, self.actor)

    def tearDown(self):
        self.fx.tearDown()

    def _approve(self):
        return [
            {"action": "approve", "id": n["id"]}
            for n in self.h1["approved_nodes"]
        ]

    def _counts(self):
        with self.factory() as conn:
            row = conn.execute(
                """SELECT
                   (SELECT count(*) FROM tb_deep_v2_readiness_reports
                     WHERE project_id=%s AND run_id=%s),
                   (SELECT count(*) FROM tb_deep_v2_reviews
                     WHERE project_id=%s AND run_id=%s AND review_no=2),
                   (SELECT status FROM tb_deep_v2_runs
                     WHERE project_id=%s AND id=%s)""",
                (self.pid,self.rid,self.pid,self.rid,self.pid,self.rid),
            ).fetchone()
        return row

    def test_preview_is_authorized_read_only_and_never_spends(self):
        preview = self.repo.preview_review2(self.pid, self.rid, self.actor)
        self.assertTrue(preview["readiness"]["passed"], preview["readiness"]["blocking_errors"])
        self.assertEqual(preview["provider_calls"], 0)
        self.assertEqual(preview["candidate_nodes"], self.h1["approved_nodes"])
        self.assertEqual(self._counts(), (0,0,"review2"))
        after = self.repo.inspect_pass2(self.pid, self.rid, self.actor)
        self.assertEqual(after["spent_usd"], self.before["spent_usd"])
        self.assertEqual(after["reserved_usd"], self.before["reserved_usd"])

    def test_approve_all_saves_matching_human_review_and_signed_readiness(self):
        done = self.repo.submit_review2(self.pid, self.rid, self.actor, self._approve())
        self.assertEqual(done["status"], "approved")
        self.assertIs(done["published"], False)
        self.assertEqual(done["provider_calls"], 0)
        self.assertEqual(self._counts(), (1,1,"approved"))
        stored = self.repo.get_review2(self.pid, self.rid, self.actor)
        self.assertEqual(stored["approved_nodes"], self.h1["approved_nodes"])
        self.assertEqual(stored["approved_sha256"], done["approved_nodes_sha256"])
        self.assertEqual(stored["readiness"]["report_sha256"],
                         done["readiness_report_sha256"])
        self.assertEqual(stored["readiness"]["blocking_errors"], [])
        with self.assertRaisesRegex(PipelineError, "review2_not_pending"):
            self.repo.submit_review2(self.pid,self.rid,self.actor,self._approve())

    def test_empty_or_duplicate_human_decisions_roll_back_both_writes(self):
        for decisions in ([], self._approve()[:-1],
                          self._approve()+self._approve()[:1]):
            with self.subTest(case=decisions), self.assertRaises(PipelineError):
                self.repo.submit_review2(self.pid, self.rid, self.actor, decisions)
            self.assertEqual(self._counts(), (0,0,"review2"))

    def test_bad_search_signal_edit_is_blocked_and_rolls_back(self):
        choices = self._approve()
        edited = deepcopy(self.h1["approved_nodes"][1])
        edited["search_signals"] = ["viral"]
        choices[1] = {"action": "edit", "id": edited["id"], "node": edited}
        with self.assertRaises(PipelineError):
            self.repo.submit_review2(self.pid, self.rid, self.actor, choices)
        self.assertEqual(self._counts(), (0,0,"review2"))

    def test_locked_h1_field_cannot_be_changed_or_dropped(self):
        choices = self._approve()
        edited = deepcopy(self.h1["approved_nodes"][1])
        edited["validation_focus"] = ["Alter approved original H1 focus"]
        choices[1] = {"action": "edit", "id": edited["id"], "node": edited}
        with self.assertRaises(PipelineError):
            self.repo.submit_review2(self.pid, self.rid, self.actor, choices)
        self.assertEqual(self._counts(), (0,0,"review2"))

    def test_manual_insert_of_review2_without_readiness_is_refused(self):
        candidate = self.h1["approved_nodes"]
        with self.assertRaises(RaiseException):
            with self.factory() as conn:
                conn.execute(
                    """INSERT INTO tb_deep_v2_reviews(
                         project_id,run_id,review_no,actor,candidate_sha256,
                         approved_sha256,decisions,approved_nodes)
                       VALUES(%s,%s,2,%s,%s,%s,%s,%s)""",
                    (self.pid,self.rid,self.actor,canonical_sha(candidate),
                     canonical_sha(candidate),Jsonb(self._approve()),
                     Jsonb(candidate)),
                )
        self.assertEqual(self._counts(), (0,0,"review2"))

    def test_readiness_report_cannot_be_deleted_or_modified(self):
        self.repo.submit_review2(self.pid,self.rid,self.actor,self._approve())
        with self.assertRaises(RaiseException):
            with self.factory() as conn:
                conn.execute(
                    """DELETE FROM tb_deep_v2_readiness_reports
                       WHERE project_id=%s AND run_id=%s""",
                    (self.pid,self.rid),
                )
        with self.assertRaises(RaiseException):
            with self.factory() as conn:
                conn.execute(
                    """UPDATE tb_deep_v2_reviews
                       SET reviewed_at=clock_timestamp()
                       WHERE project_id=%s AND run_id=%s AND review_no=2""",
                    (self.pid,self.rid),
                )
        self.assertEqual(self._counts(), (1,1,"approved"))

    def test_wrong_project_or_reviewer_cannot_read_or_approve(self):
        with self.assertRaises((ValueError,PipelineError)):
            self.repo.submit_review2(
                self.fx.fx.fx.base.other, self.rid, self.actor, self._approve())
        with self.assertRaises((ValueError,PipelineError)):
            self.repo.preview_review2(self.pid,self.rid,"not-authorized")
        self.assertEqual(self._counts(), (0,0,"review2"))

    def test_post_review2_budget_does_not_change_and_no_new_attempt(self):
        with self.factory() as conn:
            before = conn.execute(
                """SELECT count(*) FROM tb_deep_v2_attempts
                   WHERE project_id=%s AND run_id=%s""",
                (self.pid,self.rid),
            ).fetchone()[0]
        self.repo.submit_review2(self.pid,self.rid,self.actor,self._approve())
        with self.factory() as conn:
            after = conn.execute(
                """SELECT count(*) FROM tb_deep_v2_attempts
                   WHERE project_id=%s AND run_id=%s""",
                (self.pid,self.rid),
            ).fetchone()[0]
        self.assertEqual(before,after)
        approved = self.repo.inspect_pass2(self.pid,self.rid,self.actor)
        self.assertEqual(approved["spent_usd"],self.before["spent_usd"])
        self.assertEqual(approved["reserved_usd"],self.before["reserved_usd"])

if __name__ == "__main__":
    unittest.main()
