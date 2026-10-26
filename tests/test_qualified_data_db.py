"""Disposable PostgreSQL 17 integration tests for the full local QDP path.

Run only with CM_TEST_PG_DSN pointing at a NON-PRODUCTION disposable database.
"""
from __future__ import annotations

import json
import os
import unittest
import uuid

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None

from intelligence.qualified_data.contracts import QDPError
from intelligence.qualified_data import repository as qdp

DSN = os.environ.get("CM_TEST_PG_DSN")


@unittest.skipUnless(DSN and psycopg is not None, "Disposable CM_TEST_PG_DSN required")
class QualifiedDataDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from intelligence.migrate import apply_migrations
        with psycopg.connect(DSN, autocommit=True) as conn:
            apply_migrations(conn)
            # DISPOSABLE DB ONLY: simulate SRE-owned S4 schema while the peer
            # migration lives on its independent branch. Never patch the
            # immutable production QDP SQL or claim that SRE is installed.
            conn.execute("""
              ALTER TABLE sre_classification_runs
                ADD COLUMN IF NOT EXISTS execution_mode TEXT NOT NULL DEFAULT 'legacy'
                CHECK(execution_mode IN ('legacy','offline_fixture','live'))
            """)
            conn.execute("""
              ALTER TABLE sre_content_versions
                ADD COLUMN IF NOT EXISTS content_sha256 TEXT
            """)

    def setUp(self):
        self.pid = uuid.uuid4().hex
        self.key = "qdp-" + uuid.uuid4().hex
        self.source = "qdp_test_" + uuid.uuid4().hex[:12]
        self.category = str(uuid.uuid4())
        self.factory = lambda: psycopg.connect(DSN, autocommit=True, row_factory=dict_row)
        with self.factory() as c:
            c.execute(
                """INSERT INTO project_registry(id,name,profile_revision,profile_hash,created_at,updated_at)
                   VALUES (%s,'QDP test',1,%s,now(),now())""",
                (self.pid,"a"*64))
            c.execute("INSERT INTO sre_workspaces(project_id) VALUES (%s)",(self.pid,))
            c.execute(
                """INSERT INTO sources(id,provider,source_type,status,can_store_metadata,
                                        can_store_metrics,can_derive_scores,can_analyze_media)
                   VALUES (%s,'qdp_test','manual','enabled',true,false,false,false)""",
                (self.source,))
            self.policy = c.execute(
                """INSERT INTO source_policy_versions(source_id,version,is_active,permissions)
                   VALUES (%s,1,true,%s::jsonb) RETURNING id""",
                (self.source,json.dumps({"can_store_metadata":True}))).fetchone()["id"]
            self.vid = c.execute(
                """INSERT INTO videos(platform,platform_video_id,canonical_url,title)
                   VALUES ('tiktok',%s,%s,'fitness routine') RETURNING id""",
                (uuid.uuid4().hex,"https://www.tiktok.com/@test/video/7123456789012345678")
            ).fetchone()["id"]
            c.execute(
                """INSERT INTO source_video_metadata
                   (video_id,source_id,source_policy_version_id,fetched_at,title,published_at)
                   VALUES (%s,%s,%s,now(),'fitness routine',now()-interval '2 days')""",
                (self.vid,self.source,self.policy))
            self.taxonomy = c.execute(
                """INSERT INTO sre_taxonomy_versions(project_id,version,status)
                   VALUES (%s,1,'draft') RETURNING id""",
                (self.pid,)).fetchone()["id"]
            c.execute(
                """INSERT INTO sre_taxonomy_categories
                   (project_id,taxonomy_version_id,category_key,name,relation_type,
                    definition,rules,related_fact_ids,review_status,independent)
                   VALUES (%s,%s,%s,'fitness','direct','fitness content',%s::jsonb,
                           '[]'::jsonb,'approved',true)""",
                (self.pid,self.taxonomy,self.category,
                 json.dumps({"include":["fitness"],"exclude":[],
                             "positive_examples":[],"negative_examples":[]})))
            c.execute(
                """UPDATE sre_taxonomy_versions SET status='approved',
                    approved_by='test',approved_at=now() WHERE id=%s""",
                (self.taxonomy,))
            self.pipeline = c.execute(
                """INSERT INTO sre_pipeline_configs(project_id,name,version,config,created_by)
                   VALUES (%s,'QDP-fixture',1,'{"stages":[]}'::jsonb,'test')
                   RETURNING id""", (self.pid,)).fetchone()["id"]
            c.execute(
                """INSERT INTO sre_project_videos(project_id,video_id)
                   VALUES (%s,%s)""",(self.pid,self.vid))
            content_version = c.execute(
                """INSERT INTO sre_content_versions
                   (project_id,video_id,revision,content,source_id,source_policy_version_id)
                   VALUES (%s,%s,1,'{"title":"fitness routine"}'::jsonb,%s,%s)
                   RETURNING id""",
                (self.pid,self.vid,self.source,self.policy)).fetchone()["id"]
            c.execute("UPDATE sre_content_versions SET content_sha256=%s WHERE id=%s",
                      ("a"*64,content_version))
            self.sre_run = str(c.execute(
                """INSERT INTO sre_classification_runs
                   (project_id,taxonomy_version_id,pipeline_id,resolved_models,status,execution_mode)
                   VALUES (%s,%s,%s,'{"classifier":"anthropic/claude-opus-5.5"}'::jsonb,
                           'completed','live') RETURNING id""",
                (self.pid,self.taxonomy,self.pipeline)).fetchone()["id"])
            manifest = {
                "schema_version":"sre-run-plan/v1",
                "project_id":self.pid, "run_id":self.sre_run,
                "taxonomy_version_id":self.taxonomy, "taxonomy_sha256":"b"*64,
                "items":[{
                    "video_id":self.vid,"content_version_id":content_version,
                    "content_sha256":"a"*64,"hypothesis_keys":[self.category]}],
            }
            c.execute("UPDATE sre_classification_runs SET dataset_snapshot=%s::jsonb WHERE id=%s::uuid",
                      (json.dumps(manifest),self.sre_run))
            c.execute(
                """INSERT INTO sre_classification_results
                   (run_id,project_id,video_id,content_version_id,taxonomy_version_id,
                    category_key,decision,reason,evidence,stage_trace)
                   VALUES (%s::uuid,%s,%s,%s,%s,%s::uuid,'match','evidence present',
                    '[{"source":"title","quote":"fitness"}]'::jsonb,
                    '[{"provider":"openrouter","model_id":"anthropic/claude-opus-5.5"}]'::jsonb)""",
                (self.sre_run,self.pid,self.vid,content_version,self.taxonomy,self.category))

    def tearDown(self):
        with self.factory() as c:
            c.execute("DELETE FROM qdp_runs WHERE project_id=%s",(self.pid,))
            c.execute("DELETE FROM jobs WHERE job_type=%s AND payload->>'project_id'=%s",
                      (qdp.JOB_TYPE,self.pid))
            # Approved SRE taxonomy is DB-immutable by design. Keep those
            # isolated random fixtures in the disposable TEST database only.
            # Never bypass production immutability triggers for test cleanup.

    def test_sre_modes_not_qualified_for_production(self):
        with self.factory() as c:
            for mode in ("offline_fixture", "legacy"):
                c.execute("UPDATE sre_classification_runs SET execution_mode=%s WHERE id=%s::uuid",
                          (mode,self.sre_run))
                with self.subTest(mode=mode),self.assertRaises(QDPError) as context:
                    qdp.preview(self.pid,self.sre_run,connect=self.factory)
                self.assertEqual(context.exception.code,"sre_not_live")
                with self.subTest(create_mode=mode),self.assertRaises(QDPError):
                    qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
            c.execute("UPDATE sre_classification_runs SET execution_mode='live' WHERE id=%s::uuid",
                      (self.sre_run,))
        self.assertEqual(qdp.preview(self.pid,self.sre_run,connect=self.factory)["totals"]["qualified"],1)

    def test_existing_idempotency_run_cannot_replay_offline(self):
        result = qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        with self.factory() as c:
            c.execute("UPDATE sre_classification_runs SET execution_mode='offline_fixture' WHERE id=%s::uuid",
                      (self.sre_run,))
        with self.assertRaises(QDPError) as rejected:
            qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        self.assertEqual(rejected.exception.code,"sre_not_live")
        with self.factory() as c:
            self.assertEqual(c.execute("SELECT count(*) AS n FROM qdp_runs WHERE id=%s::uuid",
                (result["run_id"],)).fetchone()["n"],1)

    def test_negative_sre_lineage_stored_without_qualified_item(self):
        # A no_match video belongs to the SRE input but must NOT be in
        # qdp_items. Its original result+evidence lineage still must persist.
        with self.factory() as c:
            other_video = c.execute(
                """INSERT INTO videos(platform,platform_video_id,canonical_url,title)
                   VALUES ('tiktok',%s,%s,'unrelated post') RETURNING id""",
                (uuid.uuid4().hex,
                 "https://www.tiktok.com/@test/video/8123456789012345678")).fetchone()["id"]
            c.execute("INSERT INTO sre_project_videos(project_id,video_id) VALUES (%s,%s)",
                      (self.pid,other_video))
            other_content = c.execute(
                """INSERT INTO sre_content_versions
                   (project_id,video_id,revision,content,source_id,
                    source_policy_version_id,content_sha256)
                   VALUES (%s,%s,1,'{"title":"unrelated post"}'::jsonb,%s,%s,%s)
                   RETURNING id""",
                (self.pid,other_video,self.source,self.policy,"c"*64)).fetchone()["id"]
            other_result = c.execute(
                """INSERT INTO sre_classification_results
                   (run_id,project_id,video_id,content_version_id,taxonomy_version_id,
                    category_key,decision,reason,evidence,stage_trace)
                   VALUES (%s::uuid,%s,%s,%s,%s,%s::uuid,'no_match','unrelated',
                     '[]'::jsonb,
                     '[{"provider":"openrouter","model_id":"anthropic/claude-opus-5.5"}]'::jsonb)
                   RETURNING id""",
                (self.sre_run,self.pid,other_video,other_content,self.taxonomy,
                 self.category)).fetchone()["id"]
            row = c.execute(
                "SELECT dataset_snapshot FROM sre_classification_runs WHERE id=%s::uuid",
                (self.sre_run,)).fetchone()
            manifest = row["dataset_snapshot"]
            manifest["items"].append({
                "video_id":other_video,"content_version_id":other_content,
                "content_sha256":"c"*64,"hypothesis_keys":[self.category],
            })
            c.execute(
                "UPDATE sre_classification_runs SET dataset_snapshot=%s::jsonb WHERE id=%s::uuid",
                (json.dumps(manifest),self.sre_run))

        qdp_run = qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)["run_id"]
        with self.factory() as c:
            self.assertIsNone(c.execute(
                "SELECT 1 FROM qdp_items WHERE run_id=%s::uuid AND video_id=%s",
                (qdp_run,other_video)).fetchone())
            c.execute(
                """INSERT INTO qdp_item_lineage
                   (run_id,project_id,video_id,category_key,sre_result_id,
                    content_version_id,content_sha256,evidence_sha256,
                    model_decision,effective_decision)
                   VALUES (%s::uuid,%s,%s,%s::uuid,%s,%s,%s,%s,'no_match','no_match')""",
                (qdp_run,self.pid,other_video,self.category,other_result,
                 other_content,"c"*64,"e"*64))
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_item_lineage WHERE run_id=%s::uuid",
                (qdp_run,)).fetchone()["n"],1)
            with self.assertRaises(psycopg.errors.RaiseException):
                c.execute(
                    """UPDATE qdp_item_lineage SET evidence_sha256=%s
                       WHERE run_id=%s::uuid AND video_id=%s""",
                    ("f"*64,qdp_run,other_video))
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                c.execute(
                    """INSERT INTO qdp_item_lineage
                       (run_id,project_id,video_id,category_key,sre_result_id,
                        content_version_id,content_sha256,evidence_sha256,
                        model_decision,effective_decision)
                       VALUES (%s::uuid,%s,%s,%s::uuid,%s,%s,%s,%s,'no_match','no_match')""",
                    (qdp_run,self.pid,other_video,str(uuid.uuid4()),other_result,
                     other_content,"c"*64,"e"*64))

    def test_pinned_manifest_missing_pair_rejected_before_run_create(self):
        with self.factory() as c:
            row = c.execute("SELECT dataset_snapshot FROM sre_classification_runs WHERE id=%s::uuid",
                            (self.sre_run,)).fetchone()
            manifest = row["dataset_snapshot"]
            manifest["items"][0]["hypothesis_keys"].append(str(uuid.uuid4()))
            c.execute("UPDATE sre_classification_runs SET dataset_snapshot=%s::jsonb WHERE id=%s::uuid",
                      (json.dumps(manifest),self.sre_run))
        with self.assertRaises(QDPError) as bad:
            qdp.preview(self.pid,self.sre_run,connect=self.factory)
        self.assertEqual(bad.exception.code,"sre_taxonomy_coverage")
        with self.assertRaises(QDPError):
            qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)

    def test_pinned_content_hash_mismatch_rejected(self):
        with self.factory() as c:
            c.execute("UPDATE sre_content_versions SET content_sha256=%s WHERE video_id=%s",
                      ("e"*64,self.vid))
        with self.assertRaises(QDPError) as bad:
            qdp.preview(self.pid,self.sre_run,connect=self.factory)
        self.assertEqual(bad.exception.code,"sre_content_mismatch")

    def test_unapproved_and_foreign_sre_runs_rejected(self):
        with self.factory() as c:
            c.execute("UPDATE sre_classification_runs SET status='failed' WHERE id=%s::uuid",
                      (self.sre_run,))
        with self.assertRaises(QDPError) as failed:
            qdp.preview(self.pid,self.sre_run,connect=self.factory)
        self.assertEqual(failed.exception.code,"sre_not_completed")
        with self.assertRaises(QDPError) as foreign:
            qdp.preview("b"*32,self.sre_run,connect=self.factory)
        self.assertEqual(foreign.exception.status,404)

    def test_end_to_end_local_pipeline_and_revocation(self):
        before=qdp.preview(self.pid,self.sre_run,connect=self.factory)
        self.assertEqual(before["totals"]["qualified"],1)
        result=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        self.assertEqual(result["totals"]["qualified"],1)
        replay=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        self.assertTrue(replay["replayed"])
        with self.assertRaises(QDPError):
            qdp.create_run(self.pid,self.sre_run,self.key,{"max_age_days":1},
                           connect=self.factory)
        run_id=result["run_id"]
        processed=qdp.work_once(connect=self.factory)
        self.assertEqual(processed["video_id"],self.vid)
        detail=qdp.get_run(self.pid,run_id,connect=self.factory)
        self.assertEqual(detail["run"]["status"],"partial")
        self.assertEqual(detail["items"][0]["metadata"]["title"],"fitness routine")
        self.assertEqual(detail["items"][0]["groups"]["A"]["state"],"blocked")
        self.assertEqual(detail["items"][0]["groups"]["C"]["reason_code"],"score_rights_missing")
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) AS count FROM qdp_enrichments WHERE run_id=%s::uuid",
                (run_id,)).fetchone()["count"],1)
            c.execute("UPDATE sources SET status='disabled' WHERE id=%s",(self.source,))
        after=qdp.get_run(self.pid,run_id,connect=self.factory)
        self.assertIsNone(after["items"][0]["metadata"])
        self.assertEqual(after["items"][0]["reason_code"],
                         "source_policy_inactive_or_expired")
        self.assertEqual(qdp.purge_revoked(connect=self.factory),1)

    def test_human_rejection_and_freshness(self):
        with self.factory() as c:
            c.execute(
                """INSERT INTO sre_human_labels
                   (project_id,video_id,taxonomy_version_id,category_key,label,reviewer)
                   VALUES (%s,%s,%s,%s::uuid,'no_match','test')""",
                (self.pid,self.vid,self.taxonomy,self.category))
        result=qdp.preview(self.pid,self.sre_run,connect=self.factory)
        self.assertEqual(result["totals"]["qualified"],0)
        self.assertEqual(result["totals"]["excluded"],1)

    def test_cancellation_prevents_processing(self):
        result=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        stopped=qdp.cancel_run(self.pid,result["run_id"],connect=self.factory)
        self.assertEqual(stopped["status"],"cancelled")
        self.assertIsNone(qdp.work_once(connect=self.factory))
        detail=qdp.get_run(self.pid,result["run_id"],connect=self.factory)
        self.assertEqual(detail["run"]["status"],"cancelled")
        self.assertEqual(detail["items"][0]["status"],"cancelled")

    def test_missing_authorized_metadata_blocks_no_fabrication(self):
        with self.factory() as c:
            c.execute("DELETE FROM source_video_metadata WHERE video_id=%s",(self.vid,))
        run=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        result=qdp.work_once(connect=self.factory)
        self.assertEqual(result["state"],"blocked")
        self.assertEqual(result["reason"],"metadata_rights_or_source_missing")
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_enrichments WHERE run_id=%s::uuid",
                (run["run_id"],)).fetchone()["n"],0)

    def test_two_workers_do_not_double_prepare(self):
        from concurrent.futures import ThreadPoolExecutor
        run=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(qdp.work_once,connect=self.factory,worker_id="qdp-thread-a")
            b=pool.submit(qdp.work_once,connect=self.factory,worker_id="qdp-thread-b")
            got=[a.result(),b.result()]
        self.assertEqual(sum(x is not None for x in got),1)
        self.assertEqual(sum(bool(x and x.get("video_id")==self.vid) for x in got),1)
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_enrichments WHERE run_id=%s::uuid",
                (run["run_id"],)).fetchone()["n"],1)

    def test_cross_project_run_is_not_readable(self):
        result=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        with self.assertRaises(QDPError) as ctx:
            qdp.get_run("f"*32,result["run_id"],connect=self.factory)
        self.assertEqual(ctx.exception.status,404)


if __name__ == "__main__":
    unittest.main()
