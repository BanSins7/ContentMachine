"""Group A PostgreSQL schema: repeatable versioned measurements + timestamp timelines.

No video parsing, remote APIs, test downloads, or source rights elevation.
"""
from __future__ import annotations

import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from intelligence.migrate import apply_migrations

SCHEMA = ROOT / "intelligence" / "migrations" / "003_video_features_a.sql"


class VideoFeatureAStaticSchema(unittest.TestCase):
    def test_additive_schema_has_expected_contract_without_removed_features(self):
        sql = SCHEMA.read_text(encoding="utf-8").lower()
        for name in (
            "video_feature_a_analyses", "video_feature_a_scenes",
            "video_feature_a_speech_segments", "video_feature_a_screen_text",
            "video_feature_a_audio_events", "video_feature_a_summary",
            "source_id text", "source_policy_version_id", "analysis_version",
            "has_subtitles", "speech_duration_sec", "cta_start_sec",
            "measurement_methods", "unavailable_reasons",
        ):
            self.assertIn(name, sql)
        for unwanted in ("avg_scene_sec", "width_px", "height_px", "resolution_px", " fps "):
            # File comments mention the omitted fields, so check SQL column lines.
            self.assertNotRegex(sql, r"(?m)^\s*" + unwanted.strip() + r"\s+")
        for destructive in ("drop table", "truncate ", "delete from videos", "alter table videos"):
            self.assertNotIn(destructive, sql)


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "PostgreSQL CI DSN not provided")
class VideoFeatureAPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.conn = psycopg.connect(os.environ["CM_TEST_PG_DSN"], autocommit=True)
        apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.source = "a-schema-" + uuid4().hex
        self.conn.execute(
            """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
               VALUES (%s,'fixture','owned','enabled',TRUE)""",
            (self.source,),
        )
        self.videos = []

    def tearDown(self):
        for video_id in self.videos:
            self.conn.execute("DELETE FROM videos WHERE id=%s", (video_id,))
        self.conn.execute("DELETE FROM sources WHERE id=%s", (self.source,))

    def video(self):
        key = uuid4().hex
        row = self.conn.execute(
            """INSERT INTO videos(platform,platform_video_id,canonical_url)
               VALUES('youtube',%s,%s) RETURNING id""",
            (key,"https://www.youtube.com/watch?v="+key),
        ).fetchone()
        self.videos.append(row[0])
        return row[0]

    def analysis(self, video_id, **fields):
        cols = ["video_id", "source_id", "analysis_key", "analysis_version", "method_name"]
        values = [video_id, self.source, uuid4().hex, "A1", "fixture"]
        for key, value in fields.items():
            cols.append(key)
            values.append(value)
        sql = "INSERT INTO video_feature_a_analyses (" + ",".join(cols) + ") VALUES (" + ",".join(["%s"]*len(cols)) + ") RETURNING id"
        return self.conn.execute(sql, tuple(values)).fetchone()[0]

    def test_migrations_are_reentrant_and_existing_video_rows_unchanged(self):
        video = self.video()
        self.assertEqual(apply_migrations(self.conn), [])
        row = self.conn.execute(
            "SELECT platform,platform_video_id FROM videos WHERE id=%s",(video,)
        ).fetchone()
        self.assertEqual(row[0],"youtube")

    def test_pending_without_measurements_does_not_invent_zeros(self):
        ident = self.analysis(self.video())
        record = self.conn.execute(
            """SELECT scene_count,first_text_sec,first_speech_sec,speech_wpm,
                      speech_coverage_pct,has_subtitles
                 FROM video_feature_a_summary WHERE analysis_id=%s""",(ident,)
        ).fetchone()
        self.assertEqual(record,(None,None,None,None,None,"unknown"))

    def test_timeline_and_measured_derived_fields(self):
        ident = self.analysis(
            self.video(), duration_sec="40", aspect_ratio="9:16",
            spoken_words=60, speech_duration_sec="30",
            has_subtitles="true", cta_start_sec="36.05",
            scene_status="complete",speech_status="complete",
            screen_text_status="complete",audio_status="complete",
            state="completed",
        )
        for n, start, end in [(1,0,2),(2,2,8),(3,8,24),(4,24,40)]:
            self.conn.execute(
                """INSERT INTO video_feature_a_scenes(analysis_id,scene_index,start_sec,end_sec)
                   VALUES (%s,%s,%s,%s)""",(ident,n,start,end))
        self.conn.execute(
            """INSERT INTO video_feature_a_speech_segments
               (analysis_id,start_sec,end_sec,spoken_text) VALUES
               (%s,0.7,9,'example words'),(%s,12,38,'later words')""",(ident,ident))
        self.conn.execute(
            """INSERT INTO video_feature_a_screen_text
               (analysis_id,start_sec,end_sec,screen_text)
               VALUES (%s,0.4,2.5,'Observed text')""",(ident,))
        self.conn.execute(
            """INSERT INTO video_feature_a_audio_events
               (analysis_id,event_type,start_sec,end_sec,label)
               VALUES (%s,'music',0,40,'background')""",(ident,))
        record=self.conn.execute(
            """SELECT duration_sec,aspect_ratio,scene_count,first_text_sec,
                      first_speech_sec,speech_wpm,speech_coverage_pct,cta_start_sec
               FROM video_feature_a_summary WHERE analysis_id=%s""",(ident,)
        ).fetchone()
        self.assertEqual(record,(Decimal("40"),"9:16",4,Decimal("0.4"),
                                 Decimal("0.7"),Decimal("120.00"),
                                 Decimal("75.00"),Decimal("36.05")))
        events=self.conn.execute(
            """SELECT scene_index,start_sec,end_sec FROM video_feature_a_scenes
               WHERE analysis_id=%s ORDER BY scene_index""",(ident,)
        ).fetchall()
        self.assertEqual(len(events),4)
        self.assertEqual(events[2],(3,Decimal("8"),Decimal("24")))

    def test_zero_words_and_zero_speech_do_not_divide_by_zero(self):
        ident=self.analysis(self.video(),duration_sec=10,spoken_words=0,
                            speech_duration_sec=0,scene_status="complete",
                            speech_status="complete")
        result=self.conn.execute(
            """SELECT spoken_words,speech_wpm,speech_coverage_pct,scene_count
               FROM video_feature_a_summary WHERE analysis_id=%s""",(ident,)
        ).fetchone()
        self.assertEqual(result,(0,None,Decimal("0.00"),0))

    def test_denied_sources_cannot_create_analysis(self):
        video=self.video()
        self.conn.execute(
            "UPDATE sources SET can_analyze_media=FALSE WHERE id=%s",(self.source,))
        with self.assertRaises(Exception):
            self.analysis(video)
        self.conn.execute(
            "UPDATE sources SET can_analyze_media=TRUE,status='disabled' WHERE id=%s",(self.source,))
        with self.assertRaises(Exception):
            self.analysis(video)

    def test_media_asset_must_match_video_and_have_permission(self):
        first=self.video()
        other=self.video()
        media=self.conn.execute(
            """INSERT INTO media_assets(video_id,source_id,media_kind,local_path,
                          permission) VALUES (%s,%s,'video','/fixture/test.mp4','metadata_only')
               RETURNING id""",(first,self.source)
        ).fetchone()[0]
        with self.assertRaises(Exception):
            self.analysis(first,media_asset_id=media)
        self.conn.execute(
            "UPDATE media_assets SET permission='analysis_allowed' WHERE id=%s",(media,))
        with self.assertRaises(Exception):
            self.analysis(other,media_asset_id=media)
        ident=self.analysis(first,media_asset_id=media)
        self.assertIsInstance(ident,int)

    def test_invalid_ranges_and_duplicate_scene_indices_rejected(self):
        ident=self.analysis(self.video(),duration_sec=10)
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO video_feature_a_scenes
                   (analysis_id,scene_index,start_sec,end_sec)
                   VALUES (%s,1,3,3)""",(ident,))
        self.conn.execute(
            """INSERT INTO video_feature_a_scenes
               (analysis_id,scene_index,start_sec,end_sec)
               VALUES (%s,1,0,4)""",(ident,))
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO video_feature_a_scenes
                   (analysis_id,scene_index,start_sec,end_sec)
                   VALUES (%s,1,4,8)""",(ident,))
        with self.assertRaises(Exception):
            self.analysis(self.video(),duration_sec=10,
                          speech_duration_sec=11)
        with self.assertRaises(Exception):
            self.analysis(self.video(),duration_sec=10,
                          cta_start_sec=11)

    def test_distinct_analysis_versions_remain_linked_to_same_video(self):
        video=self.video()
        first=self.analysis(video,analysis_version="A1",state="completed")
        second=self.analysis(video,analysis_version="A2",state="pending")
        self.assertNotEqual(first,second)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM video_feature_a_analyses WHERE video_id=%s",(video,)
        ).fetchone()[0],2)
        self.conn.execute("DELETE FROM videos WHERE id=%s",(video,))
        self.videos.remove(video)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM video_feature_a_analyses WHERE video_id=%s",(video,)
        ).fetchone()[0],0)


if __name__ == "__main__":
    unittest.main()
