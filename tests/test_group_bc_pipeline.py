"""Offline contract regressions for the certified Group B + Group C join.

PG17/live QDP/GA/C approvals are deliberately a separate release gate.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import UUID
import unittest

from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.integration.analytics_contract import (
    HandoffDecision, HandoffState,
)
from intelligence.group_c.analytics_bridge import PersistedC7Handoff
from intelligence.group_bc.pipeline import (
    ExactPair, JointBlocked, VERSION, certified_payload, read_exact_pair,
)

PROJECT = "a" * 32
B_RUN = "00000000-0000-4000-8000-000000000001"
C_RUN = "00000000-0000-4000-8000-000000000002"
QDP_RUN = "00000000-0000-4000-8000-000000000003"
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_E = "e" * 64


def make_pair():
    return ExactPair(
        project_id=PROJECT, video_id=101, source_id="source-101",
        qdp_run_id=QDP_RUN, b_run_id=B_RUN, c_run_id=C_RUN,
        b_analysis_id=201, c_metric_id=301, source_policy_version_id=4,
        b_preparation_sha256=SHA_B, c_preparation_sha256=SHA_C,
    )


def make_b(pair):
    payload = {
        "schema_version": "group-b-analytics-handoff/v1",
        "project_id": pair.project_id, "run_id": pair.b_run_id,
        "analysis_id": pair.b_analysis_id, "video_id": pair.video_id,
        "source_id": pair.source_id, "qdp_run_id": pair.qdp_run_id,
        "qdp_preparation_sha256": pair.b_preparation_sha256,
        "source_policy_version_id": pair.source_policy_version_id,
        "uae_qualification_status": "not_evaluated",
        "group_c_qualification_status": "not_evaluated",
    }
    return HandoffDecision(
        HandoffState.CANDIDATE, (), pair.project_id, pair.b_run_id,
        pair.b_analysis_id, canonical_hash(payload), payload, False,
    )


def make_c(pair):
    return PersistedC7Handoff(
        project_id=pair.project_id, run_id=pair.c_run_id,
        video_id=pair.video_id, calculated_metric_id=pair.c_metric_id,
        snapshot_id=401, previous_snapshot_id=400,
        published_metadata_id=501, calculation_config_id=601,
        source_id=pair.source_id, source_policy_version_id=pair.source_policy_version_id,
        metric_definition="platform_post_views", metric_version="v1",
        creator_cohort_id=701, prior_cohort_id=702, niche_cohort_id=None,
        creator_freeze_id=801, prior_freeze_id=802, niche_freeze_id=None,
        review_request_id=B_RUN, review_decision_id=C_RUN,
        activation_event_id=QDP_RUN, evidence_sha256=SHA_E,
        breakout_score=Decimal("1.25"),
        score_creator_component=Decimal("0.50"),
        score_growth_component=Decimal("0.25"),
        score_engagement_component=Decimal("0.30"),
        score_freshness_component=Decimal("0.20"),
    )


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _ReadConnection:
    def __init__(self, row):
        self.row = row
        self.query = None
        self.args = None

    def execute(self, sql, args):
        self.query = sql
        self.args = args
        return _Cursor(self.row)


class GroupBCContractTests(unittest.TestCase):
    def test_joins_valid_independent_preparation_hashes(self):
        pair = make_pair()
        payload = certified_payload(pair, make_b(pair), make_c(pair))
        self.assertEqual(payload["schema_version"], VERSION)
        self.assertEqual(payload["qdp_run_id"], QDP_RUN)
        self.assertEqual(payload["group_b"]["preparation_sha256"], SHA_B)
        self.assertEqual(payload["group_c"]["preparation_sha256"], SHA_C)
        self.assertEqual(payload["group_b"]["analysis_id"], 201)
        self.assertEqual(payload["group_c"]["calculated_metric_id"], 301)
        self.assertEqual(payload["analytics_qualification"], "not_evaluated")
        self.assertNotIn("breakout_score", payload)

    def test_rejects_wrong_video_and_policy(self):
        pair = make_pair()
        for change in ({"video_id": 102}, {"source_policy_version_id": 5},
                       {"source_id": "other"}, {"run_id": B_RUN}):
            c = make_c(pair)
            try:
                invalid = replace(c, **change)
            except TypeError:
                continue
            with self.assertRaises(JointBlocked):
                certified_payload(pair, make_b(pair), invalid)

    def test_rejects_wrong_qdp_generation_even_when_hash_valid(self):
        pair = make_pair()
        b = make_b(pair)
        p = dict(b.payload, qdp_run_id=C_RUN)
        b = replace(b, payload=p, payload_sha256=canonical_hash(p))
        with self.assertRaisesRegex(JointBlocked, "group_b_exact_lineage_mismatch"):
            certified_payload(pair, b, make_c(pair))

    def test_rejects_changed_b_hash_and_unverified_b(self):
        pair = make_pair()
        b = make_b(pair)
        with self.assertRaisesRegex(JointBlocked, "group_b_fresh_candidate_unavailable"):
            certified_payload(pair, replace(b, payload_sha256=SHA_E), make_c(pair))
        with self.assertRaisesRegex(JointBlocked, "group_b_fresh_candidate_unavailable"):
            certified_payload(pair, replace(b, state=HandoffState.BLOCKED), make_c(pair))

    def test_rejects_missing_or_malformed_pair(self):
        with self.assertRaises(JointBlocked):
            replace(make_pair(), b_run_id="bad")
        with self.assertRaises(JointBlocked):
            replace(make_pair(), b_analysis_id=0)

    def test_sql_reader_is_scoped_and_joins_only_accepted_same_qdp(self):
        pair = make_pair()
        c = _ReadConnection({
            field: getattr(pair, field) for field in pair.__dataclass_fields__
        })
        loaded = read_exact_pair(c, project_id=PROJECT, b_run_id=B_RUN, c_run_id=C_RUN)
        self.assertEqual(loaded, pair)
        self.assertEqual(c.args, (PROJECT, B_RUN, C_RUN))
        for statement in (
            "br.state='completed'", "cr.state='completed'",
            "bi.status='accepted'", "ci.state='accepted'",
            "ci.qdp_run_id=bi.qdp_run_id",
            "cr.source_policy_version_id=br.source_policy_version_id",
            "FOR SHARE OF br,bi,cr,ci",
        ):
            self.assertIn(statement, c.query)

    def test_no_data_no_delivery(self):
        c = _ReadConnection(None)
        with self.assertRaisesRegex(JointBlocked, "exact_completed_pair_unavailable"):
            read_exact_pair(c, project_id=PROJECT, b_run_id=B_RUN, c_run_id=C_RUN)


if __name__ == "__main__":
    unittest.main()
