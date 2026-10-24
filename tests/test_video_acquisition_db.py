"""Stage 06 real PostgreSQL 17 tests. No external site requests or yt-dlp."""
import hashlib
import os
import unittest
from uuid import uuid4
from unittest.mock import patch
DSN=os.environ.get('CM_TEST_PG_DSN')

@unittest.skipUnless(DSN,'Requires ephemeral CI PostgreSQL 17')
class VideoDB(unittest.TestCase):
    def setUp(self):
        import psycopg
        from intelligence.project_sources import video_jobs as j
        from intelligence.project_sources.video_adapters import VideoIdentity,AcquisitionProblem
        self.j=j;self.pid=uuid4().hex;self.sid=str(uuid4());self.other=uuid4().hex
        with psycopg.connect(DSN) as c:
            for pid in (self.pid,self.other):
                c.execute('''INSERT INTO project_registry(id,name,profile_revision,profile_hash,created_at,updated_at)
                  VALUES(%s,'video test',1,%s,now(),now())''',(pid,'a'*64))
            c.execute('''INSERT INTO project_sources
              (id,project_id,source_type,canonical_url,platform,platform_native_id,link_kind,
               ingestion_mode,original_input,identity_key)
              VALUES (%s,%s,'user_video_reference',%s,'youtube',%s,'video','video_reference',%s,%s)''',
              (self.sid,self.pid,'https://www.youtube.com/watch?v=AbCdEf12345',
              'AbCdEf12345','https://www.youtube.com/watch?v=AbCdEf12345','youtube:video:AbCdEf12345'))
            vers=c.execute('''INSERT INTO project_source_versions(project_id,source_id,version_no,source_locator)
              VALUES(%s,%s,1,%s::jsonb) RETURNING id''',(self.pid,self.sid,'{"kind":"registered_only"}')).fetchone()[0]
            c.execute('UPDATE project_sources SET active_version_id=%s WHERE id=%s',(vers,self.sid))
            c.commit()
        self.mockproj=patch.object(j,'_pid',return_value={'id':self.pid});self.mockproj.start();self.addCleanup(self.mockproj.stop)
        self.mockconn=patch.object(j,'_conn',side_effect=lambda:psycopg.connect(DSN));self.mockconn.start();self.addCleanup(self.mockconn.stop)
        self.ident=VideoIdentity('youtube','AbCdEf12345','https://www.youtube.com/shorts/AbCdEf12345')
        self.payload={'platform':'youtube','native_id':'AbCdEf12345','canonical_url':self.ident.canonical_url,
            'video_sha256':hashlib.sha256(b'fake-video').hexdigest(),'byte_size':100,'video_path':'/fake/YouTubeArchive/single/AbCdEf12345/video.mp4',
            'metadata':{'id':'AbCdEf12345','source_url':self.ident.canonical_url},'attachments':{},
            'availability_status':'available','acquisition_verified':True}

    def _fake_adapter(self):
        from types import SimpleNamespace
        return SimpleNamespace(validate_url=lambda url:self.ident,resolve_identity=lambda url:self.ident,
                               collect_outputs=lambda identity:self.payload)

    def test_enqueue_idempotence_scope_and_cancel_retry(self):
        a=self.j.enqueue(self.pid,self.sid,{})
        self.assertTrue(a['created'])
        b=self.j.enqueue(self.pid,self.sid,{})
        self.assertEqual(b['job_id'],a['job_id']);self.assertFalse(b['created'])
        self.assertEqual(len(self.j.list_jobs(self.pid,self.sid)['jobs']),1)
        with self.assertRaises(self.j.AcquisitionProblem):self.j.result(self.other,self.sid,a['job_id'])
        self.assertEqual(self.j.cancel(self.pid,self.sid,a['job_id'])['state'],'cancelled')
        self.assertEqual(self.j.retry(self.pid,self.sid,a['job_id'])['state'],'queued')
        with self.assertRaises(self.j.AcquisitionProblem):self.j.retry(self.pid,self.sid,a['job_id'])
        self.assertEqual(self.j.cancel(self.pid,self.sid,a['job_id'])['state'],'cancelled')

    def test_claim_success_project_versions_and_reuse(self):
        from intelligence.project_sources import video_jobs as j
        with patch.object(j,'adapter_for',return_value=self._fake_adapter()):
            job=j.enqueue(self.pid,self.sid,{})
            output=j.run_one(runner=lambda *_:self.fail('cached video should skip downloader'))
            self.assertEqual(output['state'],'completed');self.assertTrue(output['cache_reused'])
            result=j.result(self.pid,self.sid,job['job_id'])
            self.assertEqual(result['result']['video_sha256'],self.payload['video_sha256'])
            with self.assertRaises(j.AcquisitionProblem):j.cancel(self.pid,self.sid,job['job_id'])
            added=j.enqueue(self.pid,self.sid,{'refresh':True})
            again=j.run_one(runner=lambda *_:self.fail('should re-use file'))
            self.assertEqual(again['state'],'completed')
            with j._conn() as c:
                self.assertEqual(c.execute('SELECT count(*) FROM project_media_assets WHERE project_id=%s AND asset_kind=\'video\'',(self.pid,)).fetchone()[0],1)
                self.assertEqual(c.execute('SELECT count(*) FROM project_source_versions WHERE project_id=%s AND source_id=%s',(self.pid,self.sid)).fetchone()[0],2)
            self.assertEqual(j.result(self.pid,self.sid,added['job_id'])['state'],'completed')

    def test_failed_download_not_fake_success(self):
        from intelligence.project_sources.video_adapters import AcquisitionProblem
        from types import SimpleNamespace
        def nothing(identity):raise AcquisitionProblem('Unavailable','asset_unavailable')
        adapter=SimpleNamespace(validate_url=lambda _:self.ident,resolve_identity=lambda _:self.ident,
                                collect_outputs=nothing)
        with patch.object(self.j,'adapter_for',return_value=adapter):
            req=self.j.enqueue(self.pid,self.sid,{})
            res=self.j.run_one(runner=lambda job,progress,cancelled:{'status':'failed','error':'403 forbidden'})
            self.assertEqual(res['state'],'unavailable')
            self.assertIsNone(self.j.result(self.pid,self.sid,req['job_id'])['result'])
            self.assertEqual(self.j.retry(self.pid,self.sid,req['job_id'])['state'],'queued')
            self.j.cancel(self.pid,self.sid,req['job_id'])

if __name__=='__main__':unittest.main()
