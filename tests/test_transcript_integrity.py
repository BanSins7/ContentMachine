"""Stage 07 source-version provenance checks; real downloads never started."""
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from intelligence.project_sources import transcript_jobs as jobs
from intelligence.project_sources.transcript_normalizer import TranscriptProblem

VID=str(uuid4())
SHA=hashlib.sha256(b'dummy-video').hexdigest()

class Provenance(unittest.TestCase):
    def fixture(self):
        manifest={'source_version_id':VID,'video_sha256':SHA,'byte_size':100,
                  'platform':'youtube','native_id':'AbCdEf12345',
                  'canonical_url':'https://www.youtube.com/shorts/AbCdEf12345',
                  'attachments':{'subtitles':{'path':'/allowed/subtitles.srt','sha256':'a'*64,'byte_size':10}}}
        actual={'video_sha256':SHA,'byte_size':100,'attachments':{'subtitles':dict(manifest['attachments']['subtitles'])}}
        adapter=SimpleNamespace(resolve_identity=lambda _:SimpleNamespace(native_id='AbCdEf12345'),collect_outputs=lambda _:actual)
        job={'video_source_version_id':VID,'manifest':manifest}
        return job,adapter,actual

    def test_accept_matching_provenance(self):
        job,adapter,actual=self.fixture()
        with patch.object(jobs,'adapter_for',return_value=adapter):
            self.assertEqual(jobs._match_manifest(job)[1],actual)

    def test_refuse_changed_video_hash(self):
        job,adapter,actual=self.fixture()
        actual['video_sha256']='b'*64
        with patch.object(jobs,'adapter_for',return_value=adapter), self.assertRaises(TranscriptProblem) as ex:
            jobs._match_manifest(job)
        self.assertEqual(ex.exception.code,'video_hash_mismatch')

    def test_refuse_changed_sidecar(self):
        job,adapter,actual=self.fixture()
        actual['attachments']['subtitles']['sha256']='c'*64
        with patch.object(jobs,'adapter_for',return_value=adapter), self.assertRaises(TranscriptProblem) as ex:
            jobs._match_manifest(job)
        self.assertEqual(ex.exception.code,'sidecar_changed')

    def test_refuse_legacy_manifest_of_other_source_version(self):
        job,adapter,_=self.fixture()
        job['video_source_version_id']=str(uuid4())
        with patch.object(jobs,'adapter_for',return_value=adapter), self.assertRaises(TranscriptProblem) as ex:
            jobs._match_manifest(job)
        self.assertEqual(ex.exception.code,'source_version_mismatch')

    def test_invalid_asr_options(self):
        for data in ({'mode':'openrouter'}, {'language':'de'}, {'mode':'reuse_sidecar','hidden':True}):
            with self.subTest(data=data), self.assertRaises(TranscriptProblem):
                jobs._settings(data)

if __name__=='__main__':unittest.main()
