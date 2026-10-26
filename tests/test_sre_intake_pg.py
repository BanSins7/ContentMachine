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
        from intelligence.relevance.ports.discovery import read_verified_discovery
        pinned = read_verified_discovery(self.conn, self.project, one['content_version_id'])
        self.assertEqual(pinned['discovery_id'], self.discovery)
        self.assertEqual(pinned['video_id'], self.video)
        self.assertEqual(pinned['content_sha256'], one['content_sha256'])
        with self.assertRaises(SREContractError):
            read_verified_discovery(self.conn, self.other, one['content_version_id'])
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

    def test_s3_revision_is_append_only_and_discovery_unlink_revokes_read(self):
        snap=attach_discovery(self.conn,self.project,self.discovery)
        content_id=snap["content_version_id"]
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    """UPDATE sre_content_versions
                       SET content='{"title":"Tampered"}'::jsonb
                       WHERE id=%s""", (content_id,),
                )
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    "UPDATE sre_content_versions SET expires_at=NULL WHERE id=%s",
                    (content_id,),
                )
        # The project association is a live proof of the selected discovery:
        # unlink must hide its S3 content, even while source policy stays active.
        self.conn.execute(
            """DELETE FROM sre_project_discoveries
               WHERE project_id=%s AND discovery_id=%s""",
            (self.project,self.discovery),
        )
        with self.assertRaises(SREContractError):
            read_content(self.conn,self.project,content_id)

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


    def test_m07_real_d1_direct_video_provenance_replay_and_tenant(self):
        from intelligence.relevance.ports.d1_provenance import (
            read_d1_provenance, validate_d1_provenance,
        )
        first=attach_discovery(self.conn,self.project,self.discovery)
        second=attach_discovery(self.conn,self.project,self.discovery)
        self.assertEqual(first["content_version_id"],second["content_version_id"])
        pinned=read_d1_provenance(self.conn,self.project,first["content_version_id"])
        self.assertEqual(pinned,validate_d1_provenance(pinned))
        self.assertEqual(pinned["project_id"],self.project)
        self.assertEqual(pinned["video_id"],self.video)
        self.assertEqual(pinned["discovery_id"],self.discovery)
        self.assertEqual(pinned["source_id"],self.source)
        self.assertEqual(pinned["source_policy_version_id"],self.policy)
        self.assertEqual(pinned["metadata_observation_id"],self.observation)
        self.assertEqual(pinned["search_query_id"],None)
        self.assertEqual(pinned["source_request_id"],None)
        self.assertEqual(pinned["search_run_id"],None)
        self.assertEqual(pinned["platform"],"youtube")
        self.assertEqual(pinned["platform_video_id"],self.video_identity)
        self.assertEqual(pinned["content_sha256"],first["content_sha256"])
        self.assertEqual(pinned,read_d1_provenance(
            self.conn,self.project,first["content_version_id"]))
        with self.assertRaises(SREContractError):
            read_d1_provenance(self.conn,self.other,first["content_version_id"])
        # Removing explicit project ownership immediately makes D1 inaccessible.
        self.conn.execute("""DELETE FROM sre_project_discoveries
            WHERE project_id=%s AND discovery_id=%s""",
            (self.project,self.discovery))
        with self.assertRaises(SREContractError):
            read_d1_provenance(self.conn,self.project,first["content_version_id"])

    def test_m07_real_d1_request_query_run_provenance_mismatch_denied(self):
        from intelligence.relevance.ports.d1_provenance import read_d1_provenance
        run=self.conn.execute(
            """INSERT INTO search_runs(idempotency_key,config,planner_version)
               VALUES(%s,'{}'::jsonb,'sre-m07') RETURNING id""",
            ("sre-m07-"+uuid4().hex,),
        ).fetchone()[0]
        query=self.conn.execute(
            """INSERT INTO search_queries(source_id,query_text,method)
               VALUES(%s,'m07 approved query','manual') RETURNING id""",
            (self.source,),
        ).fetchone()[0]
        request=self.conn.execute(
            """INSERT INTO source_requests(run_id,source_id,query_id,logical_request_key)
               VALUES (%s,%s,%s,%s) RETURNING id""",
            (run,self.source,query,"sre-m07-"+uuid4().hex),
        ).fetchone()[0]
        self.conn.execute(
            """UPDATE discoveries
               SET run_id=%s,search_query_id=%s,source_request_id=%s
               WHERE id=%s""",
            (run,query,request,self.discovery),
        )
        item=attach_discovery(self.conn,self.project,self.discovery)
        proof=read_d1_provenance(self.conn,self.project,item["content_version_id"])
        self.assertEqual(proof["search_run_id"],run)
        self.assertEqual(proof["search_query_id"],query)
        self.assertEqual(proof["source_request_id"],request)
        self.assertEqual(proof["metadata_observation_id"],self.observation)
        self.assertEqual(proof["video_id"],self.video)
        # A valid stored S3 revision is not enough if D1 changes its lineage.
        self.conn.execute(
            "UPDATE discoveries SET run_id=NULL WHERE id=%s",(self.discovery,))
        with self.assertRaises(SREContractError):
            read_d1_provenance(self.conn,self.project,item["content_version_id"])
        self.conn.execute(
            "UPDATE discoveries SET run_id=%s WHERE id=%s",(run,self.discovery))
        self.conn.execute(
            "UPDATE discoveries SET source_request_id=NULL WHERE id=%s",
            (self.discovery,))
        with self.assertRaises(SREContractError):
            read_d1_provenance(self.conn,self.project,item["content_version_id"])
        self.conn.execute(
            "UPDATE discoveries SET source_request_id=%s WHERE id=%s",
            (request,self.discovery))
        self.conn.execute(
            "UPDATE discoveries SET search_query_id=NULL WHERE id=%s",
            (self.discovery,))
        with self.assertRaises(SREContractError):
            read_d1_provenance(self.conn,self.project,item["content_version_id"])
        self.conn.execute(
            "UPDATE discoveries SET search_query_id=%s WHERE id=%s",
            (query,self.discovery))
        self.assertEqual(read_d1_provenance(
            self.conn,self.project,item["content_version_id"])["search_query_id"],query)
        # Same source-specific content is denied after policy revocation.
        self.conn.execute(
            "UPDATE source_policy_versions SET is_active=FALSE WHERE id=%s",
            (self.policy,))
        with self.assertRaises(SREContractError):
            read_d1_provenance(self.conn,self.project,item["content_version_id"])

    def test_m07_d1_query_from_foreign_source_is_denied(self):
        from intelligence.relevance.ports.d1_provenance import read_d1_provenance
        different="m07_foreign_"+uuid4().hex
        self.conn.execute(
            """INSERT INTO sources(id,provider,source_type,status,
                can_store_metadata,can_analyze_media)
               VALUES(%s,'sre_fake','manual','enabled',TRUE,FALSE)""",
            (different,))
        query=self.conn.execute(
            """INSERT INTO search_queries(source_id,query_text,method)
               VALUES(%s,'foreign search query','manual') RETURNING id""",
            (different,),
        ).fetchone()[0]
        self.conn.execute("UPDATE discoveries SET search_query_id=%s WHERE id=%s",
                          (query,self.discovery))
        snap=attach_discovery(self.conn,self.project,self.discovery)
        with self.assertRaises(SREContractError):
            read_d1_provenance(self.conn,self.project,snap["content_version_id"])


    def test_m08_live_gate_before_send_and_publish_provenance(self):
        from intelligence.relevance.live_content_gate import read_live_content_gate
        version=attach_discovery(self.conn,self.project,self.discovery)
        cid=version["content_version_id"]
        pins={"video_id":self.video,"content_version_id":cid,
              "content_sha256":version["content_sha256"],
              "source_id":self.source,"source_policy_version_id":self.policy,
              "discovery_id":self.discovery,"metadata_observation_id":self.observation,
              "completeness":"metadata_only"}
        verified=read_live_content_gate(
            self.conn,self.project,cid,phase="planning",pins=pins,
        )
        self.assertEqual(verified["content"]["data"]["title"],"Approved title")
        self.assertEqual(verified["content"]["data"]["transcript"],None)
        self.assertEqual(verified["content"]["data"]["scenes"],None)
        self.assertEqual(verified["provenance"]["metadata_observation_id"],self.observation)
        self.assertEqual(verified["content_sha256"],version["content_sha256"])
        self.assertEqual(len(verified["version_pin_sha256"]),64)
        with self.assertRaises(SREContractError) as denied:
            read_live_content_gate(
                self.conn,self.project,cid,phase="before_external_send",
                pins=pins,external_llm=False,
            )
        self.assertEqual(denied.exception.code,"rights_denied")
        self.conn.execute(
            """UPDATE source_policy_versions SET
               permissions=jsonb_set(permissions,'{can_external_llm}','true'::jsonb)
               WHERE id=%s""", (self.policy,),
        )
        sent=read_live_content_gate(
            self.conn,self.project,cid,phase="before_external_send",pins=pins,
        )
        self.assertTrue(sent["content"]["rights"]["can_external_llm"])
        self.assertEqual(sent["content"]["data"]["title"],"Approved title")
        self.assertEqual(read_live_content_gate(
            self.conn,self.project,cid,phase="before_publish",pins=pins,
        )["content"]["content_version_id"],cid)
        with self.assertRaises(SREContractError):
            read_live_content_gate(
                self.conn,self.other,cid,phase="result_read",pins=pins,
            )
        wrong=dict(pins,content_sha256="f"*64)
        with self.assertRaises(SREContractError) as mismatch:
            read_live_content_gate(
                self.conn,self.project,cid,phase="result_read",pins=wrong,
            )
        self.assertEqual(mismatch.exception.code,"snapshot_mismatch")

    def test_m08_late_revocation_and_observation_specific_expiry(self):
        from intelligence.relevance.live_content_gate import read_live_content_gate
        snap=attach_discovery(self.conn,self.project,self.discovery)
        cid=snap["content_version_id"]
        self.assertEqual(read_live_content_gate(
            self.conn,self.project,cid,phase="before_publish",
        )["content"]["video_id"],self.video)
        # Source-observation has its own expiry, independent of S3 version SHA.
        self.conn.execute(
            """UPDATE source_video_metadata SET
               fetched_at=now()-interval '1 day',
               expires_at=now()-interval '1 second' WHERE id=%s""",
            (self.observation,),
        )
        with self.assertRaises(SREContractError):
            read_live_content_gate(self.conn,self.project,cid,phase="before_publish")
        # Restore the observation and separately revoke the source policy.
        self.conn.execute(
            """UPDATE source_video_metadata
               SET expires_at=now()+interval '5 days' WHERE id=%s""",
            (self.observation,),
        )
        self.assertEqual(read_live_content_gate(
            self.conn,self.project,cid,phase="result_read",
        )["content"]["video_id"],self.video)
        self.conn.execute(
            "UPDATE source_policy_versions SET is_active=FALSE WHERE id=%s",
            (self.policy,),
        )
        with self.assertRaises(SREContractError) as denied:
            read_live_content_gate(self.conn,self.project,cid,phase="before_publish")
        self.assertEqual(denied.exception.code,"rights_denied")


if __name__ == "__main__":
    unittest.main()
