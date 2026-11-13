"""C0-C7 PostgreSQL contract QA against an isolated PostgreSQL 17 CI database.

No live collectors, inference, scores or production data are created.
"""
from __future__ import annotations

import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intelligence import migrate

MIGRATION = Path(__file__).resolve().parents[1] / "intelligence" / "migrations" / "003_performance_c.sql"
TABLES = (
    "comparison_cohorts", "comparison_cohort_members",
    "metric_calculation_configs", "calculated_metrics",
)


class PerformanceCStaticQA(unittest.TestCase):
    def test_additive_and_no_fake_normalization(self):
        text = MIGRATION.read_text(encoding="utf-8").lower()
        self.assertNotIn("drop table", text)
        self.assertNotIn("truncate ", text)
        self.assertNotIn("delete from", text)
        self.assertIn("normalization_enabled = false", text)
        self.assertIn("weighted_engagement_smoothed", text)
        self.assertIn("views_growth_per_hour", text)
        self.assertIn("unique nulls not distinct", text)
        self.assertNotIn("creator_ratio_norm", text)
        self.assertNotIn("growth_norm", text)

@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "CM_TEST_PG_DSN not provided")
class PerformanceCPostgresQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.db = os.environ["CM_TEST_PG_DSN"]
        cls.conn = psycopg.connect(cls.db, autocommit=True)
        migrate.apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.source = "c03-" + uuid4().hex
        self.creators = []
        self.videos = []
        self.configs = []
        self.cohorts = []
        self.topic = self.conn.execute(
            "INSERT INTO topics(name) VALUES (%s) RETURNING id",
            ("C03 QA " + uuid4().hex,),
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO sources(id,provider,source_type,status,can_store_metrics,can_derive_scores)
               VALUES (%s,'ci_fixture','manual','enabled',TRUE,TRUE)""",
            (self.source,),
        )
        self.creator = self.conn.execute(
            """INSERT INTO creators(platform,platform_creator_id)
               VALUES ('youtube',%s) RETURNING id""",
            ("c03c" + uuid4().hex,),
        ).fetchone()[0]

    def tearDown(self):
        for v in self.videos:
            self.conn.execute("DELETE FROM calculated_metrics WHERE post_id=%s", (v,))
        # Deleting a cohort cascades to its members, including a frozen cohort:
        # do not issue a manual member edit after it was frozen.
        for c in self.cohorts:
            self.conn.execute("DELETE FROM comparison_cohorts WHERE id=%s", (c,))
        for v in self.videos:
            self.conn.execute("DELETE FROM videos WHERE id=%s", (v,))
        for c in self.configs:
            self.conn.execute("DELETE FROM metric_calculation_configs WHERE id=%s", (c,))
        self.conn.execute("DELETE FROM creators WHERE id=%s", (self.creator,))
        self.conn.execute("DELETE FROM topics WHERE id=%s", (self.topic,))
        self.conn.execute("DELETE FROM sources WHERE id=%s", (self.source,))

    def post(self, kind="video"):
        code = uuid4().hex[:11]
        v = self.conn.execute(
            """INSERT INTO videos
               (platform,platform_video_id,canonical_url,creator_id,c_content_type)
               VALUES ('youtube',%s,%s,%s,%s) RETURNING id""",
            (code, "https://www.youtube.com/watch?v="+code, self.creator,kind),
        ).fetchone()[0]
        self.videos.append(v)
        return v

    def snapshot(self, post, *, observed_at="2026-10-19T10:00:00+00", views=0, key=None):
        key = key or uuid4().hex
        row = self.conn.execute(
            """INSERT INTO metric_snapshots
              (video_id,source_id,observed_at,views,likes,comments,shares,saves,
               source_metric_definition,source_metric_version,snapshot_key)
              VALUES (%s,%s,%s,%s,NULL,NULL,NULL,NULL,'views-v1','1',%s)
              RETURNING id""",
            (post,self.source,observed_at,views,key),
        ).fetchone()
        return row[0]

    def config(self, *, active=False, alpha=None, k=None):
        row=self.conn.execute(
            """INSERT INTO metric_calculation_configs(calculation_version,is_active,alpha,bayesian_k)
               VALUES (%s,%s,%s,%s) RETURNING id""",
            ("C03-ci-"+uuid4().hex,active,alpha,k),
        ).fetchone()[0]
        self.configs.append(row)
        return row

    def cohort(self, purpose, *, post_count=3):
        row=self.conn.execute(
            """INSERT INTO comparison_cohorts
              (purpose,definition_version,platform,creator_id,topic_id,content_type,
               source_id,evaluated_as_of,post_count,median_views)
              VALUES (%s,'ci-v1','youtube',%s,%s,'video',%s,
                '2026-10-19T10:00:00+00',%s,250)
              RETURNING id""",
            (purpose,self.creator if purpose=="creator" else None,
             self.topic if purpose!="creator" else None,self.source,post_count),
        ).fetchone()[0]
        self.cohorts.append(row)
        return row

    def test_no_duplicate_post_fact_tables_and_migration_reentrant(self):
        self.assertEqual(migrate.apply_migrations(self.conn),[])
        for table in TABLES:
            self.assertIsNotNone(
                self.conn.execute("SELECT to_regclass(%s)",("public."+table,)).fetchone()[0])
        existing=self.conn.execute(
            "SELECT version FROM schema_migrations WHERE version='003_performance_c.sql'"
        ).fetchone()
        self.assertEqual(existing,("003_performance_c.sql",))
        self.assertIsNotNone(self.conn.execute("SELECT to_regclass('videos')").fetchone()[0])
        self.assertIsNotNone(self.conn.execute("SELECT to_regclass('metric_snapshots')").fetchone()[0])
        self.assertIsNone(self.conn.execute("SELECT to_regclass('content_posts')").fetchone()[0])
        self.assertIsNone(self.conn.execute("SELECT to_regclass('metrics_snapshots')").fetchone()[0])

    def test_video_and_carousel_only_with_unknown_old_kind(self):
        video=self.post(None)
        self.assertIsNone(self.conn.execute(
            "SELECT c_content_type FROM videos WHERE id=%s",(video,)
        ).fetchone()[0])
        carousel=self.post("carousel")
        self.assertEqual(self.conn.execute(
            "SELECT c_content_type FROM videos WHERE id=%s",(carousel,)
        ).fetchone()[0],"carousel")
        with self.assertRaises(Exception):
            self.post("story")

    def test_raw_snapshot_null_zero_duplicate_and_immutability(self):
        post=self.post()
        sid=self.snapshot(post,views=0,key="c03-"+uuid4().hex)
        self.assertEqual(self.conn.execute(
            "SELECT views,likes,comments,shares,saves FROM metric_snapshots WHERE id=%s",
            (sid,)).fetchone(),(0,None,None,None,None))
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE metric_snapshots SET views=1 WHERE id=%s",(sid,))
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE metric_snapshots SET observed_at=now() WHERE id=%s",(sid,))
        self.conn.execute("""UPDATE metric_snapshots
            SET expires_at=now()+interval '1 day',
                data_quality_status='unverified' WHERE id=%s""",(sid,))
        self.assertEqual(self.conn.execute(
            "SELECT data_quality_status FROM metric_snapshots WHERE id=%s",(sid,)
        ).fetchone()[0],"unverified")
        with self.assertRaises(Exception):
            self.snapshot(post,observed_at="2026-10-19T10:00:00+00",views=12)
        with self.assertRaises(Exception):
            self.snapshot(post,observed_at="2026-10-19T11:00:00+00",views=-1)

    def test_membership_snapshot_must_refer_to_same_post(self):
        p1=self.post()
        p2=self.post()
        s1=self.snapshot(p1)
        coh=self.cohort("creator")
        self.conn.execute(
            """INSERT INTO comparison_cohort_members
              (cohort_id,comparison_post_id,comparison_snapshot_id)
              VALUES (%s,%s,%s)""",(coh,p1,s1))
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO comparison_cohort_members
                  (cohort_id,comparison_post_id,comparison_snapshot_id)
                  VALUES (%s,%s,%s)""",(coh,p2,s1))

    def test_config_unset_parameters_no_normalization_one_active(self):
        stage12 = self.conn.execute(
            "SELECT to_regclass('public.group_c_config_activation_events')"
        ).fetchone()[0] is not None
        # After 059, a bare is_active=TRUE INSERT must be rejected until
        # an exact approved same-transaction audit event exists.
        if stage12:
            with self.assertRaises(Exception):
                self.config(active=True)
        cfg=self.config(active=not stage12)
        value=self.conn.execute(
            """SELECT alpha,bayesian_k,normalization_enabled,freshness_half_life_h,
                      weights_json->>'views_growth_per_hour'
               FROM metric_calculation_configs WHERE id=%s""",(cfg,)
        ).fetchone()
        self.assertEqual(value,(None,None,False,Decimal(48), "0.30"))
        with self.assertRaises(Exception):
            self.conn.execute(
                "UPDATE metric_calculation_configs SET normalization_enabled=TRUE WHERE id=%s",
                (cfg,))
        with self.assertRaises(Exception):
            self.conn.execute(
                "UPDATE metric_calculation_configs SET freshness_half_life_h=24 WHERE id=%s",
                (cfg,))
        with self.assertRaises(Exception):
            self.conn.execute(
                """UPDATE metric_calculation_configs
                   SET weights_json='{"creator_ratio":1.0}'::jsonb WHERE id=%s""",
                (cfg,))
        with self.assertRaises(Exception):
            self.config(active=True)

    def test_score_lineage_null_policy_and_raw_sum(self):
        post=self.post()
        current=self.snapshot(post,views=16000)
        previous=self.snapshot(post,observed_at="2026-10-19T07:00:00+00",views=10000)
        config=self.config(alpha=100,k=100)
        creator=self.cohort("creator")
        self.conn.execute("UPDATE comparison_cohorts SET is_frozen=TRUE WHERE id=%s",(creator,))
        metadata=self.conn.execute("""INSERT INTO source_video_metadata
           (video_id,source_id,fetched_at,published_at)
           VALUES (%s,%s,'2026-10-19T10:00:00+00','2026-10-18T10:00:00+00')
           RETURNING id""",(post,self.source)).fetchone()[0]
        self.conn.execute(
            """INSERT INTO calculated_metrics(post_id,snapshot_id,calculation_config_id,
                previous_snapshot_id,score_status,calculation_status)
               VALUES (%s,%s,%s,%s,'insufficient_data','insufficient_data')""",
            (post,current,config,previous))
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO calculated_metrics(post_id,snapshot_id,calculation_config_id,
                    previous_snapshot_id) VALUES (%s,%s,%s,%s)""",
                (post,current,config,previous))
        self.conn.execute(
            """INSERT INTO calculated_metrics
              (post_id,snapshot_id,calculation_config_id,score_status,calculation_status,
               creator_ratio,views_growth_per_hour,weighted_engagement_smoothed,
               freshness_score,score_creator_component,score_growth_component,
               score_engagement_component,score_freshness_component,breakout_score,
               previous_snapshot_id,published_metadata_id,creator_cohort_id,
               creator_median_views,alpha_used,k_used,p_l_used,p_c_used,p_s_used)
             VALUES (%s,%s,%s,'complete','complete',2,2000,0.07,70,
                      0.9,600,0.0105,7,607.9105,%s,%s,%s,250,100,100,
                      0.04,0.005,0.002)""",
             (post,current,config,previous,metadata,creator))
        # Once complete, a result is historical and cannot be silently modified.
        with self.assertRaises(Exception):
            self.conn.execute("""UPDATE calculated_metrics SET breakout_score=0
                WHERE post_id=%s AND score_status='complete'""",(post,))
        # Removing source publication evidence must remove dependent scores.
        self.conn.execute("DELETE FROM source_video_metadata WHERE id=%s",(metadata,))
        self.assertEqual(self.conn.execute("""SELECT count(*) FROM calculated_metrics
            WHERE post_id=%s AND score_status='complete'""",(post,)).fetchone()[0],0)
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO calculated_metrics
                 (post_id,snapshot_id,calculation_config_id,score_status,breakout_score)
                 VALUES (%s,%s,%s,'insufficient_data',10)""",
                (post,previous,config))
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO calculated_metrics
                 (post_id,snapshot_id,calculation_config_id,score_status,breakout_score)
                 VALUES (%s,%s,%s,'complete',10)""",
                (post,previous,config))
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO calculated_metrics
                 (post_id,snapshot_id,calculation_config_id,score_status,calculation_status,
                  creator_ratio,views_growth_per_hour,weighted_engagement_smoothed,
                  freshness_score,score_creator_component,score_growth_component,
                  score_engagement_component,score_freshness_component,breakout_score)
                 VALUES (%s,%s,%s,'complete','complete',1,1,0.05,10,1,1,1,1,5)""",
                (post,previous,config))

    def test_version_and_frozen_cohort_are_immutable(self):
        config=self.config(alpha=100,k=100)
        with self.assertRaises(Exception):
            self.conn.execute(
                "UPDATE metric_calculation_configs SET alpha=200 WHERE id=%s",(config,))
        stage12 = self.conn.execute(
            "SELECT to_regclass('public.group_c_config_activation_events')"
        ).fetchone()[0] is not None
        if stage12:
            with self.assertRaises(Exception):
                self.conn.execute(
                    "UPDATE metric_calculation_configs SET is_active=TRUE WHERE id=%s",
                    (config,))
        else:
            self.conn.execute(
                "UPDATE metric_calculation_configs SET is_active=TRUE WHERE id=%s",
                (config,))
        self.assertEqual(self.conn.execute(
            "SELECT alpha FROM metric_calculation_configs WHERE id=%s",(config,)
        ).fetchone()[0],Decimal(100))
        cohort=self.cohort("creator")
        post=self.post()
        snap=self.snapshot(post)
        self.conn.execute("""INSERT INTO comparison_cohort_members
            (cohort_id,comparison_post_id,comparison_snapshot_id)
            VALUES (%s,%s,%s)""",(cohort,post,snap))
        self.conn.execute(
            "UPDATE comparison_cohorts SET is_frozen=TRUE WHERE id=%s",(cohort,))
        with self.assertRaises(Exception):
            self.conn.execute(
                "UPDATE comparison_cohorts SET median_views=999 WHERE id=%s",(cohort,))
        with self.assertRaises(Exception):
            self.conn.execute(
                """DELETE FROM comparison_cohort_members
                   WHERE cohort_id=%s AND comparison_post_id=%s""",(cohort,post))

    def test_previous_snapshot_same_post_and_purpose_matched(self):
        current_post=self.post()
        another_post=self.post()
        current=self.snapshot(current_post)
        wrong=self.snapshot(another_post)
        config=self.config()
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO calculated_metrics
                  (post_id,snapshot_id,previous_snapshot_id,calculation_config_id)
                  VALUES (%s,%s,%s,%s)""",(current_post,current,wrong,config))
        creator=self.cohort("creator")
        niche=self.cohort("niche")
        prior=self.cohort("engagement_prior")
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO calculated_metrics
                  (post_id,snapshot_id,calculation_config_id,creator_cohort_id)
                  VALUES (%s,%s,%s,%s)""",(current_post,current,config,niche))
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO calculated_metrics
                  (post_id,snapshot_id,calculation_config_id,prior_cohort_id)
                  VALUES (%s,%s,%s,%s)""",(current_post,current,config,creator))
        self.conn.execute(
            """INSERT INTO calculated_metrics
              (post_id,snapshot_id,calculation_config_id,creator_cohort_id,
               niche_cohort_id,prior_cohort_id)
              VALUES (%s,%s,%s,%s,%s,%s)""",
            (current_post,current,config,creator,niche,prior))
