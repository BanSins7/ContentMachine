"""Stage10 quote grounding: all tests local, no provider calls."""
import hashlib
import unittest
from intelligence.project_sources import evidence_normalizer as e

class Grounding(unittest.TestCase):
    def test_exact_quotes_and_spoofed_offsets(self):
        self.assertEqual(e.validate_quote('abcdef','bcd'),(1,4))
        self.assertEqual(e.validate_quote('abcdef','bcd',1,4),(1,4))
        for a,b in [(1,5),(2,4),(None,5)]:
            with self.subTest(a=a,b=b),self.assertRaises(e.EvidenceProblem):e.validate_quote('abcdef','bcd',a,b)
        with self.assertRaises(e.EvidenceProblem):e.validate_quote('abcdef','invented')

    def test_document_exact_segment_and_invalid_offset(self):
        obj={'schema_version':'document-segments/v1','parser_version':'doc/1','format':'.pdf','status':'completed',
             'segments':[{'text':'Факт, подтверждённый PDF','locator':{'page':2,'block':3,'start_char':0,'end_char':23}},
                         {'text':'wrong','locator':{'page':4,'start_char':0,'end_char':10}}]}
        obj['segments'][0]['locator']['end_char']=len(obj['segments'][0]['text'])
        rows,w=e.extract(obj,'research_document')
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['locator']['page'],2)
        self.assertIn('invalid_document_offsets',w)
        self.assertEqual(rows[0]['kind'],'external_claim')

    def test_web_model_reported_only_quote_not_inferred_claim(self):
        page='Our platform allows recipe saving and meal tracking.'
        good={'schema_version':'web-source-extract/v1','trust_level':'model_reported_unverified',
              'page_text':page,'page_url':'https://example.org/',
              'page_text_sha256':hashlib.sha256(page.encode()).hexdigest(),
              'claims':[{'claim':'Has AI cooking coach','quote':'recipe saving',
                         'quote_start':page.index('recipe saving'),'quote_end':page.index('recipe saving')+len('recipe saving')},
                        {'claim':'Invented feature','quote':'NOT FOUND','quote_start':1,'quote_end':10}], 'sections':[]}
        rows,w=e.extract(good,'competitor_website')
        self.assertEqual(rows[0]['text'],'recipe saving')
        self.assertEqual(rows[0]['trust'],'model_reported_unverified')
        self.assertEqual(rows[0]['scope'],'competitor_research')
        self.assertIn('unverified_web_quote',w)
        good['page_text']='adversarial replacement'
        with self.assertRaises(e.EvidenceProblem):e.extract(good,'product_website')

    def test_video_srt_timing_validation(self):
        src={'schema_version':'transcript/v1','video_sha256':'a'*64,'processing_status':'completed',
             'segments':[{'start_ms':100,'end_ms':400,'text':'hello','source':'srt'},
                         {'start_ms':200,'end_ms':300,'text':'fake overlap','source':'asr'}]}
        rows,w=e.extract(src,'user_video_reference')
        self.assertEqual(len(rows),1);self.assertIn('invalid_speech_time',w)
        self.assertEqual(rows[0]['scope'],'creative_reference')

    def test_ocr_frame_point_not_full_scene(self):
        data={'schema_version':'vision-prepare/v1','video_sha256':'a'*64,
              'frames':[{'frame_id':'f001','sha256':'b'*64,'time_ms':3000,
                         'ocr':{'status':'ok','text':'SALE','start_ms':3000,'end_ms':3040}}]}
        rows,w=e.extract(data,'user_video_reference')
        self.assertEqual(rows[0]['locator']['end_ms'],3040)
        data['frames'][0]['ocr']['end_ms']=5000
        self.assertEqual(e.extract(data,'user_video_reference')[0],[])

    def test_vision_requires_prep_and_frame_time(self):
        prep={'schema_version':'vision-prepare/v1','video_sha256':'a'*64,'video_duration_ms':10000,
              'frames':[{'frame_id':'f001','sha256':'b'*64,'scene_id':'sc001','time_ms':1500}],
              'scenes':[{'scene_id':'sc001','start_ms':0,'end_ms':10000,'frame_ids':['f001']}]}
        model={'schema_version':'visual-observations/v1','video_sha256':'a'*64,
               'scenes':[{'scene_id':'sc001','start_ms':0,'end_ms':10000,
                          'frame_ids':['f001'],'visual_description':'Person holds bag'}]}
        with self.assertRaises(e.EvidenceProblem):e.extract(model,'user_video_reference')
        rows,w=e.extract(model,'user_video_reference',prepared=prep)
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['locator']['start_ms'],rows[0]['locator']['end_ms']),(1500,1540))
        self.assertEqual(rows[0]['review_status'],'needs_review')
        model['scenes'][0]['frame_ids']=['f009']
        self.assertEqual(e.extract(model,'user_video_reference',prepared=prep)[0],[])

    def test_fusion_evidence_checks_against_segment(self):
        row={'schema_version':'video-intelligence/v1','evidence':{'speech:1':{
             'kind':'asr_segment','exact_text':'spoken words','locator':{'start_ms':0,'end_ms':1000}}},
             'transcript':{'segments':[{'evidence_id':'speech:1','text':'spoken words'}]},
             'ocr_segments':[],'visual_observations':[]}
        rows,w=e.extract(row,'user_video_reference');self.assertEqual(len(rows),1)
        row['evidence']['speech:1']['exact_text']='hallucination'
        self.assertEqual(e.extract(row,'user_video_reference')[0],[])

    def test_profile_and_numeric_types(self):
        row=e.profile_candidate('15%','product.price_growth',3,'f'*64)
        self.assertEqual(row[0]['typed_value']['type'],'percentage')
        self.assertEqual(row[0]['locator']['profile_revision'],3)
        self.assertEqual(e.typed_value('2026-02-30'),None)
        self.assertEqual(e.typed_value('0 USD'),{'type':'measurement','value':'0','unit':'USD'})
        self.assertIsNone(e.typed_value('not available'))
        self.assertIsNone(e.typed_value('100 views per week'))

    def test_exact_duplicate_fingerprint_independent_citations(self):
        self.assertEqual(e.fingerprint(' A  B '),e.fingerprint('a b'))
        self.assertNotEqual(e.fingerprint('a  b'),e.fingerprint('a c'))
        self.assertEqual(e.scope_for('competitor_website'),'competitor_research')
        self.assertEqual(e.scope_for('user_video_reference'),'creative_reference')

    def test_near_duplicate_not_semantic_automatic(self):
        a='This is the product that helps people plan their meals'
        b='This is the product that helps people plan the meals'
        self.assertGreaterEqual(e.lexical_similarity(a,b),0.87)
        self.assertEqual(e.lexical_similarity('Views 300 in 2025','Views 900 in 2025'),0)

if __name__=='__main__':unittest.main()
