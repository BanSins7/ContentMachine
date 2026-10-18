"""Source-first Group B B1-B8 PostgreSQL contract tests.

PostgreSQL tests require ephemeral CM_TEST_PG_DSN from CI.
No external API, downloading, analysis inference, or production writes.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from intelligence.migrate import apply_migrations

MIGRATION = ROOT / "intelligence" / "migrations" / "005_content_features_b.sql"

class BStatic(unittest.TestCase):
    def test_all_eight_contracts_and_no_old_schema_mutation(self):
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        for table in (
            "content_b_analyses","content_b_hooks","content_b_hook_parts",
            "content_b_formats","content_b_story_sampling","content_b_story_frames",
            "content_b_story_intervals","content_b_story_slides",
            "content_b_visual_content","content_b_editing","content_b_subtitles",
            "content_b_cta_analyses","content_b_ctas","content_b_cta_summary",
            "content_b_categories","content_b_topics","content_b_subtopics",
            "content_b_topic_assignments","content_b_story_patterns",
        ):
            self.assertIn(table,sql)
        for feature in ("avg_shot_duration","cuts_first_5_sec","before_after_phase",
                        "myth_busting","animation_motion_graphics","link_in_bio",
                        "destination_keyword","is_command","font_style",
                        "story_pattern","needs_review"):
            self.assertIn(feature,sql)
        for destructive in ("drop table","truncate ","delete from videos","update videos set",
                            "alter table videos","alter table topics"):
            self.assertNotIn(destructive,sql)


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "Ephemeral PostgreSQL CI DSN not provided")
class BDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.conn = psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True)
        apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.source="b-schema-"+uuid4().hex
        self.conn.execute(
            """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
               VALUES (%s,'test','owned','enabled',TRUE)""",(self.source,))
        self.videos=[]
        self.categories=[]

    def tearDown(self):
        # Deleting the canonical publication cascades all B classifications.
        for vid in self.videos:
            self.conn.execute("DELETE FROM videos WHERE id=%s",(vid,))
        for category in self.categories:
            self.conn.execute("DELETE FROM content_b_categories WHERE id=%s",(category,))
        self.conn.execute("DELETE FROM sources WHERE id=%s",(self.source,))

    def video(self,kind=None):
        code=uuid4().hex
        vid=self.conn.execute(
            """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
               VALUES ('tiktok',%s,%s,%s) RETURNING id""",
            (code,"https://www.tiktok.com/@test/video/"+code,kind)
        ).fetchone()[0]
        self.videos.append(vid)
        return vid

    def analysis(self,vid,content_type="video",**fields):
        keys=["video_id","source_id","analysis_key","analysis_version","method_name","content_type"]
        values=[vid,self.source,uuid4().hex,"B1","fixture",content_type]
        for k,v in fields.items():
            if k in keys:
                values[keys.index(k)]=v
            else:
                keys.append(k);values.append(v)
        return self.conn.execute(
            "INSERT INTO content_b_analyses ("+",".join(keys)+") VALUES ("+
            ",".join(["%s"]*len(keys))+") RETURNING id",values,
        ).fetchone()[0]

    def insert(self,table,**data):
        cols=list(data)
        return self.conn.execute(
            "INSERT INTO "+table+" ("+",".join(cols)+") VALUES ("+
            ",".join(["%s"]*len(cols))+") RETURNING *",
            list(data.values()),
        ).fetchone()

    def category_tree(self):
        slug=uuid4().hex[:10]
        category=self.conn.execute(
            "INSERT INTO content_b_categories(slug,name) VALUES (%s,'Здоровье') RETURNING id",
            ("health_"+slug,),
        ).fetchone()[0]
        self.categories.append(category)
        topic=self.conn.execute(
            """INSERT INTO content_b_topics(category_id,slug,name)
               VALUES (%s,'facial_puffiness','Отёчность лица') RETURNING id""",(category,),
        ).fetchone()[0]
        subtopic=self.conn.execute(
            """INSERT INTO content_b_subtopics(topic_id,slug,name)
               VALUES (%s,'morning_puffiness_causes','Причины отёчности') RETURNING id""",
            (topic,),
        ).fetchone()[0]
        return category,topic,subtopic

    def test_migration_repeatable_and_legacy_catalog_untouched(self):
        video=self.video()
        self.assertEqual(apply_migrations(self.conn),[])
        self.assertEqual(self.conn.execute(
            "SELECT content_kind FROM videos WHERE id=%s",(video,)
        ).fetchone(),(None,))
        self.assertIsNotNone(self.conn.execute(
            "SELECT to_regclass('public.carousel_feature_a_items')"
        ).fetchone()[0])
        self.assertIsNotNone(self.conn.execute(
            "SELECT to_regclass('public.content_b_ctas')"
        ).fetchone()[0])

    def test_b1_hook_full_taxonomy_form_channels_and_parts(self):
        analysis=self.analysis(self.video("video"))
        self.insert("content_b_hooks",analysis_id=analysis,
                    start_sec="0.0",end_sec="4.2",
                    spoken_text="Почему? Вот три причины",
                    screen_text=None,visual_description="Ведущий перед камерой",
                    mechanisms=["problem","promise","curiosity"],
                    is_question=True,has_number=True,direct_address=False,
                    has_negation=False,is_command=False,
                    delivery=["speech","visual_action"],classification_status="inferred")
        self.insert("content_b_hook_parts",analysis_id=analysis,part_index=1,
                    start_sec=0,end_sec=2,verbatim_text="Почему?",
                    mechanisms=["problem"])
        self.insert("content_b_hook_parts",analysis_id=analysis,part_index=2,
                    start_sec=2,end_sec=4.2,verbatim_text="Вот три причины",
                    mechanisms=["promise","curiosity"])
        result=self.conn.execute(
            "SELECT mechanisms,is_question,delivery FROM content_b_hooks WHERE analysis_id=%s",
            (analysis,),
        ).fetchone()
        self.assertEqual(result,(["problem","promise","curiosity"],True,["speech","visual_action"]))
        with self.assertRaises(Exception):
            self.insert("content_b_hook_parts",analysis_id=analysis,part_index=3,
                        mechanisms=["not_in_vocab"])
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE content_b_hooks SET mechanisms=ARRAY['problem','problem'] WHERE analysis_id=%s",(analysis,))

    def test_b2_video_and_carousel_formats_are_exclusive(self):
        video=self.analysis(self.video("slideshow_video"))
        self.insert("content_b_formats",analysis_id=video,video_primary="slideshow_video",
                    video_secondary=None,classification_status="inferred")
        with self.assertRaises(Exception):
            self.conn.execute(
                "UPDATE content_b_formats SET video_secondary='slideshow_video' WHERE analysis_id=%s",
                (video,))
        with self.assertRaises(Exception):
            self.insert("content_b_formats",analysis_id=self.analysis(self.video("video")),
                        carousel_format="listicle",classification_status="inferred")
        car=self.analysis(self.video("photo_carousel"),content_type="carousel")
        self.insert("content_b_formats",analysis_id=car,carousel_format="step_by_step",
                    classification_status="inferred")
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE content_b_formats SET video_primary='talking_head' WHERE analysis_id=%s",(car,))

    def test_b3_26_role_pattern_repetitions_and_before_after_phase(self):
        a=self.analysis(self.video("video"))
        self.insert("content_b_story_sampling",analysis_id=a,first_5_sec_fps=2,
                    remaining_fps=1,max_sampled_frames_target=60)
        for index,time,role,phase in [
            (1,"0.5","hook",None),(2,"2","problem",None),
            (3,"5","before_after","before"),(4,"8","before_after","after"),
            (5,"12","cta",None),
        ]:
            self.insert("content_b_story_frames",analysis_id=a,
                        frame_index=index,time_sec=time,role=role,
                        before_after_phase=phase)
            self.insert("content_b_story_intervals",analysis_id=a,
                        interval_index=index,start_sec=str(index*2),
                        end_sec=str(index*2+2),role=role,
                        before_after_phase=phase,boundary_quality="estimated")
        pattern=self.conn.execute(
            "SELECT story_pattern FROM content_b_story_patterns WHERE analysis_id=%s",(a,)
        ).fetchone()[0]
        self.assertEqual(pattern,["hook","problem","before_after","before_after","cta"])
        with self.assertRaises(Exception):
            self.insert("content_b_story_frames",analysis_id=a,frame_index=6,role="transition")
        with self.assertRaises(Exception):
            self.insert("content_b_story_frames",analysis_id=a,frame_index=6,role="other",needs_review=False)
        with self.assertRaises(Exception):
            self.insert("content_b_story_frames",analysis_id=a,frame_index=6,role="step",before_after_phase="before")

    def test_b3_carousel_one_role_per_slide_and_repeated_steps(self):
        a=self.analysis(self.video("photo_carousel"),content_type="carousel")
        for i,r in enumerate(["hook","step","step","cta"],1):
            self.insert("content_b_story_slides",analysis_id=a,slide_index=i,
                        role=r,screen_text="Текст "+str(i))
        self.assertEqual(self.conn.execute(
            "SELECT story_pattern FROM content_b_story_patterns WHERE analysis_id=%s",(a,)
        ).fetchone()[0],["hook","step","step","cta"])
        with self.assertRaises(Exception):
            self.insert("content_b_story_frames",analysis_id=a,frame_index=1,role="hook")
        with self.assertRaises(Exception):
            self.insert("content_b_editing",analysis_id=a,editing_type="single_take",
                        cut_count=0,cuts_per_minute=0,avg_shot_duration=15,cuts_first_5_sec=0)

    def test_b4_four_visual_fields_with_unverified_ai_null(self):
        a=self.analysis(self.video("video"))
        self.insert("content_b_visual_content",analysis_id=a,ai_generated=None,
                    person_visible=True,product_visible=False,logo_visible=True)
        self.assertEqual(self.conn.execute(
            "SELECT ai_generated,person_visible,product_visible,logo_visible "
            "FROM content_b_visual_content WHERE analysis_id=%s",(a,)
        ).fetchone(),(None,True,False,True))
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE content_b_visual_content SET person_visible=NULL WHERE analysis_id=%s",(a,))

    def test_b5_original_four_metrics_including_avg_shot_duration(self):
        a=self.analysis(self.video("video"))
        self.insert("content_b_editing",analysis_id=a,editing_type="fast_cuts",
                    cut_count=18,cuts_per_minute="36",avg_shot_duration="1.5789",cuts_first_5_sec=4)
        with self.assertRaises(Exception):
            self.insert("content_b_editing",analysis_id=self.analysis(self.video("video")),
                        editing_type="slow_cuts",cut_count=15,cuts_per_minute=33,
                        avg_shot_duration=2,cuts_first_5_sec=2)
        b=self.analysis(self.video("video"))
        self.insert("content_b_editing",analysis_id=b,editing_type="single_take",
                    cut_count=0,cuts_per_minute=0,avg_shot_duration=22,cuts_first_5_sec=0)

    def test_b6_subtitle_none_vs_exact_seven_style_fields(self):
        a=self.analysis(self.video("video"))
        self.insert("content_b_subtitles",analysis_id=a,subtitle_type="none")
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE content_b_subtitles SET text_color='#FFFFFF' WHERE analysis_id=%s",(a,))
        b=self.analysis(self.video("video"))
        self.insert("content_b_subtitles",analysis_id=b,subtitle_type="dynamic",
                    position="center",font_size="large",font_style="extra_bold",
                    text_color="#FFFFFF",highlight_color="#FFFF00",
                    background_style="outline",display_mode="word_by_word")
        with self.assertRaises(Exception):
            self.insert("content_b_subtitles",analysis_id=self.analysis(
                self.video("photo_carousel"),content_type="carousel"),subtitle_type="none")

    def test_b7_multiple_ctas_channels_destinations_and_three_state_summary(self):
        a=self.analysis(self.video("video"))
        self.insert("content_b_cta_analyses",analysis_id=a,analysis_status="partial")
        self.assertEqual(self.conn.execute(
            "SELECT has_cta FROM content_b_cta_summary WHERE analysis_id=%s",(a,)
        ).fetchone(),(None,))
        self.insert("content_b_ctas",analysis_id=a,occurrence_index=1,
                    cta_type="app_install",text="Скачай по ссылке в профиле",
                    spoken_text="Скачай по ссылке в профиле",screen_text="Link in bio",
                    delivery=["speech","on_screen_text"],
                    start_sec="24.5",end_sec=28,destination_type="link_in_bio")
        self.insert("content_b_ctas",analysis_id=a,occurrence_index=2,
                    cta_type="follow",text="Подпишись",
                    delivery=["speech"],start_sec=29,end_sec=30,
                    destination_type="profile",destination_service="instagram",
                    destination_handle="@somebody")
        self.conn.execute("UPDATE content_b_cta_analyses SET analysis_status='complete' WHERE analysis_id=%s",(a,))
        self.assertEqual(self.conn.execute(
            "SELECT has_cta,detected_occurrences FROM content_b_cta_summary WHERE analysis_id=%s",
            (a,)
        ).fetchone(),(True,2))
        b=self.analysis(self.video("video"))
        self.insert("content_b_cta_analyses",analysis_id=b,analysis_status="complete")
        self.assertEqual(self.conn.execute(
            "SELECT has_cta,detected_occurrences FROM content_b_cta_summary WHERE analysis_id=%s",
            (b,)
        ).fetchone(),(False,0))
        with self.assertRaises(Exception):
            self.insert("content_b_ctas",analysis_id=a,occurrence_index=3,
                        cta_type="none",text="None",delivery=["speech"],destination_type="unknown")

    def test_b7_carousel_slide_positions_and_unknowns(self):
        a=self.analysis(self.video("mixed_carousel"),content_type="carousel")
        self.insert("content_b_cta_analyses",analysis_id=a,analysis_status="complete")
        self.insert("content_b_ctas",analysis_id=a,occurrence_index=1,
                    cta_type="save",text="Сохрани",screen_text="Сохрани",
                    delivery=["on_screen_text"],slide_index=4,destination_type="current_post")
        with self.assertRaises(Exception):
            self.insert("content_b_ctas",analysis_id=a,occurrence_index=2,
                        cta_type="comment",text="Напиши",delivery=["on_screen_text"],
                        start_sec=5,destination_type="comments")
        with self.assertRaises(Exception):
            self.insert("content_b_ctas",analysis_id=a,occurrence_index=2,
                        cta_type="comment",text="Напиши",delivery=["speech","speech"],
                        slide_index=5,destination_type="comments")
        with self.assertRaises(Exception):
            self.insert("content_b_ctas",analysis_id=a,occurrence_index=2,
                        cta_type="comment",text="Напиши",delivery=["on_screen_text"],
                        slide_index=5,destination_type="profile",
                        destination_other_desc="Invalid description")

    def test_b8_hierarchy_stable_ids_reuse_and_parent_integrity(self):
        category,topic,subtopic=self.category_tree()
        a=self.analysis(self.video("video"))
        self.insert("content_b_topic_assignments",analysis_id=a,category_id=category,
                    topic_id=topic,subtopic_id=subtopic)
        b=self.analysis(self.video("photo_carousel"),content_type="carousel")
        self.insert("content_b_topic_assignments",analysis_id=b,category_id=category,
                    topic_id=topic,subtopic_id=None)
        self.conn.execute("UPDATE content_b_topics SET name='Отечность лица' WHERE id=%s",(topic,))
        self.assertEqual(self.conn.execute(
            "SELECT topic_id FROM content_b_topic_assignments WHERE analysis_id=%s",(a,)
        ).fetchone(),(topic,))
        with self.assertRaises(Exception):
            self.conn.execute("INSERT INTO content_b_topics(category_id,slug,name) VALUES (%s,'facial_puffiness','Дубль')",(category,))
        other_category,_,_=self.category_tree()
        with self.assertRaises(Exception):
            self.insert("content_b_topic_assignments",
                        analysis_id=self.analysis(self.video("video")),
                        category_id=other_category,topic_id=topic)

    def test_source_permissions_versions_and_unknown_media_kind(self):
        video=self.video()
        a=self.analysis(video)  # A URL alone leaves content_kind unknown.
        self.assertIsInstance(a,int)
        with self.assertRaises(Exception):
            self.analysis(self.video("image_post"),content_type="carousel")
        with self.assertRaises(Exception):
            self.analysis(self.video("photo_carousel"),content_type="video")
        self.conn.execute("UPDATE sources SET can_analyze_media=FALSE WHERE id=%s",(self.source,))
        with self.assertRaises(Exception):
            self.analysis(self.video())
        with self.assertRaises(Exception):
            self.insert("content_b_visual_content",analysis_id=a,
                        person_visible=False,product_visible=False,logo_visible=False)

    def test_versions_and_delete_cascade(self):
        video=self.video("video")
        a=self.analysis(video)
        self.insert("content_b_formats",analysis_id=a,video_primary="talking_head",
                    classification_status="inferred")
        self.conn.execute("UPDATE content_b_analyses SET state='completed' WHERE id=%s",(a,))
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE content_b_formats SET video_primary='skit' WHERE analysis_id=%s",(a,))
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE content_b_analyses SET analysis_version='B2' WHERE id=%s",(a,))
        b=self.analysis(video,analysis_version="B2")
        self.insert("content_b_formats",analysis_id=b,video_primary="skit",
                    classification_status="inferred")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM content_b_analyses WHERE video_id=%s",(video,)
        ).fetchone()[0],2)
        self.conn.execute("DELETE FROM videos WHERE id=%s",(video,))
        self.videos.remove(video)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM content_b_analyses WHERE video_id=%s",(video,)
        ).fetchone()[0],0)

if __name__=="__main__":
    unittest.main()
