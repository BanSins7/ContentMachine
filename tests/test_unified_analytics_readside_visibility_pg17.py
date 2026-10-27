"""UA-MOD-19 real PostgreSQL 17 visibility denial on unapproved snapshots.

Uses disposable CM_TEST_PG_DSN and the existing UA15 fixture. These
are real SQL reads but NOT a real SRE/QDP/A/B/C approval integration.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from intelligence.unified_analytics import readside_visibility as vis
from test_unified_analytics_transactional_persistence import TransactionalPersistencePG17


class Candidate:
    def __init__(self,scope):
        self.scope=scope


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable CM_TEST_PG_DSN PostgreSQL 17 required")
class ReadsideVisibilityPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TransactionalPersistencePG17.setUpClass()

    def setUp(self):
        self.fixture=TransactionalPersistencePG17(
            methodName="test_actual_pg17_idempotent_replay_checksum_and_all_seven_refs"
        )
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_real_089_view_blocks_historical_partial_without_metadata_leak(self):
        f=self.fixture
        receipt=f.save()
        self.assertGreater(receipt.snapshot_id,0)
        with f.connect() as db:
            view=db.execute(
                "SELECT * FROM uae_readside_visibility_candidates "
                "WHERE project_id=%s",(f.project,),
            ).fetchall()
            self.assertEqual(view,[])
            # Internal helper must never return a partial/blocked snapshot,
            # even when a full seven-row matrix is physically persisted.
            with patch.object(vis,"JoinedCandidatePins",Candidate):
                pin=vis.VisibilityPins(receipt.snapshot_id,Candidate(f.scope))
                self.assertIsNone(
                    vis.VisibleSnapshotReader(db).visible_one(pin)
                )

    def test_real_source_revocation_and_invalidation_cannot_unhide(self):
        f=self.fixture
        saved=f.save()
        with f.connect() as db:
            with db.transaction():
                db.execute(
                    "UPDATE sources SET status='revoked' WHERE id=%s",
                    (f.source,),
                )
                db.execute(
                    "INSERT INTO uae_snapshot_invalidations("
                    "project_id,snapshot_id,reason_code,reason_sha256)"
                    " VALUES(%s,%s,'source_revoked',%s)",
                    (f.project,saved.snapshot_id,"b"*64),
                )
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_readside_visibility_candidates "
                "WHERE project_id=%s",(f.project,),
            ).fetchone()["n"],0)

    def test_real_rejected_qualified_insert_stays_denied_after_089(self):
        # UA15 migration 085 must remain active after UA19 readside install.
        f=self.fixture
        with f.connect() as db:
            count=db.execute(
                "SELECT count(*) AS n FROM pg_trigger WHERE "
                "tgname='uae_v1_no_forged_qualified_insert' "
                "AND tgenabled='O'"
            ).fetchone()["n"]
            self.assertEqual(count,1)
            view=db.execute(
                "SELECT pg_get_viewdef('uae_readside_visibility_candidates'::regclass)"
                " AS sql"
            ).fetchone()["sql"].lower()
            self.assertIn("uae_snapshot_invalidations",view)
            self.assertIn("source_policy_versions",view)


if __name__=="__main__":
    unittest.main()
