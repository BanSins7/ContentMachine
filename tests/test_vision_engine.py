"""Stage 08 deterministic local vision and strict model output tests; no paid calls."""
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from intelligence.project_sources import vision_engine as v

class VisionEngine(unittest.TestCase):
    def fixture(self):
        import imageio_ffmpeg
        f=tempfile.TemporaryDirectory();self.addCleanup(f.cleanup)
        p=Path(f.name)/'movie.mp4'
        cmd=[imageio_ffmpeg.get_ffmpeg_exe(),'-y','-loglevel','error','-f','lavfi','-i','color=red:s=320x240:d=2',
             '-f','lavfi','-i','color=blue:s=320x240:d=2','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]',
             '-map','[v]','-c:v','mpeg4','-pix_fmt','yuv420p',str(p)]
        run=subprocess.run(cmd,capture_output=True,timeout=30)
        self.assertEqual(run.returncode,0,run.stderr[:1000]);return p

    def test_probe_sampler_scene_and_no_audio(self):
        p=self.fixture();meta=v.probe(p)
        self.assertGreater(meta['duration_ms'],3000);self.assertFalse(meta['audio_present'])
        self.assertGreater(meta['width'],0)
        cuts=v.scene_cuts(p,meta['duration_ms'])
        self.assertIsInstance(cuts,list)
        samples=v.sampling(meta['duration_ms'],cuts,v.settings({'max_frames':5}))
        self.assertTrue(1<=len(samples)<=5);self.assertEqual(samples,sorted(samples))
        jpg=v.frame_jpeg(p,samples[0]);self.assertTrue(jpg.startswith(b'\xff\xd8'))
        self.assertEqual(jpg[-2:],b'\xff\xd9')
        stored={}
        payload=v.prepare(p,{'max_frames':5,'ocr':False},lambda fid,jpeg:stored.setdefault(fid,jpeg) and 'vision_assets/'+fid+'.jpg')
        self.assertGreater(len(payload['frames']),0)
        self.assertTrue(all(x['time_precision']=='sample_only' for x in payload['frames']))
        self.assertFalse(payload['frame_sampling']['all_frames_analyzed'])
        self.assertEqual(payload['probe']['audio_present'],False)

    def test_config_sampling_edges(self):
        for invalid in [{'max_frames':0},{'max_frames':9},{'max_frames':True},{'ocr':1},{'unknown':3}]:
            with self.assertRaises(v.VisionProblem):v.settings(invalid)
        for ms in (200,1000,1800,15000,900000):
            picks=v.sampling(ms,[],v.settings({'max_frames':8}))
            self.assertTrue(all(0<=x<ms for x in picks))
            self.assertLessEqual(len(picks),8)
        samples=v.sampling(900000,[30000,40000,90000,100000],v.settings({}))
        self.assertLessEqual(len(samples),8)

    def test_bad_frame_partial_and_no_frames(self):
        def bad(i,t):
            if t>=1000:raise v.VisionProblem('frame error','frame_failed')
            return b'\xff\xd8' + b'A'*22 + b'\xff\xd9'
        fake=lambda _: {'duration_ms':5000,'audio_present':False,'width':320,'height':200,'codec':'h264'}
        ocr=lambda x:{'status':'no_text_detected','text':'','engine':'tesseract','error':None}
        prepared=v.prepare('movie',{'max_frames':4},lambda *_:'safe-relative',prober=fake,cut_finder=lambda *_:[],extractor=bad,ocr=ocr)
        self.assertEqual(prepared['status'],'partial')
        self.assertIn('frame_failed',prepared['warnings'])
        with self.assertRaises(v.VisionProblem):
            v.prepare('movie',{},lambda *_:'safe',prober=fake,cut_finder=lambda *_:[],
                      extractor=lambda *_:(_ for _ in ()).throw(v.VisionProblem('fail')),ocr=ocr)

    def sample(self):
        return {'scenes':[{'scene_id':'sc001','start_ms':0,'end_ms':1500,'frame_ids':['f001']},
                          {'scene_id':'sc002','start_ms':1500,'end_ms':2500,'frame_ids':['f002']}],
                'frames':[{'frame_id':'f001','time_ms':500,'sha256':v.sha(b'jpeg1')},
                          {'frame_id':'f002','time_ms':1700,'sha256':v.sha(b'jpeg2')}],
                'frame_sampling':{'all_frames_analyzed':False},'video_duration_ms':2500}

    def observation(self):
        return {'scene_id':'sc001','start_ms':0,'end_ms':1500,'frame_ids':['f001'],
                'visual_description':'Человек перед столом','observed_visible':['Стол виден'],
                'actions':['Рука движется'],'objects':['Стол'],
                'people_observations':['Фигура человека без идентификации'],
                'product_logo_visibility':'uncertain','camera_editing':['Статичная камера'],
                'inferred_interpretation':None,'uncertainty':'Не видно текста'}

    def test_vision_validation_rejects_fabricated_evidence(self):
        prepared=self.sample();answer={'schema_version':v.OBS_VERSION,'scenes':[self.observation()], 'limitations':['Выборка кадров']}
        valid=v.validate_observations(answer,prepared)
        self.assertEqual(valid['status'],'partial');self.assertEqual(valid['trust_level'],'model_reported_unverified')
        for change in ({'frame_ids':['f099']},{'scene_id':'sc099'},{'start_ms':4},
                       {'product_logo_visibility':'always'},{'observed_visible':['']},
                       {'confidence':0.99}):
            bad={**self.observation(),**change}
            with self.assertRaises(v.VisionProblem):v.validate_observations({**answer,'scenes':[bad]},prepared)

    def test_images_in_request_and_mock_provider(self):
        prepared=self.sample();frames=[('f001',b'jpeg1'),('f002',b'jpeg2')]
        payload=v.provider_payload(prepared,frames)
        self.assertEqual(payload['model'],v.MODEL)
        self.assertEqual(sum(x['type']=='image_url' for x in payload['messages'][0]['content']),2)
        answer={'schema_version':v.OBS_VERSION,'scenes':[self.observation()],'limitations':['Не все кадры изучены']}
        data={'id':'resp123','choices':[{'message':{'content':json.dumps(answer,ensure_ascii=False)}}],
              'usage':{'cost':0.003,'prompt_tokens':100,'completion_tokens':50}}
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,_):return json.dumps(data).encode()
        calls=[]
        def opener(req,timeout):
            calls.append((req.full_url,req.get_header('Authorization'),timeout));return Response()
        result=v.openrouter_vision(prepared,frames,'test-only-key',opener=opener)
        self.assertEqual(result['observations']['status'],'partial')
        self.assertEqual(result['usage']['cost_usd'],0.003)
        self.assertEqual(len(calls),1)
        with self.assertRaises(v.VisionProblem):v.provider_payload(prepared,[('f001',b'WRONG')])

    def test_ocr_unknown_and_empty(self):
        o=v.ocr_jpeg(b'not_an_image',tesseract=lambda image:'OCR text')
        self.assertEqual(o['status'],'failed')

if __name__=='__main__':unittest.main()
