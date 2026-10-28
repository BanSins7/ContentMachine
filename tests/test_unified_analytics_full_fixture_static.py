"""UA30 standalone completeness and no forged effective approval invariants.

These static tests pin real UAE contracts/migrations; they are not a
replacement for disposable PostgreSQL17, source-backed peer approvals,
human-reviewed Group C, or local Linux/Windows execution.
"""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


class FullFixtureStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def read(path):
            return (ROOT/path).read_text(encoding="utf-8")
        cls.guard=read("intelligence/migrations/085_unified_analytics_transaction_guards.sql")
        cls.schema=read("intelligence/migrations/080_unified_analytics_core.sql")
        cls.qdp=read("intelligence/migrations/082_unified_analytics_qdp_handoff_inbox.sql")
        cls.queue=read("intelligence/migrations/088_unified_analytics_qualification_jobs.sql")
        cls.visible=read("intelligence/migrations/089_unified_analytics_readside_visibility.sql")
        cls.join=read("intelligence/unified_analytics/joined_context.py")
        cls.orch=read("intelligence/unified_analytics/qualification_orchestrator.py")
        cls.assembly=read("intelligence/unified_analytics/snapshot_assembly.py")
        cls.worker=read("intelligence/unified_analytics/worker.py")
        cls.aggregates=read("intelligence/unified_analytics/descriptive_aggregation.py")
        cls.http=read("intelligence/unified_analytics/http_service.py")
        cls.e2e=read("tests/test_unified_analytics_full_fixture_e2e.py")
        cls.pg=read("tests/test_unified_analytics_full_fixture_e2e_pg17.py")
        cls.qdp_pg=read("tests/test_unified_analytics_full_qdp_queue_e2e_pg17.py")
        cls.stats=read("tests/test_unified_analytics_full_fixture_statistics.py")

    def test_real_085_qualified_veto_enabled(self):
        self.assertIn("IF NEW.status = 'qualified' THEN",self.guard)
        self.assertIn("BEFORE INSERT ON uae_unified_snapshots",self.guard)
        self.assertNotIn("DROP TRIGGER",self.guard)

    def test_real_085_complete_evidence_veto_enabled(self):
        self.assertIn("IF NEW.checked IS TRUE OR NEW.is_current IS TRUE",self.guard)
        self.assertIn("OR NEW.state = 'complete'",self.guard)
        self.assertIn("BEFORE INSERT ON uae_snapshot_evidence",self.guard)

    def test_db_payload_seven_refs_exact_at_commit(self):
        self.assertIn("IF n <> 7 OR expected IS DISTINCT FROM actual",self.guard)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED",self.guard)

    def test_real_pg_fixture_does_not_override_085(self):
        for text in (self.pg,self.qdp_pg):
            for forbidden in ("DISABLE TRIGGER","ALTER TABLE uae_",
                              "DROP TRIGGER","SET session_replication_role",
                              "TRUNCATE "):
                self.assertNotIn(forbidden,text)

    def test_exact_all_seven_port_identity(self):
        self.assertIn('"source_policy","d1","qdp","sre",',self.join)
        self.assertIn('"group_a","group_b","group_c"',self.join)
        self.assertIn("REQUIRED_PORTS",self.e2e)

    def test_qdp_independent_a_b_c_receipts_mandatory(self):
        self.assertIn("CHECK (analysis_group IN ('A','B','C'))",self.qdp)
        self.assertIn("count(DISTINCT h.analysis_group)",self.visible)
        self.assertIn("=3",self.visible)
        self.assertIn('for kind in ("A","B","C")',self.qdp_pg)

    def test_join_per_port_reads_recheck_source_rights(self):
        self.assertIn("GuardedUAEReads(ports,rights)",self.join)
        self.assertIn("original=rights.check_current_rights(scope)",self.join)
        self.assertIn("current=rights.check_current_rights(scope)",self.join)
        self.assertIn("join_current_rights_changed",self.join)

    def test_group_b_parent_a_and_c_snapshot_binding(self):
        self.assertIn("join_b_wrong_group_a_parent",self.join)
        self.assertIn("join_c_config_or_snapshot_changed",self.join)
        self.assertIn("join_abc_preparation_sha_mismatch",self.join)

    def test_fake_complete_claims_still_partial(self):
        self.assertIn('"complete_claims_unapproved"',self.join)
        self.assertIn("qualification_effective_approval_unavailable",self.orch)
        self.assertIn("if self.status is QualificationStatus.QUALIFIED:",self.orch)
        self.assertIn('"located_unapproved"',self.orch)

    def test_no_forged_true_evidence_in_snapshot_assembly(self):
        self.assertIn('"checked":False,"is_current":False',self.assembly)
        self.assertIn('or self.decision.status is QualificationStatus.QUALIFIED',
                      self.assembly)
        self.assertNotIn('"checked":True,"is_current":True',self.assembly)

    def test_atomic_worker_never_claims_unapproved_qualified(self):
        self.assertIn("worker_unapproved_qualification_denied",self.worker)
        self.assertIn("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ",
                      self.worker)
        self.assertIn("if self.resolver is None",self.worker)

    def test_qualified_view_is_private_fresh_read_only(self):
        self.assertIn("security_invoker=true",self.visible)
        self.assertIn("WHERE sn.status='qualified'",self.visible)
        self.assertIn("NOT EXISTS",self.visible)
        self.assertIn("source_policy_revoked",self.visible)
        self.assertIn("NO GRANT",self.visible)

    def test_all_uae_snapshot_tables_scope_project(self):
        for table in ("uae_qualification_requests",
                      "uae_unified_snapshots",
                      "uae_snapshot_evidence"):
            self.assertIn("CREATE TABLE "+table,self.schema)
        self.assertIn("UNIQUE(project_id, id)",self.schema)

    def test_no_group_c_recomputation_in_uae(self):
        for source in (self.join,self.orch,self.assembly,self.worker):
            self.assertNotIn("compute_c_metrics(",source)
            self.assertNotIn("UPDATE calculated_metrics",source)

    def test_aggregates_min_sample_gated_by_current_visibility(self):
        self.assertIn("MIN_SAMPLE = 5",self.aggregates)
        self.assertIn("VisibleSnapshotReader(db)",self.aggregates)
        self.assertIn("if len(pins)<min_sample:",self.aggregates)
        self.assertIn("return None",self.aggregates)

    def test_fixture_video_and_two_carousel_kinds(self):
        for kind in ('"video"','"photo_carousel"','"mixed_carousel"',
                     '"slideshow_video"'):
            self.assertIn(kind,self.e2e)
        self.assertIn("test_photo_carousel_private_seven_port_snapshot_golden",self.pg)
        self.assertIn("test_mixed_carousel_private_seven_port_snapshot_golden",self.pg)

    def test_pg17_disposable_only_and_17_version_guard(self):
        for text in (self.pg,self.qdp_pg):
            self.assertIn('CM_TEST_PG_DSN',text)
            self.assertIn("skipUnless",text)
        self.assertIn("TransactionalPersistencePG17.setUpClass()",self.pg)
        self.assertIn("QueueRealPG17.setUpClass()",self.qdp_pg)

    def test_pg17_negative_gates_wrong_peer_and_revocation(self):
        for method in ("test_085_blocks_direct_qualified_snapshot_insert",
                       "test_085_blocks_forged_complete_or_checked_evidence",
                       "test_source_permission_revoke_cannot_publish_prior_snapshot"):
            self.assertIn(method,self.pg)
        self.assertIn("test_carousel_three_handoffs_queue_then_missing_c_blocks_claim",
                      self.qdp_pg)
        self.assertIn("test_missing_candidate_resolver_does_not_consume_a_queue_job",
                      self.qdp_pg)

    def test_no_fake_positive_qualified_golden(self):
        for text in (self.e2e,self.pg,self.qdp_pg):
            self.assertNotIn("DISABLE TRIGGER",text)
            self.assertNotIn("qualified=True",text)
            self.assertNotIn('status="qualified"',text)
        self.assertIn("qualification_effective_approval_unavailable",self.e2e)

    def test_independent_math_floor_and_no_public_significance(self):
        self.assertIn("test_pure_sample_floor_four_rejected",self.stats)
        self.assertIn("test_one_revoked_mock_pin_cancels_entire_aggregate",self.stats)
        self.assertIn("test_unbounded_sample_more_than_50_refused",self.stats)
        self.assertIn("confidence_intervals_computed",self.stats)
        self.assertIn("aggregate_requires_live_qualified_pins",self.http)

    def test_no_peer_branches_or_host_routes_modified_by_fixture(self):
        self.assertNotIn("requests.post(",self.e2e)
        self.assertNotIn("requests.get(",self.pg)
        self.assertNotIn("app.py",self.qdp_pg)
        self.assertNotIn("os.system",self.e2e)


if __name__=="__main__":
    unittest.main()
