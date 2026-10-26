"""No network or paid calls: Stage 02 URL and questionnaire parsing."""
import unittest
from intelligence.project_sources.normalize import ReferenceError, normalize_url, parse_legacy

class TestReferenceNormalize(unittest.TestCase):
    def test_video_id_dedup_across_youtube_urls(self):
        one=normalize_url('https://youtu.be/AbCdEf12345?t=3&utm_medium=email','user_video_reference')
        two=normalize_url('https://www.youtube.com/shorts/AbCdEf12345?feature=share','user_video_reference')
        self.assertEqual(one.identity_key,two.identity_key)
        self.assertEqual(one.canonical_url,'https://www.youtube.com/watch?v=AbCdEf12345')
        self.assertEqual(one.link_kind,'video')

    def test_tiktok_and_instagram_identity(self):
        a=normalize_url('https://www.tiktok.com/@first/video/7371234567890123456','user_video_reference')
        b=normalize_url('https://www.tiktok.com/@second/video/7371234567890123456?lang=en','user_video_reference')
        self.assertEqual(a.identity_key,b.identity_key)
        self.assertEqual(a.platform_native_id if hasattr(a,'platform_native_id') else a.native_id,'7371234567890123456')
        c=normalize_url('https://instagram.com/reels/Cz4Afgh5Z/?utm_source=x','user_video_reference')
        d=normalize_url('https://www.instagram.com/p/Cz4Afgh5Z/','user_video_reference')
        self.assertEqual(c.identity_key,d.identity_key)

    def test_profiles_are_not_videos(self):
        for u in ('https://www.youtube.com/@creator','https://www.tiktok.com/@creator','https://instagram.com/creator/'):
            self.assertEqual(normalize_url(u,'competitor_account').link_kind,'account')
            with self.assertRaises(ReferenceError): normalize_url(u,'user_video_reference')
        with self.assertRaises(ReferenceError):
            normalize_url('https://www.tiktok.com/@who/video/7371234567890123456','competitor_account')

    def test_shortlinks_defer_redirect(self):
        p=normalize_url('https://vm.tiktok.com/ZMabc123/','user_video_reference')
        self.assertEqual(p.link_kind,'shortlink')
        self.assertIsNone(p.native_id)
        self.assertEqual(p.ingestion_mode,'video_reference')

    def test_web_ai_and_tracking(self):
        s=normalize_url('https://Example.com/product?q=one&utm_campaign=x&lang=ru#pricing','product_website')
        self.assertEqual(s.canonical_url,'https://example.com/product?lang=ru&q=one')
        self.assertEqual(s.ingestion_mode,'web_ai')

    def test_reject_nonpublic_credentials_malformed(self):
        for url in ('file:///etc/passwd','https://localhost/a','https://127.0.0.1/a',
                    'https://foo.local/x','https://user:pass@example.com/a',
                    'https://example.com:9999/x','not-a-url','https://[::1]/'):
            with self.subTest(url=url),self.assertRaises(ReferenceError):
                normalize_url(url,'research_link')

    def test_legacy_provenance_multiple_and_unknown(self):
        data={
            'competitors':'Alpha — https://competitor.com\nOnly brand name',
            'competitor_socials':'TikTok: https://tiktok.com/@brand',
            'video_references':'https://youtu.be/AbCdEf12345 — хороший хук',
            'product_url':'https://myapp.com https://myapp.com/features',
            'promoter_accounts':'https://instagram.com/promoter/'
        }
        proposals,warnings=parse_legacy(data)
        self.assertEqual(len(proposals),6)
        self.assertEqual(len(warnings),1)
        self.assertEqual(proposals[0]['origin_field'],'product_url')
        self.assertEqual(proposals[0]['original_input'],data['product_url'])
        self.assertEqual(proposals[4]['origin_field'],'promoter_accounts')
        self.assertEqual(proposals[5]['origin_field'],'video_references')

    def test_deterministic_import_keys(self):
        p={'video_references':'https://youtu.be/AbCdEf12345'}
        first,_=parse_legacy(p)
        second,_=parse_legacy({'video_references': 'https://www.youtube.com/shorts/AbCdEf12345'})
        self.assertEqual(first[0]['legacy_import_key'],second[0]['legacy_import_key'])

if __name__=='__main__': unittest.main()
