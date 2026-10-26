"""GA-MOD-03 PG17: isolated schema, independent migration, legacy upgrade.

Never runs on production. Each PostgreSQL integration test creates/drops its own
unique PostgreSQL SCHEMA. A test-only PostgreSQL service is required.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from intelligence import migrate

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "intelligence" / "migrations" / "054_group_a_module_core.sql"
LEGACY = ROOT / "intelligence" / "group_a" / "legacy_sql" / "017_group_a_technical_extraction.sql"
DSN = os.getenv("CM_TEST_PG_DSN")


class GA03StaticSchema(unittest.TestCase):
    def test_forward_only_self_contained_and_existing_schemas_untouched(self):
        sql = MIGRATION.read_text("utf-8")
        self.assertTrue(MIGRATION.is_file())
        self.assertTrue(LEGACY.is_file())
        self.assertNotIn("REFERENCES qdp_", sql.lower())
        self.assertNotIn("JOIN qdp_", sql.lower())
        for table in ("ga_runs", "ga_media_refs", "ga_inbox", "ga_steps",
                      "ga_results", "ga_outbox", "ga_cache_refs"):
            self.assertIn("CREATE TABLE IF NOT EXISTS " + table, sql)
        for obj in ("ga_local_readable_results", "ga_legacy_a1_inventory",
                    "ga_run_guard", "ga_media_guard", "ga_result_guard",
                    "ga_inbox_guard", "ga_outbox_guard", "ga_step_guard"):
            self.assertIn(obj, sql)
        for banned in ("DROP TABLE", "TRUNCATE ", "DELETE FROM videos",
                       "ALTER TABLE videos", "DROP SCHEMA"):
            self.assertNotIn(banned, sql.upper())
        self.assertFalse((ROOT / "intelligence" / "migrations" /
                          "017_group_a_technical_extraction.sql").exists())
        self.assertEqual(sql.count("LANGUAGE plpgsql AS $"), 7)
        self.assertEqual(sql.count("END; $;"), 7)
        self.assertEqual(sql.count("$ga_legacy$"), 2)


@unittest.skipUnless(DSN, "CM_TEST_PG_DSN required (disposable PG17)")
class GA03Postgres(unittest.TestCase):
    def isolate(self):
        import psycopg
        from psycopg import sql
        name = "ga03_" + uuid4().hex[:16]
        conn = psycopg.connect(DSN, autocommit=True)
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(name)))
        self.addCleanup(conn.close)
        def cleanup():
            # Rollback test role's temporary namespace only. Never public.
            conn.execute("RESET search_path")
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                sql.Identifier(name)))
        self.addCleanup(cleanup)
        return conn, name

    def fixture(self, conn):
        from psycopg.types.json import Jsonb
        pid = uuid4().hex
        src = "ga03-" + uuid4().hex
        conn.execute("""INSERT INTO project_registry
            (id,name,profile_revision,profile_hash,created_at,updated_at)
            VALUES(%s,'GA isolated',1,%s,now(),now())""", (pid, "d"*64))
        conn.execute("INSERT INTO sre_workspaces(project_id) VALUES(%s)", (pid,))
        conn.execute("""INSERT INTO sources
            (id,provider,source_type,status,can_analyze_media)
            VALUES(%s,'fixture','owned','enabled',true)""", (src,))
        policy = conn.execute("""INSERT INTO source_policy_versions
            (source_id,version,is_active,permissions)
            VALUES(%s,1,true,%s) RETURNING id""",
            (src, Jsonb({"can_analyze_media": True}))).fetchone()[0]
        vid = conn.execute("""INSERT INTO videos
            (platform,platform_video_id,canonical_url,content_kind)
            VALUES('youtube',%s,%s,'video') RETURNING id""",
            (uuid4().hex, "https://youtube.com/watch?v="+uuid4().hex)).fetchone()[0]
        foreign = conn.execute("""INSERT INTO videos
            (platform,platform_video_id,canonical_url,content_kind)
            VALUES('youtube',%s,%s,'video') RETURNING id""",
            (uuid4().hex, "https://youtube.com/watch?v="+uuid4().hex)).fetchone()[0]
        conn.execute("INSERT INTO sre_project_videos(project_id,video_id) VALUES(%s,%s)",
                     (pid,vid))
        asset = conn.execute("""INSERT INTO media_assets
            (video_id,source_id,media_kind,local_path,sha256,permission)
            VALUES(%s,%s,'video','/tmp/fixture.mp4',%s,'analysis_allowed')
            RETURNING id""", (vid,src,"c"*64)).fetchone()[0]
        wrong_asset = conn.execute("""INSERT INTO media_assets
            (video_id,source_id,media_kind,local_path,sha256,permission)
            VALUES(%s,%s,'video','/tmp/foreign.mp4',%s,'analysis_allowed')
            RETURNING id""", (foreign,src,"b"*64)).fetchone()[0]
        return dict(project=pid,source=src,policy=policy,video=vid,
                    foreign_video=foreign,asset=asset,wrong_asset=wrong_asset,
                    qdp_run=uuid4())

    def add_run(self, conn, fixture, *, project=None, video=None, suffix=""):
        from psycopg.types.json import Jsonb
        f = fixture
        return conn.execute("""INSERT INTO ga_runs
           (project_id,qdp_run_id,video_id,source_id,source_policy_version_id,
            qdp_input_sha256,manifest_sha256,config_sha256,config,content_kind)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'video')
           RETURNING id""",
           (project or f["project"],f["qdp_run"],video or f["video"],f["source"],
            f["policy"],"a"*64,"b"*64,("e" if not suffix else "f")*64,Jsonb({}))).fetchone()[0]

    def test_fresh_migration_integrity_project_isolation_and_retention(self):
        from psycopg.types.json import Jsonb
        conn, name = self.isolate()
        applied = migrate.apply_migrations(conn)
        self.assertIn(MIGRATION.name, applied)
        self.assertEqual(migrate.apply_migrations(conn), [])
        for obj in ("ga_runs","ga_inbox","ga_steps","ga_results",
                    "ga_outbox","ga_media_refs","ga_cache_refs",
                    "ga_local_readable_results","ga_legacy_a1_inventory"):
            self.assertIsNotNone(conn.execute("SELECT to_regclass(%s)",
                                      (name+"."+obj,)).fetchone()[0])
        self.assertEqual(conn.execute(
            "SELECT count(*) FROM ga_legacy_a1_inventory").fetchone()[0], 0)
        f = self.fixture(conn)
        with self.assertRaises(Exception):
            self.add_run(conn,f,video=f["foreign_video"])
        with self.assertRaises(Exception):
            self.add_run(conn,f,project="f"*32)
        rid = self.add_run(conn,f)
        with self.assertRaises(Exception):
            self.add_run(conn,f)  # same lineage/config normally replayed idempotently
        with self.assertRaises(Exception):
            # A run may not become completed without its full pinned manifest
            # and a separate immutable completed A003/A004 snapshot.
            conn.execute("UPDATE ga_runs SET state='completed' WHERE id=%s",(rid,))
        conn.execute("""INSERT INTO ga_media_refs
            (run_id,project_id,video_id,source_id,slide_index,media_asset_id,
             media_kind,asset_sha256,byte_size,verified_at)
            VALUES(%s,%s,%s,%s,1,%s,'video',%s,120,now())""",
            (rid,f["project"],f["video"],f["source"],f["asset"],"c"*64))
        for wrong_id,wrong_hash in ((f["wrong_asset"],"b"*64),
                                    (f["asset"],"a"*64)):
            with self.assertRaises(Exception):
                conn.execute("""INSERT INTO ga_media_refs
                    (run_id,project_id,video_id,source_id,slide_index,media_asset_id,
                    media_kind,asset_sha256,byte_size,verified_at)
                    VALUES(%s,%s,%s,%s,2,%s,'video',%s,100,now())""",
                    (rid,f["project"],f["video"],f["source"],wrong_id,wrong_hash))
        with self.assertRaises(Exception):
            conn.execute("UPDATE ga_media_refs SET byte_size=3 WHERE run_id=%s",(rid,))
        step = conn.execute("""INSERT INTO ga_steps
            (run_id,project_id,step_key,step_kind,input_sha256)
            VALUES(%s,%s,'first','technical',%s) RETURNING id""",
            (rid,f["project"],"a"*64)).fetchone()[0]
        with self.assertRaises(Exception):
            conn.execute("UPDATE ga_steps SET attempts=-1 WHERE id=%s",(step,))
        conn.execute("""UPDATE ga_steps SET state='completed',
            output_sha256=%s,finished_at=now() WHERE id=%s""",("d"*64,step))
        with self.assertRaises(Exception):
            conn.execute("UPDATE ga_steps SET output_sha256=%s WHERE id=%s",
                        ("e"*64,step))
        event = uuid4()
        conn.execute("""INSERT INTO ga_inbox(event_id,run_id,project_id,
            qdp_run_id,video_id,input_sha256,payload_sha256,pointer)
            SELECT %s,id,project_id,qdp_run_id,video_id,qdp_input_sha256,%s,%s
            FROM ga_runs WHERE id=%s""",
            (event,"e"*64,Jsonb({"schema_version":"qdp-handoff/v1"}),rid))
        with self.assertRaises(Exception):
            conn.execute("UPDATE ga_inbox SET payload_sha256=%s WHERE event_id=%s",
                         ("f"*64,event))
        a003 = conn.execute("""INSERT INTO video_feature_a_analyses
            (video_id,source_id,media_asset_id,analysis_key,analysis_version,method_name)
            VALUES(%s,%s,%s,%s,'A1','fixture') RETURNING id""",
            (f["video"],f["source"],f["asset"],uuid4().hex)).fetchone()[0]
        result = conn.execute("""INSERT INTO ga_results
            (run_id,project_id,video_id,source_id,state,video_a_analysis_id,
             result_sha256,evidence,snapshot,method_version)
            VALUES(%s,%s,%s,%s,'partial',%s,%s,%s,%s,'fixture/v1') RETURNING id""",
            (rid,f["project"],f["video"],f["source"],a003,"b"*64,
             Jsonb([]),Jsonb({"status":"partial"}))).fetchone()[0]
        with self.assertRaises(Exception):
            # A preliminary A003 cannot be relabelled as completed by GA.
            conn.execute("""INSERT INTO ga_results
                (run_id,project_id,video_id,source_id,state,video_a_analysis_id,
                 result_sha256,evidence,snapshot,method_version)
                VALUES(%s,%s,%s,%s,'completed',%s,%s,'[]','{}','fixture/v1')""",
                (rid,f["project"],f["video"],f["source"],a003,"d"*64))
        with self.assertRaises(Exception):
            conn.execute("UPDATE ga_results SET snapshot='{}'::jsonb WHERE id=%s",(result,))
        with self.assertRaises(Exception):
            conn.execute("""INSERT INTO ga_results
                (run_id,project_id,video_id,source_id,state,video_a_analysis_id,
                 result_sha256,evidence,snapshot,method_version)
                VALUES(%s,%s,%s,%s,'completed',%s,%s,'[]','{}','fixture/v1')""",
                (rid,f["project"],f["foreign_video"],f["source"],a003,"d"*64))
        conn.execute("""INSERT INTO ga_outbox(run_id,project_id,result_id,
            payload_sha256,payload) VALUES(%s,%s,%s,%s,%s)""",
            (rid,f["project"],result,"e"*64,Jsonb({"schema_version":"group-a-to-b/v1"})))
        with self.assertRaises(Exception):
            conn.execute("UPDATE ga_outbox SET payload=%s WHERE run_id=%s",
                         (Jsonb({"altered":True}),rid))
        conn.execute("""INSERT INTO ga_cache_refs
            (run_id,project_id,media_asset_id,cache_kind,input_sha256,result_sha256)
            VALUES(%s,%s,%s,'probe',%s,%s)""",
            (rid,f["project"],f["asset"],"a"*64,"f"*64))
        with self.assertRaises(Exception):
            conn.execute("""INSERT INTO ga_cache_refs
                (run_id,project_id,media_asset_id,cache_kind,input_sha256,result_sha256)
                VALUES(%s,%s,%s,'vision',%s,%s)""",
                (rid,f["project"],f["wrong_asset"],"a"*64,"f"*64))
        conn.execute("UPDATE ga_runs SET state='partial' WHERE id=%s",(rid,))
        self.assertEqual(conn.execute(
            "SELECT count(*) FROM ga_local_readable_results WHERE run_id=%s",
            (rid,)).fetchone()[0],1)
        conn.execute("UPDATE sources SET status='disabled' WHERE id=%s",(f["source"],))
        self.assertEqual(conn.execute(
            "SELECT count(*) FROM ga_local_readable_results WHERE run_id=%s",
            (rid,)).fetchone()[0],0)
        conn.execute("UPDATE ga_runs SET state='blocked' WHERE id=%s",(rid,))
        with self.assertRaises(Exception):
            conn.execute("UPDATE ga_runs SET state='running' WHERE id=%s",(rid,))
        # A003's pre-existing FK cleanup trigger also checks current source
        # policy. Restore the source before testing media retention cascade.
        conn.execute("UPDATE sources SET status='enabled' WHERE id=%s",(f["source"],))
        conn.execute("DELETE FROM media_assets WHERE id=%s",(f["asset"],))
        self.assertEqual(conn.execute(
            "SELECT count(*) FROM ga_media_refs WHERE run_id=%s",(rid,)).fetchone()[0],0)
        self.assertEqual(conn.execute(
            "SELECT count(*) FROM ga_cache_refs WHERE run_id=%s",(rid,)).fetchone()[0],0)
        conn.execute("DELETE FROM videos WHERE id=%s",(f["video"],))
        self.assertEqual(conn.execute(
            "SELECT count(*) FROM ga_runs WHERE id=%s",(rid,)).fetchone()[0],0)

    def test_forward_only_legacy_a1_upgrade_and_checksum_guard(self):
        conn, name = self.isolate()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = sorted(migrate.MIGRATIONS.glob("[0-9]*.sql"))
            earlier = [f for f in files if f.name != MIGRATION.name]
            for src in earlier:
                shutil.copy2(src,root/src.name)
            with patch.object(migrate,"MIGRATIONS",root):
                applied = migrate.apply_migrations(conn)
                self.assertEqual(applied,[f.name for f in earlier])
                self.assertEqual(migrate.apply_migrations(conn),[])
            # Minimal QDP namespace needed to install archival 017 unchanged.
            # The production GA 054 migration itself does NOT depend on this.
            conn.execute("""CREATE TABLE qdp_items (
               run_id UUID NOT NULL, project_id TEXT NOT NULL,
               video_id BIGINT NOT NULL, PRIMARY KEY(run_id,project_id,video_id))""")
            conn.execute("""CREATE TABLE qdp_readable_enrichments (
               run_id UUID,project_id TEXT,video_id BIGINT,media_asset_id BIGINT,
               source_id TEXT,source_policy_version_id BIGINT)""")
            conn.execute("""CREATE TABLE qdp_preparations (
               run_id UUID,project_id TEXT,video_id BIGINT,analysis_group TEXT,
               state TEXT,input_sha256 TEXT)""")
            conn.execute(LEGACY.read_text("utf-8"))
            before = conn.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid='group_a_tasks'::regclass").fetchone()[0]
            self.assertGreater(before,0)
            with tempfile.TemporaryDirectory() as latest:
                new_file = Path(latest)/MIGRATION.name
                shutil.copy2(MIGRATION,new_file)
                with patch.object(migrate,"MIGRATIONS",Path(latest)):
                    self.assertEqual(migrate.apply_migrations(conn),[MIGRATION.name])
                    self.assertEqual(migrate.apply_migrations(conn),[])
                    new_file.write_text(new_file.read_text("utf-8")+"\n-- checksum drift")
                    with self.assertRaisesRegex(RuntimeError,
                        "Migration modified after deployment"):
                        migrate.apply_migrations(conn)
            after = conn.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid='group_a_tasks'::regclass").fetchone()[0]
            self.assertEqual(after,before)
            self.assertIsNotNone(conn.execute(
                "SELECT to_regclass(%s)",(name+".ga_runs",)).fetchone()[0])
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM ga_legacy_a1_inventory").fetchone()[0],0)


if __name__ == "__main__":
    unittest.main()
