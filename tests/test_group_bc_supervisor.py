"""Bounded scheduling regression checks without PostgreSQL or paid providers."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from intelligence.group_bc import (
    GroupBCSupervisor, JointBlocked, JointReceipt,
)
from intelligence.group_bc.supervisor import find_ready_pairs


PROJECT = "f" * 32
B1 = "00000000-0000-4000-8000-000000000011"
B2 = "00000000-0000-4000-8000-000000000012"
C1 = "00000000-0000-4000-8000-000000000021"
C2 = "00000000-0000-4000-8000-000000000022"


class ContextConnection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Cursors:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class DiscoveryConnection(ContextConnection):
    def __init__(self, rows):
        self.rows = rows
        self.query = None

    def execute(self, query, args):
        self.query = query
        self.args = args
        return Cursors(self.rows)


class FakePipeline:
    def __init__(self, blocked=None):
        self.blocked = blocked
        self.prepared = []
        self.delivered = []

    def prepare(self, conn, **kwargs):
        self.prepared.append(kwargs)
        if kwargs["b_run_id"] == self.blocked:
            raise JointBlocked("not_authorized")
        return JointReceipt(
            "00000000-0000-4000-8000-000000000030",
            kwargs["project_id"], kwargs["b_run_id"],
            kwargs["c_run_id"], "a" * 64, True, "queued",
        )

    def deliver_one(self, conn, **kwargs):
        self.delivered.append(kwargs)
        return JointReceipt(
            kwargs["receipt_id"], kwargs["project_id"],
            B2, C2, "a" * 64, False, "delivered",
        )


class SupervisorTests(unittest.TestCase):
    def test_scoped_discovery_and_candidate_filter(self):
        conn = DiscoveryConnection([
            {"b_run_id": B1, "c_run_id": C1},
            {"b_run_id": B2, "c_run_id": C2},
        ])
        rows = find_ready_pairs(conn, project_id=PROJECT)
        self.assertEqual(rows, ((B1, C1), (B2, C2)))
        self.assertEqual(conn.args, (PROJECT, 32))
        for token in (
            "group_b_runs", "group_c_runs", "group_c_inbox",
            "ci.qdp_run_id=bi.qdp_run_id",
            "cr.source_policy_version_id=br.source_policy_version_id",
            "group_bc_joint_receipts",
        ):
            self.assertIn(token, conn.query)

    def test_skips_failed_pair_and_delivers_next_with_exact_receipt(self):
        fake = FakePipeline(blocked=B1)
        factory = lambda: ContextConnection()
        supervisor = GroupBCSupervisor(factory, pipeline=fake)
        current = lambda conn, pair: object()
        with patch("intelligence.group_bc.supervisor.find_ready_pairs",
                   return_value=((B1, C1), (B2, C2))):
            report = supervisor.step(
                project_id=PROJECT, b_current_provider=current,
                b_compatibility=object(), b_verifier=object(),
                c_ports=object(), sink=object(),
            )
        self.assertEqual(report.candidates_seen, 2)
        self.assertEqual(report.blocked_pairs, ("not_authorized",))
        self.assertEqual(report.prepared.b_run_id, B2)
        self.assertEqual(report.delivered.state, "delivered")
        self.assertIs(fake.prepared[-1]["b_current"], current)
        self.assertEqual(fake.delivered[0]["receipt_id"],
                         report.prepared.receipt_id)

    def test_without_downstream_sink_receipt_stays_queued(self):
        fake = FakePipeline()
        supervisor = GroupBCSupervisor(lambda: ContextConnection(), pipeline=fake)
        with patch("intelligence.group_bc.supervisor.find_ready_pairs",
                   return_value=((B1, C1),)):
            report = supervisor.step(
                project_id=PROJECT, b_current_provider=lambda conn, pair: None,
                b_compatibility=object(), b_verifier=object(),
                c_ports=object(),
            )
        self.assertEqual(report.prepared.state, "queued")
        self.assertIsNone(report.delivered)
        self.assertFalse(fake.delivered)

    def test_rejects_bad_scope_and_missing_source_port(self):
        supervisor = GroupBCSupervisor(lambda: ContextConnection(),
                                       pipeline=FakePipeline())
        with self.assertRaises(JointBlocked):
            supervisor.step(
                project_id="invalid", b_current_provider=lambda conn, pair: None,
                b_compatibility=None, b_verifier=None, c_ports=None,
            )
        with self.assertRaises(JointBlocked):
            supervisor.step(
                project_id=PROJECT, b_current_provider=None,
                b_compatibility=None, b_verifier=None, c_ports=None,
            )


if __name__ == "__main__":
    unittest.main()
