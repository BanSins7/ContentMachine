"""UA-MOD-24 static PG17 migration and contract invariants (no DB needed)."""
from __future__ import annotations

from pathlib import Path
import unittest

from intelligence.unified_analytics import project_ranked_views as rank


class RankedMigrationStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=(Path(__file__).resolve().parents[1] /
                 "intelligence/migrations" /
                 "090_unified_analytics_project_aggregate_runs.sql").read_text(
                     encoding="utf-8",
                 ).lower()

    def test_project_fk_and_unique_run_versions(self):
        self.assertIn("references project_registry(id) on delete cascade",self.sql)
        self.assertIn("unique (project_id,request_key,run_revision)",self.sql)
        self.assertIn("run_revision integer not null check (run_revision>0)",
                      self.sql)

    def test_digest_only_immutable_manifest_fields(self):
        for name in ("lineage_sha256","stats_sha256","ranked_sha256",
                     "compatibility_sha256","schema_version","sort_mode",
                     "sample_size","rtb_evidence_state"):
            self.assertIn(name,self.sql)
        self.assertNotIn("breakout_score",self.sql)
        self.assertNotIn("jsonb",self.sql)
        self.assertNotIn("media_path",self.sql)

    def test_restrictions_real_min_n_and_fixed_sort(self):
        self.assertIn("sample_size between 5 and 50",self.sql)
        for name in ("breakout_desc","views_desc","video_id_asc"):
            self.assertIn(name,self.sql)
        self.assertIn("unavailable_not_bound",self.sql)

    def test_update_immutable_but_retention_cascade_unblocked(self):
        self.assertIn("before update on uae_project_aggregate_runs",self.sql)
        self.assertNotIn("before update or delete",self.sql)
        self.assertNotIn("before delete",self.sql)
        self.assertIn("project-level on delete cascade",self.sql)

    def test_no_public_grant_or_rank_publisher(self):
        for bad in ("grant select","grant insert","create materialized view",
                    "create policy","create view","create table calculated_metrics",
                    "compute_c_metrics("):
            self.assertNotIn(bad,self.sql)

    def test_rank_run_read_requires_pinned_uuid(self):
        self.assertIn("id=%s::uuid",rank.RUN_READ_ID_SQL)
        self.assertIn("project_id=%s",rank.RUN_READ_ID_SQL)
        self.assertIn("on conflict",rank.RUN_INSERT_SQL.lower())

    def test_explicit_rtb_unbound_not_producer_signature(self):
        self.assertEqual(rank.RTB_STATUS,"unavailable_not_bound")
        self.assertNotIn("rtb_verified_sha256",self.sql)

    def test_only_owner_internal_contract_no_public_route(self):
        self.assertFalse(hasattr(rank,"register_http_endpoint"))
        self.assertFalse(hasattr(rank,"publish_qualified"))
        self.assertFalse(hasattr(rank,"get_all_project_snapshots"))


if __name__=="__main__":
    unittest.main()
