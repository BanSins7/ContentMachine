"""UA-MOD-04 migration checks.

Static checks run on any Python 3 checkout. PostgreSQL tests ONLY run against
a disposable PostgreSQL 17 database explicitly supplied by CM_TEST_PG_DSN.
These fixtures test SQL integrity, NOT genuine SRE/QDP/A/B/C availability.
"""
from __future__ import annotations

import os
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

SQL_PATH = (Path(__file__).resolve().parents[1] /
            "intelligence/migrations/080_unified_analytics_core.sql")
MIGRATION_DIR = SQL_PATH.parent
TABLES = (
    "uae_qualification_requests",
    "uae_unified_snapshots",
    "uae_snapshot_evidence",
    "uae_snapshot_invalidations",
    "uae_audit_events",
)


class UAECoreMigrationStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = SQL_PATH.read_text(encoding="utf-8").lower()

    def test_unique_migration_name_after_main_and_known_peer_prefix(self):
        files = [p.name for p in MIGRATION_DIR.glob("[0-9]*.sql")]
        self.assertEqual(files.count(SQL_PATH.name), 1)
        self.assertTrue(SQL_PATH.name.startswith("080_unified_analytics"))
        self.assertIn("053_ke_c_qualified_execution.sql", files)
        self.assertNotIn("016_analytics_unified.sql", files)
        self.assertNotIn("017_analytics_jobs_and_read_gates.sql", files)
        self.assertNotIn("018_analytics_audit_hardening.sql", files)

    def test_only_uae_tables_are_created(self):
        declared = re.findall(r"create\s+table\s+(\w+)\s*\(", self.sql)
        self.assertEqual(set(declared), set(TABLES))
        self.assertEqual(len(declared), len(TABLES))
        for forbidden in ("create table calculated_metrics",
                          "create table metric_snapshots",
                          "create table sources",
                          "create table videos",
                          "create table group_a_",
                          "create table group_b_",
                          "create table qdp_"):
            self.assertNotIn(forbidden, self.sql)
        self.assertNotRegex(self.sql, r"(?m)^\s*(drop|truncate)\s+")
        self.assertNotRegex(self.sql, r"(?m)^\s*alter\s+table\s+(?!uae_)")

    def test_project_source_video_and_policy_fks(self):
        for referent in ("project_registry(id)", "videos(id)", "sources(id)",
                         "source_policy_versions(id)"):
            self.assertIn("references " + referent, self.sql)
        self.assertIn("uae_snapshot_scope_guard()", self.sql)
        self.assertIn("r.qdp_input_sha256 = new.qdp_input_sha256", self.sql)
        self.assertIn("r.source_policy_version_id = new.source_policy_version_id", self.sql)
        self.assertIn("p.permissions->>'can_derive_scores' = 'true'", self.sql)

    def test_version_hash_and_kind_constraints(self):
        self.assertGreaterEqual(self.sql.count("^[0-9a-f]{64}$"), 8)
        self.assertIn("unique(project_id, request_key)", self.sql)
        self.assertIn("unique(project_id, request_id, inputs_sha256)", self.sql)
        self.assertIn("unique(project_id, video_id, source_id, snapshot_version)", self.sql)
        self.assertIn("content_type = 'carousel'", self.sql)
        self.assertIn("status <> 'qualified'", self.sql)

    def test_qualified_deferred_constraints_and_immutable_output(self):
        self.assertEqual(self.sql.count("deferrable initially deferred"), 2)
        self.assertIn("uae_assert_qualified_evidence", self.sql)
        self.assertIn("good_count <> 7", self.sql)
        self.assertIn("e.checked is true and e.is_current is true", self.sql)
        self.assertIn("uae_snapshot_immutable_update", self.sql)
        self.assertIn("uae_evidence_immutable_update", self.sql)
        self.assertIn("uae_invalidations_immutable_update", self.sql)

    def test_081_composite_membership_is_additive_and_source_of_truth(self):
        p = MIGRATION_DIR / "081_unified_analytics_project_membership.sql"
        s = p.read_text(encoding="utf-8").lower()
        self.assertIn("foreign key (project_id, video_id)", s)
        self.assertIn("references sre_project_videos(project_id, video_id)", s)
        self.assertIn("on delete cascade", s)
        self.assertIn("uae_request_project_video_membership_fk", s)
        self.assertNotRegex(s, r"(?m)^\s*(drop|truncate)\s+")
        self.assertNotIn("create table sre_project_videos", s)

    def test_never_provides_a_prequalified_read_view(self):
        self.assertNotRegex(self.sql, r"(?m)^\s*create\s+(or\s+replace\s+)?view")
        self.assertNotIn("compute_c_metrics", self.sql)
        self.assertNotIn("insert into calculated_metrics", self.sql)


