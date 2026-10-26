"""Stage 01 PostgreSQL contract tests; use the isolated CI database only."""
import os
import unittest
from uuid import uuid4

DSN = os.environ.get('CM_TEST_PG_DSN')


@unittest.skipUnless(DSN, 'CM_TEST_PG_DSN not set (integration database unavailable)')
class ProjectSourcesDatabase(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.conn = psycopg.connect(DSN)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.projects = [uuid4().hex, uuid4().hex]
        self.sha = 'a' * 64
        for project in self.projects:
            self.conn.execute("""INSERT INTO project_registry
                (id,name,profile_revision,profile_hash,created_at,updated_at)
                VALUES (%s,'test',1,%s,now(),now())""", (project, self.sha))
            self.conn.execute("""INSERT INTO project_profile_versions
                (project_id,revision,schema_version,profile_hash,profile)
                VALUES (%s,1,1,%s,'{}'::jsonb)""", (project, self.sha))

    def fixture(self, project):
        source, version = uuid4(), uuid4()
        self.conn.execute("""INSERT INTO project_sources(id,project_id,source_type,original_input)
                           VALUES (%s,%s,'research_document','fixture')""", (source, project))
        self.conn.execute("""INSERT INTO project_source_versions
            (id,project_id,source_id,version_no,content_sha256,byte_size)
            VALUES (%s,%s,%s,1,%s,4)""", (version, project, source, self.sha))
        return source, version

    def rejected(self, sql, args):
        import psycopg
        with self.assertRaises(psycopg.Error):
            with self.conn.transaction():
                self.conn.execute(sql, args)

    def test_permissions_default_deny_and_pinned_context(self):
        project = self.projects[0]
        source, version = self.fixture(project)
        rights = self.conn.execute("""SELECT can_fetch,can_store,can_analyze,can_send_to_llm
             FROM project_sources WHERE id=%s""", (source,)).fetchone()
        self.assertEqual(rights, (False, False, False, False))
        self.conn.execute("UPDATE project_sources SET active_version_id=%s WHERE id=%s", (version,source))
        evidence = uuid4()
        self.conn.execute("""INSERT INTO project_evidence
             (id,project_id,source_id,source_version_id,evidence_kind,
              exact_text_or_observation,locator,extraction_engine,extraction_version)
             VALUES (%s,%s,%s,%s,'user_claim','A fact','{"kind":"text","start_char":0,"end_char":6}'::jsonb,'fixture','1')""",
             (evidence,project,source,version))
        ctx = uuid4()
        self.conn.execute("""INSERT INTO project_context_snapshots
             (id,project_id,context_version,context_sha256,profile_revision,profile_hash,context_payload,source_manifest)
             VALUES (%s,%s,1,%s,1,%s,'{}'::jsonb,'[]'::jsonb)""",
             (ctx,project,self.sha,self.sha))
        self.conn.execute("""INSERT INTO project_context_source_pins
             (project_id,context_id,source_id,source_version_id) VALUES (%s,%s,%s,%s)""",
             (project,ctx,source,version))
        self.conn.execute("""INSERT INTO project_context_evidence_pins
             (project_id,context_id,evidence_id) VALUES (%s,%s,%s)""",(project,ctx,evidence))
        self.conn.execute("""INSERT INTO project_context_approvals(project_id,context_id,decision,approved_by)
            VALUES (%s,%s,'approved','owner')""",(project,ctx))
        self.rejected("UPDATE project_source_versions SET version_no=2 WHERE id=%s",(version,))
        self.rejected("DELETE FROM project_evidence WHERE id=%s",(evidence,))
        self.rejected("UPDATE project_context_snapshots SET context_sha256=%s WHERE id=%s",('b'*64,ctx))
        self.rejected("DELETE FROM project_context_approvals WHERE context_id=%s",(ctx,))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM project_context_source_pins WHERE context_id=%s",(ctx,)).fetchone()[0], 1)

    def test_cross_project_fks_and_versions_are_isolated(self):
        a,b = self.projects
        source_a, version_a = self.fixture(a)
        source_b, version_b = self.fixture(b)
        self.rejected("""INSERT INTO project_source_versions
            (project_id,source_id,version_no) VALUES (%s,%s,2)""", (b,source_a))
        self.rejected("UPDATE project_sources SET active_version_id=%s WHERE id=%s", (version_b,source_a))
        self.rejected("""INSERT INTO project_media_assets
            (project_id,source_id,source_version_id,asset_kind,content_sha256,byte_size,media_type,storage_ref)
            VALUES (%s,%s,%s,'document',%s,4,'text/plain','opaque')""", (b,source_a,version_a,self.sha))
        self.rejected("""INSERT INTO project_context_snapshots
            (project_id,context_version,context_sha256,profile_revision,profile_hash,context_payload,source_manifest)
            VALUES (%s,1,%s,1,%s,'{}'::jsonb,'[]'::jsonb)""", (a,self.sha,'b'*64))
        self.assertNotEqual(source_a,source_b)

    def test_identity_cannot_move_between_projects_before_versioning(self):
        a, b = self.projects
        source = uuid4()
        self.conn.execute("""INSERT INTO project_sources
            (id,project_id,source_type,original_input)
            VALUES (%s,%s,'research_link','Original source statement')""", (source, a))
        self.rejected('UPDATE project_sources SET project_id=%s WHERE id=%s', (b,source))
        self.rejected("UPDATE project_sources SET original_input='Forged' WHERE id=%s", (source,))
        self.conn.execute('UPDATE project_sources SET user_notes=%s WHERE id=%s', ('allowed note',source))
        self.assertEqual(self.conn.execute('SELECT project_id FROM project_sources WHERE id=%s',
                                           (source,)).fetchone()[0], a)

    def test_duplicate_version_and_project_scoped_idempotency(self):
        a,b = self.projects
        sa,va = self.fixture(a)
        sb,vb = self.fixture(b)
        self.rejected("""INSERT INTO project_source_versions
            (project_id,source_id,version_no) VALUES (%s,%s,1)""", (a,sa))
        for p,s,v in ((a,sa,va),(b,sb,vb)):
            self.conn.execute("""INSERT INTO source_extraction_jobs
                (project_id,source_id,source_version_id,stage,idempotency_key)
                VALUES (%s,%s,%s,'document_parse','same-key')""", (p,s,v))
        self.rejected("""INSERT INTO source_extraction_jobs
            (project_id,source_id,source_version_id,stage,idempotency_key)
            VALUES (%s,%s,%s,'document_parse','same-key')""", (a,sa,va))
        self.rejected("""INSERT INTO source_extraction_jobs
            (project_id,source_id,source_version_id,stage,idempotency_key,cost_estimate_usd)
            VALUES (%s,%s,%s,'vision','paid-key',1)""", (a,sa,va))
        self.rejected("""INSERT INTO source_extraction_jobs
            (project_id,source_id,source_version_id,stage,idempotency_key,cost_estimate_usd,consent_ref)
            VALUES (%s,%s,%s,'vision','paid-blank-key',1,'')""", (a,sa,va))
        self.rejected("UPDATE source_extraction_jobs SET project_id=%s WHERE project_id=%s",(b,a))


if __name__ == '__main__':
    unittest.main()
