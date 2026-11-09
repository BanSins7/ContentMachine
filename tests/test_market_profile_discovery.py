"""Profile seed discovery: offline, fully mocked; no network or database."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from intelligence.market_intelligence.profile_discovery import (
    ListingUnavailable, canonical_profile, parse_listing, enumerate_profile, MAX_POSTS
)
from intelligence.market_intelligence.http_api import handle_get
from intelligence.market_intelligence.profile_jobs import JOB_TYPE, MAX_POSTS as JOB_MAX


class ProfileDiscoveryTests(unittest.TestCase):
    def test_tiktok_real_ids_are_deduplicated(self):
        url="https://www.tiktok.com/@creator"
        one="https://www.tiktok.com/@creator/video/1234567890123456789"
        result=parse_listing("tiktok",url,{"entries":[
            {"id":"1234567890123456789","webpage_url":one},
            {"id":"1234567890123456789","webpage_url":one},
            {"id":"unsafe"},
            {"id":"1234567890123456788","webpage_url":"https://www.tiktok.com/@creator/video/1234567890123456788"},
        ]})
        self.assertEqual(len(result["urls"]),2)
        self.assertEqual(result["urls"][0],one)
        self.assertFalse(result["capped"])

    def test_instagram_never_fabricates_invalid_reels(self):
        profile="https://www.instagram.com/creator"
        result=parse_listing("instagram",profile,{"entries":[
            {"webpage_url":"https://www.instagram.com/reel/AbCdEfGhI/"},
            {"webpage_url":"https://evil.com/reel/AbCdEfGhI/"},
            {"id":"1234567890"},
        ]})
        self.assertEqual(result["urls"],["https://www.instagram.com/reel/AbCdEfGhI/"])

    def test_youtube_channel_url(self):
        platform,profile=canonical_profile("https://www.youtube.com/@channel")
        self.assertEqual((platform,profile),("youtube","https://www.youtube.com/@channel"))
        result=parse_listing("youtube",profile,{"entries":[{"id":"AbCdEfGhI12"},
          {"webpage_url":"https://www.youtube.com/watch?v=AbCdEfGhI13"}]})
        self.assertEqual(len(result["urls"]),2)

    def test_missing_entries_and_closed_profiles_fail(self):
        for record in [None,{},{"entries":None},{"entries":[]},{"entries":[{}]}]:
            with self.subTest(record=record):
                with self.assertRaises(ListingUnavailable):
                    parse_listing("tiktok","https://www.tiktok.com/@author",record)

    def test_bad_source_does_not_trigger_fetch(self):
        with patch.dict("sys.modules",{"yt_dlp":SimpleNamespace(YoutubeDL=None)}):
            with self.assertRaises(ListingUnavailable):
                enumerate_profile("http://127.0.0.1/internal")
        with self.assertRaises(ListingUnavailable):
            parse_listing("youtube","https://www.youtube.com/@channel",{"entries":[]},limit=0)

    def test_yt_dlp_is_called_once_and_not_downloading(self):
        class Y:
            def __init__(self,opts): self.options=opts
            def __enter__(self): return self
            def __exit__(self,*a): return None
            def extract_info(self,url,download=False):
                self.assertion_url=url
                assert url=="https://www.youtube.com/@demo/shorts"
                assert download is False
                return {"entries":[{"id":"AbCdEfGhI12"}]}
        with patch.dict("sys.modules",{"yt_dlp":SimpleNamespace(YoutubeDL=Y)}):
            answer=enumerate_profile("https://www.youtube.com/@demo")
        self.assertEqual(len(answer["urls"]),1)
        self.assertEqual(answer["completeness"],"unknown_platform_may_limit_listing")

    @patch("intelligence.market_intelligence.http_api.MarketIntelligenceService")
    def test_author_status_uses_scoped_project(self, service):
        service.return_value.authors_status.return_value={"ok":True,"jobs":[]}
        handler=SimpleNamespace(responses=[],json_response=lambda code, body:handler.responses.append((code,body)))
        url=SimpleNamespace(path="/api/projects/"+("b"*32)+"/market-intelligence/authors",query="")
        self.assertTrue(handle_get(handler,url))
        self.assertEqual(handler.responses[0][0],200)
        service.return_value.authors_status.assert_called_once_with("b"*32)


if __name__=="__main__":
    unittest.main()
