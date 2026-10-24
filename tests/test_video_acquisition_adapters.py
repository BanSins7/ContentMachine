"""Offline adapter verification using fake MP4/metadata; never call yt-dlp."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from intelligence.project_sources.video_adapters import (
    VideoIdentity, AcquisitionProblem, TikTokAdapter, YouTubeAdapter,InstagramAdapter)

VIDEO=b'\x00\x00\x00\x18ftypisom'+b'\x00'*100


class Adapters(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def store(self,sub,mp4name,metaname,meta):
        directory=self.root/sub;directory.mkdir(parents=True,exist_ok=True)
        p=directory/mp4name;m=directory/metaname
        p.write_bytes(VIDEO)
        m.write_text(json.dumps({**meta,'video_sha256':hashlib.sha256(VIDEO).hexdigest()}))
        return p,m
    def test_identity_validation_all_three_platforms(self):
        cases=[(TikTokAdapter(),'https://www.tiktok.com/@u123/video/7234567890123456789','7234567890123456789'),
               (YouTubeAdapter(),'https://youtu.be/AbCdEf12345','AbCdEf12345'),
               (InstagramAdapter(),'https://www.instagram.com/reel/ABCd123/','ABCd123')]
        for a,u,expect in cases:
            with self.subTest(platform=a.platform):self.assertEqual(a.resolve_identity(u).native_id,expect)
        for a,u in [(TikTokAdapter(),'https://vm.tiktok.com/ABCDEF/'),
                    (YouTubeAdapter(),'https://youtube.com/@channel'),
                    (InstagramAdapter(),'https://instagram.com/user/')]:
            with self.assertRaises(AcquisitionProblem):a.resolve_identity(u)
    def test_metadata_hash_path_checks_and_optional_sidecars(self):
        a=YouTubeAdapter();url='https://www.youtube.com/shorts/AbCdEf12345'
        ident=a.resolve_identity(url)
        mp4,meta=self.store('single/AbCdEf12345','video.mp4','metadata.json',{'id':'AbCdEf12345','source_url':url})
        mp4.with_name('subtitles.srt').write_bytes(b'')
        mp4.with_name('transcript.txt').write_text('Hello',encoding='utf-8')
        result=a.verify_asset(ident,self.root,mp4,meta)
        self.assertEqual(result['video_sha256'],hashlib.sha256(VIDEO).hexdigest())
        self.assertEqual(result['attachments']['subtitles']['byte_size'],0)
        self.assertIn('transcript',result['attachments'])
        self.assertEqual(result['attachments']['transcript']['byte_size'],5)
        obj=json.loads(meta.read_text());obj['id']='WrongId0001';meta.write_text(json.dumps(obj))
        with self.assertRaisesRegex(AcquisitionProblem,'другому видео'):
            a.verify_asset(ident,self.root,mp4,meta)
    def test_wrong_hash_traversal_and_symlinks(self):
        a=TikTokAdapter();url='https://www.tiktok.com/@u123/video/7234567890123456789';i=a.resolve_identity(url)
        mp4,meta=self.store('u123','7234567890123456789.mp4','7234567890123456789.json',
                            {'shortcode':i.native_id,'source_url':url})
        mp4.write_bytes(VIDEO+b'changed')
        with self.assertRaisesRegex(AcquisitionProblem,'SHA-256'):
            a.verify_asset(i,self.root,mp4,meta)
        mp4.unlink();mp4.symlink_to(meta)
        with self.assertRaises(AcquisitionProblem):a.verify_asset(i,self.root,mp4,meta)
    def test_instagram_requires_exact_shortcode_metadata(self):
        a=InstagramAdapter();i=a.resolve_identity('https://www.instagram.com/reel/ABCd123/')
        mp4,meta=self.store('instagram','ABCd123.mp4','ABCd123.json',
                            {'verified_shortcode':'ABCd123','shortcode':'ABCd123','source_url':i.canonical_url})
        self.assertEqual(a.verify_asset(i,self.root,mp4,meta)['native_id'],'ABCd123')
        obj=json.loads(meta.read_text());obj['verified_shortcode']='ZZZZZ99';meta.write_text(json.dumps(obj))
        with self.assertRaises(AcquisitionProblem):a.verify_asset(i,self.root,mp4,meta)
    def test_existing_worker_functions_are_reused(self):
        from worker import tiktok_worker as t,youtube_worker as y,local_worker as w
        a=TikTokAdapter();i=a.resolve_identity('https://www.tiktok.com/@u123/video/7234567890123456789')
        with patch.object(t,'download_job') as run:a.request_download('job',i);run.assert_called_once()
        a=YouTubeAdapter();i=a.resolve_identity('https://www.youtube.com/shorts/AbCdEf12345')
        with patch.object(y,'download_job') as run:a.request_download('job',i);run.assert_called_once()
        a=InstagramAdapter();i=a.resolve_identity('https://www.instagram.com/reel/ABCd123/')
        with patch.object(w,'exact_download_job') as run:a.request_download('job',i);run.assert_called_once()

if __name__=='__main__':unittest.main()
