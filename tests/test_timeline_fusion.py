"""Stage09 deterministic fusion: no provider, disk, or database dependencies."""
import copy
import hashlib
import unittest
from intelligence.project_sources import timeline_fusion as f

H='a'*64
S='00000000-0000-4000-8000-000000000001';V='00000000-0000-4000-8000-000000000002';A='00000000-0000-4000-8000-000000000003';T='00000000-0000-4000-8000-000000000004';P='00000000-0000-4000-8000-000000000005';R='00000000-0000-4000-8000-000000000006'

def inputs():
    a={'platform':'youtube','canonical_url':'https://www.youtube.com/shorts/AbCdEf12345',
       'native_id':'AbCdEf12345','video_sha256':H,'acquisition_verified':True}
    t={'schema_version':'transcript/v1','video_sha256':H,'speech_status':'speech_detected','processing_status':'completed',
       'timing_coverage':{'duration_ms':3000},'segments':[{'start_ms':0,'end_ms':1000,'text':'Привет, заходи','language':'ru','source':'srt'},
       {'start_ms':1000,'end_ms':2500,'text':'Ссылка в профиле','language':'ru','source':'srt'}],
       'languages':['ru'],'source':'srt','warnings':[]}
    p={'schema_version':'vision-prepare/v1','video_sha256':H,'video_source_version_id':V,
       'acquisition_job_id':A,'video_duration_ms':3000,'status':'completed',
       'scenes':[{'scene_id':'sc001','start_ms':0,'end_ms':1500,'boundary_kind':'detected_cut','frame_ids':['f001']},
                 {'scene_id':'sc002','start_ms':1500,'end_ms':3000,'boundary_kind':'detected_cut','frame_ids':['f002']}],
       'frames':[{'frame_id':'f001','scene_id':'sc001','time_ms':500,'sha256':'b'*64,'ocr':{'status':'ok','text':'Акция','start_ms':500,'end_ms':540}},
                 {'frame_id':'f002','scene_id':'sc002','time_ms':2000,'sha256':'c'*64,'ocr':{'status':'no_text_detected','text':'','start_ms':2000,'end_ms':2040}}],
       'warnings':[]}
    r={'schema_version':'visual-observations/v1','video_sha256':H,
       'video_source_version_id':V,'prepare_job_id':P,'status':'partial','scenes':[
           {'scene_id':'sc001','start_ms':0,'end_ms':1500,'frame_ids':['f001'],
            'visual_description':'Красный продукт на столе','observed_visible':['красная коробка'],
            'camera_editing':['крупный план'],'product_logo_visibility':'uncertain',
            'inferred_interpretation':'возможный хук','uncertainty':'Один кадр'}]}
    return {'acquisition':a,'project_id':'project-1','source_id':S,'source_version_id':V,
            'acquisition_job_id':A,'transcript':t,'transcript_job_id':T,'prepared':p,
            'prepare_job_id':P,'observations':r,'vision_job_id':R}

