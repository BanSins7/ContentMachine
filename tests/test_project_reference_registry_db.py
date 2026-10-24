"""PostgreSQL 17 integration tests; no remote site or LLM called."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

DSN=os.environ.get('CM_TEST_PG_DSN')

@unittest.skipUnless(DSN, 'Needs ephemeral CI PostgreSQL 17')
class TestProjectReferencesDB(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.c=psycopg.connect(DSN)
        self.addCleanup(self.c.close)
        self.addCleanup(self.c.rollback)
        self.a,self.b=uuid4().hex,uuid4().hex
        for pid in (self.a,self.b):
            self.c.execute("""INSERT INTO project_registry
            (id,name,profile_revision,profile_hash,created_at,updated_at)
            VALUES (%s,'test',1,%s,now(),now())""",(pid,'a'*64))
            self.c.execute("""INSERT INTO project_profile_versions
            (project_id,revision,schema_version,profile_hash,profile)
            VALUES (%s,1,1,%s,'{}'::jsonb)""",(pid,'a'*64))
        from intelligence.project_sources import repository as repo
        self.repo=repo
        self.project={"profile_revision":1,"product_url":"https://product.example.com",
                     "video_references":"https://youtu.be/AbCdEf12345 — sample",
                     "competitor_socials":"https://instagram.com/testbrand/"}
        self.p=patch.object(repo,'_project',side_effect=lambda pid:{**self.project,'id':pid})
        # Each service method owns/commits/closes its own fresh psycopg connection.
        # Never hand out the fixture connection (closing it invalidates other tests).
        self.conn=patch.object(repo,'_connection',side_effect=lambda:psycopg.connect(DSN))
        self.p.start();self.addCleanup(self.p.stop)
        self.conn.start();self.addCleanup(self.conn.stop)
        # Commit fixtures for second connections to see them. Cleanup explicit at tearDown.
        self.c.commit()

    def test_manual_create_deduplicate_scope_and_archive(self):
        r=self.repo
        first=r.create_source(self.a,{"source_type":"user_video_reference","url":"https://youtu.be/AbCdEf12345"})['source']
        self.assertEqual(first['status'],'registered')
        self.assertIsNotNone(first['active_version_id'])
        self.assertEqual(first['ingestion_mode'],'video_reference')
        with self.assertRaises(r.RegistryProblem) as e:
            r.create_source(self.a,{"source_type":"user_video_reference","url":"https://youtube.com/shorts/AbCdEf12345"})
        self.assertEqual(e.exception.http_status,409)
        other=r.create_source(self.b,{"source_type":"user_video_reference","url":"https://youtube.com/shorts/AbCdEf12345"})['source']
        self.assertNotEqual(first['source_id'],other['source_id'])
        with self.assertRaises(r.RegistryProblem) as e:
            r.archive_source(self.b,first['source_id'],first['updated_at'])
        self.assertEqual(e.exception.http_status,404)
        with self.assertRaises(r.RegistryProblem) as e:
            r.update_source(self.a,first['source_id'],{"label":"x","expected_updated_at":"2000-01-01T00:00:00+00:00"})
        self.assertEqual(e.exception.http_status,409)
        changed=r.update_source(self.a,first['source_id'],{"label":"Demo","expected_updated_at":first['updated_at']})['source']
        self.assertEqual(changed['label'],'Demo')
        archived=r.archive_source(self.a,first['source_id'],changed['updated_at'])['source']
        self.assertIsNotNone(archived['tombstoned_at'])
        self.assertEqual(r.list_sources(self.a)['count'],0)
        self.assertEqual(r.list_sources(self.a,include_archived=True)['count'],1)
        version_row=self.c.execute("SELECT count(*) FROM project_source_versions WHERE source_id=%s",
                                   (first['source_id'],)).fetchone()
        self.assertEqual(version_row[0],1)

    def test_legacy_import_idempotent_provenance(self):
        r=self.repo
        res=r.import_legacy(self.a)
        self.assertEqual(res['created'],3)
        self.assertEqual(res['origins_added'],3)
        self.assertEqual(r.import_legacy(self.a)['created'],0)
        rows=r.list_sources(self.a)['sources']
        self.assertEqual(len(rows),3)
        self.assertEqual(sum(len(v['origins']) for v in rows),3)
        self.assertTrue(any(v['original_input'].endswith('sample') for v in rows))
        self.assertEqual(r.import_legacy(self.b,dry_run=True)['proposed'],3)
        self.assertEqual(r.list_sources(self.b)['count'],0)

    def test_legacy_reconciliation_is_non_destructive(self):
        r=self.repo
        self.assertEqual(r.import_legacy(self.a)['created'],3)
        self.project['product_url']=''
        output=r.import_legacy(self.a)
        self.assertEqual(len(output['stale_candidates']),1)
        self.assertEqual(output['stale_candidates'][0]['origin_field'],'product_url')
        self.assertEqual(r.list_sources(self.a)['count'],3)

    def test_db_constraints_identity_and_origin_immutability(self):
        import psycopg
        s=self.repo.create_source(self.a,{"url":"https://example.com","source_type":"product_website"})['source']
        # Use a separate transaction and restore it after expected SQL errors.
        with psycopg.connect(DSN) as c:
            def rejected(sql,args):
                with self.assertRaises(psycopg.Error):
                    with c.transaction():c.execute(sql,args)
            rejected('UPDATE project_sources SET identity_key=%s WHERE id=%s',('tampered',s['source_id']))
            rejected('UPDATE project_sources SET project_id=%s WHERE id=%s',(self.b,s['source_id']))
            rejected("UPDATE project_source_versions SET version_no=5 WHERE source_id=%s",(s['source_id'],))

if __name__=='__main__':unittest.main()
