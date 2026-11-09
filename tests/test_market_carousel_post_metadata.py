"""Bright Data carousel caption / hashtags / music metadata preservation.

Offline tests; do not call Bright Data or the LLM.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from intelligence.market_intelligence.post_metadata import extract_post_metadata
from intelligence.market_intelligence import carousel_studio as st

URL = "https://www.tiktok.com/@example/photo/7660142485791968544"


class TestPostMetadata(unittest.TestCase):
    def test_raw_description_hashtags_and_music_are_preserved(self):
        music = {
            "id": "67890123456789",
            "title": "Original music / Оригинал",
            "author": "Test creator",
            "extra": {"duration": 22, "available": True},
        }
        hashtags = [
            {"name": "fyp", "id": "123"},
            {"name": "дневник", "view_count": 450},
        ]
        record = {
            "url": URL, "play_count": 200, "digg_count": 20,
            "description": "Мой день 🌙  #fyp  #дневник\n", 
            "hashtags": hashtags, "music": music, "original_sound": False,
        }
        result = st._metrics(record)
        self.assertEqual(result["observed_post_metadata"], {
            "description": record["description"],
            "hashtags": hashtags,
            "music": music,
            "original_sound": False,
        })
        # Counters do not accidentally treat music IDs or hashtag IDs as stats.
        self.assertNotIn("music.id", result["observed_numeric_fields"])
        self.assertNotIn("hashtags", result["observed_numeric_fields"])
        self.assertEqual(result["observed_counters"]["views"], 200)
        self.assertEqual(json.loads(json.dumps(result,ensure_ascii=False))[
            "observed_post_metadata"]["description"],record["description"])

    def test_fallback_is_field_by_field_and_empty_is_not_missing(self):
        profile = {
            "description":"caption in profile",
            "hashtags":["#profile"],
            "music":{"title":"Example sound"},
            "original_sound":True,
        }
        detail = {
            "description":"",  # empty caption is an actual value
            "hashtags":["#detail"], "play_count":100
        }
        result = st._metrics(detail,profile_record=profile)
        self.assertEqual(result["observed_post_metadata"], {
            "description":"", "hashtags":["#detail"],
            "music":{"title":"Example sound"}, "original_sound":True,
        })
        self.assertEqual(extract_post_metadata({}, {}), {
            "description":None,"hashtags":None,"music":None,
            "original_sound":None,
        })
        self.assertEqual(extract_post_metadata(
            {"hashtags":"#one #two","music":"sound-name"})["hashtags"],
            "#one #two")

    def test_invalid_data_does_not_leak_into_results(self):
        data=extract_post_metadata({
            "description": 123,
            "hashtags": [object()],
            "music": {"value": float("nan")},
            "original_sound": object(),
        }, {"description":"fallback caption","music":{"title":"original"}})
        self.assertEqual(data, {
            "description":"fallback caption", "hashtags":None,
            "music":{"title":"original"}, "original_sound":None,
        })

    def test_profile_snapshot_caches_metadata_for_resume_and_export(self):
        with TemporaryDirectory() as tmp,patch.object(st,"ROOT",Path(tmp)):
            run="e"*32
            folder=Path(tmp)/run
            folder.mkdir()
            with st.db() as conn:
                conn.execute("""INSERT INTO studio_runs(
                    id,source_kind,source_input,state,phase,progress,created_at,
                    updated_at,output_dir)
                    VALUES(?, 'profile', ?, 'queued','',0,?,?,?)""",
                    (run,"https://www.tiktok.com/@example",st.utc(),st.utc(),str(folder)))
            raw={
                "url":URL,"author_username":"example",
                "play_count":250,"digg_count":25,
                "description":"Сохранённое описание",
                "hashtags":["#story","#art"],
                "music":{"title":"Звук из TikTok","id":"56"},
                "original_sound":True,
            }
            with patch.object(st,"_trigger_profile",return_value="s_123456789abc"),patch.object(
                st,"_bd_stream",return_value=iter([raw])):
                posts,scope=st._discover(run,"profile","https://www.tiktok.com/@example")
            self.assertEqual(len(posts),1)
            self.assertEqual(scope,"returned_profile_snapshot_only")
            with st.db() as conn:
                channel=json.loads(conn.execute(
                    "SELECT counts_json FROM studio_channel_posts WHERE run_id=?",
                    (run,)).fetchone()[0])
            self.assertEqual(channel["observed_post_metadata"]["description"],raw["description"])
            self.assertEqual(channel["observed_post_metadata"]["hashtags"],raw["hashtags"])
            restored=st._metrics(None,{"url":URL,"views":250},
                                 profile_record=channel["observed_post_metadata"])
            self.assertEqual(restored["observed_post_metadata"]["music"],raw["music"])
            self.assertEqual(restored["observed_counters"]["views"],250)


if __name__ == "__main__":
    unittest.main()