class TimelineFusion(unittest.TestCase):
    def test_full_join_with_exact_ocr_audio_frame_references(self):
        d=f.fuse(**inputs())
        self.assertEqual(d['schema_version'],'video-intelligence/v1')
        self.assertEqual(d['video_sha256'],H)
        self.assertTrue(d['review']['status']=='needs_review')
        self.assertEqual(d['coverage']['frames_sampled'],2)
        self.assertFalse(d['coverage']['all_frames_analyzed'])
        sample=[r for r in d['timeline'] if r['start_ms']==500][0]
        self.assertEqual(sample['on_screen_text'],'Акция')
        self.assertEqual(sample['spoken_text'],'Привет, заходи')
        self.assertEqual(sample['visual_description'],'Красный продукт на столе')
        self.assertEqual(sample['end_ms'],540)
        outside=next(x for x in d['timeline'] if x['start_ms']==540)
        self.assertIsNone(outside['visual_description'])
        self.assertIsNone(outside['on_screen_text'])
        self.assertEqual(d['timeline'][-1]['end_ms'],3000)
        for row in d['timeline']:
            for eid in row['speech_evidence_ids']+row['ocr_evidence_ids']+row['vision_evidence_ids']:
                self.assertIn(eid,d['evidence'])
        self.assertEqual(d['group_a_compatible_unpublished']['spoken_words'],5)
        self.assertIsNone(d['group_a_compatible_unpublished']['cta_start_sec'])
        self.assertEqual(d['creative_analysis']['narrative']['review_status'],'unclassified')
        self.assertIn('model_reported_unverified',str(d['evidence']))

    def test_deterministic_checksum(self):
        x=inputs();a=f.fuse(**x);b=f.fuse(**copy.deepcopy(x))
        self.assertEqual(f.sha(f.canonical(a)),f.sha(f.canonical(b)))
        self.assertEqual(f.canonical(a),f.canonical(b))

    def test_no_speech_with_ocr_only(self):
        x=inputs();x['transcript']['segments']=[];x['transcript']['speech_status']='no_speech'
        x['observations']=None;x['vision_job_id']=None
        d=f.fuse(**x)
        self.assertEqual(d['coverage']['speech_status'],'no_speech')
        self.assertEqual(d['group_a_compatible_unpublished']['spoken_words'],0)
        self.assertIsNone(d['creative_analysis']['content_format']['label'])
        self.assertEqual(d['ocr_segments'][0]['text'],'Акция')
        self.assertIsNone(d['timeline'][0]['spoken_text'])
        self.assertEqual(d['processing_status'],'partial')

    def test_unknown_speech_not_zero(self):
        x=inputs();x['transcript']['segments']=[];x['transcript']['speech_status']='unknown'
        x['observations']=None;x['vision_job_id']=None
        d=f.fuse(**x)
        self.assertIsNone(d['group_a_compatible_unpublished']['spoken_words'])
        self.assertIn('timed_speech_missing',d['warnings'])

    def test_transcript_only_with_duration(self):
        x=inputs();x['prepared']=None;x['prepare_job_id']=None;x['observations']=None;x['vision_job_id']=None
        d=f.fuse(**x)
        self.assertEqual(d['coverage']['frames_sampled'],0)
        self.assertIn('visual_preparation_missing',d['warnings'])
        self.assertEqual(d['timeline'][-1]['end_ms'],3000)

    def test_preparation_only_with_missing_duration_of_transcript(self):
        x=inputs();x['transcript']=None;x['transcript_job_id']=None;x['observations']=None;x['vision_job_id']=None
        d=f.fuse(**x)
        self.assertIn('transcript_missing',d['warnings'])
        self.assertIsNone(d['group_a_compatible_unpublished']['spoken_words'])
        self.assertEqual(d['coverage']['duration_ms'],3000)

    def test_deny_wrong_pins_and_hashes(self):
        for field,path in [('transcript',('video_sha256','d'*64)),('prepared',('video_sha256','d'*64)),
                           ('prepared',('video_source_version_id',S)),('observations',('prepare_job_id',A))]:
            x=inputs();x[field][path[0]]=path[1]
            with self.subTest(field=field,alter=path):
                with self.assertRaises(f.FusionProblem):f.fuse(**x)

    def test_deny_fabricated_frame_and_scene(self):
        x=inputs();x['observations']['scenes'][0]['frame_ids']=['f999']
        with self.assertRaises(f.FusionProblem):f.fuse(**x)
        x=inputs();x['prepared']['frames'][0]['scene_id']='sc999'
        with self.assertRaises(f.FusionProblem):f.fuse(**x)

    def test_reject_ocr_lifetime_inflation(self):
        x=inputs();x['prepared']['frames'][0]['ocr']['end_ms']=1500
        with self.assertRaises(f.FusionProblem):f.fuse(**x)

    def test_reject_out_of_order_speech_and_duration(self):
        x=inputs();x['transcript']['segments'][1]['start_ms']=800
        with self.assertRaises(f.FusionProblem):f.fuse(**x)
        x=inputs();x['transcript']['timing_coverage']['duration_ms']=6000
        with self.assertRaises(f.FusionProblem):f.fuse(**x)

    def test_no_modalities_is_explicit_unavailable(self):
        x=inputs();x['transcript']=None;x['transcript_job_id']=None;x['prepared']=None;x['prepare_job_id']=None;x['observations']=None;x['vision_job_id']=None
        with self.assertRaises(f.FusionProblem):f.fuse(**x)

    def test_model_inference_not_promoted_into_business_fact(self):
        d=f.fuse(**inputs())
        self.assertIsNone(d['creative_analysis']['narrative']['summary'])
        self.assertIsNone(d['group_b_candidates_unpublished']['topics'])
        self.assertFalse(d['review']['group_b_published'])
        self.assertNotIn('possible hook',d['group_b_candidates_unpublished']['hook'])

if __name__=='__main__':unittest.main()
