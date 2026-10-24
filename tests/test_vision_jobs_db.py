"""Stage 08 PostgreSQL 17 integration; all provider and platform operations mocked."""
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

DSN=os.environ.get('CM_TEST_PG_DSN')

@unittest.skipUnless(DSN,'Requires PostgreSQL 17 in CI')
class VisionDB(unittest.TestCase):
    def setUp(self):
        import psycopg
        from intelligence.project_sources import vision_jobs as jobs,video_jobs as video
        from intelligence.project_sources.video_adapters import VideoIdentity
        self.j=jobs;self.vid=video;self.pid=uuid4().hex;self.sid=str(uuid4())
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        self.ident=VideoIdentity('youtube','AbCdEf12345','https://www.youtube.com/shorts/AbCdEf12345')
        self.digest=hashlib.sha256(b'fake-mp4-bytes').hexdigest()
        self.asset={'platform':'youtube','native_id':self.ident.native_id,'canonical_url':self.ident.canonical_url,
          'video_sha256':self.digest,'byte_size':100,
          'video_path':str(self.root/'fake-video.mp4'),'attachments':{},'metadata':{'duration':3},
          'acquisition_verified':True,'availability_status':'available'}
        self.adapter=type('Adapter',(),{
           'validate_url':lambda x,url:self.ident,'resolve_identity':lambda x,url:self.ident,
           'collect_outputs':lambda x,ident:self.asset})()
        with psycopg.connect(DSN) as c:
            c.execute("""INSERT INTO project_registry(id,name,profile_revision,profile_hash,created_at,updated_at)
                  VALUES(%s,'stage08',1,%s,now(),now())""",(self.pid,'a'*64))
            c.execute("""INSERT INTO project_sources(id,project_id,source_type,canonical_url,platform,
              platform_native_id,link_kind,ingestion_mode,original_input,identity_key)
              VALUES(%s,%s,'user_video_reference',%s,'youtube','AbCdEf12345','video',
              'video_reference',%s,'youtube:video:AbCdEf12345')""",
              (self.sid,self.pid,self.ident.canonical_url,self.ident.canonical_url))
            v=c.execute("""INSERT INTO project_source_versions(project_id,source_id,version_no,source_locator)
                VALUES(%s,%s,1,'{"kind":"registered_only"}'::jsonb) RETURNING id""",
                (self.pid,self.sid)).fetchone()[0]
            c.execute('UPDATE project_sources SET active_version_id=%s WHERE project_id=%s AND id=%s',(v,self.pid,self.sid))
        self.patches=[patch.object(jobs,'_pid',return_value={}),
                      patch.object(video,'_pid',return_value={}),
                      patch.object(video,'_conn',side_effect=lambda:psycopg.connect(DSN)),
                      patch.object(jobs,'_conn',side_effect=lambda:psycopg.connect(DSN)),
                      patch.object(video,'adapter_for',return_value=self.adapter),
                      patch.object(jobs,'adapter_for',return_value=self.adapter)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        video.enqueue(self.pid,self.sid,{})
        d=video.run_one(runner=lambda *_:self.fail('Already verified asset should not download'))
        self.assertEqual(d['state'],'completed')

    def tearDown(self):
        if DSN:
            import psycopg
            with psycopg.connect(DSN) as c:
                c.execute("""UPDATE source_extraction_jobs SET state='cancelled',lease_until=NULL,
                    updated_at=clock_timestamp() WHERE project_id=%s AND stage='vision'
                    AND state IN ('queued','processing')""",(self.pid,))

    def prepared(self,video,settings,pid,jid):
        from intelligence.project_sources import vision_engine as eng
        self.assertEqual(pid,self.pid)
        frame=b'JPEG-test-bytes'
        return {'schema_version':eng.VERSION,'status':'partial','probe':{'duration_ms':3000,'audio_present':False},
          'video_duration_ms':3000,'frame_sampling':{'sampled_frames':1,'all_frames_analyzed':False},
          'scenes':[{'scene_id':'sc001','start_ms':0,'end_ms':3000,'boundary_kind':'sampling_bin','frame_ids':['f001']}],
          'frames':[{'frame_id':'f001','time_ms':1000,'scene_id':'sc001','sha256':eng.sha(frame),
                     'storage_ref':'vision_assets/'+str(jid)+'/f001_'+eng.sha(frame)+'.jpg',
                     'byte_size':len(frame),'ocr':{'status':'unavailable','text':''}}],
          'warnings':['ocr_missing']}

    def test_prepare_idempotence_and_project_scoping(self):
        with patch.object(self.j,'_asset',return_value=(self.root/'fake-video.mp4',self._manifest())):
            one=self.j.enqueue_prepare(self.pid,self.sid,{'config':{'max_frames':6}})
            two=self.j.enqueue_prepare(self.pid,self.sid,{'config':{'max_frames':6}})
            self.assertEqual(one['job_id'],two['job_id']);self.assertFalse(two['created'])
            done=self.j.run_one(local_runner=self.prepared)
        self.assertEqual(done['state'],'partial')
        output=self.j.result(self.pid,self.sid,one['job_id'])
        self.assertEqual(output['result']['video_sha256'],self.digest)
        self.assertEqual(output['result']['frames'][0]['time_precision'] if 'time_precision' in output['result']['frames'][0] else 'sample_fixture','sample_fixture')
        other=uuid4().hex
        with self.assertRaises(self.j.eng.VisionProblem):self.j.result(other,self.sid,one['job_id'])
        with self.j._conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM source_processing_outputs WHERE project_id=%s AND output_type='visual_scenes'",(self.pid,)).fetchone()[0],1)

    def test_quote_consent_paid_single_call_and_history(self):
        from intelligence.project_sources import vision_engine as eng
        one=self.j.enqueue_prepare(self.pid,self.sid,{})
        with patch.object(self.j,'_asset',return_value=(self.root/'fake-video.mp4',self._manifest())):
            self.j.run_one(local_runner=self.prepared)
            quote=self.j.quote(self.pid,self.sid,{'prepare_job_id':one['job_id']})
            self.assertFalse(quote['price_is_guaranteed_cap'])
            with self.assertRaises(self.j.eng.VisionProblem):
                self.j.enqueue_analysis(self.pid,self.sid,{'quote_id':quote['quote_id'],'approved_estimate_usd':'0.01'})
            job=self.j.enqueue_analysis(self.pid,self.sid,{'quote_id':quote['quote_id'],'approved_estimate_usd':quote['estimate_usd']})
            dup=self.j.enqueue_analysis(self.pid,self.sid,{'quote_id':quote['quote_id'],'approved_estimate_usd':quote['estimate_usd']})
            self.assertFalse(dup['created']);self.assertEqual(dup['job_id'],job['job_id'])
            calls=[]
            def provider(prep,imgs,key):
                calls.append(key)
                scene={'scene_id':'sc001','start_ms':0,'end_ms':3000,'frame_ids':['f001'],
                  'visual_description':'Кадр с предметом','observed_visible':['Стол'],
                  'actions':[],'objects':['Стол'],'people_observations':[],
                  'product_logo_visibility':'uncertain','camera_editing':[],
                  'inferred_interpretation':None,'uncertainty':None}
                return {'observations':eng.validate_observations({'schema_version':eng.OBS_VERSION,
                        'scenes':[scene],'limitations':['Выборка кадров']},prep),
                        'usage':{'cost_usd':0.002},'provider_request_id':'test-vision'}
            with patch.object(self.j,'_frame_bytes',return_value=[('f001',b'JPEG-test-bytes')]):
                res=self.j.run_one(remote_runner=provider,key_provider=lambda:'fake-test-key')
        self.assertEqual(res['state'],'completed');self.assertEqual(len(calls),1)
        output=self.j.result(self.pid,self.sid,job['job_id'])
        self.assertEqual(output['result']['scenes'][0]['frame_ids'],['f001'])
        self.assertEqual(output['charge_state'],'charged')
        with self.assertRaises(self.j.eng.VisionProblem):self.j.retry_prepare(self.pid,self.sid,job['job_id'])
        self.assertEqual(len(self.j.list_jobs(self.pid,self.sid)['jobs']),2)

    def test_paid_provider_failure_not_retried(self):
        one=self.j.enqueue_prepare(self.pid,self.sid,{})
        with patch.object(self.j,'_asset',return_value=(self.root/'fake-video.mp4',self._manifest())):
            self.j.run_one(local_runner=self.prepared)
            q=self.j.quote(self.pid,self.sid,{'prepare_job_id':one['job_id']})
            job=self.j.enqueue_analysis(self.pid,self.sid,{'quote_id':q['quote_id'],'approved_estimate_usd':q['estimate_usd']})
            with patch.object(self.j,'_frame_bytes',return_value=[('f001',b'JPEG-test-bytes')]):
                status=self.j.run_one(remote_runner=lambda *_:(_ for _ in ()).throw(self.j.eng.VisionProblem('error','provider_uncertain')),
                                      key_provider=lambda:'fake')
        self.assertEqual(status['state'],'needs_review')
        self.assertEqual(self.j.result(self.pid,self.sid,job['job_id'])['charge_state'],'unknown')
        self.assertIsNone(self.j.run_one(local_runner=self.prepared))
        with self.j._conn() as c:
            self.assertEqual(c.execute("SELECT attempts FROM source_extraction_jobs WHERE id=%s",(job['job_id'],)).fetchone()[0],1)

    def test_missing_key_local_checkpoint_not_lost(self):
        one=self.j.enqueue_prepare(self.pid,self.sid,{})
        with patch.object(self.j,'_asset',return_value=(self.root/'fake-video.mp4',self._manifest())):
            self.j.run_one(local_runner=self.prepared)
            q=self.j.quote(self.pid,self.sid,{'prepare_job_id':one['job_id']})
            paid=self.j.enqueue_analysis(self.pid,self.sid,{'quote_id':q['quote_id'],'approved_estimate_usd':q['estimate_usd']})
            with patch.object(self.j,'_frame_bytes',return_value=[('f001',b'JPEG-test-bytes')]):
                res=self.j.run_one(remote_runner=lambda *_:self.fail('No key should block before provider'),key_provider=lambda:'')
        self.assertEqual(res['state'],'unavailable')
        self.assertEqual(self.j.result(self.pid,self.sid,one['job_id'])['state'],'partial')
        self.assertEqual(self.j.result(self.pid,self.sid,paid['job_id'])['charge_state'],'not_applicable')

    def _manifest(self):
        with self.j._conn() as c:
            out=c.execute('''SELECT o.output FROM project_video_acquisition_runs r
                JOIN source_processing_outputs o ON o.project_id=r.project_id AND o.id=r.result_output_id
                WHERE r.project_id=%s AND r.source_id=%s ORDER BY r.created_at DESC LIMIT 1''',
                (self.pid,self.sid)).fetchone()[0]
        return out

if __name__=='__main__':unittest.main()
