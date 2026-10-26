"""Stage 03 PostgreSQL17 integration: add/replace/restart/legacy/immutability."""
import base64
import hashlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

DSN=os.environ.get('CM_TEST_PG_DSN')

@unittest.skipUnless(DSN,'Needs CI PostgreSQL 17')
class ResearchFileDB(unittest.TestCase):
    def setUp(self):
        import psycopg
        from intelligence.project_sources import file_library as lib
        self.lib=lib;self.pid=uuid.uuid4().hex
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/self.pid/'research').mkdir(parents=True)
        self.record={'id':self.pid,'files':[]}
        from projects import storage_root
        self.patches=[patch.object(lib,'_project',return_value=self.record),
                      patch.object(lib,'_connect',side_effect=lambda:psycopg.connect(DSN)),
                      patch('projects.storage_root',return_value=self.root)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        with psycopg.connect(DSN) as c:
            c.execute('INSERT INTO project_registry(id,name,profile_revision,profile_hash,created_at,updated_at) VALUES (%s,\'stage03\',1,%s,now(),now())',(self.pid,'c'*64))
            c.commit()

    @staticmethod
    def payload(data=b'%PDF-1.4 original',name='report.pdf',**kw):
        return {'name':name,'data_base64':base64.b64encode(data).decode(),'uploaded_by':'local_test',**kw}

    def test_add_replace_download_archive_restore_pinned_version(self):
        import psycopg
        r=self.lib.add_file(self.pid,self.payload());fid=r['file_id']
        listing=self.lib.list_files(self.pid)['files']
        self.assertEqual(len(listing),1)
        row=listing[0]
        self.assertEqual(row['storage_status'],'ready')
        first_hash=row['content_sha256']
        self.assertEqual(self.lib.file_download(self.pid,fid)[0],b'%PDF-1.4 original')
        r2=self.lib.replace_file(self.pid,fid,self.payload(b'%PDF-1.4 changed',expected_updated_at=row['updated_at']))
        self.assertEqual(r2['version']['version_no'],2)
        self.assertEqual(self.lib.file_download(self.pid,fid)[0],b'%PDF-1.4 changed')
        self.assertEqual(self.lib.file_download(self.pid,fid,1)[0],b'%PDF-1.4 original')
        current=self.lib.list_files(self.pid)['files'][0]
        self.assertNotEqual(current['content_sha256'],first_hash)
        self.assertEqual(len(self.lib.versions(self.pid,fid)['versions']),2)
        archived=self.lib.archive_file(self.pid,fid,current['updated_at'])
        self.assertTrue(archived['archived'])
        self.assertEqual(self.lib.list_files(self.pid)['count'],0)
        self.assertEqual(self.lib.list_files(self.pid,True)['count'],1)
        self.assertEqual(self.lib.file_download(self.pid,fid,1)[0],b'%PDF-1.4 original')
        current=self.lib.list_files(self.pid,True)['files'][0]
        self.assertFalse(self.lib.restore_file(self.pid,fid,current['updated_at'])['archived'])
        with psycopg.connect(DSN) as c:
            with self.assertRaises(psycopg.Error):
                with c.transaction():c.execute('UPDATE project_research_file_versions SET content_sha256=%s WHERE project_id=%s',(hashlib.sha256(b'bad').hexdigest(),self.pid))

    def test_legacy_import_does_not_mutate_legacy_record(self):
        data=b'%PDF-1.4 legacy'
        stored=uuid.uuid4().hex+'.pdf';(self.root/self.pid/'research'/stored).write_bytes(data)
        self.record['files']=[{'name':'Old research.pdf','size':len(data),'stored_name':stored}]
        before=json.dumps(self.record,sort_keys=True)
        self.assertEqual(self.lib.import_legacy(self.pid,dry_run=True)['created'],0)
        self.assertEqual(self.lib.import_legacy(self.pid)['created'],1)
        self.assertEqual(self.lib.import_legacy(self.pid)['created'],0)
        self.assertEqual(json.dumps(self.record,sort_keys=True),before)
        self.assertEqual((self.root/self.pid/'research'/stored).read_bytes(),data)
        file_id=self.lib.list_files(self.pid)['files'][0]['file_id']
        self.assertEqual(self.lib.file_download(self.pid,file_id)[0],data)

    def test_failed_db_insert_does_not_leave_new_blob(self):
        from unittest.mock import patch
        with patch.object(self.lib,'_save_version',side_effect=RuntimeError('simulated insert failure')):
            with self.assertRaises(RuntimeError):self.lib.add_file(self.pid,self.payload())
        self.assertEqual(self.lib.list_files(self.pid)['count'],0)
        self.assertEqual(list((self.root/self.pid/'research_library').rglob('*.bin')),[])

    def test_mismatch_and_project_scope(self):
        fid=self.lib.add_file(self.pid,self.payload())['file_id']
        with self.assertRaises(self.lib.FileProblem):self.lib.replace_file(self.pid,fid,self.payload(expected_updated_at='2000-01-01T00:00:00+00:00'))
        self.assertEqual(self.lib.versions(self.pid,fid)['versions'][0]['version_no'],1)
        other=uuid.uuid4().hex
        with self.assertRaises(self.lib.FileProblem):self.lib.file_download(other,fid)

if __name__=='__main__':unittest.main()
