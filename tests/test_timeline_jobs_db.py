"""Stage09 PostgreSQL17 integration tests. All platform/audio/vision work mocked."""
import hashlib
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

DSN=os.environ.get('CM_TEST_PG_DSN')

@unittest.skipUnless(DSN,'Requires PostgreSQL 17 in CI')
class FusionDB(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.types.json import Jsonb
        from intelligence.project_sources import video_jobs as video, timeline_jobs as jobs
        from intelligence.project_sources import timeline_fusion as fusion
        from intelligence.project_sources.video_adapters import VideoIdentity
        self.v=video;self.j=jobs;self.f=fusion
        self.pid=uuid4().hex;self.sid=str(uuid4())
        ident=VideoIdentity('youtube','AbCdEf12345','https://www.youtube.com/shorts/AbCdEf12345')
        hashv=hashlib.sha256(b'fake mp4 original').hexdigest()
        self.asset={'platform':'youtube','native_id':ident.native_id,'canonical_url':ident.canonical_url,
            'video_sha256':hashv,'byte_size':100,'video_path':'/archive/YouTubeArchive/single/AbCdEf12345/video.mp4',
            'attachments':{},'metadata':{'duration':3},'acquisition_verified':True,'availability_status':'available'}
        self.adapter=type('Adapter',(),{'validate_url':lambda x,url:ident,'resolve_identity':lambda x,url:ident,
                                       'collect_outputs':lambda x,identity:self.asset})()
        with psycopg.connect(DSN) as c:
            c.execute("INSERT INTO project_registry(id,name,profile_revision,profile_hash,created_at,updated_at) VALUES(%s,'stage09',1,%s,now(),now())",(self.pid,'a'*64))
            c.execute("""INSERT INTO project_sources(id,project_id,source_type,canonical_url,platform,
                platform_native_id,link_kind,ingestion_mode,original_input,identity_key)
                VALUES(%s,%s,'user_video_reference',%s,'youtube','AbCdEf12345','video','video_reference',%s,'youtube:video:AbCdEf12345')""",
                (self.sid,self.pid,ident.canonical_url,ident.canonical_url))
            ver=c.execute("INSERT INTO project_source_versions(project_id,source_id,version_no,source_locator) VALUES(%s,%s,1,'{\"kind\":\"registered_only\"}'::jsonb) RETURNING id",(self.pid,self.sid)).fetchone()[0]
            c.execute('UPDATE project_sources SET active_version_id=%s WHERE project_id=%s AND id=%s',(ver,self.pid,self.sid))
        ps=[patch.object(video,'_pid',return_value={}),patch.object(video,'_conn',side_effect=lambda:psycopg.connect(DSN)),
            patch.object(video,'adapter_for',return_value=self.adapter),
            patch.object(jobs,'_pid',return_value={}),patch.object(jobs,'_conn',side_effect=lambda:psycopg.connect(DSN))]
        for p in ps:p.start();self.addCleanup(p.stop)
        a=video.enqueue(self.pid,self.sid,{})
        self.aid=a['job_id']
        done=video.run_one(runner=lambda *_:self.fail('Should reuse existing verified MP4'))
        self.assertEqual(done['state'],'completed')
        with psycopg.connect(DSN) as c:
            row=c.execute('''SELECT result_source_version_id,result_output_id FROM project_video_acquisition_runs
                    WHERE project_id=%s AND job_id=%s''',(self.pid,self.aid)).fetchone()
            self.ver,self.ao=row
            self.tjid=c.execute("INSERT INTO source_extraction_jobs(project_id,source_id,source_version_id,stage,state,idempotency_key,max_attempts) VALUES(%s,%s,%s,'asr','completed',%s,3) RETURNING id",(self.pid,self.sid,self.ver,'test-asr:'+uuid4().hex)).fetchone()[0]
            t={'schema_version':'transcript/v1','video_sha256':hashv,'speech_status':'speech_detected','processing_status':'completed',
               'timing_coverage':{'duration_ms':3000},'segments':[{'start_ms':0,'end_ms':1800,'text':'Hello world','source':'srt','language':'en'}],
               'languages':['en'],'source':'srt','warnings':[]}
            c.execute('''INSERT INTO project_transcript_runs
              (project_id,job_id,source_id,video_source_version_id,acquisition_job_id,mode,
               normalizer_version,settings,config_sha256)
              VALUES(%s,%s,%s,%s,%s,'reuse_sidecar','transcript-normalizer/1.0.0',%s,%s)''',
               (self.pid,self.tjid,self.sid,self.ver,self.aid,Jsonb({'mode':'reuse_sidecar'}),'b'*64))
            self.to=c.execute("""INSERT INTO source_processing_outputs(project_id,source_id,source_version_id,
              extraction_job_id,output_type,schema_version,output,output_sha256)
              VALUES(%s,%s,%s,%s,'transcript','transcript/v1',%s,%s) RETURNING id""",
              (self.pid,self.sid,self.ver,self.tjid,Jsonb(t),fusion.sha(fusion.canonical(t)))).fetchone()[0]
            c.execute('UPDATE project_transcript_runs SET result_output_id=%s WHERE project_id=%s AND job_id=%s',(self.to,self.pid,self.tjid))
            self.pjid=c.execute("INSERT INTO source_extraction_jobs(project_id,source_id,source_version_id,stage,state,idempotency_key,max_attempts) VALUES(%s,%s,%s,'vision','partial',%s,3) RETURNING id",(self.pid,self.sid,self.ver,'test-vision:'+uuid4().hex)).fetchone()[0]
            p={'schema_version':'vision-prepare/v1','video_sha256':hashv,'video_source_version_id':str(self.ver),
               'acquisition_job_id':self.aid,'status':'partial','video_duration_ms':3000,
               'frames':[{'frame_id':'f001','scene_id':'sc001','time_ms':1000,'sha256':'c'*64,
                          'ocr':{'status':'ok','start_ms':1000,'end_ms':1040,'text':'BUY'}}],
               'scenes':[{'scene_id':'sc001','start_ms':0,'end_ms':3000,'frame_ids':['f001'],'boundary_kind':'sampling_bin'}],
               'warnings':['single_frame_sample']}
            c.execute('''INSERT INTO project_vision_runs(project_id,job_id,source_id,video_source_version_id,acquisition_job_id,
                mode,config,config_sha256) VALUES(%s,%s,%s,%s,%s,'prepare',%s,%s)''',
                (self.pid,self.pjid,self.sid,self.ver,self.aid,Jsonb({'max_frames':2}),'d'*64))
            self.po=c.execute("""INSERT INTO source_processing_outputs(project_id,source_id,source_version_id,
              extraction_job_id,output_type,schema_version,output,output_sha256)
              VALUES(%s,%s,%s,%s,'visual_scenes','vision-prepare/v1',%s,%s) RETURNING id""",
              (self.pid,self.sid,self.ver,self.pjid,Jsonb(p),fusion.sha(fusion.canonical(p)))).fetchone()[0]
            c.execute('UPDATE project_vision_runs SET result_output_id=%s WHERE project_id=%s AND job_id=%s',(self.po,self.pid,self.pjid))

    def tearDown(self):
        if not DSN:return
        import psycopg
        with psycopg.connect(DSN) as c:
            c.execute("UPDATE source_extraction_jobs SET state='cancelled',lease_until=NULL,updated_at=clock_timestamp() WHERE project_id=%s AND stage='timeline_fusion' AND state IN ('queued','processing')",(self.pid,))

    def test_real_db_fusion_pin_idempotence_export(self):
        created=self.j.enqueue(self.pid,self.sid,{})
        duplicate=self.j.enqueue(self.pid,self.sid,{})
        self.assertFalse(duplicate['created']);self.assertEqual(created['job_id'],duplicate['job_id'])
        finished=self.j.run_one()
        self.assertEqual(finished['state'],'partial')
        result=self.j.result(self.pid,self.sid,created['job_id'])
        self.assertEqual(result['intelligence']['schema_version'],'video-intelligence/v1')
        self.assertEqual(result['intelligence']['timeline_count'],4)
        self.assertEqual(result['intelligence']['ocr_segments'][0]['text'],'BUY')
        self.assertEqual(result['intelligence']['video_source_version_id'],str(self.ver))
        snapshot=self.j.result(self.pid,self.sid,created['job_id'],export=True)['intelligence']
        self.assertIn('evidence',snapshot)
        self.assertEqual(self.f.sha(self.f.canonical(snapshot)),result['result_sha256'])
        with self.assertRaises(self.f.FusionProblem):self.j.retry(self.pid,self.sid,created['job_id'])

    def test_transcript_only_and_preparation_only(self):
        a=self.j.enqueue(self.pid,self.sid,{'include_vision':False})
        out=self.j.run_one()
        self.assertEqual(out['state'],'partial')
        self.assertEqual(self.j.result(self.pid,self.sid,a['job_id'])['intelligence']['coverage']['frames_sampled'],0)
        b=self.j.enqueue(self.pid,self.sid,{'include_transcript':False})
        self.j.run_one()
        self.assertEqual(self.j.result(self.pid,self.sid,b['job_id'])['intelligence']['coverage']['speech_status'],'unavailable')

    def test_wrong_source_and_version_explicit_inputs(self):
        with self.assertRaises(self.f.FusionProblem):self.j.enqueue(self.pid,str(uuid4()),{})
        with self.assertRaises(self.f.FusionProblem):self.j.enqueue(self.pid,self.sid,{'transcript_job_id':str(uuid4())})
        with self.assertRaises(self.f.FusionProblem):self.j.enqueue(self.pid,self.sid,{'vision_job_id':str(uuid4())})
        with self.assertRaises(self.f.FusionProblem):self.j.enqueue(self.pid,self.sid,{'include_vision':False,'prepare_job_id':str(self.pjid)})

    def test_project_isolation_and_retry(self):
        jid=self.j.enqueue(self.pid,self.sid,{})['job_id']
        with self.assertRaises(self.f.FusionProblem):self.j.result(uuid4().hex,self.sid,jid)
        with self.assertRaises(self.f.FusionProblem):self.j.result(self.pid,str(uuid4()),jid)
        state=self.j.run_one(builder=lambda **kw:(_ for _ in ()).throw(self.f.FusionProblem('bad input','input_integrity')))
        self.assertEqual(state['state'],'unavailable')
        self.assertEqual(self.j.retry(self.pid,self.sid,jid)['state'],'queued')
        self.j.run_one()
        self.assertEqual(self.j.result(self.pid,self.sid,jid)['state'],'partial')

if __name__=='__main__':unittest.main()
