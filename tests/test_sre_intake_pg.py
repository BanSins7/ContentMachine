"""S3 real PG17 D1 intake tests. Strictly disposable CM_TEST_PG_DSN."""
from __future__ import annotations

import os
import unittest
from datetime import timedelta
from uuid import uuid4
from json import dumps

from intelligence import migrate
from intelligence.relevance.intake import attach_discovery, read_content
from intelligence.relevance.v1_contracts import SREContractError


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "disposable PostgreSQL required")
class S3DiscoveryIntakePG(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.dsn = os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            migrate.apply_migrations(conn)

    def setUp(self):
        import psycopg
        self.conn = psycopg.connect(self.dsn)
        self.project = uuid4().hex
        self.other = uuid4().hex
        self.source = "sre_fixture_" + uuid4().hex
        self.video_identity = uuid4().hex[:11]
        for project in (self.project,self.other):
            self.conn.execute(
                """INSERT INTO project_registry
                   (id,name,profile_revision,profile_hash,created_at,updated_at)
                   VALUES (%s,'SRE fixture',1,%s,now(),now())""",
                (project,"b"*64),
            )
            self.conn.execute("INSERT INTO sre_workspaces(project_id) VALUES (%s)",(project,))
        self.conn.execute(
            """INSERT INTO sources (id,provider,source_type,status,
               can_store_metadata,can_analyze_media,retention_days)
               VALUES (%s,'sre_fake','manual','enabled',TRUE,FALSE,30)""",
            (self.source,),
        )
        self.policy = self.conn.execute(
            """INSERT INTO source_policy_versions
               (source_id,version,is_active,permissions,retention_policy,reviewed_at)
               VALUES (%s,1,TRUE,%s::jsonb,
                 '{"retention_days":14}'::jsonb,now()) RETURNING id""",
            (self.source,dumps({"can_store_metadata":True,
                "can_semantic_classify":True,"can_external_llm":False})),
        ).fetchone()[0]
        self.video = self.conn.execute(
            """INSERT INTO videos(platform,platform_video_id,canonical_url,title)
               VALUES ('youtube',%s,%s,'Global title must never be copied')
               RETURNING id""",
            (self.video_identity,"https://www.youtube.com/watch?v="+self.video_identity),
        ).fetchone()[0]
        self.discovery = self.conn.execute(
            """INSERT INTO discoveries(video_id,source_id,method)
               VALUES (%s,%s,'manual_import') RETURNING id""",
            (self.video,self.source),
        ).fetchone()[0]
        self.observation = self.conn.execute(
            """INSERT INTO source_video_metadata
               (video_id,source_id,source_policy_version_id,fetched_at,
                title,description,is_current,expires_at)
               VALUES (%s,%s,%s,now(),'Approved title','Approved description',
                       TRUE,now()+interval '5 days') RETURNING id""",
            (self.video,self.source,self.policy),
        ).fetchone()[0]

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_explicit_attach_creates_one_reusable_revision_and_real_lineage(self):
        one=attach_discovery(self.conn,self.project,self.discovery)
        self.assertFalse(one["replayed"])
        self.assertEqual(one["revision"],1)
        self.assertEqual(one["video_id"],self.video)
        self.assertEqual(one["data"]["title"],"Approved title")
        self.assertEqual(one["data"]["description"],"Approved description")
        self.assertEqual(one["data"]["hashtags"],[])
        self.assertEqual(one["provenance"]["discovery_id"],self.discovery)
        self.assertFalse(one["rights"]["can_external_llm"])
        two=attach_discovery(self.conn,self.project,self.discovery)
        self.assertEqual(two["content_version_id"],one["content_version_id"])
        self.assertTrue(two["replayed"])
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM sre_content_versions
               WHERE project_id=%s AND video_id=%s""",
            (self.project,self.video)).fetchone()[0],1)
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM sre_project_discoveries
               WHERE project_id=%s AND discovery_id=%s""",
            (self.project,self.discovery)).fetchone()[0],1)
        with self.assertRaises(SREContractError):
            read_content(self.conn,self.other,one["content_version_id"])
        with self.assertRaises(SREContractError):
            read_content(self.conn,self.project,one["content_version_id"],external_llm=True)

    def test_revocation_blocks_existing_views_reads_and_reintake(self):
        snap=attach_discovery(self.conn,self.project,self.discovery)
        self.conn.execute("UPDATE source_policy_versions SET is_active=FALSE WHERE id=%s",
                          (self.policy,))
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_readable_content_versions WHERE id=%s",
            (snap["content_version_id"],)).fetchone()[0],0)
        with self.assertRaises(SREContractError):
            read_content(self.conn,self.project,snap["content_version_id"])
        with self.assertRaises(SREContractError):
            attach_discovery(self.conn,self.project,self.discovery)

    def test_revocation_source_and_metadata_deletion(self):
        snap=attach_discovery(self.conn,self.project,self.discovery)
        self.conn.execute(
            "UPDATE source_video_metadata SET deleted_at=now(),is_current=FALSE WHERE id=%s",
            (self.observation,),
        )
        with self.assertRaises(SREContractError):
            read_content(self.conn,self.project,snap["content_version_id"])
        with self.assertRaises(SREContractError):
            attach_discovery(self.conn,self.project,self.discovery)
        self.conn.execute("UPDATE sources SET status='disabled' WHERE id=%s",(self.source,))
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_readable_content_versions WHERE id=%s",
            (snap["content_version_id"],)).fetchone()[0],0)

    def test_source_permissions_missing_do_not_allow_auto_attach(self):
        self.conn.execute(
            """UPDATE source_policy_versions
               SET permissions='{"can_store_metadata":true}'::jsonb
               WHERE id=%s""", (self.policy,),
        )
        with self.assertRaises(SREContractError):
            attach_discovery(self.conn,self.project,self.discovery)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_project_discoveries WHERE project_id=%s",
            (self.project,)).fetchone()[0],0)

    def test_observation_change_yields_new_revision_without_rewriting_old(self):
        first=attach_discovery(self.conn,self.project,self.discovery)
        self.conn.execute(
            "UPDATE source_video_metadata SET title='Revised title' WHERE id=%s",
            (self.observation,),
        )
        second=attach_discovery(self.conn,self.project,self.discovery)
        self.assertFalse(second["replayed"])
        self.assertEqual(second["revision"],2)
        self.assertNotEqual(first["content_sha256"],second["content_sha256"])
        self.assertEqual(read_content(self.conn,self.project,first["content_version_id"])
                         ["data"]["title"],"Approved title")
        self.assertEqual(read_content(self.conn,self.project,second["content_version_id"])
                         ["data"]["title"],"Revised title")

    def test_empty_source_observation_never_uses_unrelated_global_title(self):
        self.conn.execute("DELETE FROM source_video_metadata WHERE id=%s",(self.observation,))
        result=attach_discovery(self.conn,self.project,self.discovery)
        self.assertIsNone(result["data"]["title"])
        self.assertIsNone(result["data"]["description"])
        self.assertEqual(result["provenance"]["source_request_id"],None)
        self.assertEqual(result["completeness"],"metadata_only")

    def test_direct_sql_content_insert_without_explicit_permission_is_blocked(self):
        self.conn.execute("INSERT INTO sre_project_videos(project_id,video_id) VALUES(%s,%s)",
                          (self.project,self.video))
        self.conn.execute(
            """UPDATE source_policy_versions SET permissions='{}'::jsonb
               WHERE id=%s""",(self.policy,),
        )
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    """INSERT INTO sre_content_versions
                       (project_id,video_id,revision,source_id,
                        source_policy_version_id,content)
                       VALUES(%s,%s,1,%s,%s,'{"title":"Bypass"}'::jsonb)""",
                    (self.project,self.video,self.source,self.policy),
                )

    def test_raw_sql_string_true_cannot_masquerade_as_reviewed_json_boolean(self):
        self.conn.execute(
            "INSERT INTO sre_project_videos(project_id,video_id) VALUES(%s,%s)",
            (self.project,self.video),
        )
        self.conn.execute(
            """UPDATE source_policy_versions
               SET permissions='{"can_store_metadata":"true",
                                 "can_semantic_classify":"true"}'::jsonb
               WHERE id=%s""", (self.policy,),
        )
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    """INSERT INTO sre_content_versions
                       (project_id,video_id,revision,source_id,
                        source_policy_version_id,content)
                       VALUES(%s,%s,1,%s,%s,'{"title":"No permission"}'::jsonb)""",
                    (self.project,self.video,self.source,self.policy),
                )

    def test_wrong_provider_request_lineage_is_rejected(self):
        run = self.conn.execute(
            """INSERT INTO search_runs(idempotency_key,config,planner_version)
               VALUES(%s,'{}'::jsonb,'sre-test') RETURNING id""",
            ("sre-"+uuid4().hex,),
        ).fetchone()[0]
        request = self.conn.execute(
            """INSERT INTO source_requests(run_id,source_id,logical_request_key)
               VALUES (%s,%s,%s) RETURNING id""",
            (run,self.source,"sre-"+uuid4().hex),
        ).fetchone()[0]
        # source_request_id belongs to run, but discovery.run_id omitted
        self.conn.execute(
            "UPDATE discoveries SET source_request_id=%s WHERE id=%s",
            (request,self.discovery),
        )
        with self.assertRaises(SREContractError):
            attach_discovery(self.conn,self.project,self.discovery)

    def test_reviewed_external_policy_only_allows_read_not_paid_call(self):
        snap=attach_discovery(self.conn,self.project,self.discovery)
        self.conn.execute(
            """UPDATE source_policy_versions
               SET permissions=jsonb_set(permissions,'{can_external_llm}','true'::jsonb)
               WHERE id=%s""", (self.policy,),
        )
        authorized=read_content(self.conn,self.project,snap["content_version_id"],
                                external_llm=True)
        self.assertTrue(authorized["rights"]["can_external_llm"])
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_classification_runs WHERE project_id=%s",
            (self.project,)).fetchone()[0],0)


if __name__ == "__main__":
    unittest.main()
