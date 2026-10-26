"""Stage07 real PostgreSQL17 integration. All external platform/Whisper calls are mocked."""
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

DSN=os.environ.get('CM_TEST_PG_DSN')

@unittest.skipUnless(DSN,'Requires PostgreSQL 17 in CI')
class TranscriptJobsDB(unittest.TestCase):
    def setUp(self):
        import psycopg
        from intelligence.project_sources import transcript_jobs as tr,video_jobs as video
        from intelligence.project_sources.video_adapters import VideoIdentity
        self.tr=tr;self.video=video;self.pid=uuid4().hex;self.sid=str(uuid4())
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        data=b'1\n00:00:00,120 --> 00:00:01,200\nHello!\n'
        self.srt=root/'subtitles.srt';self.srt.write_bytes(data)
        self.att={'path':str(self.srt),'sha256':hashlib.sha256(data).hexdigest(),'byte_size':len(data)}
        self.ident=VideoIdentity('youtube','AbCdEf12345','https://www.youtube.com/shorts/AbCdEf12345')
        self.payload={'platform':'youtube','native_id':'AbCdEf12345','canonical_url':self.ident.canonical_url,
          'video_sha256':hashlib.sha256(b'fake-mp4-bytes').hexdigest(),'byte_size':100,
          'video_path':'/dummy/YouTubeArchive/single/AbCdEf12345/video.mp4',
          'metadata':{'id':'AbCdEf12345','source_url':self.ident.canonical_url,'subtitle_status':'ok','language':'en','duration':2},
          'attachments':{'subtitles':self.att},'availability_status':'available','acquisition_verified':True}
        self.adapter=SimpleNamespace(validate_url=lambda url:self.ident,resolve_identity=lambda url:self.ident,
                                     collect_outputs=lambda ident:self.payload)
        with psycopg.connect(DSN) as c:
            c.execute("""INSERT INTO project_registry(id,name,profile_revision,profile_hash,created_at,updated_at)
              VALUES(%s,'stage07 integration',1,%s,now(),now())""",(self.pid,'a'*64))
            c.execute("""INSERT INTO project_sources(id,project_id,source_type,canonical_url,platform,
                 platform_native_id,link_kind,ingestion_mode,original_input,identity_key)
                 VALUES (%s,%s,'user_video_reference',%s,'youtube','AbCdEf12345','video','video_reference',%s,%s)""",
                 (self.sid,self.pid,self.ident.canonical_url,self.ident.canonical_url,'youtube:video:AbCdEf12345'))
            v=c.execute("""INSERT INTO project_source_versions(project_id,source_id,version_no,source_locator)
                 VALUES(%s,%s,1,'{"kind":"registered_only"}'::jsonb) RETURNING id""",(self.pid,self.sid)).fetchone()[0]
            c.execute('UPDATE project_sources SET active_version_id=%s WHERE id=%s',(v,self.sid))
            c.commit()
        self.patches=[patch.object(tr,'_project',return_value={}),patch.object(video,'_pid',return_value={}),
                      patch.object(tr,'_connection',side_effect=lambda:psycopg.connect(DSN)),
                      patch.object(video,'_conn',side_effect=lambda:psycopg.connect(DSN)),
                      patch.object(video,'adapter_for',return_value=self.adapter)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        video.enqueue(self.pid,self.sid,{})
        result=video.run_one(runner=lambda *_:self.fail('Existing asset should skip downloader'))
        self.assertEqual(result['state'],'completed')
        self.verified={'video_path':self.payload['video_path'],'attachments':{'subtitles':self.att}}

    def tearDown(self):
        # Stage 07 doesn't leave queued work that would affect following tests.
        if DSN:
            import psycopg
            with psycopg.connect(DSN) as c:
                c.execute("""UPDATE source_extraction_jobs SET state='cancelled',lease_until=NULL,
                     updated_at=clock_timestamp() WHERE project_id=%s AND stage='asr'
                     AND state IN ('queued','processing')""",(self.pid,))
                c.commit()

    def _verify(self,job):
        return job['manifest'],self.verified

    def test_exact_video_pin_completed_pagination_and_repeat_idempotent(self):
        j=self.tr.enqueue(self.pid,self.sid,{'mode':'reuse_sidecar'})
        same=self.tr.enqueue(self.pid,self.sid,{'mode':'reuse_sidecar'})
        self.assertEqual(j['job_id'],same['job_id']);self.assertFalse(same['created'])
        with patch.object(self.tr,'_match_manifest',side_effect=self._verify):
            status=self.tr.run_one()
        self.assertEqual(status['state'],'completed')
        item=self.tr.result(self.pid,self.sid,j['job_id'],offset=0,limit=1)
        self.assertEqual(item['transcript']['schema_version'],'transcript/v1')
        self.assertEqual(item['transcript']['segments'][0]['start_ms'],120)
        self.assertEqual(item['transcript']['video_sha256'],self.payload['video_sha256'])
        self.assertEqual(item['transcript']['processing_status'],'completed')
        with self.assertRaises(self.tr.TranscriptProblem):self.tr.retry(self.pid,self.sid,j['job_id'])
        with self.tr._connection() as c:
            self.assertEqual(c.execute('''SELECT count(*) FROM source_processing_outputs
                 WHERE project_id=%s AND output_type='transcript' ''',(self.pid,)).fetchone()[0],1)
        self.assertEqual(len(self.tr.list_jobs(self.pid,self.sid)['jobs']),1)

    def test_new_local_asr_is_separate_explicit_version(self):
        base=self.tr.enqueue(self.pid,self.sid,{'mode':'reuse_sidecar'})
        with patch.object(self.tr,'_match_manifest',side_effect=self._verify):self.tr.run_one()
        rerun=self.tr.enqueue(self.pid,self.sid,{'mode':'local_asr','language':'ru'})
        self.assertNotEqual(base['job_id'],rerun['job_id'])
        with patch.object(self.tr,'_match_manifest',side_effect=self._verify):
            out=self.tr.run_one(local_asr=lambda *_:([{'start_ms':20,'end_ms':1100,'text':'Привет','language':'ru'}],'ru','small'))
        self.assertEqual(out['state'],'completed')
        transcript=self.tr.result(self.pid,self.sid,rerun['job_id'])['transcript']
        self.assertEqual(transcript['engine'],'local_whisper')
        self.assertEqual(transcript['segments'][0]['language'],'ru')
        self.assertEqual(transcript['segments'][0]['start_ms'],20)
        self.assertEqual(len(self.tr.list_jobs(self.pid,self.sid)['jobs']),2)
        with self.tr._connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM source_processing_outputs WHERE project_id=%s AND output_type='transcript'",(self.pid,)).fetchone()[0],2)

    def test_missing_sidecar_or_hash_mismatch_not_success(self):
        j=self.tr.enqueue(self.pid,self.sid,{})
        self.srt.write_bytes(b'changed')
        with patch.object(self.tr,'_match_manifest',side_effect=self._verify):state=self.tr.run_one()
        self.assertEqual(state['state'],'unavailable')
        self.assertIsNone(self.tr.result(self.pid,self.sid,j['job_id'])['transcript'] if 'transcript' in self.tr.result(self.pid,self.sid,j['job_id']) else None)
        self.assertEqual(self.tr.retry(self.pid,self.sid,j['job_id'])['state'],'queued')

    def test_other_project_cannot_read_or_enqueue(self):
        jid=self.tr.enqueue(self.pid,self.sid,{})['job_id']
        other=uuid4().hex
        with self.assertRaises(self.tr.TranscriptProblem):self.tr.result(other,self.sid,jid)
        with self.assertRaises(self.tr.TranscriptProblem):self.tr.enqueue(self.pid,str(uuid4()),{})
        with self.assertRaises(self.tr.TranscriptProblem):self.tr.enqueue(self.pid,self.sid,{'mode':'remote_paid'})

    def test_txt_only_without_timestamps_is_unavailable_not_no_speech(self):
        j=self.tr.enqueue(self.pid,self.sid,{})
        original=self.verified
        text=b'voice without timestamps'
        txt=self.srt.with_name('transcript.txt');txt.write_bytes(text)
        self.verified={'video_path':self.payload['video_path'],'attachments':{'transcript':{'path':str(txt),'sha256':hashlib.sha256(text).hexdigest(),'byte_size':len(text)}}}
        with patch.object(self.tr,'_match_manifest',side_effect=self._verify):out=self.tr.run_one()
        self.assertEqual(out['state'],'unavailable')
        t=self.tr.result(self.pid,self.sid,j['job_id'])['transcript']
        self.assertEqual(t['segment_count'],0)
        self.assertIn('untimed_text_only',t['warnings'])
        self.verified=original

if __name__=='__main__':unittest.main()
