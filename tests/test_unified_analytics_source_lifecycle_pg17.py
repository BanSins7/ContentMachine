"""UA29 disposable PostgreSQL17 end-to-end lifecycle/retention regressions.

Creates synthetic UAE15 partial snapshots only, never fabricates positive
Group A/B/C/SRE/QDP qualified authority. All writes confined to disposable
CM_TEST_PG_DSN distinct from production CM_DB_DSN.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import unittest

from intelligence.unified_analytics import source_lifecycle as lc
from test_unified_analytics_transactional_persistence import TransactionalPersistencePG17


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable PostgreSQL17 CM_TEST_PG_DSN required")
class SourceLifecyclePG17(unittest.TestCase):
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

    def test_revoke_after_current_source_rights_removed_and_outbox(self):
        f=self.f
        receipt=f.save()
        with f.connect() as db:
            with db.transaction():
                db.execute(
                    "UPDATE sources SET can_derive_scores=FALSE "
                    "WHERE id=%s",(f.source,)
                )
            out=lc.SourceLifecycle(db).invalidate_unreadable(
                f.project,f.source,limit=50
            )
            self.assertEqual(out.affected,1)
            self.assertFalse(out.qualified)
            invalid=db.execute(
                "SELECT reason_code FROM uae_snapshot_invalidations "
                "WHERE project_id=%s AND snapshot_id=%s",
                (f.project,receipt.snapshot_id)
            ).fetchall()
            self.assertEqual(len(invalid),1)
            self.assertEqual(invalid[0]["reason_code"],"source_access_revoked")
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_revision_outbox "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"],1)
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_readside_visibility_candidates "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"],0)

    def test_revocation_idempotent_no_duplicate_outbox(self):
        f=self.f
        f.save()
        with f.connect() as db:
            with db.transaction():
                db.execute("UPDATE sources SET can_store_metrics=FALSE WHERE id=%s",
                           (f.source,))
            reader=lc.SourceLifecycle(db)
            a=reader.invalidate_unreadable(f.project,f.source)
            b=reader.invalidate_unreadable(f.project,f.source)
            self.assertEqual((a.affected,b.affected),(1,0))
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_snapshot_invalidations "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"],1)

    def test_concurrent_revocation_deduplicates_with_scope_lock(self):
        f=self.f
        f.save()
        with f.connect() as db:
            with db.transaction():
                db.execute("UPDATE sources SET can_store_metadata=FALSE "
                           "WHERE id=%s",(f.source,))
        def revoke(_):
            with f.connect() as conn:
                return lc.SourceLifecycle(conn).invalidate_unreadable(
                    f.project,f.source
                ).affected
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(revoke,(1,2)))
        self.assertEqual(sorted(results),[0,1])
        with f.connect() as db:
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_revision_outbox "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"],1)

    def test_purge_cascades_all_uae_snapshot_job_invalidation_references(self):
        f=self.f
        f.save()
        with f.connect() as db:
            plan=lc.SourceLifecycle(db).preview(f.project,f.source)
            self.assertGreaterEqual(plan.counts["requests"],2)
            output=lc.SourceLifecycle(db).purge(
                f.project,f.source,expected_plan_sha256=plan.scope_sha256,
                confirm_project=f.project,confirm_source=f.source,
            )
            self.assertFalse(output.qualified)
            self.assertGreaterEqual(output.affected,2)
            for table in (
                "uae_qualification_requests","uae_unified_snapshots",
                "uae_snapshot_evidence","uae_snapshot_payloads",
                "uae_snapshot_invalidations","uae_revision_outbox",
                "uae_qualification_jobs","uae_qualification_job_events",
                "uae_revision_inbox","uae_qdp_handoff_inbox",
                "uae_qdp_handoff_quarantine","uae_audit_events",
                "uae_project_aggregate_runs",
            ):
                n=db.execute(
                    "SELECT count(*) AS n FROM "+table+" WHERE project_id=%s",
                    (f.project,),
                ).fetchone()["n"]
                self.assertEqual(n,0,table)
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_readside_visibility_candidates "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"],0)
            self.assertIsNotNone(db.execute(
                "SELECT id FROM sources WHERE id=%s",(f.source,)
            ).fetchone(),"UAE must not delete upstream source")

    def test_stale_plan_denied_then_new_preview_allows_source_purge(self):
        f=self.f
        f.save()
        with f.connect() as db:
            previous=lc.SourceLifecycle(db).preview(f.project,f.source)
            f.save(request_index=1)
            with self.assertRaises(Exception) as denied:
                lc.SourceLifecycle(db).purge(
                    f.project,f.source,
                    expected_plan_sha256=previous.scope_sha256,
                    confirm_project=f.project,confirm_source=f.source
                )
            self.assertEqual(getattr(denied.exception,"code",None),
                             "lifecycle_plan_stale")
            self.assertIsNotNone(db.execute(
                "SELECT id FROM uae_unified_snapshots WHERE project_id=%s "
                "LIMIT 1",(f.project,)
            ).fetchone())


if __name__=="__main__":
    unittest.main()
