"""Additive 004 carousel Group A schema and PostgreSQL behavior.

Run against ephemeral CI database only (CM_TEST_PG_DSN).
No media download, OCR, ASR, platform API, or production database mutation.
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

MIGRATION = ROOT / "intelligence" / "migrations" / "004_carousel_feature_a.sql"


class CarouselSchemaStaticTests(unittest.TestCase):
    def test_004_is_additive_and_covers_agreed_features(self):
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        for name in (
            "carousel_feature_a_analyses", "carousel_feature_a_items",
            "carousel_feature_a_summary", "content_kind", "slide_index",
            "item_type", "on_screen_text", "screen_word_count",
            "cta_slide_index", "has_audio", "aspect_ratio",
            "video_a_analysis_id", "source_policy_version_id",
            "measurement_methods", "unavailable_reasons"
        ):
            self.assertIn(name, sql)
        for invalid in ("drop table", "truncate ", "delete from videos",
                        "update videos set", "alter table videos"):
            self.assertNotIn(invalid, sql)
        for unwanted in ("avg_scene_sec", "width_px", "height_px", "fps"):
            # Mentioned exclusions in comments are fine; column definitions are not.
            self.assertNotRegex(sql, r"(?m)^\s*" + unwanted + r"\s+")


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "CM_TEST_PG_DSN not configured")
class CarouselSchemaPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.conn = psycopg.connect(os.environ["CM_TEST_PG_DSN"], autocommit=True)
        apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.source = "carousel-a-" + uuid4().hex
        self.conn.execute(
            """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
               VALUES (%s,'test','owned','enabled',TRUE)""",
            (self.source,),
        )
        self.videos = []

    def tearDown(self):
        for video in self.videos:
            self.conn.execute("DELETE FROM videos WHERE id=%s", (video,))
        self.conn.execute("DELETE FROM sources WHERE id=%s", (self.source,))

    def video(self, kind=None):
        code = uuid4().hex
        video_id = self.conn.execute(
            """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
               VALUES ('instagram',%s,%s,%s) RETURNING id""",
            (code, "https://www.instagram.com/p/" + code + "/", kind)
        ).fetchone()[0]
        self.videos.append(video_id)
        return video_id

    def analysis(self, video_id, kind="photo_carousel", **fields):
        names=["video_id","source_id","analysis_key","analysis_version",
               "method_name","content_kind"]
        vals=[video_id,self.source,uuid4().hex,"A1","test-extractor",kind]
        for key, value in fields.items():
            if key in names:
                vals[names.index(key)] = value
            else:
                names.append(key)
                vals.append(value)
        return self.conn.execute(
            "INSERT INTO carousel_feature_a_analyses (" + ",".join(names) +
            ") VALUES (" + ",".join(["%s"]*len(names)) + ") RETURNING id",
            tuple(vals),
        ).fetchone()[0]

    def slide(self, analysis_id, index, kind="image", **fields):
        names=["analysis_id","slide_index","item_type"]
        vals=[analysis_id,index,kind]
        for key, value in fields.items():
            names.append(key)
            vals.append(value)
        return self.conn.execute(
            "INSERT INTO carousel_feature_a_items (" + ",".join(names) +
            ") VALUES (" + ",".join(["%s"]*len(names)) + ") RETURNING id",
            tuple(vals),
        ).fetchone()[0]

    def complete(self, ident, **fields):
        assigns = ["state='completed'", "slide_status='complete'"]
        params = []
        for key, value in fields.items():
            assigns.append(key+"=%s")
            params.append(value)
        self.conn.execute(
            "UPDATE carousel_feature_a_analyses SET " + ",".join(assigns) + " WHERE id=%s",
            tuple(params+[ident]),
        )

    def test_migration_idempotent_preserves_legacy_video(self):
        video=self.video()
        self.assertEqual(apply_migrations(self.conn), [])
        row=self.conn.execute(
            "SELECT content_kind FROM videos WHERE id=%s",(video,)
        ).fetchone()
        self.assertEqual(row,(None,))
        self.assertEqual(
            self.conn.execute(
                "SELECT to_regclass('public.video_feature_a_scenes')"
            ).fetchone()[0],
            "video_feature_a_scenes",
        )

    def test_completed_photo_carousel_exact_order_and_text_summary(self):
        video=self.video("photo_carousel")
        ident=self.analysis(video,has_audio="unknown",cta_slide_index=3,
                            slide_status="partial",text_status="complete")
        self.slide(ident,1,aspect_ratio="4:5",text_status="complete",
                   on_screen_text="",screen_word_count=0)
        self.slide(ident,2,aspect_ratio="1:1",text_status="complete",
                   on_screen_text="First real text",screen_word_count=3,
                   ocr_method="fixture",ocr_confidence_pct=98)
        self.slide(ident,3,aspect_ratio="9:16",text_status="complete",
                   on_screen_text="Install the app today",screen_word_count=4)
        self.complete(ident)
        result=self.conn.execute(
            """SELECT content_kind,slide_count,first_text_slide,
                      total_screen_word_count,cta_slide_index,has_audio
               FROM carousel_feature_a_summary WHERE analysis_id=%s""",(ident,)
        ).fetchone()
        self.assertEqual(result,("photo_carousel",3,2,7,3,"unknown"))
        rows=self.conn.execute(
            """SELECT slide_index,item_type,aspect_ratio,screen_word_count
               FROM carousel_feature_a_items WHERE analysis_id=%s
               ORDER BY slide_index""",(ident,)
        ).fetchall()
        self.assertEqual(rows,[(1,"image","4:5",0),(2,"image","1:1",3),(3,"image","9:16",4)])

    def test_mixed_carousel_video_segment_links_existing_group_a(self):
        video=self.video("mixed_carousel")
        media=self.conn.execute(
            """INSERT INTO media_assets(video_id,source_id,media_kind,local_path,permission)
               VALUES (%s,%s,'video','/fixture/clip.mp4','analysis_allowed')
               RETURNING id""",(video,self.source)
        ).fetchone()[0]
        clip=self.conn.execute(
            """INSERT INTO video_feature_a_analyses(
                 video_id,source_id,media_asset_id,analysis_key,analysis_version,
                 method_name,state,duration_sec)
               VALUES (%s,%s,%s,%s,'A1','fixture','completed',4.25) RETURNING id""",
            (video,self.source,media,uuid4().hex),
        ).fetchone()[0]
        carousel=self.analysis(video,kind="mixed_carousel",has_audio="true")
        self.slide(carousel,1,"image",text_status="complete",on_screen_text="Hello",screen_word_count=1)
        self.slide(carousel,2,"video",media_asset_id=media,video_a_analysis_id=clip,
                   duration_sec="4.25",text_status="complete",
                   on_screen_text="Demo",screen_word_count=1)
        self.slide(carousel,3,"image",text_status="complete",on_screen_text="",screen_word_count=0)
        self.complete(carousel,text_status="complete")
        result=self.conn.execute(
            """SELECT a.video_id, i.item_type, i.duration_sec, v.duration_sec
               FROM carousel_feature_a_items i
               JOIN carousel_feature_a_analyses a ON a.id=i.analysis_id
               JOIN video_feature_a_analyses v ON v.id=i.video_a_analysis_id
               WHERE i.analysis_id=%s AND i.slide_index=2""",(carousel,)
        ).fetchone()
        self.assertEqual(result,(video,"video",Decimal("4.25"),Decimal("4.25")))
        summary=self.conn.execute(
            "SELECT slide_count,total_screen_word_count FROM carousel_feature_a_summary WHERE analysis_id=%s",
            (carousel,),
        ).fetchone()
        self.assertEqual(summary,(3,2))

    def test_single_image_post_and_no_duration_for_static_slide(self):
        video=self.video("image_post")
        a=self.analysis(video,kind="image_post")
        with self.assertRaises(Exception):
            self.slide(a,1,"image",duration_sec=10)
        self.slide(a,1,"image",text_status="complete",on_screen_text="",screen_word_count=0)
        self.complete(a,text_status="complete")
        result=self.conn.execute(
            """SELECT slide_count,first_text_slide,total_screen_word_count
               FROM carousel_feature_a_summary WHERE analysis_id=%s""",(a,)
        ).fetchone()
        self.assertEqual(result,(1,None,0))

    def test_unknown_data_does_not_turn_into_zero(self):
        video=self.video()
        a=self.analysis(video)
        self.assertEqual(self.conn.execute(
            """SELECT slide_count,first_text_slide,total_screen_word_count
               FROM carousel_feature_a_summary WHERE analysis_id=%s""",(a,)
        ).fetchone(),(None,None,None))
        self.slide(a,1)
        self.slide(a,2)
        self.complete(a)
        self.assertEqual(self.conn.execute(
            """SELECT slide_count,first_text_slide,total_screen_word_count
               FROM carousel_feature_a_summary WHERE analysis_id=%s""",(a,)
        ).fetchone(),(2,None,None))

    def test_deny_rights_and_wrong_content_kind(self):
        a_video=self.video("video")
        with self.assertRaises(Exception):
            self.analysis(a_video)
        video=self.video()
        self.conn.execute(
            "UPDATE sources SET can_analyze_media=FALSE WHERE id=%s",(self.source,))
        with self.assertRaises(Exception):
            self.analysis(video)
        self.conn.execute(
            "UPDATE sources SET can_analyze_media=TRUE,status='disabled' WHERE id=%s",(self.source,))
        with self.assertRaises(Exception):
            self.analysis(video)

    def test_reject_invalid_slide_sequence_kind_and_cta(self):
        a=self.analysis(self.video(),cta_slide_index=5)
        self.slide(a,1)
        self.slide(a,3)
        with self.assertRaises(Exception):
            self.complete(a)
        self.slide(a,2,"video",duration_sec=2)
        with self.assertRaises(Exception):
            self.complete(a) # photo-only kind cannot include video clip
        self.conn.execute(
            "DELETE FROM carousel_feature_a_items WHERE analysis_id=%s AND slide_index=2",(a,))
        with self.assertRaises(Exception):
            self.complete(a) # gap between 1 and 3
        self.conn.execute(
            "UPDATE carousel_feature_a_items SET slide_index=2 WHERE analysis_id=%s AND slide_index=3",(a,))
        with self.assertRaises(Exception):
            self.complete(a) # CTA=5 beyond 2
        self.complete(a,cta_slide_index=2)
        with self.assertRaises(Exception):
            self.slide(a,3)
        with self.assertRaises(Exception):
            self.conn.execute("DELETE FROM carousel_feature_a_items WHERE analysis_id=%s AND slide_index=1",(a,))
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE carousel_feature_a_analyses SET cta_slide_index=1 WHERE id=%s",(a,))

    def test_reject_wrong_slide_asset_linkage_and_expired_media(self):
        video=self.video()
        other=self.video()
        media=self.conn.execute(
            """INSERT INTO media_assets(video_id,source_id,media_kind,local_path,permission)
               VALUES (%s,%s,'image','/fixture/one.png','metadata_only')
               RETURNING id""",(video,self.source)
        ).fetchone()[0]
        ident=self.analysis(video)
        with self.assertRaises(Exception):
            self.slide(ident,1,media_asset_id=media)
        self.conn.execute(
            "UPDATE media_assets SET permission='analysis_allowed',expires_at=now()-interval '1 minute' WHERE id=%s",
            (media,),
        )
        with self.assertRaises(Exception):
            self.slide(ident,1,media_asset_id=media)
        self.conn.execute(
            "UPDATE media_assets SET expires_at=NULL WHERE id=%s",(media,),
        )
        other_analysis=self.analysis(other)
        with self.assertRaises(Exception):
            self.slide(other_analysis,1,media_asset_id=media)
        self.slide(ident,1,media_asset_id=media)

    def test_reject_bad_ocr_claims_and_nonvideo_clip_analysis(self):
        ident=self.analysis(self.video())
        with self.assertRaises(Exception):
            self.slide(ident,1,screen_word_count=4,text_status="complete")
        with self.assertRaises(Exception):
            self.slide(ident,1,on_screen_text="Visible",text_status="complete")
        with self.assertRaises(Exception):
            self.slide(ident,1,"image",video_a_analysis_id=999999999)
        self.slide(ident,1,"image",screen_word_count=0,on_screen_text="",text_status="complete")
        with self.assertRaises(Exception):
            self.slide(ident,1)

    def test_repeated_analysis_preserves_prior_observations_and_delete_cascades(self):
        video=self.video()
        first=self.analysis(video)
        self.slide(first,1)
        self.slide(first,2)
        self.complete(first)
        second=self.analysis(video,analysis_version="A2")
        self.slide(second,1)
        self.slide(second,2)
        self.slide(second,3)
        self.complete(second)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM carousel_feature_a_analyses WHERE video_id=%s",(video,)
        ).fetchone()[0],2)
        self.conn.execute("DELETE FROM videos WHERE id=%s",(video,))
        self.videos.remove(video)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM carousel_feature_a_analyses WHERE video_id=%s",(video,)
        ).fetchone()[0],0)


if __name__ == "__main__":
    unittest.main()
