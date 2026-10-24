"""Full licensed-data *fixture* E2E for A+B+C -> qualified -> descriptive read.

Uses a single isolated PostgreSQL transaction rolled back at end of each test.
No external API, live media analysis or fabricated permission for production.
All A/B/C/SRE facts in the transaction are explicitly supplied test observations.
"""
from __future__ import annotations
import json
import os
import unittest
from contextlib import contextmanager
from decimal import Decimal
from uuid import uuid4

from intelligence import migrate
from intelligence.analytics.service import run_qualification, list_unified
from intelligence.analytics.aggregate import aggregate_project
from intelligence.analytics.metrics import compute_c_metrics

@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "Disposable PostgreSQL required")
class CompleteQualifiedFixtureE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True) as c:
            migrate.apply_migrations(c)

    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn=psycopg.connect(os.environ["CM_TEST_PG_DSN"], row_factory=dict_row)
        self.pid=uuid4().hex
        self.sid="aql-licensed-test-"+uuid4().hex
        self.code=uuid4().hex
        self.created=[]

    def tearDown(self):
        # Never delete approved RTB or SRE records; roll back test transaction.
        self.conn.rollback()
        self.conn.close()

    @contextmanager
    def factory(self):
        yield self.conn

    def insert(self,query,args=()):
        return self.conn.execute(query,args).fetchone()

    def fixtures(self):
        q=self.insert
        q("""INSERT INTO project_registry
             (id,name,profile_revision,profile_hash,created_at,updated_at)
             VALUES (%s,'analytics licensed fixture',1,%s,now(),now())
             RETURNING id""",(self.pid,"0"*64))
        q("INSERT INTO sre_workspaces(project_id) VALUES (%s) RETURNING project_id",(self.pid,))
        q("""INSERT INTO sources
             (id,provider,source_type,status,can_store_metadata,
              can_analyze_media,can_store_metrics,can_derive_scores)
             VALUES (%s,'fixture','commercial','enabled',true,true,true,true)
             RETURNING id""",(self.sid,))
        self.policy=q("""INSERT INTO source_policy_versions
             (source_id,version,is_active,permissions,reviewed_at,retention_policy)
             VALUES(%s,1,true,%s::jsonb,now(),
                    '{"derived_retention_days":30}'::jsonb) RETURNING id""",
            (self.sid,json.dumps({k:True for k in (
              "can_store_metadata","can_analyze_media",
              "can_store_metrics","can_derive_scores")})))["id"]
        self.creator=q("""INSERT INTO creators(platform,platform_creator_id)
                         VALUES ('tiktok',%s) RETURNING id""",
                       ("aql-creator-"+uuid4().hex,))["id"]
        self.vid=q("""INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind,creator_id)
              VALUES('tiktok',%s,%s,'video',%s) RETURNING id""",
                   (self.code,"https://www.tiktok.com/@ci/video/"+self.code,self.creator))["id"]
        self.peer=q("""INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind,creator_id)
              VALUES('tiktok',%s,%s,'video',%s) RETURNING id""",
                   ("peer-"+self.code,"https://www.tiktok.com/@ci/video/peer-"+self.code,self.creator))["id"]
        q("INSERT INTO sre_project_videos(project_id,video_id) VALUES(%s,%s) RETURNING project_id",
          (self.pid,self.vid))
        # Real SRE schema: taxonomy starts draft, category is reviewed, then approved.
        taxonomy=q("""INSERT INTO sre_taxonomy_versions(project_id,version,status)
                VALUES(%s,1,'draft') RETURNING id""",(self.pid,))["id"]
        category=str(uuid4())
        rules={"include":["product used"],"exclude":["not the product"],
               "positive_examples":["approved fixture"],"negative_examples":["off-topic"]}
        q("""INSERT INTO sre_taxonomy_categories
           (project_id,taxonomy_version_id,category_key,name,relation_type,definition,
            rules,review_status,origin,independent)
           VALUES(%s,%s,%s,'Qualified theme','direct','Reviewed fixture theme',
                  %s::jsonb,'approved','user_added',true) RETURNING id""",
          (self.pid,taxonomy,category,json.dumps(rules)))
        q("""UPDATE sre_taxonomy_versions SET status='approved',
           approved_by='fixture-owner',approved_at=now()
           WHERE id=%s RETURNING id""",(taxonomy,))
        q("""UPDATE sre_workspaces SET active_taxonomy_id=%s
             WHERE project_id=%s RETURNING project_id""",(taxonomy,self.pid))
        self.sre_label=q("""INSERT INTO sre_human_labels
          (project_id,video_id,taxonomy_version_id,category_key,label,reviewer)
          VALUES (%s,%s,%s,%s,'match','fixture-owner') RETURNING id""",
          (self.pid,self.vid,taxonomy,category))["id"]
        # Group A: media evidence is a verified licensed asset, not just
        # a completed analysis row with no original source media.
        self.media=q("""INSERT INTO media_assets
          (video_id,source_id,media_kind,local_path,sha256,permission)
          VALUES (%s,%s,'video','fixture://approved-media',%s,'analysis')
          RETURNING id""",(self.vid,self.sid,"a"*64))["id"]
        self.a=q("""INSERT INTO video_feature_a_analyses
           (video_id,source_id,source_policy_version_id,media_asset_id,analysis_key,analysis_version,
            method_name,state,scene_status,speech_status,screen_text_status,audio_status,
            measured_at,duration_sec,aspect_ratio,spoken_words,speech_duration_sec,has_subtitles)
           VALUES (%s,%s,%s,%s,%s,'a/fixture-v1','fixture-measure','completed',
                   'complete','complete','complete','complete',now(),20,'9:16',32,12,'false')
           RETURNING id""",(self.vid,self.sid,self.policy,self.media,"aql-"+self.code))["id"]
        q("""INSERT INTO video_feature_a_scenes
           (analysis_id,scene_index,start_sec,end_sec)
           VALUES(%s,1,0,20) RETURNING analysis_id""",(self.a,))
        q("""INSERT INTO video_feature_a_speech_segments
           (analysis_id,start_sec,end_sec,spoken_text)
           VALUES (%s,1,13,'Test transcript') RETURNING id""",(self.a,))
        q("""INSERT INTO video_feature_a_screen_text
           (analysis_id,start_sec,end_sec,screen_text)
           VALUES (%s,0,4,'Fixture title') RETURNING id""",(self.a,))
        # Group B parent is pending while semantic components are populated.
        self.b=q("""INSERT INTO content_b_analyses
           (video_id,source_id,source_policy_version_id,video_a_analysis_id,
            analysis_key,analysis_version,taxonomy_version,content_type,method_name,state)
           VALUES (%s,%s,%s,%s,%s,'b/fixture-v1','1.0','video','manual-test','pending')
           RETURNING id""",(self.vid,self.sid,self.policy,self.a,"bql-"+self.code))["id"]
        q("""INSERT INTO content_b_hooks
           (analysis_id,spoken_text,mechanisms,delivery,classification_status)
           VALUES(%s,'Why does this work?',ARRAY['curiosity'],ARRAY['speech'],'inferred')
           RETURNING analysis_id""",(self.b,))
        q("""INSERT INTO content_b_formats
           (analysis_id,video_primary,classification_status)
           VALUES (%s,'talking_head','inferred') RETURNING analysis_id""",(self.b,))
        q("""INSERT INTO content_b_story_frames
           (analysis_id,frame_index,time_sec,role,needs_review)
           VALUES(%s,1,0,'hook',false) RETURNING frame_index""",(self.b,))
        q("""INSERT INTO content_b_visual_content
           (analysis_id,ai_generated,person_visible,product_visible,logo_visible)
           VALUES(%s,NULL,true,false,false) RETURNING analysis_id""",(self.b,))
        q("""INSERT INTO content_b_editing
           (analysis_id,editing_type,cut_count,cuts_per_minute,avg_shot_duration,cuts_first_5_sec)
           VALUES(%s,'single_take',0,0,20,0) RETURNING analysis_id""",(self.b,))
        q("""INSERT INTO content_b_subtitles(analysis_id,subtitle_type)
           VALUES(%s,'none') RETURNING analysis_id""",(self.b,))
        q("""INSERT INTO content_b_cta_analyses(analysis_id,analysis_status)
           VALUES (%s,'complete') RETURNING analysis_id""",(self.b,))
        cat=q("""INSERT INTO content_b_categories(slug,name)
                 VALUES (%s,'Analytics fixture category') RETURNING id""",
                 ("aql_"+uuid4().hex,))["id"]
        topic=q("""INSERT INTO content_b_topics(category_id,slug,name)
                   VALUES (%s,%s,'Fixture topic') RETURNING id""",
                   (cat,"aql_"+uuid4().hex))["id"]
        q("""INSERT INTO content_b_topic_assignments
          (analysis_id,category_id,topic_id,taxonomy_version,needs_review)
          VALUES (%s,%s,%s,'1.0',false) RETURNING analysis_id""",
          (self.b,cat,topic))
        q("""UPDATE content_b_analyses SET state='completed',analyzed_at=now()
             WHERE id=%s RETURNING id""",(self.b,))
        # Group C: observed_at evidence tied to same source, definition and
        # version, one comparable same-author peer and frozen cohort sets.
        metadata=q("""INSERT INTO source_video_metadata
           (video_id,source_id,source_policy_version_id,fetched_at,published_at)
           VALUES (%s,%s,%s,'2026-10-22 12:00+00','2026-10-22 10:00+00')
           RETURNING id""",(self.vid,self.sid,self.policy))["id"]
        self.m0=q("""INSERT INTO metric_snapshots
           (video_id,source_id,source_policy_version_id,observed_at,
            views,likes,comments,shares,source_metric_definition,source_metric_version)
           VALUES (%s,%s,%s,'2026-10-23 09:00+00',
                   240,20,3,2,'video_views/v1','1') RETURNING id""",
                   (self.vid,self.sid,self.policy))["id"]
        self.m1=q("""INSERT INTO metric_snapshots
           (video_id,source_id,source_policy_version_id,observed_at,
            views,likes,comments,shares,source_metric_definition,source_metric_version)
           VALUES (%s,%s,%s,'2026-10-23 11:00+00',
                   300,30,6,4,'video_views/v1','1') RETURNING id""",
                   (self.vid,self.sid,self.policy))["id"]
        peer_snap=q("""INSERT INTO metric_snapshots
           (video_id,source_id,source_policy_version_id,observed_at,
            views,likes,comments,shares,source_metric_definition,source_metric_version)
           VALUES (%s,%s,%s,'2026-10-23 08:00+00',
                   120,10,1,1,'video_views/v1','1') RETURNING id""",
                   (self.peer,self.sid,self.policy))["id"]
        self.cfg=q("""INSERT INTO metric_calculation_configs
            (calculation_version,alpha,bayesian_k,is_active)
            VALUES (%s,5,10,true) RETURNING id""",
            ("aql-config-"+uuid4().hex,))["id"]
        q("""INSERT INTO analytics_config_approvals
           (project_id,source_id,config_id,approved_by,approval_reason,
            min_creator_posts,min_prior_posts,min_niche_posts)
           VALUES (%s,%s,%s,'fixture-owner','reviewed fixture cohort rules',1,1,1)
           RETURNING config_id""",(self.pid,self.sid,self.cfg))
        cohort_common="definition_version,platform,content_type,source_id,source_metric_definition,evaluated_as_of,post_count,median_views"
        creator_cohort=q("""INSERT INTO comparison_cohorts
           (purpose,creator_id,"""+cohort_common+""")
           VALUES ('creator',%s,'aql-fixture-v1','tiktok','video',%s,'video_views/v1',
                   '2026-10-23 10:00+00',1,120) RETURNING id""",
            (self.creator,self.sid))["id"]
        prior_cohort=q("""INSERT INTO comparison_cohorts
           (purpose,"""+cohort_common+""",prior_like_rate,prior_comment_rate,prior_share_rate)
           VALUES ('engagement_prior','aql-fixture-v1','tiktok','video',%s,'video_views/v1',
                   '2026-10-23 10:00+00',1,120,0.08,0.02,0.01) RETURNING id""",
            (self.sid,))["id"]
        for coh in (creator_cohort,prior_cohort):
            q("""INSERT INTO comparison_cohort_members
               (cohort_id,comparison_post_id,comparison_snapshot_id)
               VALUES(%s,%s,%s) RETURNING cohort_id""",(coh,self.peer,peer_snap))
            q("""UPDATE comparison_cohorts SET is_frozen=true WHERE id=%s RETURNING id""",(coh,))
            q("""INSERT INTO analytics_cohort_approvals
               (project_id,source_id,cohort_id,source_metric_version,
                definition_version,min_post_count,approved_by,approval_reason)
               VALUES(%s,%s,%s,'1','aql-fixture-v1',1,'fixture-owner',
                      'reviewed test members and frozen source metric version')
               RETURNING cohort_id""",(self.pid,self.sid,coh))

    def test_exact_real_db_full_qualified_lineage_and_aggregate(self):
        self.fixtures()
        result=run_qualification(self.pid,self.vid,self.sid,"full-aql-20261023",connect=self.factory)
        self.assertEqual(result["status"],"qualified",result["blockers"])
        self.assertEqual(result["blockers"],[])
        self.assertEqual(result["lineage"]["a_analysis_id"],self.a)
        self.assertEqual(result["lineage"]["b_analysis_id"],self.b)
        self.assertEqual(result["lineage"]["metric_snapshot_id"],self.m1)
        self.assertEqual(result["lineage"]["relevance_origin"],"human")
        self.assertEqual(result["lineage"]["source_policy_version_id"],self.policy)
        self.assertEqual(result["metrics_c"]["score_status"],"complete")
        self.assertIsNotNone(result["metrics_c"]["breakout_score"])
        self.assertEqual(result["features_b"]["format"]["video_primary"],"talking_head")
        read=list_unified(self.pid,connect=self.factory)
        self.assertEqual(len(read),1)
        self.assertEqual(read[0]["fingerprint"],result["fingerprint"])
        # No artificial C threshold: N=1 suppresses all descriptive segments.
        agg=aggregate_project(self.pid,self.sid,self.cfg,"hook_mechanism",2,connect=self.factory)
        self.assertEqual(agg["qualified_video_count"],1)
        self.assertEqual(agg["segments"],[])
        self.assertEqual(agg["low_sample_groups_suppressed"],1)
        # No double inserts on replay, same exact snapshot ID and C result ID.
        replay=run_qualification(self.pid,self.vid,self.sid,"full-aql-20261023",connect=self.factory)
        self.assertEqual(replay["id"],result["id"])
        self.assertEqual(self.insert("SELECT count(*) AS n FROM calculated_metrics WHERE post_id=%s",
                                      (self.vid,))["n"],1)

if __name__=="__main__":
    unittest.main()
