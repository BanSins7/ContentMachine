"""Stage13 PostgreSQL17 preflight: only current approved pinned Evidence; no writes."""
import os,unittest
from unittest.mock import patch
from uuid import uuid4
from test_context_db import ContextDB
from intelligence.taxonomy_builder.context_binding import ui_preflight
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

DSN=os.environ.get('CM_TEST_PG_DSN')

@unittest.skipUnless(DSN,'Requires PostgreSQL17 CI service')
class Stage13PreflightPostgres(ContextDB):
    def test_readonly_preflight_exact_evidence_and_project_boundary(self):
        import psycopg
        with psycopg.connect(DSN) as conn:
            conn.execute("""INSERT INTO project_evidence_reviews
                (project_id,evidence_id,decision,reviewed_by)
                VALUES(%s,%s,'approved','local_owner')""",(self.pid,self.eid))
        draft=self.s.preview(self.pid)
        cid=self.s.create_snapshot(self.pid,{'expected_context_sha256':draft['context_sha256']})['context_id']
        self.s.decide(self.pid,cid,{'decision':'approved','expected_context_sha256':draft['context_sha256'],
                  'acknowledge_partial':False,'excluded_source_ids':[]})
        with psycopg.connect(DSN) as conn:
            count_before=conn.execute("SELECT count(*) FROM tb_cost_quotes WHERE project_id=%s",
                                      (self.pid,)).fetchone()[0]
        with patch('intelligence.taxonomy_builder.pipeline_repository._pid',
                   side_effect=lambda p: self.pid if p==self.pid else (_ for _ in ()).throw(
                       PipelineError('Wrong project',404))),patch(
                'intelligence.taxonomy_builder.pipeline_repository._connect',
                side_effect=lambda: psycopg.connect(DSN)):
            data=ui_preflight(self.pid,cid,900)
            self.assertTrue(data['no_provider_call'])
            self.assertTrue(data['no_quote_created'])
            self.assertEqual(data['context_binding']['context_id'],cid)
            self.assertEqual(data['context_binding']['selected_evidence_ids'],[self.eid])
            self.assertEqual(data['evidence'][0]['evidence_id'],self.eid)
            self.assertEqual(data['eligible_fact_count'],1)
            with self.assertRaises(PipelineError):
                ui_preflight(self.other,cid,900)
            with self.assertRaises(PipelineError):
                ui_preflight(self.pid,cid,99)
        with psycopg.connect(DSN) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM tb_cost_quotes WHERE project_id=%s",
                (self.pid,)).fetchone()[0],count_before)

if __name__=='__main__':unittest.main()
