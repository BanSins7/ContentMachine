"""PSI 04 PostgreSQL 17 integration; isolated local document parsing, no external APIs."""
from __future__ import annotations

import base64
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

DSN=os.environ.get('CM_TEST_PG_DSN')

@unittest.skipUnless(DSN,'Needs ephemeral PostgreSQL 17')
class DocumentJobsDB(unittest.TestCase):
    def setUp(self):
        import psycopg
        from intelligence.project_sources import file_library as lib,document_jobs as jobs
        self.lib=lib;self.jobs=jobs;self.pid=uuid4().hex
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/self.pid/'research').mkdir(parents=True)
        self.project={'id':self.pid,'files':[]}
        from projects import storage_root
        self.patches=[patch.object(lib,'_project',return_value=self.project),
                      patch.object(lib,'_connect',side_effect=lambda:psycopg.connect(DSN)),
                      patch('projects.storage_root',return_value=self.root)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        with psycopg.connect(DSN) as conn:
            conn.execute("""INSERT INTO project_registry
               (id,name,profile_revision,profile_hash,created_at,updated_at)
               VALUES (%s,'stage04',1,%s,now(),now())""",(self.pid,'d'*64))

    def tearDown(self):
        # Never leave runnable jobs pointing at a deleted temp fixture folder.
        import psycopg
        with psycopg.connect(DSN) as conn:
            conn.execute("UPDATE source_extraction_jobs SET state='cancelled', "
                "lease_until=NULL,updated_at=clock_timestamp() "
                "WHERE project_id=%s AND state IN ('queued','processing')",(self.pid,))

    @staticmethod
    def payload(data='Пример аналитики\n'.encode('utf-8'),name='research.txt'):
        return {'name':name,'data_base64':base64.b64encode(data).decode(), 'uploaded_by':'fixture'}

    def test_enqueue_complete_reuse_and_pinning(self):
        a=self.lib.add_file(self.pid,self.payload())
        fid=a['file_id']
        j=self.jobs.enqueue(self.pid,fid,{'config':{'ocr':False}})
        self.assertTrue(j['created'])
        self.assertEqual(j['state'],'queued')
        dup=self.jobs.enqueue(self.pid,fid,{'config':{}})
        self.assertFalse(dup['created'])
        self.assertEqual(j['job_id'],dup['job_id'])
        # In a CI postgres test, use direct parser to avoid spawning a subprocess
        # under the unittest runner; isolated_extract itself has a separate test.
        from intelligence.project_sources.document_parser import extract
        with patch.object(self.jobs,'isolated_extract',side_effect=lambda blob,name,cfg:extract(blob,name,config=cfg)) as run:
            done=self.jobs.run_one()
        self.assertEqual(done['state'],'completed')
        run.assert_called_once()
        result=self.jobs.get_result(self.pid,fid,j['job_id'])
        self.assertEqual(result['state'],'completed')
        self.assertEqual(result['result']['segments'][0]['text'],'Пример аналитики\n')
        self.assertEqual(result['result']['segments'][0]['locator']['line_start'],1)
        self.assertEqual(result['result_sha256'],hashlib.sha256(
            self.jobs.canonical({k:v for k,v in result['result'].items() if k not in ('segment_count','offset','has_more')})).hexdigest())
        self.assertIsNone(self.jobs.run_one())
        second=self.lib.add_file(self.pid,self.payload())
        fid2=second['file_id']
        other=self.jobs.enqueue(self.pid,fid2,{})
        with patch.object(self.jobs,'isolated_extract',side_effect=AssertionError('must use same-project SHA cache')):
            cached=self.jobs.run_one()
        self.assertEqual(cached['state'],'completed')
        self.assertTrue(cached['cached'])
        self.assertEqual(self.jobs.get_result(self.pid,fid2,other['job_id'])['result_sha256'],result['result_sha256'])

    def test_version_immutable_job_by_exact_sha(self):
        first=self.lib.add_file(self.pid,self.payload())
        fid=first['file_id']
        job1=self.jobs.enqueue(self.pid,fid,{})
        file_state=self.lib.list_files(self.pid)['files'][0]
        self.lib.replace_file(self.pid,fid,{**self.payload(b'Second version\n'),
             'expected_updated_at':file_state['updated_at']})
        job2=self.jobs.enqueue(self.pid,fid,{})
        self.assertNotEqual(job1['job_id'],job2['job_id'])
        self.assertEqual(job1['file_version'],1)
        self.assertEqual(job2['file_version'],2)
        self.assertEqual(self.jobs.enqueue(self.pid,fid,{'version':1})['job_id'],job1['job_id'])

    def test_corrupt_source_failed_not_completed(self):
        first=self.lib.add_file(self.pid,self.payload())
        fid=first['file_id']
        job=self.jobs.enqueue(self.pid,fid,{})
        from psycopg import connect
        with connect(DSN) as conn:
            ref=conn.execute('SELECT storage_ref FROM project_research_file_versions WHERE project_id=%s AND file_id=%s',
                (self.pid,fid)).fetchone()[0]
        path=self.lib._safe_path(self.root/self.pid,ref,must_exist=True)
        path.write_bytes(b'tampered')
        status=self.jobs.run_one()
        self.assertEqual(status['state'],'failed')
        result=self.jobs.get_result(self.pid,fid,job['job_id'])
        self.assertEqual(result['error_code'],'source_integrity')
        self.assertNotIn('result',result)
        retry=self.jobs.retry(self.pid,fid,job['job_id'])
        self.assertEqual(retry['state'],'queued')

    def test_invalid_config_and_other_project_are_rejected(self):
        first=self.lib.add_file(self.pid,self.payload())
        fid=first['file_id']
        with self.assertRaises(self.jobs.DocumentJobProblem):
            self.jobs.enqueue(self.pid,fid,{'config':{'ocr':'yes'}})
        with self.assertRaises(self.jobs.DocumentJobProblem):
            self.jobs.enqueue(uuid4().hex,fid,{})

if __name__=='__main__':unittest.main()
