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
            # Disposable-only SRE S3 peer schema. Production SRE owns this
            # migration; never create or copy it into QDP-owned migrations.
            conn.execute("""
              ALTER TABLE sre_content_versions
                ADD COLUMN IF NOT EXISTS metadata_observation_id BIGINT
                  REFERENCES source_video_metadata(id)
            """)
            conn.execute("""
              CREATE TABLE IF NOT EXISTS sre_project_discoveries (
                project_id TEXT NOT NULL,
                video_id BIGINT NOT NULL,
                discovery_id BIGINT NOT NULL REFERENCES discoveries(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY(project_id,discovery_id),
                FOREIGN KEY(project_id,video_id)
                  REFERENCES sre_project_videos(project_id,video_id) ON DELETE CASCADE
              )
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
            self.metadata_id = c.execute(
                """INSERT INTO source_video_metadata
                   (video_id,source_id,source_policy_version_id,fetched_at,title,published_at)
                   VALUES (%s,%s,%s,now(),'fitness routine',now()-interval '2 days')
                   RETURNING id""",
                (self.vid,self.source,self.policy)).fetchone()["id"]
            self.discovery_id = c.execute(
                """INSERT INTO discoveries(video_id,source_id,method)
                   VALUES (%s,%s,'qdp_disposable_s3_fixture') RETURNING id""",
                (self.vid,self.source)).fetchone()["id"]
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
            c.execute(
                """INSERT INTO sre_project_discoveries
                   (project_id,video_id,discovery_id) VALUES (%s,%s,%s)""",
                (self.pid,self.vid,self.discovery_id))
            content_version = c.execute(
                """INSERT INTO sre_content_versions
                   (project_id,video_id,revision,content,provenance,
                    source_id,source_policy_version_id,metadata_observation_id)
                   VALUES (%s,%s,1,'{"title":"fitness routine"}'::jsonb,%s::jsonb,
                           %s,%s,%s) RETURNING id""",
                (self.pid,self.vid,json.dumps({"discovery_id":self.discovery_id}),
                 self.source,self.policy,self.metadata_id)).fetchone()["id"]
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

    def _sre_completion_event(self):
        from datetime import datetime, timezone
        from intelligence.qualified_data.sre_inbox import VERSION, COMPLETED
        publication_id = str(uuid.uuid4())
        with self.factory() as c:
            row = c.execute(
                "SELECT dataset_snapshot FROM sre_classification_runs WHERE id=%s::uuid",
                (self.sre_run,)).fetchone()
            snapshot = dict(row["dataset_snapshot"])
            snapshot["publication_id"] = publication_id
            c.execute(
                "UPDATE sre_classification_runs SET dataset_snapshot=%s::jsonb WHERE id=%s::uuid",
                (json.dumps(snapshot),self.sre_run))
        return {
            "schema_version": VERSION,
            "event_id": str(uuid.uuid4()),
            "event_type": COMPLETED,
            "project_id": self.pid,
            "run_id": self.sre_run,
            "publication_id": publication_id,
            "taxonomy_version_id": self.taxonomy,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }

    def test_sre_completed_delivery_accepts_then_replays_without_jobs(self):
        from intelligence.qualified_data.sre_inbox import receive_sre_completion
        from intelligence.qualified_data.contracts import sha256
        event = self._sre_completion_event()
        expected_hash = sha256(event)
        receipt = receive_sre_completion(
            event, expected_hash, connect=self.factory)
        self.assertTrue(receipt["ack"])
        self.assertEqual(receipt["state"], "accepted")
        self.assertFalse(receipt["replayed"])
        second = receive_sre_completion(event, expected_hash, connect=self.factory)
        self.assertTrue(second["ack"])
        self.assertTrue(second["replayed"])
        self.assertEqual(second["event_id"], receipt["event_id"])
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_sre_inbox WHERE event_id=%s::uuid",
                (event["event_id"],)).fetchone()["n"],1)
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_runs WHERE project_id=%s",
                (self.pid,)).fetchone()["n"],0)
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM jobs WHERE job_type=%s AND payload->>'project_id'=%s",
                (qdp.JOB_TYPE,self.pid)).fetchone()["n"],0)

    def test_sre_same_event_id_new_digest_records_conflict_without_overwrite(self):
        from intelligence.qualified_data.sre_inbox import receive_sre_completion
        from intelligence.qualified_data.contracts import sha256
        event = self._sre_completion_event()
        ok = receive_sre_completion(event,sha256(event),connect=self.factory)
        changed = dict(event, publication_id=str(uuid.uuid4()))
        conflict = receive_sre_completion(changed,sha256(changed),connect=self.factory)
        self.assertFalse(conflict["ack"])
        self.assertEqual(conflict["reason_code"],"event_id_hash_conflict")
        with self.factory() as c:
            row = c.execute(
                "SELECT state,payload_sha256 FROM qdp_sre_inbox WHERE event_id=%s::uuid",
                (event["event_id"],)).fetchone()
            self.assertEqual(row["state"],"accepted")
            self.assertEqual(row["payload_sha256"],ok["payload_sha256"])
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_sre_inbox_quarantine WHERE event_id=%s::uuid",
                (event["event_id"],)).fetchone()["n"],1)

    def test_sre_transport_hash_mismatch_never_acks(self):
        from intelligence.qualified_data.sre_inbox import receive_sre_completion
        event = self._sre_completion_event()
        response = receive_sre_completion(event,"a"*64,connect=self.factory)
        self.assertFalse(response["ack"])
        self.assertEqual(response["reason_code"],"transport_hash_mismatch")
        with self.factory() as c:
            self.assertIsNone(c.execute(
                "SELECT 1 FROM qdp_sre_inbox WHERE event_id=%s::uuid",
                (event["event_id"],)).fetchone())
            self.assertEqual(c.execute(
                "SELECT reason_code FROM qdp_sre_inbox_quarantine WHERE event_id=%s::uuid",
                (event["event_id"],)).fetchone()["reason_code"],
                "transport_hash_mismatch")

    def test_sre_bad_publication_and_non_live_run_quarantined(self):
        from intelligence.qualified_data.sre_inbox import receive_sre_completion
        from intelligence.qualified_data.contracts import sha256
        event=self._sre_completion_event()
        bad=dict(event,publication_id=str(uuid.uuid4()))
        reply=receive_sre_completion(bad,sha256(bad),connect=self.factory)
        self.assertFalse(reply["ack"])
        self.assertEqual(reply["reason_code"],"sre_event_publication_mismatch")
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT state FROM qdp_sre_inbox WHERE event_id=%s::uuid",
                (bad["event_id"],)).fetchone()["state"],"quarantined")
            c.execute(
                "UPDATE sre_classification_runs SET execution_mode='offline_fixture' WHERE id=%s::uuid",
                (self.sre_run,))
        event["event_id"]=str(uuid.uuid4())
        denied=receive_sre_completion(event,sha256(event),connect=self.factory)
        self.assertFalse(denied["ack"])
        self.assertEqual(denied["reason_code"],"sre_not_live")

    def test_sre_cross_project_claim_not_accepted(self):
        from intelligence.qualified_data.sre_inbox import receive_sre_completion
        from intelligence.qualified_data.contracts import sha256
        event=self._sre_completion_event()
        foreign=uuid.uuid4().hex
        with self.factory() as c:
            c.execute(
                """INSERT INTO project_registry
                   (id,name,profile_revision,profile_hash,created_at,updated_at)
                   VALUES (%s,'cross-project integration test',1,%s,now(),now())""",
                (foreign,"a"*64))
        try:
            bad=dict(event,project_id=foreign)
            response=receive_sre_completion(bad,sha256(bad),connect=self.factory)
            self.assertFalse(response["ack"])
            self.assertEqual(response["reason_code"],"sre_not_found")
        finally:
            with self.factory() as c:
                c.execute("DELETE FROM qdp_sre_inbox WHERE project_id=%s",(foreign,))
                c.execute("DELETE FROM project_registry WHERE id=%s",(foreign,))

    def test_sre_delivery_rolls_back_before_commit_and_replays_after_commit(self):
        from intelligence.qualified_data.sre_inbox import receive_sre_completion
        from intelligence.qualified_data.contracts import sha256
        event=self._sre_completion_event()
        digest=sha256(event)
        def fail():
            raise RuntimeError("simulated crash before commit")
        with self.assertRaises(RuntimeError):
            receive_sre_completion(event,digest,connect=self.factory,
                                   _before_commit=fail)
        with self.factory() as c:
            self.assertIsNone(c.execute(
                "SELECT 1 FROM qdp_sre_inbox WHERE event_id=%s::uuid",
                (event["event_id"],)).fetchone())
        first=receive_sre_completion(event,digest,connect=self.factory)
        self.assertTrue(first["ack"])
        # Simulates losing the HTTP/gRPC ACK after COMMIT.
        after=receive_sre_completion(event,digest,connect=self.factory)
        self.assertTrue(after["ack"])
        self.assertTrue(after["replayed"])

    def test_concurrent_duplicate_event_deliveries_ack_one_durable_row(self):
        from concurrent.futures import ThreadPoolExecutor
        from intelligence.qualified_data.sre_inbox import receive_sre_completion
        from intelligence.qualified_data.contracts import sha256
        event=self._sre_completion_event()
        digest=sha256(event)
        def dispatch(_):
            return receive_sre_completion(event,digest,connect=self.factory)
        with ThreadPoolExecutor(max_workers=4) as executor:
            receipts=list(executor.map(dispatch,range(8)))
        self.assertTrue(all(x["ack"] for x in receipts))
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_sre_inbox WHERE event_id=%s::uuid",
                (event["event_id"],)).fetchone()["n"],1)

    def test_run_manifest_and_all_sre_results_pinned_atomically(self):
        from intelligence.qualified_data.run_manifest import read_run_manifest
        result=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        with self.factory() as c:
            pinned=read_run_manifest(c,self.pid,result["run_id"])
            self.assertEqual(pinned["expected_videos"],1)
            self.assertEqual(pinned["expected_pairs"],1)
            self.assertEqual(pinned["manifest"]["totals"]["qualified"],1)
            self.assertEqual(len(pinned["manifest"]["by_video"]),1)
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_item_lineage WHERE run_id=%s::uuid",
                (result["run_id"],)).fetchone()["n"],1)
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM jobs WHERE job_type=%s AND payload->>'run_id'=%s",
                (qdp.JOB_TYPE,result["run_id"])).fetchone()["n"],1)
            with self.assertRaises(psycopg.errors.RaiseException):
                c.execute(
                    "UPDATE qdp_run_manifests SET taxonomy_sha256=%s WHERE run_id=%s::uuid",
                    ("f"*64,result["run_id"]))

    def test_concurrent_create_same_key_is_single_manifest_and_job(self):
        from concurrent.futures import ThreadPoolExecutor
        def create(_):
            return qdp.create_run(self.pid,self.sre_run,self.key,
                                  connect=self.factory)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(create,range(12)))
        ids={r["run_id"] for r in results}
        self.assertEqual(len(ids),1)
        rid=results[0]["run_id"]
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_run_manifests WHERE run_id=%s::uuid",
                (rid,)).fetchone()["n"],1)
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM jobs WHERE job_type=%s AND payload->>'run_id'=%s",
                (qdp.JOB_TYPE,rid)).fetchone()["n"],1)

    def test_empty_qualified_set_is_terminal_no_work_with_auditable_manifest(self):
        from intelligence.qualified_data.run_manifest import read_run_manifest
        with self.factory() as c:
            # SRE classification rows can be immutable; use a legitimate
            # current human correction rather than rewriting model evidence.
            c.execute(
                """INSERT INTO sre_human_labels
                   (project_id,video_id,taxonomy_version_id,category_key,
                    label,reviewer,reason)
                   VALUES (%s,%s,%s,%s::uuid,'no_match','qdp-test','human veto')""",
                (self.pid,self.vid,self.taxonomy,self.category))
        result=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        self.assertEqual(result["totals"]["qualified"],0)
        self.assertEqual(result["status"],"completed")
        with self.factory() as c:
            self.assertEqual(read_run_manifest(c,self.pid,result["run_id"])
                             ["expected_pairs"],1)
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_items WHERE run_id=%s::uuid",
                (result["run_id"],)).fetchone()["n"],0)
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_item_lineage WHERE run_id=%s::uuid",
                (result["run_id"],)).fetchone()["n"],1)
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM jobs WHERE job_type=%s AND payload->>'run_id'=%s",
                (qdp.JOB_TYPE,result["run_id"])).fetchone()["n"],0)

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
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_item_lineage WHERE run_id=%s::uuid",
                (qdp_run,)).fetchone()["n"],2)
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_item_lineage
                   WHERE run_id=%s::uuid AND video_id=%s AND model_decision='no_match'""",
                (qdp_run,other_video)).fetchone()["n"],1)
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

    def test_review_veto_changes_selection_fingerprint_and_idempotency(self):
        # DB-authoritative current human label, not browser JSON.
        first = qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        with self.factory() as c:
            c.execute(
                """INSERT INTO sre_human_labels
                   (project_id,video_id,taxonomy_version_id,category_key,
                    label,reviewer,reason)
                   VALUES (%s,%s,%s,%s::uuid,'no_match','qdp-test','human veto')""",
                (self.pid,self.vid,self.taxonomy,self.category))
        preview = qdp.preview(self.pid,self.sre_run,connect=self.factory)
        self.assertEqual(preview["totals"],
                         {"qualified":0,"needs_review":0,"excluded":1})
        with self.assertRaises(QDPError) as changed:
            qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        self.assertEqual(changed.exception.code,"idempotency_conflict")
        alternate = qdp.create_run(self.pid,self.sre_run,self.key+"-v2",
                                   connect=self.factory)
        self.assertEqual(alternate["totals"]["qualified"],0)
        self.assertEqual(alternate["status"],"completed")
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_items WHERE run_id=%s::uuid",
                (alternate["run_id"],)).fetchone()["n"],0)
        self.assertTrue(first["run_id"] != alternate["run_id"])

    def test_worker_rechecks_human_veto_before_any_preparation(self):
        created = qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        with self.factory() as c:
            c.execute(
                """INSERT INTO sre_human_labels
                   (project_id,video_id,taxonomy_version_id,category_key,
                    label,reviewer,reason)
                   VALUES (%s,%s,%s,%s::uuid,'no_match','qdp-test','reviewed')""",
                (self.pid,self.vid,self.taxonomy,self.category))
        worked=qdp.work_once(connect=self.factory)
        self.assertEqual(worked["state"],"blocked")
        self.assertEqual(worked["reason"],"sre_evidence_revoked_or_changed")
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_enrichments WHERE run_id=%s::uuid",
                (created["run_id"],)).fetchone()["n"],0)

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
            # SRE S3 pins metadata_observation_id via FK; a correct revoke is
            # soft delete, never an invalid hard delete of referenced evidence.
            c.execute("""UPDATE source_video_metadata
                         SET is_current=FALSE,deleted_at=now()
                         WHERE video_id=%s""",(self.vid,))
        run=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        result=qdp.work_once(connect=self.factory)
        self.assertEqual(result["state"],"blocked")
        self.assertEqual(result["reason"],"pinned_metadata_unavailable_or_revoked")
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_enrichments WHERE run_id=%s::uuid",
                (run["run_id"],)).fetchone()["n"],0)

    def test_exact_pinned_metadata_ignores_newer_cross_source_observation(self):
        from intelligence.qualified_data.run_manifest import read_pinned_video
        from intelligence.qualified_data.provenance import read_exact_metadata
        item=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        other="second_"+uuid.uuid4().hex[:16]
        with self.factory() as c:
            c.execute(
                """INSERT INTO sources(id,provider,source_type,status,can_store_metadata)
                   VALUES (%s,'qdp_test','manual','enabled',true)""",(other,))
            newpolicy=c.execute(
                """INSERT INTO source_policy_versions
                   (source_id,version,is_active,permissions)
                   VALUES (%s,1,true,'{"can_store_metadata":true}'::jsonb)
                   RETURNING id""",(other,)).fetchone()["id"]
            late=c.execute(
                """INSERT INTO source_video_metadata
                   (video_id,source_id,source_policy_version_id,fetched_at,title,published_at)
                   VALUES (%s,%s,%s,now()+interval '1 minute','CROSS-SOURCE BAIT',
                           NULL) RETURNING id""",
                (self.vid,other,newpolicy)).fetchone()["id"]
            pinned=read_pinned_video(c,self.pid,item["run_id"],self.vid)
            data,reason=read_exact_metadata(c,self.pid,self.vid,pinned)
            self.assertIsNone(reason)
            self.assertEqual(data["metadata_id"],self.metadata_id)
            self.assertNotEqual(data["metadata_id"],late)
            self.assertEqual(data["source_id"],self.source)
            self.assertEqual(data["discovery_id"],self.discovery_id)
        processed=qdp.work_once(connect=self.factory)
        self.assertIn(processed["state"],("blocked","partial","ready"))
        with self.factory() as c:
            saved=c.execute(
                """SELECT metadata_id,source_id,metadata
                   FROM qdp_enrichments WHERE run_id=%s::uuid""",
                (item["run_id"],)).fetchone()
            self.assertIsNotNone(saved)
            self.assertEqual(saved["metadata_id"],self.metadata_id)
            self.assertEqual(saved["source_id"],self.source)
            self.assertEqual(saved["metadata"]["discovery_id"],self.discovery_id)

    def test_pinned_source_revocation_does_not_fallback(self):
        item=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        with self.factory() as c:
            c.execute("UPDATE source_video_metadata SET is_current=FALSE WHERE id=%s",
                      (self.metadata_id,))
        result=qdp.work_once(connect=self.factory)
        self.assertEqual(result["state"],"blocked")
        self.assertIn(result["reason"],(
            "pinned_metadata_unavailable_or_revoked","sre_content_unreadable"))
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_enrichments WHERE run_id=%s::uuid",
                (item["run_id"],)).fetchone()["n"],0)

    def test_null_published_at_is_not_fabricated(self):
        from intelligence.qualified_data.provenance import read_exact_metadata
        from intelligence.qualified_data.run_manifest import read_pinned_video
        item=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        with self.factory() as c:
            c.execute("UPDATE source_video_metadata SET published_at=NULL WHERE id=%s",
                      (self.metadata_id,))
            pinned=read_pinned_video(c,self.pid,item["run_id"],self.vid)
            metadata,reason=read_exact_metadata(c,self.pid,self.vid,pinned)
            self.assertIsNone(reason)
            self.assertIsNone(metadata["published_at"])

    def test_cross_project_discovery_fails_closed(self):
        from intelligence.qualified_data.provenance import read_exact_metadata
        from intelligence.qualified_data.run_manifest import read_pinned_video
        item=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        with self.factory() as c:
            c.execute("DELETE FROM sre_project_discoveries WHERE project_id=%s",
                      (self.pid,))
            pinned=read_pinned_video(c,self.pid,item["run_id"],self.vid)
            data,reason=read_exact_metadata(c,self.pid,self.vid,pinned)
            self.assertIsNone(data)
            self.assertEqual(reason,"sre_discovery_lineage_missing")

    def _insert_c_metric_inputs(self, *, n=2, mismatch=False,
                                 correction=False, null_last=False):
        with self.factory() as c:
            c.execute(
                """UPDATE sources SET can_store_metrics=true,
                          can_derive_scores=true WHERE id=%s""",(self.source,))
            c.execute(
                """UPDATE source_policy_versions
                   SET permissions='{"can_store_metadata":true,
                                      "can_store_metrics":true,
                                      "can_derive_scores":true}'::jsonb
                   WHERE id=%s""",(self.policy,))
            config=c.execute(
                """INSERT INTO metric_calculation_configs
                   (calculation_version,alpha,bayesian_k,is_active)
                   VALUES (%s,1,2,true) RETURNING id""",
                ("qdp-test-"+uuid.uuid4().hex,)).fetchone()["id"]
            ids=[]
            for index in range(n):
                definition="plays" if mismatch and index==n-1 else "views"
                views=(100+index*20) if not correction else (100-index*20)
                if null_last and index==n-1:
                    views=None
                new=c.execute(
                    """INSERT INTO metric_snapshots
                       (video_id,source_id,source_policy_version_id,
                        observed_at,views,precision,observation_time_assumed,
                        data_quality_status,source_metric_definition,source_metric_version)
                       VALUES (%s,%s,%s,now()-(%s * interval '1 hour'),%s,
                               'exact',FALSE,'ok',%s,'v1') RETURNING id""",
                    (self.vid,self.source,self.policy,12-index*4,views,definition)
                ).fetchone()["id"]
                ids.append(new)
        return ids,config

    def test_c_two_measured_points_prepare_only_verified_history(self):
        ids, config=self._insert_c_metric_inputs()
        created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        result=qdp.work_once(connect=self.factory)
        self.assertEqual(result["state"],"partial")  # A/B have no media.
        with self.factory() as c:
            cgroup=c.execute(
                """SELECT state,reason_code,inputs
                   FROM qdp_preparations WHERE run_id=%s::uuid AND analysis_group='C'""",
                (created["run_id"],)).fetchone()
            self.assertEqual(cgroup["state"],"ready")
            self.assertIsNone(cgroup["reason_code"])
            self.assertEqual(cgroup["inputs"]["metric_snapshot_ids"],ids)
            self.assertEqual(cgroup["inputs"]["score_config_id"],config)
            self.assertEqual(cgroup["inputs"]["metric_definition"],
                             {"name":"views","version":"v1"})
            self.assertNotIn("breakout_score",cgroup["inputs"])
            enrichment=c.execute(
                """SELECT metrics,metric_snapshot_ids FROM qdp_enrichments
                   WHERE run_id=%s::uuid""",(created["run_id"],)).fetchone()
            self.assertEqual(enrichment["metrics"]["growth_pair_ids"],ids)
            self.assertEqual(enrichment["metric_snapshot_ids"],ids)
            other=c.execute(
                """SELECT analysis_group,state FROM qdp_preparations
                   WHERE run_id=%s::uuid AND analysis_group IN ('A','B')""",
                (created["run_id"],)).fetchall()
            self.assertTrue(all(r["state"]=="blocked" for r in other))

    def test_c_one_point_blocked_without_affecting_a_b(self):
        self._insert_c_metric_inputs(n=1)
        created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        qdp.work_once(connect=self.factory)
        with self.factory() as c:
            rows=c.execute(
                """SELECT analysis_group,state,reason_code
                   FROM qdp_preparations WHERE run_id=%s::uuid""",
                (created["run_id"],)).fetchall()
            groups={r["analysis_group"]:r for r in rows}
            self.assertEqual(groups["C"]["reason_code"],"metric_history_insufficient")
            self.assertEqual(groups["A"]["reason_code"],"media_rights_or_asset_missing")
            self.assertEqual(groups["B"]["reason_code"],"media_rights_or_asset_missing")

    def test_c_mismatched_definitions_block(self):
        self._insert_c_metric_inputs(mismatch=True)
        created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        qdp.work_once(connect=self.factory)
        with self.factory() as c:
            r=c.execute(
                """SELECT state,reason_code FROM qdp_preparations
                   WHERE run_id=%s::uuid AND analysis_group='C'""",
                (created["run_id"],)).fetchone()
            self.assertEqual(r["state"],"blocked")
            self.assertEqual(r["reason_code"],"metric_definition_incompatible")

    def test_c_counter_correction_never_ready(self):
        self._insert_c_metric_inputs(correction=True)
        created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        qdp.work_once(connect=self.factory)
        with self.factory() as c:
            r=c.execute(
                """SELECT state,reason_code FROM qdp_preparations
                   WHERE run_id=%s::uuid AND analysis_group='C'""",
                (created["run_id"],)).fetchone()
            self.assertEqual(r["reason_code"],"metric_counter_correction")

    def test_c_no_rights_no_metric_history_exposed(self):
        self._insert_c_metric_inputs()
        with self.factory() as c:
            c.execute("""UPDATE source_policy_versions
                         SET permissions='{"can_store_metadata":true}'::jsonb
                         WHERE id=%s""",(self.policy,))
        created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        qdp.work_once(connect=self.factory)
        with self.factory() as c:
            r=c.execute(
                """SELECT metrics,metric_snapshot_ids FROM qdp_enrichments
                   WHERE run_id=%s::uuid""",
                (created["run_id"],)).fetchone()
            self.assertEqual(r["metric_snapshot_ids"],[])
            self.assertEqual(r["metrics"]["observations"],[])
            cgroup=c.execute(
                """SELECT reason_code FROM qdp_preparations
                   WHERE run_id=%s::uuid AND analysis_group='C'""",
                (created["run_id"],)).fetchone()
            self.assertEqual(cgroup["reason_code"],"score_rights_missing")

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