class _FixtureRollback(Exception):
    pass


@unittest.skipUnless(os.environ.get("CM_TEST_PG_DSN"),
                     "CM_TEST_PG_DSN disposable PostgreSQL 17 database required")
class UAECoreMigrationPG17Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("CM_DB_DSN") == os.environ.get("CM_TEST_PG_DSN"):
            raise RuntimeError("Refusing to run mutation fixtures against production CM_DB_DSN")
        import psycopg
        from intelligence import migrate
        cls.psycopg = psycopg
        cls.dsn = os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            version = int(conn.execute("SHOW server_version_num").fetchone()[0])
            if version < 170000 or version >= 180000:
                raise unittest.SkipTest("Disposable PostgreSQL 17 explicitly required")
            cls.first_install = migrate.apply_migrations(conn)
            cls.second_install = migrate.apply_migrations(conn)

    def test_migration_runner_is_checksum_idempotent(self):
        self.assertEqual(self.second_install, [])
        with self.psycopg.connect(self.dsn, autocommit=True) as conn:
            row = conn.execute(
                "SELECT version, length(sha256) FROM schema_migrations WHERE version = %s",
                (SQL_PATH.name,),
            ).fetchone()
            self.assertEqual(row, (SQL_PATH.name, 64))
            for table in TABLES:
                self.assertEqual(conn.execute("SELECT to_regclass(%s)",
                               (table,)).fetchone()[0], table)

    def test_concurrent_migration_replay_is_lock_safe(self):
        from intelligence import migrate
        def run():
            with self.psycopg.connect(self.dsn, autocommit=True) as conn:
                return migrate.apply_migrations(conn)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run) for _ in range(2)]
            self.assertEqual([x.result(timeout=60) for x in futures], [[], []])

    def test_negative_lineage_checks_duplicate_and_deferred_coverage(self):
        ident = uuid4().hex
        bad_sha = "0" * 64
        sid, pid = "uae-test-" + ident, ident
        with self.psycopg.connect(self.dsn, autocommit=True) as conn:
            try:
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO project_registry(id,name,profile_revision,profile_hash,"
                        "created_at,updated_at) VALUES(%s,'UAE schema QA',1,%s,now(),now())",
                        (pid, bad_sha))
                    conn.execute(
                        "INSERT INTO sources(id,provider,source_type,status,"
                        "can_store_metrics,can_derive_scores,can_analyze_media,can_store_metadata)"
                        " VALUES(%s,'local','manual','enabled',TRUE,TRUE,TRUE,TRUE)",
                        (sid,))
                    pol_id = conn.execute(
                        "INSERT INTO source_policy_versions(source_id,version,is_active,permissions)"
                        " VALUES(%s,1,TRUE,%s::jsonb) RETURNING id",
                        (sid, '{"can_store_metadata":true,"can_analyze_media":true,'
                         '"can_store_metrics":true,"can_derive_scores":true}'),
                    ).fetchone()[0]
                    vid = conn.execute(
                        "INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)"
                        " VALUES('youtube',%s,'https://example.invalid/test','video') RETURNING id",
                        (ident,)
                    ).fetchone()[0]
                    # 081: a globally existing videos.id is insufficient:
                    # register exact (project_id,video_id) membership first.
                    conn.execute("INSERT INTO sre_workspaces(project_id) VALUES(%s)", (pid,))
                    conn.execute(
                        "INSERT INTO sre_project_videos(project_id,video_id) VALUES(%s,%s)",
                        (pid,vid))
                    req = conn.execute(
                        "INSERT INTO uae_qualification_requests("
                        "project_id,video_id,source_id,source_policy_version_id,"
                        "qdp_run_id,qdp_input_sha256,content_kind,request_key,request_sha256)"
                        " VALUES(%s,%s,%s,%s,%s,%s,'video',%s,%s) RETURNING id",
                        (pid,vid,sid,pol_id,str(uuid4()),bad_sha,
                         "schema-test:" + ident,bad_sha),
                    ).fetchone()[0]
                    # 081: other project exists, and the same global video exists,
                    # but the composite project-video membership is absent.
                    other_project = uuid4().hex
                    conn.execute(
                        "INSERT INTO project_registry(id,name,profile_revision,"
                        "profile_hash,created_at,updated_at)"
                        " VALUES(%s,'Other UAE project',1,%s,now(),now())",
                        (other_project,bad_sha))
                    with self.assertRaises(self.psycopg.Error):
                        with conn.transaction():
                            conn.execute(
                                "INSERT INTO uae_qualification_requests("
                                "project_id,video_id,source_id,source_policy_version_id,"
                                "qdp_run_id,qdp_input_sha256,content_kind,request_key,"
                                "request_sha256)"
                                " VALUES(%s,%s,%s,%s,%s,%s,'video',%s,%s)",
                                (other_project,vid,sid,pol_id,str(uuid4()),
                                 bad_sha,"cross-project:"+ident,bad_sha))
                    # Same project / idempotency key is rejected by UNIQUE.
                    with self.assertRaises(self.psycopg.Error):
                        with conn.transaction():
                            conn.execute(
                                "INSERT INTO uae_qualification_requests("
                                "project_id,video_id,source_id,source_policy_version_id,"
                                "qdp_run_id,qdp_input_sha256,content_kind,request_key,request_sha256)"
                                " SELECT project_id,video_id,source_id,source_policy_version_id,"
                                "qdp_run_id,qdp_input_sha256,content_kind,request_key,request_sha256"
                                " FROM uae_qualification_requests WHERE id=%s", (req,))
                    # Source-policy mismatch fails after both FKs resolve:
                    # provider B exists, but policy version belongs to provider A.
                    other_source = sid + "-other"
                    conn.execute(
                        "INSERT INTO sources(id,provider,source_type,status) "
                        "VALUES(%s,'local','manual','enabled')", (other_source,))
                    with self.assertRaises(self.psycopg.Error):
                        with conn.transaction():
                            conn.execute(
                                "INSERT INTO uae_qualification_requests("
                                "project_id,video_id,source_id,source_policy_version_id,"
                                "qdp_run_id,qdp_input_sha256,content_kind,request_key,request_sha256)"
                                " VALUES(%s,%s,%s,%s,%s,%s,'video',%s,%s)",
                                (pid,vid,other_source,pol_id,str(uuid4()),bad_sha,
                                 "schema-mismatch:" + ident,bad_sha))
                    # Blocked rows are permitted as *diagnostics* only.
                    snap = conn.execute(
                        "INSERT INTO uae_unified_snapshots("
                        "project_id,request_id,video_id,source_id,source_policy_version_id,"
                        "qdp_run_id,qdp_input_sha256,content_kind,content_type,"
                        "snapshot_version,status,scope_sha256,inputs_sha256,"
                        "decision_sha256,blockers,readiness,verified_as_of)"
                        " SELECT project_id,id,video_id,source_id,source_policy_version_id,"
                        "qdp_run_id,qdp_input_sha256,content_kind,'video',1,'blocked',%s,%s,%s,"
                        "'[\"no_live_sre\"]'::jsonb,'{}'::jsonb,now()"
                        " FROM uae_qualification_requests WHERE id=%s RETURNING id",
                        (bad_sha,bad_sha,bad_sha,req)
                    ).fetchone()[0]
                    with self.assertRaises(self.psycopg.Error):
                        with conn.transaction():
                            conn.execute("UPDATE uae_unified_snapshots SET status='partial' WHERE id=%s",
                                         (snap,))
                    # A qualified snapshot with fake complete/checked claims but
                    # without SEVEN child rows must be rejected at constraint check.
                    with self.assertRaises(self.psycopg.Error):
                        with conn.transaction():
                            conn.execute(
                                "INSERT INTO uae_unified_snapshots("
                                "project_id,request_id,video_id,source_id,source_policy_version_id,"
                                "qdp_run_id,qdp_input_sha256,content_kind,content_type,"
                                "snapshot_version,status,scope_sha256,inputs_sha256,"
                                "decision_sha256,sre_run_id,sre_decision_sha256,"
                                "group_a_analysis_id,group_b_analysis_id,calculated_metric_id,"
                                "metric_snapshot_id,blockers,readiness,verified_as_of)"
                                " SELECT project_id,id,video_id,source_id,source_policy_version_id,"
                                "qdp_run_id,qdp_input_sha256,content_kind,'video',2,'qualified',"
                                "%s,%s,%s,%s,%s,1,2,3,4,'[]'::jsonb,"
                                "%s::jsonb,now()"
                                " FROM uae_qualification_requests WHERE id=%s",
                                (bad_sha,bad_sha,bad_sha,str(uuid4()),bad_sha,
                                 '{"source_policy":"ready","d1":"ready","qdp":"ready",'
                                 '"sre":"ready","group_a":"ready","group_b":"ready",'
                                 '"group_c":"ready"}',req)
                            )
                            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

                    # Positive SQL-only reference matrix: seven complete children
                    # meets structural FK/trigger integrity, NOT LIVE peer proof.
                    qualified = conn.execute(
                        "INSERT INTO uae_unified_snapshots("
                        "project_id,request_id,video_id,source_id,source_policy_version_id,"
                        "qdp_run_id,qdp_input_sha256,content_kind,content_type,"
                        "snapshot_version,status,scope_sha256,inputs_sha256,"
                        "decision_sha256,sre_run_id,sre_decision_sha256,"
                        "group_a_analysis_id,group_b_analysis_id,calculated_metric_id,"
                        "metric_snapshot_id,blockers,readiness,verified_as_of)"
                        " SELECT project_id,id,video_id,source_id,source_policy_version_id,"
                        "qdp_run_id,qdp_input_sha256,content_kind,'video',2,'qualified',"
                        "%s,%s,%s,%s,%s,1,2,3,4,'[]'::jsonb,"
                        "%s::jsonb,now()"
                        " FROM uae_qualification_requests WHERE id=%s RETURNING id",
                        (bad_sha, "1"*64, "2"*64, str(uuid4()), bad_sha,
                         '{"source_policy":"ready","d1":"ready","qdp":"ready",'
                         '"sre":"ready","group_a":"ready","group_b":"ready",'
                         '"group_c":"ready"}', req)
                    ).fetchone()[0]
                    ports = ["source_policy","d1","qdp","sre",
                             "group_a","group_b","group_c"]
                    conn.execute(
                        "INSERT INTO uae_snapshot_evidence("
                        "project_id,snapshot_id,port,state,checked,is_current,"
                        "record_key,producer_schema,producer_version,"
                        "evidence_sha256,lineage_sha256)"
                        " SELECT %s,%s,p,'complete',TRUE,TRUE,'row:' || p,"
                        "'test-fixture/v1','test-v1',%s,%s"
                        " FROM unnest(%s::text[]) AS t(p)",
                        (pid,qualified,bad_sha,bad_sha,ports))
                    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
                    with self.assertRaises(self.psycopg.Error):
                        with conn.transaction():
                            conn.execute(
                                "DELETE FROM uae_snapshot_evidence WHERE project_id=%s "
                                "AND snapshot_id=%s AND port='sre'",
                                (pid,qualified))
                            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
                    raise _FixtureRollback
            except _FixtureRollback:
                pass


if __name__ == "__main__":
    unittest.main()
