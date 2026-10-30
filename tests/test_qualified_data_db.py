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
                """INSERT INTO source_policy_versions
                   (source_id,version,is_active,reviewed_at,permissions)
                   VALUES (%s,1,true,now(),%s::jsonb) RETURNING id""",
                (self.source,json.dumps({"can_store_metadata":True,
                                         "can_semantic_classify":True}))).fetchone()["id"]
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
                   VALUES (%s,1,true,'{"can_store_metadata":true,
                                       "can_semantic_classify":true}'::jsonb)
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
            "pinned_metadata_unavailable_or_revoked","sre_content_unreadable",
             "sre_evidence_revoked_or_changed"))
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
                                      "can_derive_scores":true,
                                       "can_semantic_classify":true}'::jsonb
                   WHERE id=%s""",(self.policy,))
            # Isolated PG fixture reuses a shared database across tests.
            # The C schema permits only one active config globally.
            c.execute("UPDATE metric_calculation_configs SET is_active=FALSE WHERE is_active")
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
            self.assertEqual(groups["A"]["reason_code"],"media_rights_missing")
            self.assertEqual(groups["B"]["reason_code"],"media_rights_missing")

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
                         SET permissions='{"can_store_metadata":true,
                                       "can_semantic_classify":true}'::jsonb
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

    def _media_video_source(self, content_kind="video"):
        with self.factory() as c:
            c.execute("UPDATE videos SET content_kind=%s WHERE id=%s",
                      (content_kind,self.vid))
            c.execute("UPDATE sources SET can_analyze_media=true WHERE id=%s",
                      (self.source,))
            c.execute("""UPDATE source_policy_versions SET
                         permissions='{"can_store_metadata":true,
                                        "can_analyze_media":true,
                                         "can_semantic_classify":true}'::jsonb
                         WHERE id=%s""",(self.policy,))

    def _media_asset(self, file_path, *, asset_source=None, expires_sql="NULL",
                     media_kind="video", permission="analysis"):
        import hashlib
        data=file_path.read_bytes()
        with self.factory() as c:
            return c.execute(
                """INSERT INTO media_assets
                   (video_id,source_id,media_kind,local_path,sha256,
                    permission,expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,"""+expires_sql+""")
                   RETURNING id""",
                (self.vid,asset_source or self.source,media_kind,str(file_path),
                 hashlib.sha256(data).hexdigest(),permission)).fetchone()["id"]

    def test_existing_video_asset_ready_a_b_independent_c(self):
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        self._media_video_source()
        with tempfile.TemporaryDirectory() as tmp:
            media=Path(tmp)/"real-local-record.mp4"
            media.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00test-contents")
            asset_id=self._media_asset(media)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":tmp}):
                created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
            with self.factory() as c:
                groups=c.execute(
                    """SELECT analysis_group,state,reason_code,inputs
                       FROM qdp_preparations WHERE run_id=%s::uuid""",
                    (created["run_id"],)).fetchall()
                by={g["analysis_group"]:g for g in groups}
                self.assertEqual(by["A"]["state"],"ready")
                self.assertEqual(by["B"]["state"],"ready")
                self.assertEqual(by["A"]["inputs"]["media_asset_id"],asset_id)
                self.assertEqual(by["B"]["inputs"]["media_asset_id"],asset_id)
                self.assertEqual(by["C"]["reason_code"],"score_rights_missing")
                self.assertEqual(c.execute(
                    "SELECT media_asset_id FROM qdp_enrichments WHERE run_id=%s::uuid",
                    (created["run_id"],)).fetchone()["media_asset_id"],asset_id)
                self.assertNotIn("local_path",by["A"]["inputs"])

    def test_video_asset_revoked_media_not_replaced_by_c(self):
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        self._media_video_source()
        with tempfile.TemporaryDirectory() as tmp:
            media=Path(tmp)/"revoke.mp4"
            media.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00video")
            aid=self._media_asset(media,permission="metadata_only")
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":tmp}):
                created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
            with self.factory() as c:
                by={r["analysis_group"]:r for r in c.execute(
                    """SELECT analysis_group,reason_code FROM qdp_preparations
                       WHERE run_id=%s::uuid""",(created["run_id"],)).fetchall()}
                self.assertEqual(by["A"]["reason_code"],"media_permission_missing")
                self.assertEqual(by["B"]["reason_code"],"media_permission_missing")
                self.assertEqual(by["C"]["reason_code"],"score_rights_missing")

    def test_carousel_does_not_use_video_a_or_fake_b_manifest(self):
        self._media_video_source("photo_carousel")
        created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        qdp.work_once(connect=self.factory)
        with self.factory() as c:
            by={r["analysis_group"]:r for r in c.execute(
                """SELECT analysis_group,state,reason_code FROM qdp_preparations
                   WHERE run_id=%s::uuid""",(created["run_id"],)).fetchall()}
            self.assertEqual(by["A"]["reason_code"],"video_a_carousel_unsupported")
            self.assertEqual(by["B"]["reason_code"],"media_manifest_required")
            self.assertEqual(by["C"]["reason_code"],"score_rights_missing")

    def test_unconfigured_media_root_and_missing_file_block(self):
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        self._media_video_source()
        with tempfile.TemporaryDirectory() as tmp:
            media=Path(tmp)/"not-there.mp4"
            media.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00media")
            self._media_asset(media)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":"",
                                        "CM_GROUP_A_MEDIA_ROOTS":""}):
                created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
            with self.factory() as c:
                r=c.execute(
                    """SELECT reason_code FROM qdp_preparations
                       WHERE run_id=%s::uuid AND analysis_group='A'""",
                    (created["run_id"],)).fetchone()
                self.assertEqual(r["reason_code"],"media_roots_unconfigured")

    def test_group_c_current_read_independent_of_media_rights(self):
        from intelligence.qualified_data.preparation import read_current_preparation
        self._insert_c_metric_inputs()
        created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        qdp.work_once(connect=self.factory)
        with self.factory() as c:
            c.execute("UPDATE sources SET can_analyze_media=false WHERE id=%s",
                      (self.source,))
            present=c.execute(
                """SELECT analysis_group FROM qdp_group_current_preparations
                   WHERE run_id=%s::uuid AND project_id=%s""",
                (created["run_id"],self.pid)).fetchall()
            self.assertEqual({r["analysis_group"] for r in present},{"C"})
        current=read_current_preparation(self.pid,created["run_id"],
                                          self.vid,"C",connect=self.factory)
        self.assertEqual(current["state"],"ready")
        self.assertEqual(len(current["inputs"]["metric_snapshot_ids"]),2)
        self.assertNotIn("media_asset_id",current["inputs"])

    def test_current_a_b_survive_c_rights_revocation(self):
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        from intelligence.qualified_data.preparation import read_current_preparation
        self._media_video_source()
        with tempfile.TemporaryDirectory() as tmp:
            local=Path(tmp)/"ready.mp4"
            local.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00body")
            self._media_asset(local)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":tmp}):
                created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
                with self.factory() as c:
                    c.execute(
                        """UPDATE source_policy_versions SET
                           permissions='{"can_store_metadata":true,
                                          "can_analyze_media":true,
                                         "can_semantic_classify":true}'::jsonb
                           WHERE id=%s""",(self.policy,))
                    present=c.execute(
                        """SELECT analysis_group FROM qdp_group_current_preparations
                           WHERE run_id=%s::uuid AND project_id=%s""",
                        (created["run_id"],self.pid)).fetchall()
                    self.assertEqual({r["analysis_group"] for r in present},{"A","B"})
                for group in ("A","B"):
                    current=read_current_preparation(
                        self.pid,created["run_id"],self.vid,group,connect=self.factory)
                    self.assertEqual(current["state"],"ready")
                    self.assertEqual(current["inputs"]["group"],group)

    def test_group_read_revocation_does_not_mutate_historical_fact(self):
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        from intelligence.qualified_data.preparation import read_current_preparation
        self._media_video_source()
        with tempfile.TemporaryDirectory() as tmp:
            local=Path(tmp)/"valid.mp4"
            local.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00body")
            self._media_asset(local)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":tmp}):
                created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
                with self.factory() as c:
                    c.execute("UPDATE sources SET can_analyze_media=false WHERE id=%s",
                              (self.source,))
                    original=c.execute(
                        """SELECT state FROM qdp_preparations
                           WHERE run_id=%s::uuid AND analysis_group='A'""",
                        (created["run_id"],)).fetchone()
                    self.assertEqual(original["state"],"ready")
                    self.assertEqual(c.execute(
                        """SELECT count(*) AS n FROM qdp_group_current_preparations
                           WHERE project_id=%s AND run_id=%s::uuid""",
                        (self.pid,created["run_id"])).fetchone()["n"],0)
                current=read_current_preparation(
                    self.pid,created["run_id"],self.vid,"A",connect=self.factory)
                self.assertEqual(current["state"],"blocked")
                self.assertIsNone(current["inputs"])

    def test_group_current_read_cross_project_returns_blocked(self):
        from intelligence.qualified_data.preparation import read_current_preparation
        self._insert_c_metric_inputs()
        created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        qdp.work_once(connect=self.factory)
        current=read_current_preparation(
            "f"*32,created["run_id"],self.vid,"C",connect=self.factory)
        self.assertEqual(current["state"],"blocked")
        self.assertIsNone(current["inputs"])

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

    def test_worker_outbox_one_group_event_per_group_and_atomic_receipts(self):
        created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        qdp.work_once(connect=self.factory)
        with self.factory() as c:
            events=c.execute(
                """SELECT event_type,analysis_group,payload,payload_sha256
                   FROM qdp_outbox WHERE run_id=%s::uuid
                   ORDER BY event_type,analysis_group""",
                (created["run_id"],)).fetchall()
            self.assertEqual(sum(e["event_type"]=="qualified_set_created" for e in events),1)
            self.assertEqual(sum(e["event_type"]=="run_completed" for e in events),1)
            self.assertEqual(sum(e["event_type"].startswith("preparation_") for e in events),3)
            self.assertEqual({e["analysis_group"] for e in events
                              if e["analysis_group"]}, {"A","B","C"})
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_outbox_deliveries d
                   JOIN qdp_outbox o ON o.event_id=d.event_id
                   WHERE o.run_id=%s::uuid""",
                (created["run_id"],)).fetchone()["n"],0)
        self.assertIsNone(qdp.work_once(connect=self.factory))
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_outbox WHERE run_id=%s::uuid",
                (created["run_id"],)).fetchone()["n"],5)

    def test_worker_no_duplicate_with_ten_concurrent_workers(self):
        from concurrent.futures import ThreadPoolExecutor
        created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        with ThreadPoolExecutor(max_workers=10) as pool:
            got=list(pool.map(lambda i:qdp.work_once(
                connect=self.factory,worker_id="qdp-worker-"+str(i)),range(10)))
        self.assertEqual(sum(x is not None for x in got),1)
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_enrichments WHERE run_id=%s::uuid",
                (created["run_id"],)).fetchone()["n"],1)
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_outbox WHERE run_id=%s::uuid
                   AND event_type LIKE 'preparation_%%'""",
                (created["run_id"],)).fetchone()["n"],3)

    def test_worker_before_commit_crash_rolls_back_all_group_snapshots_and_events(self):
        from unittest.mock import patch
        created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        from intelligence.qualified_data import worker_hardening
        def crash_outbox(*args,**kwargs):
            raise RuntimeError("injected outbox failure before commit")
        with patch.object(worker_hardening,"append_event",side_effect=crash_outbox):
            result=qdp.work_once(connect=self.factory)
        self.assertEqual(result["state"],"retry_or_failed")
        self.assertEqual(result["retry_delay_seconds"],30)
        with self.factory() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_enrichments WHERE run_id=%s::uuid",
                (created["run_id"],)).fetchone()["n"],0)
            self.assertEqual(c.execute(
                "SELECT count(*) AS n FROM qdp_preparations WHERE run_id=%s::uuid",
                (created["run_id"],)).fetchone()["n"],0)
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_outbox
                   WHERE run_id=%s::uuid AND event_type LIKE 'preparation_%%'""",
                (created["run_id"],)).fetchone()["n"],0)
            job=c.execute(
                "SELECT attempts,state FROM jobs WHERE job_type=%s AND payload->>'run_id'=%s",
                (qdp.JOB_TYPE,created["run_id"])).fetchone()
            self.assertEqual((job["attempts"],job["state"]),(1,"queued"))

    def test_after_commit_worker_crash_replay_does_not_duplicate_events(self):
        created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        self.assertIsNotNone(qdp.work_once(connect=self.factory))
        # Crash of the external process AFTER transaction commit: next worker
        # cannot recreate the QDP snapshot or a new outbox event.
        self.assertIsNone(qdp.work_once(connect=self.factory))
        with self.factory() as c:
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_preparations
                   WHERE run_id=%s::uuid""",(created["run_id"],)).fetchone()["n"],3)
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_outbox
                   WHERE run_id=%s::uuid AND event_type LIKE 'preparation_%%'""",
                (created["run_id"],)).fetchone()["n"],3)

    def test_exhausted_lease_reaper_finalizes_item_and_truthful_failed_run(self):
        created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        with self.factory() as c:
            c.execute(
                """UPDATE jobs SET state='running',attempts=max_attempts,
                     locked_by='dead-worker',lease_until=now()-interval '1 minute'
                   WHERE job_type=%s AND payload->>'run_id'=%s""",
                (qdp.JOB_TYPE,created["run_id"]))
        self.assertIsNone(qdp.work_once(connect=self.factory))
        with self.factory() as c:
            job=c.execute(
                "SELECT state FROM jobs WHERE job_type=%s AND payload->>'run_id'=%s",
                (qdp.JOB_TYPE,created["run_id"])).fetchone()
            item=c.execute(
                "SELECT status,reason_code FROM qdp_items WHERE run_id=%s::uuid",
                (created["run_id"],)).fetchone()
            run=c.execute(
                "SELECT status,finished_at FROM qdp_runs WHERE id=%s::uuid",
                (created["run_id"],)).fetchone()
            self.assertEqual(job["state"],"failed")
            self.assertEqual((item["status"],item["reason_code"]),
                             ("blocked","worker_attempts_exhausted"))
            self.assertEqual(run["status"],"failed")
            self.assertIsNotNone(run["finished_at"])

    def test_expired_lease_can_be_reclaimed_before_exhaustion(self):
        created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        with self.factory() as c:
            c.execute(
                """UPDATE jobs SET state='running',attempts=1,
                     locked_by='crashed',lease_generation=5,
                     lease_until=now()-interval '1 minute'
                   WHERE job_type=%s AND payload->>'run_id'=%s""",
                (qdp.JOB_TYPE,created["run_id"]))
        res=qdp.work_once(connect=self.factory,worker_id="new-owner")
        self.assertEqual(res["run_id"],created["run_id"])
        with self.factory() as c:
            job=c.execute(
                """SELECT lease_generation,state,attempts FROM jobs
                   WHERE job_type=%s AND payload->>'run_id'=%s""",
                (qdp.JOB_TYPE,created["run_id"])).fetchone()
            self.assertEqual(job["lease_generation"],6)
            self.assertEqual(job["attempts"],2)
            self.assertEqual(job["state"],"completed")

    def test_qdp_worker_never_executes_peer_jobs(self):
        with self.factory() as c:
            c.execute(
                """INSERT INTO jobs(job_type,idempotency_key,payload)
                   VALUES ('group_a.test',%s,'{}'::jsonb)""",
                ("group-a-unit-"+uuid.uuid4().hex,))
        self.assertIsNone(qdp.work_once(connect=self.factory))
        with self.factory() as c:
            row=c.execute(
                "SELECT state FROM jobs WHERE job_type='group_a.test' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(row["state"],"queued")

    def _make_disposable_consumer_receiver(self, *, crash_after_commit=False,
                                          always_fail=False):
        # Test-only peer inbox + idempotent job. This table DOES NOT exist in
        # QDP production schema; it proves a receiver's independent transaction.
        with self.factory() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS qdp_test_consumer_jobs (
                event_id UUID PRIMARY KEY,consumer TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,receipt_key TEXT NOT NULL,
                inserted_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        factory=self.factory
        class DisposableConsumer:
            calls=0
            def receive(self, pointer, payload_sha256):
                self.calls+=1
                if always_fail:
                    raise RuntimeError("peer permanently offline for test")
                receipt_key="consumer-"+pointer.event_id
                with factory() as c:
                    with c.transaction():
                        c.execute(
                            """INSERT INTO qdp_test_consumer_jobs
                               (event_id,consumer,payload_sha256,receipt_key)
                               VALUES (%s::uuid,%s,%s,%s)
                               ON CONFLICT (event_id) DO NOTHING""",
                            (pointer.event_id,"group_"+pointer.analysis_group.lower(),
                             payload_sha256,receipt_key))
                        item=c.execute(
                            """SELECT consumer,payload_sha256,receipt_key
                               FROM qdp_test_consumer_jobs WHERE event_id=%s::uuid""",
                            (pointer.event_id,)).fetchone()
                        if (item["consumer"]!="group_"+pointer.analysis_group.lower()
                                or item["payload_sha256"]!=payload_sha256):
                            raise ValueError("consumer inbox hash conflict")
                if crash_after_commit:
                    raise RuntimeError("crash after COMMIT but before QDP ACK")
                return {"event_id":pointer.event_id,
                        "consumer":"group_"+pointer.analysis_group.lower(),
                        "payload_sha256":payload_sha256,
                        "receipt_key":receipt_key,"state":"committed"}
        return DisposableConsumer()

    def _ready_c_event(self):
        self._insert_c_metric_inputs()
        result=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        qdp.work_once(connect=self.factory)
        with self.factory() as c:
            row=c.execute(
                """SELECT o.event_id FROM qdp_outbox o
                   WHERE o.run_id=%s::uuid AND o.analysis_group='C'
                     AND o.event_type='preparation_ready'""",
                (result["run_id"],)).fetchone()
            self.assertIsNotNone(row)
        return result["run_id"],str(row["event_id"])

    def test_delivery_receiver_50_replays_produce_one_consumer_job(self):
        from intelligence.qualified_data.outbox_delivery import dispatch_once
        run_id,event_id=self._ready_c_event()
        receiver=self._make_disposable_consumer_receiver()
        answers=[dispatch_once("group_c",receiver=receiver,connect=self.factory)
                 for _ in range(50)]
        self.assertEqual(answers[0]["state"],"acknowledged")
        self.assertTrue(all(x is None for x in answers[1:]))
        self.assertEqual(receiver.calls,1)
        # Replays delivered directly to the SAME durable consumer inbox must
        # remain one job as well (the producer may crash before its own ACK).
        from intelligence.qualified_data.integrations.contracts import QDPHandoffPointer
        with self.factory() as c:
            qdp_record=c.execute(
                """SELECT o.payload_sha256,p.input_sha256
                   FROM qdp_outbox o
                   JOIN qdp_preparations p ON p.run_id=o.run_id
                      AND p.video_id=o.video_id
                      AND p.analysis_group=o.analysis_group
                   WHERE o.event_id=%s::uuid""",(event_id,)).fetchone()
        replay=QDPHandoffPointer(
            event_id=event_id,project_id=self.pid,qdp_run_id=run_id,
            video_id=self.vid,analysis_group="C",
            input_sha256=qdp_record["input_sha256"])
        for _ in range(50):
            receipt=receiver.receive(replay,qdp_record["payload_sha256"])
            self.assertEqual(receipt["state"],"committed")
        self.assertEqual(receiver.calls,51)
        with self.factory() as c:
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_test_consumer_jobs
                   WHERE event_id=%s::uuid""",(event_id,)).fetchone()["n"],1)
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_delivery_receipts
                   WHERE event_id=%s::uuid AND consumer='group_c'""",
                (event_id,)).fetchone()["n"],1)
            status=c.execute(
                """SELECT state,attempts FROM qdp_outbox_deliveries
                   WHERE event_id=%s::uuid AND consumer='group_c'""",
                (event_id,)).fetchone()
            self.assertEqual((status["state"],status["attempts"]),
                             ("acknowledged",1))
            journal=c.execute(
                """SELECT transition,attempt FROM qdp_delivery_attempt_log
                   WHERE event_id=%s::uuid AND consumer='group_c'
                   ORDER BY id""",(event_id,)).fetchall()
            self.assertEqual([r["transition"] for r in journal],
                             ["leased","acknowledged"])
            self.assertEqual([r["attempt"] for r in journal],[1,1])

    def test_delivery_ten_competing_dispatchers_one_commit_and_receipt(self):
        from concurrent.futures import ThreadPoolExecutor
        from intelligence.qualified_data.outbox_delivery import dispatch_once
        _,event_id=self._ready_c_event()
        receiver=self._make_disposable_consumer_receiver()
        with ThreadPoolExecutor(max_workers=10) as pool:
            answers=list(pool.map(lambda i:dispatch_once(
                "group_c",receiver=receiver,connect=self.factory,
                worker_id="qdp-delivery-worker-"+str(i)),range(10)))
        self.assertEqual(sum(bool(x and x.get("state")=="acknowledged")
                             for x in answers),1)
        with self.factory() as c:
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_test_consumer_jobs
                   WHERE event_id=%s::uuid""",(event_id,)).fetchone()["n"],1)
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_delivery_receipts
                   WHERE event_id=%s::uuid""",(event_id,)).fetchone()["n"],1)

    def test_delivery_crash_after_peer_commit_before_ack_idempotent(self):
        from intelligence.qualified_data.outbox_delivery import dispatch_once
        _,event_id=self._ready_c_event()
        crashing=self._make_disposable_consumer_receiver(crash_after_commit=True)
        res=dispatch_once("group_c",receiver=crashing,connect=self.factory)
        self.assertEqual(res["state"],"pending")
        self.assertEqual(res["retry_after_seconds"],30)
        with self.factory() as c:
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_test_consumer_jobs
                   WHERE event_id=%s::uuid""",(event_id,)).fetchone()["n"],1)
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_delivery_receipts
                   WHERE event_id=%s::uuid""",(event_id,)).fetchone()["n"],0)
            c.execute(
                """UPDATE qdp_outbox_deliveries SET run_after=now()-interval '1 second'
                   WHERE event_id=%s::uuid""",(event_id,))
        recovering=self._make_disposable_consumer_receiver()
        result=dispatch_once("group_c",receiver=recovering,connect=self.factory)
        self.assertEqual(result["state"],"acknowledged")
        with self.factory() as c:
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_test_consumer_jobs
                   WHERE event_id=%s::uuid""",(event_id,)).fetchone()["n"],1)
            journal=c.execute(
                """SELECT transition FROM qdp_delivery_attempt_log
                   WHERE event_id=%s::uuid ORDER BY id""",
                (event_id,)).fetchall()
            self.assertEqual([r["transition"] for r in journal],
                             ["leased","retry_scheduled","leased","acknowledged"])

    def test_delivery_expired_lease_generation_fences_stale_worker(self):
        from intelligence.qualified_data.outbox_delivery import (
            claim_delivery,settle_delivery)
        _,event_id=self._ready_c_event()
        older=claim_delivery("group_c",connect=self.factory,worker_id="worker-old")
        self.assertIsNotNone(older)
        with self.factory() as c:
            c.execute(
                """UPDATE qdp_outbox_deliveries
                   SET lease_until=now()-interval '1 second'
                   WHERE event_id=%s::uuid""",(event_id,))
        newer=claim_delivery("group_c",connect=self.factory,worker_id="worker-new")
        self.assertEqual(newer["lease_generation"],older["lease_generation"]+1)
        old=settle_delivery(older,error_code="stale",connect=self.factory)
        self.assertEqual(old["state"],"lost_lease")
        now=settle_delivery(newer,error_code="temporary",connect=self.factory)
        self.assertEqual(now["state"],"pending")
        with self.factory() as c:
            r=c.execute(
                """SELECT attempts,state,reason_code FROM qdp_outbox_deliveries
                   WHERE event_id=%s::uuid""",(event_id,)).fetchone()
            self.assertEqual((r["attempts"],r["state"]),(2,"pending"))

    def test_delivery_attempt_log_is_append_only(self):
        from intelligence.qualified_data.outbox_delivery import (
            claim_delivery,settle_delivery)
        _,event_id=self._ready_c_event()
        lease=claim_delivery("group_c",connect=self.factory)
        settle_delivery(lease,error_code="temporary",connect=self.factory)
        with self.factory() as c:
            item=c.execute(
                """SELECT id FROM qdp_delivery_attempt_log
                   WHERE event_id=%s::uuid ORDER BY id LIMIT 1""",
                (event_id,)).fetchone()
            self.assertIsNotNone(item)
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                c.execute("UPDATE qdp_delivery_attempt_log SET reason_code='oops' WHERE id=%s",
                          (item["id"],))
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                c.execute("DELETE FROM qdp_delivery_attempt_log WHERE id=%s",
                          (item["id"],))

    def test_delivery_dead_letter_exhausted_without_affecting_other_consumers(self):
        from intelligence.qualified_data.outbox_delivery import (
            claim_delivery,settle_delivery)
        _,event_id=self._ready_c_event()
        with self.factory() as c:
            c.execute("""UPDATE qdp_outbox_deliveries
                         SET max_attempts=2,attempts=1
                         WHERE event_id=%s::uuid""",(event_id,))
        lease=claim_delivery("group_c",connect=self.factory)
        result=settle_delivery(lease,error_code="temporary",connect=self.factory)
        self.assertEqual(result["state"],"dead_letter")
        with self.factory() as c:
            row=c.execute(
                """SELECT state,attempts FROM qdp_outbox_deliveries
                   WHERE event_id=%s::uuid""",(event_id,)).fetchone()
            self.assertEqual((row["state"],row["attempts"]),("dead_letter",2))
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_delivery_receipts
                   WHERE event_id=%s::uuid""",(event_id,)).fetchone()["n"],0)

    def test_delivery_rechecks_current_rights_before_peer_and_never_acks(self):
        from intelligence.qualified_data.outbox_delivery import dispatch_once
        _,event_id=self._ready_c_event()
        with self.factory() as c:
            c.execute(
                """UPDATE source_policy_versions SET
                   permissions='{"can_store_metadata":true,
                                       "can_semantic_classify":true}'::jsonb
                   WHERE id=%s""",(self.policy,))
        receiver=self._make_disposable_consumer_receiver()
        res=dispatch_once("group_c",receiver=receiver,connect=self.factory)
        self.assertEqual(res["state"],"dead_letter")
        self.assertEqual(receiver.calls,0)
        with self.factory() as c:
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_delivery_receipts
                   WHERE event_id=%s::uuid""",(event_id,)).fetchone()["n"],0)

    def test_delivery_multiple_receivers_get_independent_acks_and_retries(self):
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        from intelligence.qualified_data.outbox_delivery import dispatch_once
        self._media_video_source()
        with tempfile.TemporaryDirectory() as tmp:
            local=Path(tmp)/"two.mp4"
            local.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00payload")
            self._media_asset(local)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":tmp}):
                created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
                ok=self._make_disposable_consumer_receiver()
                failing=self._make_disposable_consumer_receiver(always_fail=True)
                good=dispatch_once("group_a",receiver=ok,connect=self.factory)
                bad=dispatch_once("group_b",receiver=failing,connect=self.factory)
            self.assertEqual(good["state"],"acknowledged")
            self.assertEqual(bad["state"],"pending")
            with self.factory() as c:
                records=c.execute(
                    """SELECT d.consumer,d.state FROM qdp_outbox_deliveries d
                       JOIN qdp_outbox o ON o.event_id=d.event_id
                       WHERE o.run_id=%s::uuid""",(created["run_id"],)).fetchall()
                self.assertEqual({r["consumer"]:r["state"] for r in records},
                                 {"group_a":"acknowledged","group_b":"pending"})

    def test_group_a_peer_reader_exact_outbox_provenance_and_revocation(self):
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        from intelligence.qualified_data.integrations.group_a import (
            QDPGroupAReader,ga_envelope)
        from intelligence.qualified_data.integrations.contracts import QDPHandoffPointer
        self._media_video_source()
        with tempfile.TemporaryDirectory() as root:
            local=Path(root)/"media.mp4"
            local.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00payload")
            self._media_asset(local,permission="analysis_allowed")
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":root}):
                created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
                with self.factory() as c:
                    event=c.execute(
                        """SELECT o.event_id,o.payload_sha256,p.input_sha256
                           FROM qdp_outbox o JOIN qdp_preparations p
                           ON p.run_id=o.run_id AND p.video_id=o.video_id
                             AND p.analysis_group=o.analysis_group
                           WHERE o.run_id=%s::uuid AND o.analysis_group='A'
                           AND o.event_type='preparation_ready'""",
                        (created["run_id"],)).fetchone()
                    self.assertIsNotNone(event)
                    p=QDPHandoffPointer(event_id=str(event["event_id"]),
                        project_id=self.pid,qdp_run_id=created["run_id"],
                        video_id=self.vid,analysis_group="A",
                        input_sha256=event["input_sha256"])
                    envelope,_=ga_envelope(p,event["payload_sha256"])
                    reader=QDPGroupAReader(c)
                    self.assertEqual(reader.capability().state,"available")
                    got=reader.read_preparation(envelope["pointer"])
                    self.assertEqual(got["state"],"ready")
                    self.assertEqual(got["pointer"],envelope["pointer"])
                    bad=dict(envelope["pointer"],event_payload_sha256="f"*64)
                    self.assertIsNone(reader.read_preparation(bad))
                    c.execute("UPDATE sources SET can_analyze_media=false WHERE id=%s",
                              (self.source,))
                    self.assertIsNone(reader.read_preparation(envelope["pointer"]))

    def test_fix15_immediate_human_veto_current_view_without_poll(self):
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        from intelligence.qualified_data.preparation import read_current_preparation
        self._media_video_source()
        with tempfile.TemporaryDirectory() as root:
            file=Path(root)/"sre-human-veto.mp4"
            file.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00video")
            self._media_asset(file)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":root}):
                created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
                prior=read_current_preparation(self.pid,created["run_id"],self.vid,
                                               "A",connect=self.factory)
                self.assertEqual(prior["state"],"ready")
                with self.factory() as c:
                    c.execute(
                        """INSERT INTO sre_human_labels
                           (project_id,video_id,taxonomy_version_id,category_key,
                            label,reviewer,reason)
                           VALUES (%s,%s,%s,%s::uuid,'no_match','reviewer','veto')""",
                        (self.pid,self.vid,self.taxonomy,self.category))
                    self.assertEqual(c.execute(
                        """SELECT count(*) AS n FROM qdp_group_current_preparations
                           WHERE project_id=%s AND run_id=%s::uuid""",
                        (self.pid,created["run_id"])).fetchone()["n"],0)
                    self.assertEqual(c.execute(
                        """SELECT count(*) AS n FROM qdp_preparations
                           WHERE run_id=%s::uuid AND state='ready'""",
                        (created["run_id"],)).fetchone()["n"],2)
                current=read_current_preparation(self.pid,created["run_id"],self.vid,
                                                  "A",connect=self.factory)
                self.assertEqual(current["state"],"blocked")
                self.assertIsNone(current["inputs"])
                visible=qdp.get_run(self.pid,created["run_id"],connect=self.factory)
                self.assertEqual(visible["items"][0]["status"],"blocked")
                self.assertEqual(visible["items"][0]["reason_code"],
                                 "current_selection_or_group_rights_revoked")

    def test_fix15_group_scoped_revocation_is_atomic_idempotent_and_independent(self):
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        from intelligence.qualified_data.invalidation import invalidate_run
        self._media_video_source()
        with tempfile.TemporaryDirectory() as root:
            file=Path(root)/"group-revoke.mp4"
            file.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00body")
            self._media_asset(file)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":root}):
                q=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
                args=dict(origin="operator",reason_code="operator_revoked",
                          provenance_sha256="a"*64,video_id=self.vid,group="A",
                          connect=self.factory)
                first=invalidate_run(self.pid,q["run_id"],**args)
                second=invalidate_run(self.pid,q["run_id"],**args)
                self.assertEqual(first["state"],"invalidated")
                self.assertEqual(first["generation"],1)
                self.assertEqual(second["state"],"replayed")
                self.assertEqual(second["generation"],1)
                with self.factory() as c:
                    live=c.execute(
                        """SELECT analysis_group FROM qdp_group_current_preparations
                           WHERE project_id=%s AND run_id=%s::uuid""",
                        (self.pid,q["run_id"])).fetchall()
                    self.assertEqual({x["analysis_group"] for x in live},{"B"})
                    events=c.execute(
                        """SELECT o.event_type,o.analysis_group,d.state AS delivery_state
                           FROM qdp_outbox o
                           LEFT JOIN qdp_outbox_deliveries d ON d.event_id=o.event_id
                           WHERE o.run_id=%s::uuid AND o.analysis_group='A'
                           ORDER BY o.created_at""",
                        (q["run_id"],)).fetchall()
                    self.assertEqual(len(events),2)
                    self.assertEqual({x["event_type"]:x["delivery_state"] for x in events},
                                     {"preparation_ready":"dead_letter",
                                      "preparation_invalidated":"pending"})
                    self.assertEqual(c.execute(
                        "SELECT count(*) AS n FROM qdp_preparations WHERE run_id=%s::uuid",
                        (q["run_id"],)).fetchone()["n"],3)

    def test_fix15_invalidation_after_claim_rejects_stale_positive_ack(self):
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        from intelligence.qualified_data.outbox_delivery import (
            claim_delivery,settle_delivery)
        from intelligence.qualified_data.invalidation import invalidate_run
        self._media_video_source()
        with tempfile.TemporaryDirectory() as root:
            file=Path(root)/"claimed.mp4"
            file.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00body")
            self._media_asset(file)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":root}):
                created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
                lease=claim_delivery("group_a",connect=self.factory)
                self.assertEqual(lease["claimed_invalidation_generation"],0)
                invalidate_run(self.pid,created["run_id"],video_id=self.vid,
                               group="A",origin="operator",
                               reason_code="operator_revoked",
                               provenance_sha256="a"*64,connect=self.factory)
                receipt={"state":"committed","event_id":str(lease["event_id"]),
                         "consumer":"group_a","payload_sha256":"a"*64,
                         "receipt_key":"fake-must-never-ack"}
                result=settle_delivery(lease,receipt=receipt,connect=self.factory)
                self.assertEqual(result["state"],"lost_lease")
                with self.factory() as c:
                    self.assertEqual(c.execute(
                        """SELECT count(*) AS n FROM qdp_delivery_receipts
                           WHERE event_id=%s::uuid""",
                        (str(lease["event_id"]),)).fetchone()["n"],0)

    def test_fix15_source_revocation_at_ack_rejects_without_explicit_event(self):
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        from intelligence.qualified_data.outbox_delivery import (
            claim_delivery,settle_delivery)
        self._media_video_source()
        with tempfile.TemporaryDirectory() as root:
            file=Path(root)/"rights.mp4"
            file.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00body")
            self._media_asset(file)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":root}):
                created=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
                lease=claim_delivery("group_a",connect=self.factory)
                with self.factory() as c:
                    c.execute("UPDATE sources SET can_analyze_media=false WHERE id=%s",
                              (self.source,))
                receipt={"state":"committed","event_id":str(lease["event_id"]),
                         "consumer":"group_a","payload_sha256":"a"*64,
                         "receipt_key":"fake-must-never-ack"}
                result=settle_delivery(lease,receipt=receipt,connect=self.factory)
                self.assertEqual(result["state"],"dead_letter")
                with self.factory() as c:
                    self.assertEqual(c.execute(
                        """SELECT count(*) AS n FROM qdp_delivery_receipts
                           WHERE event_id=%s::uuid""",
                        (str(lease["event_id"]),)).fetchone()["n"],0)

    def test_fix15_explicit_refresh_forks_immutable_run(self):
        from intelligence.qualified_data.invalidation import invalidate_run,explicit_refresh
        first=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        invalidate_run(self.pid,first["run_id"],origin="operator",
                       reason_code="operator_revoked",video_id=self.vid,
                       provenance_sha256="d"*64,connect=self.factory)
        with self.assertRaises(QDPError):
            explicit_refresh(self.pid,first["run_id"],self.key,connect=self.factory)
        refresh=explicit_refresh(self.pid,first["run_id"],self.key+"-new",
                                 connect=self.factory)
        self.assertNotEqual(refresh["new_run_id"],first["run_id"])
        self.assertFalse(refresh["replayed"])
        duplicate=explicit_refresh(self.pid,first["run_id"],self.key+"-new",
                                   connect=self.factory)
        self.assertTrue(duplicate["replayed"])
        self.assertEqual(duplicate["new_run_id"],refresh["new_run_id"])
        with self.factory() as c:
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_invalidations
                   WHERE run_id=%s::uuid""",(first["run_id"],)).fetchone()["n"],1)
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_invalidations
                   WHERE run_id=%s::uuid""",(refresh["new_run_id"],)).fetchone()["n"],0)

    def test_fix15_revocation_notice_requires_real_receiver_commit(self):
        from intelligence.qualified_data.invalidation import invalidate_run
        from intelligence.qualified_data.revocation_delivery import dispatch_invalidation_once
        self._insert_c_metric_inputs()
        q=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        qdp.work_once(connect=self.factory)
        result=invalidate_run(
            self.pid,q["run_id"],origin="operator",reason_code="operator_revoked",
            provenance_sha256="c"*64,group="C",video_id=self.vid,
            connect=self.factory)
        self.assertEqual(result["new_revocations"],1)
        with self.assertRaises(QDPError):
            dispatch_invalidation_once("group_c",receiver=None,connect=self.factory)
        with self.factory() as conn:
            pending=conn.execute(
                """SELECT d.state FROM qdp_outbox o
                    JOIN qdp_outbox_deliveries d ON d.event_id=o.event_id
                   WHERE o.run_id=%s::uuid AND o.event_type='preparation_invalidated'
                     AND o.analysis_group='C'""",(q["run_id"],)).fetchone()
            self.assertEqual(pending["state"],"pending")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS qdp_test_revocation_receiver (
                   event_id UUID PRIMARY KEY, payload_sha256 TEXT NOT NULL)""")
        factory=self.factory
        class Peer:
            calls=0
            def revoke(self,pointer,payload_sha256,event):
                self.calls+=1
                with factory() as c:
                    with c.transaction():
                        c.execute(
                            """INSERT INTO qdp_test_revocation_receiver
                               (event_id,payload_sha256) VALUES (%s::uuid,%s)
                               ON CONFLICT (event_id) DO NOTHING""",
                            (pointer.event_id,payload_sha256))
                        stored=c.execute(
                            """SELECT payload_sha256 FROM qdp_test_revocation_receiver
                               WHERE event_id=%s::uuid""",
                            (pointer.event_id,)).fetchone()
                        if stored["payload_sha256"]!=payload_sha256:
                            raise ValueError("revocation receiver hash conflict")
                return {"state":"committed","event_id":pointer.event_id,
                        "consumer":"group_c","payload_sha256":payload_sha256,
                        "receipt_key":"revoked:"+pointer.event_id}
        peer=Peer()
        ack=dispatch_invalidation_once("group_c",receiver=peer,
                                        connect=self.factory)
        self.assertEqual(ack["state"],"acknowledged")
        self.assertIsNone(dispatch_invalidation_once("group_c",receiver=peer,
                                                     connect=self.factory))
        self.assertEqual(peer.calls,1)
        with self.factory() as c:
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_delivery_receipts r
                    JOIN qdp_outbox o ON o.event_id=r.event_id
                    WHERE o.run_id=%s::uuid
                      AND o.event_type='preparation_invalidated'""",
                (q["run_id"],)).fetchone()["n"],1)

    def test_fix15_reconcile_human_review_emits_irreversible_group_events(self):
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        from intelligence.qualified_data.invalidation import reconcile_current
        self._media_video_source()
        with tempfile.TemporaryDirectory() as root:
            video=Path(root)/"veto.mp4"
            video.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00body")
            self._media_asset(video)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":root}):
                q=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
                with self.factory() as c:
                    c.execute(
                        """INSERT INTO sre_human_labels
                           (project_id,video_id,taxonomy_version_id,category_key,
                            label,reviewer,reason)
                           VALUES (%s,%s,%s,%s::uuid,'no_match','human','veto')""",
                        (self.pid,self.vid,self.taxonomy,self.category))
                res=reconcile_current(self.pid,q["run_id"],connect=self.factory)
                self.assertEqual(len(res["changes"]),2)
                self.assertTrue(all(v["state"]=="invalidated" for v in res["changes"]))
                repeat=reconcile_current(self.pid,q["run_id"],connect=self.factory)
                self.assertEqual(repeat["changes"],[])
                with self.factory() as c:
                    facts=c.execute(
                        """SELECT origin,reason_code,analysis_group FROM qdp_invalidations
                           WHERE run_id=%s::uuid""",(q["run_id"],)).fetchall()
                    self.assertEqual({x["analysis_group"] for x in facts},{"A","B"})
                    self.assertTrue(all(x["origin"]=="human_review" and
                                        x["reason_code"]=="human_veto" for x in facts))

    def test_fix15_prequeued_run_level_invalidation_prevents_materialization(self):
        from intelligence.qualified_data.invalidation import invalidate_run
        q=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        answer=invalidate_run(self.pid,q["run_id"],origin="operator",
                  reason_code="operator_revoked",provenance_sha256="f"*64,
                  connect=self.factory)
        self.assertEqual(answer["generation"],1)
        work=qdp.work_once(connect=self.factory)
        self.assertEqual(work["state"],"blocked")
        self.assertEqual(work["reason"],"preparation_invalidated")
        with self.factory() as c:
            self.assertEqual(c.execute(
                """SELECT count(*) AS n FROM qdp_preparations
                   WHERE run_id=%s::uuid""",(q["run_id"],)).fetchone()["n"],0)
            detail=qdp.get_run(self.pid,q["run_id"],connect=self.factory)
            self.assertEqual(detail["items"][0]["status"],"blocked")
            self.assertEqual(detail["items"][0]["reason_code"],
                             "preparation_invalidated")

    def test_fix15_human_veto_racing_after_claim_never_positive_ack(self):
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        from intelligence.qualified_data.outbox_delivery import claim_delivery,settle_delivery
        self._media_video_source()
        with tempfile.TemporaryDirectory() as root:
            media=Path(root)/"late-veto.mp4"
            media.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00data")
            self._media_asset(media)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":root}):
                q=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
                lease=claim_delivery("group_a",connect=self.factory)
                with self.factory() as c:
                    c.execute(
                        """INSERT INTO sre_human_labels
                           (project_id,video_id,taxonomy_version_id,category_key,
                            label,reviewer)
                           VALUES (%s,%s,%s,%s::uuid,'no_match','human')""",
                        (self.pid,self.vid,self.taxonomy,self.category))
                bad_receipt={"state":"committed","event_id":str(lease["event_id"]),
                    "consumer":"group_a","payload_sha256":"e"*64,
                    "receipt_key":"must-never-ack"}
                result=settle_delivery(lease,receipt=bad_receipt,connect=self.factory)
                self.assertEqual(result["state"],"dead_letter")
                with self.factory() as c:
                    self.assertEqual(c.execute(
                        """SELECT count(*) AS n FROM qdp_delivery_receipts
                           WHERE event_id=%s::uuid""",(str(lease["event_id"]),)
                        ).fetchone()["n"],0)

    def test_fix16_eligible_picker_verified_live_only_no_uuid_in_ui(self):
        from intelligence.qualified_data.studio_read import eligible_sre_runs
        fresh=eligible_sre_runs(self.pid,limit=1,connect=self.factory)
        # Main now includes real modern M36 -> QDP inbox tables. The LIVE
        # picker must not present a legacy run without a modern READY/ACK
        # event as an eligible modern source. Legacy picker behavior is
        # separately covered by the opt-out/mocked intake tests.
        with self.factory() as c:
            modern=c.execute(
                "SELECT to_regclass('qdp_sre_live_events') AS relation"
            ).fetchone()["relation"] is not None
        if modern:
            self.assertEqual(fresh["runs"],[])
        else:
            self.assertEqual(len(fresh["runs"]),1)
            self.assertEqual(fresh["runs"][0]["id"],self.sre_run)
        self.assertIsNone(fresh["next_cursor"])
        self.assertEqual(fresh["paid_calls"],0)
        with self.factory() as c:
            c.execute(
                "UPDATE sre_classification_runs SET execution_mode='offline_fixture' WHERE id=%s::uuid",
                (self.sre_run,))
        offline=eligible_sre_runs(self.pid,connect=self.factory)
        self.assertEqual(offline["runs"],[])

    def test_fix16_paginated_history_and_video_cursor_project_scoped(self):
        first=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        second=qdp.create_run(self.pid,self.sre_run,self.key+"-next",
                              connect=self.factory)
        self.assertNotEqual(first["run_id"],second["run_id"])
        first_page=qdp.list_runs(self.pid,limit=1,connect=self.factory)
        self.assertEqual(len(first_page["runs"]),1)
        self.assertIsNotNone(first_page["next_cursor"])
        following=qdp.list_runs(self.pid,limit=1,
                                cursor=first_page["next_cursor"],
                                connect=self.factory)
        self.assertEqual(len(following["runs"]),1)
        self.assertNotEqual(following["runs"][0]["id"],first_page["runs"][0]["id"])
        self.assertIsNone(following["next_cursor"])
        first_video=qdp.get_run(self.pid,first["run_id"],limit=1,
                                connect=self.factory)
        self.assertEqual([v["video_id"] for v in first_video["items"]],[self.vid])
        self.assertIsNone(first_video["next_cursor"])
        beyond=qdp.get_run(self.pid,first["run_id"],limit=1,
                           after_video_id=self.vid,connect=self.factory)
        self.assertEqual(beyond["items"],[])
        with self.assertRaises(QDPError):
            qdp.list_runs(self.pid,limit=1,cursor=str(uuid.uuid4()),
                          connect=self.factory)
        with self.assertRaises(QDPError):
            qdp.get_run(self.pid,first["run_id"],after_video_id=0,
                        connect=self.factory)

    def test_fix16_read_only_manifest_events_and_diagnostics(self):
        from intelligence.qualified_data.studio_read import (
            diagnostics,events,manifest_summary,lineage)
        q=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        qdp.work_once(connect=self.factory)
        summary=diagnostics(self.pid,q["run_id"],connect=self.factory)
        self.assertFalse(summary["consumer_ack_is_analysis_complete"])
        self.assertEqual({g["analysis_group"] for g in summary["groups"]},
                         {"A","B","C"})
        self.assertEqual(summary["invalidation_count"],0)
        manifest=manifest_summary(self.pid,q["run_id"],connect=self.factory)
        self.assertEqual(manifest["manifest"]["expected_videos"],1)
        self.assertEqual(len(manifest["manifest"]["manifest_sha256"]),64)
        page=events(self.pid,q["run_id"],limit=1,connect=self.factory)
        self.assertEqual(len(page["events"]),1)
        self.assertIsNotNone(page["next_cursor"])
        nxt=events(self.pid,q["run_id"],limit=1,cursor=page["next_cursor"],
                   connect=self.factory)
        self.assertNotEqual(nxt["events"][0]["event_id"],
                            page["events"][0]["event_id"])
        scoped=lineage(self.pid,q["run_id"],self.vid,connect=self.factory)
        self.assertFalse(scoped["current"])
        self.assertEqual(scoped["lineage"],[])
        self.assertIsNone(scoped["source"])

    def test_fix16_lineage_hides_revoked_media_and_remains_project_scoped(self):
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        from intelligence.qualified_data.studio_read import lineage,events
        self._media_video_source()
        with tempfile.TemporaryDirectory() as root:
            file=Path(root)/"read-side.mp4"
            file.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00audio")
            media=self._media_asset(file)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":root}):
                run=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
                allowed=lineage(self.pid,run["run_id"],self.vid,connect=self.factory)
                self.assertTrue(allowed["current"])
                self.assertEqual(allowed["source"]["media_asset_id"],media)
                self.assertEqual(allowed["source"]["metric_snapshot_ids"],[])
                self.assertEqual(allowed["current_groups"],["A","B"])
                with self.factory() as conn:
                    conn.execute(
                        "UPDATE sources SET can_analyze_media=false WHERE id=%s",
                        (self.source,))
                denied=lineage(self.pid,run["run_id"],self.vid,connect=self.factory)
                self.assertFalse(denied["current"])
                self.assertEqual(denied["lineage"],[])
                self.assertIsNone(denied["source"])
                with self.assertRaises(QDPError):
                    lineage(uuid.uuid4().hex,run["run_id"],self.vid,connect=self.factory)
                with self.assertRaises(QDPError):
                    events(self.pid,run["run_id"],cursor=str(uuid.uuid4()),
                           connect=self.factory)

    def test_fix16_c_only_group_does_not_expose_unlicensed_media(self):
        from intelligence.qualified_data.studio_read import lineage
        self._insert_c_metric_inputs()
        q=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        qdp.work_once(connect=self.factory)
        result=lineage(self.pid,q["run_id"],self.vid,connect=self.factory)
        self.assertTrue(result["current"])
        self.assertEqual(result["current_groups"],["C"])
        self.assertIsNone(result["source"]["media_asset_id"])
        self.assertEqual(len(result["source"]["metric_snapshot_ids"]),2)

    def test_fix17_observability_precise_group_counts_and_no_fake_analysis(self):
        from intelligence.qualified_data.observability import snapshot
        self._media_video_source()
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            media=Path(folder)/"fixture.mp4"
            media.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00fixture")
            self._media_asset(media)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":folder}):
                q=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
                stats=snapshot(self.pid,run_id=q["run_id"],limit=10,
                               connect=self.factory)
                self.assertEqual(stats["runs"]["qualified_snapshots"],1)
                self.assertEqual(stats["runs"]["runs"],1)
                self.assertEqual(stats["analysis_completed"],None)
                self.assertEqual(stats["receipt_meaning"],"durable_consumer_inbox_only")
                per={x["analysis_group"]:x for x in stats["readiness_by_group"]}
                self.assertEqual((per["A"]["currently_ready"],
                                  per["B"]["currently_ready"]),(1,1))
                self.assertNotIn("C",per)
                self.assertEqual(len(stats["oldest_unresolved"]),2)
                self.assertTrue(all(v["event_type"]=="preparation_ready"
                                    for v in stats["oldest_unresolved"]))
                self.assertTrue(all(v["state"]=="pending"
                                    for v in stats["oldest_unresolved"]))
                with self.factory() as conn:
                    conn.execute("UPDATE sources SET can_analyze_media=false WHERE id=%s",
                                 (self.source,))
                after=snapshot(self.pid,run_id=q["run_id"],connect=self.factory)
                self.assertEqual(sum(x["currently_ready"]
                                    for x in after["readiness_by_group"]),0)
                self.assertEqual(sum(x["historical_ready"]
                                    for x in after["readiness_by_group"]),2)
                self.assertIsNone(after["analysis_completed"])

    def test_fix17_observability_is_project_scoped_and_bounded(self):
        from intelligence.qualified_data.observability import snapshot
        q=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        real=snapshot(self.pid,connect=self.factory,limit=1)
        self.assertEqual(real["runs"]["runs"],1)
        self.assertEqual(real["oldest_limit"],1)
        other=uuid.uuid4().hex
        masked=snapshot(other,connect=self.factory)
        self.assertEqual(masked["runs"]["runs"],0)
        with self.assertRaises(QDPError):
            snapshot(other,run_id=q["run_id"],connect=self.factory)
        with self.assertRaises(QDPError):
            snapshot(self.pid,run_id=q["run_id"],limit=51,connect=self.factory)

    def test_fix17_retention_limit_and_lifecycle_are_bounded(self):
        self._media_video_source()
        import tempfile,os
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"expiry.mp4"
            p.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00fixture")
            self._media_asset(p)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":folder}):
                q=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                qdp.work_once(connect=self.factory)
            with self.factory() as c:
                c.execute("UPDATE sources SET can_store_metadata=false WHERE id=%s",
                          (self.source,))
            self.assertEqual(qdp.purge_revoked(connect=self.factory,limit=1),1)
            self.assertEqual(qdp.purge_revoked(connect=self.factory,limit=1),0)
            with self.factory() as c:
                self.assertEqual(c.execute(
                    "SELECT count(*) AS n FROM qdp_enrichments WHERE run_id=%s::uuid",
                    (q["run_id"],)).fetchone()["n"],0)
        with self.assertRaises(QDPError):
            qdp.purge_revoked(connect=self.factory,limit=1001)

    def test_cross_project_run_is_not_readable(self):
        result=qdp.create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        with self.assertRaises(QDPError) as ctx:
            qdp.get_run("f"*32,result["run_id"],connect=self.factory)
        self.assertEqual(ctx.exception.status,404)


if __name__ == "__main__":
    unittest.main()
