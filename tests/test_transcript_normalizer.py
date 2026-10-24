"""Stage07 deterministic transcript QA, no paid APIs or downloads."""
import hashlib
import unittest
from intelligence.project_sources.transcript_normalizer import (TranscriptProblem, parse_srt,normalize,language)

SHA=hashlib.sha256(b'mp4').hexdigest()
def manifest(**meta):
    return {'schema_version':'video-acquisition/v1','platform':'youtube','native_id':'AbCdEf12345',
            'video_sha256':SHA,'metadata':meta}

class TranscriptNormalizer(unittest.TestCase):
    def test_valid_multiline_srt_preserves_ms_and_cyrillic(self):
        s=b'1\r\n00:00:01,020 --> 00:00:02,250\r\n\xd0\x9f\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82\r\n\r\n2\r\n00:00:03,005 --> 00:00:04,100\r\nEnglish\r\ncontinuation\r\n'
        p=normalize(manifest(language='ru',duration=10,subtitle_engine='local_whisper'),srt=s)
        self.assertEqual(p['processing_status'],'completed')
        self.assertEqual([(s['start_ms'],s['end_ms']) for s in p['segments']],[(1020,2250),(3005,4100)])
        self.assertEqual(p['segments'][0]['text'],'Привет')
        self.assertEqual(p['segments'][1]['text'],'English\ncontinuation')
        self.assertEqual(p['timing_coverage']['speech_timed_ms'],2325)
        self.assertEqual(p['timing_coverage']['fraction_of_video'],0.2325)

    def test_partial_srt_invalid_blocks_overlap_and_bounds(self):
        src=('1\n00:00:00,000 --> 00:00:01,000\nfirst\n\n'
             '2\n00:00:00,800 --> 00:00:01,200\noverlap\n\n'
             '3\n00:00:02,000 --> 00:00:03,000\nout of video\n\n'
             'garbage\nnot a cue\n').encode()
        p=normalize(manifest(duration=1.5),srt=src)
        self.assertEqual(p['segment_count'],1)
        self.assertEqual(p['processing_status'],'partial')
        self.assertIn('overlap_or_nonmonotonic',p['warnings'])
        self.assertIn('beyond_duration',p['warnings'])
        self.assertIn('invalid_cue',p['warnings'])

    def test_txt_only_never_assigns_timing(self):
        p=normalize(manifest(language='auto'),txt='Только текст'.encode())
        self.assertEqual(p['segments'],[])
        self.assertEqual(p['speech_status'],'unknown')
        self.assertEqual(p['processing_status'],'unavailable')
        self.assertIn('untimed_text_only',p['warnings'])
        self.assertIsNone(p['timing_coverage']['fraction_of_video'])

    def test_explicit_no_speech_and_failed_asr_are_different(self):
        a=normalize(manifest(subtitle_status='no_speech'),srt=b'',txt=b'')
        b=normalize(manifest(subtitle_status='error'),srt=b'',txt=b'')
        self.assertEqual(a['speech_status'],'no_speech');self.assertEqual(a['processing_status'],'completed')
        self.assertEqual(b['speech_status'],'asr_error');self.assertEqual(b['processing_status'],'unavailable')

    def test_srt_conflicts_with_no_speech_is_partial(self):
        p=normalize(manifest(subtitle_status='no_speech'),srt=b'1\n00:00:00,100 --> 00:00:01,000\nvoice\n')
        self.assertEqual(p['speech_status'],'speech_detected');self.assertIn('metadata_conflicts_speech',p['warnings'])

    def test_multiple_languages_for_actual_asr_cues(self):
        p=normalize(manifest(duration=3),asr_segments=[
            {'start_ms':0,'end_ms':1000,'text':'Привет','language':'ru','confidence':0.89},
            {'start_ms':1000,'end_ms':2000,'text':'Hello','language':'en'}],detected_language='auto')
        self.assertEqual(p['languages'],['en','ru'])
        self.assertEqual(p['segments'][0]['confidence'],0.89)
        self.assertNotIn('confidence',p['segments'][1])
        self.assertEqual(p['segments'][1]['source'],'asr')

    def test_srt_txt_mismatch_marked_partial_not_reconciled(self):
        p=normalize(manifest(),srt=b'1\n00:00:00,000 --> 00:00:01,000\nhello\n',txt=b'different')
        self.assertEqual(p['processing_status'],'partial')
        self.assertEqual(p['source_text']['txt'],'different')
        self.assertIn('srt_txt_mismatch',p['warnings'])

    def test_unknown_language_not_guessed(self):
        self.assertEqual(language('auto'),'unknown')
        self.assertEqual(language('English'),'unknown')
        self.assertEqual(language('ru'),'ru')
        self.assertEqual(language('en-US'),'en-us')
        p=normalize(manifest(language='auto'),srt=b'1\n00:00:00,100 --> 00:00:01,000\nEnglish\n')
        self.assertEqual(p['segments'][0]['language'],'unknown')

    def test_invalid_confidence_rejected_not_fabricated(self):
        p=normalize(manifest(),asr_segments=[{'start_ms':0,'end_ms':100,'text':'voice','confidence':float('nan')}])
        self.assertEqual(p['segments'],[]);self.assertIn('invalid_confidence',p['warnings'])

    def test_invalid_source_audio_only_no_fake_segments(self):
        for srt in [b'not a subtitle',b'1\n00:00:04,000 --> 00:00:02,000\nbackwards\n',b'1\n00:65:00,000 --> 00:00:01,000\nbad\n']:
            with self.subTest(srt=srt):
                p=normalize(manifest(),srt=srt)
                self.assertEqual(p['segment_count'],0);self.assertEqual(p['speech_status'],'unknown')

    def test_cp1251_subtitles_and_no_duration_coverage(self):
        s='1\n00:00:00,010 --> 00:00:00,950\nПривет мир\n'.encode('cp1251')
        output=normalize(manifest(),srt=s)
        self.assertEqual(output['segments'][0]['text'],'Привет мир')
        self.assertIsNone(output['timing_coverage']['fraction_of_video'])

    def test_new_asr_does_not_trust_old_speech_error_flag(self):
        output=normalize(manifest(subtitle_status='error'),asr_segments=[
          {'start_ms':0,'end_ms':120,'text':'recovered','language':'en'}])
        self.assertEqual(output['speech_status'],'speech_detected')
        self.assertEqual(output['processing_status'],'completed')

    def test_reject_binary_srt_and_oversize(self):
        with self.assertRaises(TranscriptProblem):parse_srt(b'\x00\x00junk')
        with self.assertRaises(TranscriptProblem):parse_srt(b'x'*(16*1024*1024+1))

    def test_srt_html_style_removed_from_timed_text(self):
        p=normalize(manifest(),srt=b'1\n00:00:00.001 --> 00:00:00.900\n<i>Hello</i>\n')
        self.assertEqual(p['segments'][0]['text'],'Hello')

if __name__=='__main__':unittest.main()
