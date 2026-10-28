"""UA28 optional disposable PostgreSQL17 read-only ops and recovery tests.

No positive qualified producer simulation, no main database, no token in SQL.
Set CM_TEST_PG_DSN to an isolated temporary PostgreSQL 17 database.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from unittest.mock import patch
from uuid import UUID
import unittest

from intelligence.unified_analytics import ops_observability as ops
from intelligence.unified_analytics import ops_cli as cli
from test_unified_analytics_transactional_persistence import TransactionalPersistencePG17


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable PostgreSQL17 CM_TEST_PG_DSN required")
class OpsPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TransactionalPersistencePG17.setUpClass()

    def setUp(self):
        self.f=TransactionalPersistencePG17(
            methodName="test_actual_pg17_idempotent_replay_checksum_and_all_seven_refs"
        )
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def test_project_scoped_partial_evidence_and_no_published_scores(self):
        f=self.f
        saved=f.save()
        with f.connect() as db:
            report=ops.ProjectOpsReader(db).inspect(f.project)
            self.assertEqual(report.schema_version,ops.OPS_WIRE)
            self.assertEqual(sum(report.snapshot_diagnostics.values()),1)
            self.assertGreaterEqual(sum(report.missing_ports.values()),1)
            self.assertFalse(report.qualified_live_readable)
            text=report.safe_json()
            self.assertNotIn(f.project,text)
            self.assertNotIn(f.source,text)
            self.assertNotIn('"video_id"',text)
            self.assertNotIn("breakout_score",text)

    def test_two_concurrent_readonly_inspections_are_stable(self):
        f=self.f
        f.save()
        def read(_):
            with f.connect() as db:
                return ops.ProjectOpsReader(db).inspect(f.project)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(read,(1,2)))
        self.assertEqual(
            [r.snapshot_diagnostics for r in results],
            [results[0].snapshot_diagnostics]*2,
        )
        self.assertTrue(all(not r.qualified_live_readable for r in results))

    def test_empty_recovery_does_not_change_jobs_or_create_snapshot(self):
        f=self.f
        with f.connect() as db:
            before=db.execute(
                "SELECT count(*) AS n FROM uae_qualification_jobs "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"]
            result=cli.recover_expired(
                f.project,db,limit=5,apply=True,
                confirmed_project=f.project,
            )
            after=db.execute(
                "SELECT count(*) AS n FROM uae_qualification_jobs "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"]
            self.assertEqual(result["recovered_jobs"],0)
            self.assertFalse(result["qualified"])
            self.assertEqual(before,after)
            self.assertEqual(f.count("uae_unified_snapshots"),0)


if __name__=="__main__":
    unittest.main()
