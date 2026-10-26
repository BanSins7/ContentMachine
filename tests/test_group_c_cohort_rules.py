"""GC-MOD-09: versioned explicit peer strata and disposable PG17 raw-latest QA."""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from intelligence.group_c.cohort_rules import (
    COHORT_RULES_VERSION, CohortRules, PostgreSQLCohortCandidates,
    select_compatible_peers,
)
from intelligence.group_c.contracts import ContractError

T = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)


def rules(**changes):
    options = dict(
        project_id="a"*32,target_video_id=99,purpose="creator",
        platform="youtube",content_type="video",
        source_id="verified-views",source_policy_version_id=4,
        metric_definition="platform_post_views",metric_version="v3",
        evaluated_as_of=T,
        published_from=T-timedelta(days=10),
        published_before=T-timedelta(days=1),
        min_age_hours=Decimal("12"),max_age_hours=Decimal("168"),
        creator_id=7,topic_id=None,max_candidates=10,
    )
    options.update(changes)
    return CohortRules(**options)


def peer(**changes):
    item = dict(
        project_id="a"*32,
        video_id=201,snapshot_id=301,metadata_id=401,
        platform="youtube",content_kind="video",c_content_type="video",
        creator_id=7,topic_id=9,
        source_id="verified-views",source_policy_version_id=4,
        metadata_policy_version_id=4,
        source_metric_definition="platform_post_views",source_metric_version="v3",
        observed_at=T-timedelta(hours=2),
        collected_at=T-timedelta(hours=1),
        published_at=T-timedelta(days=5),
        fetched_at=T-timedelta(hours=4),
        expires_at=None,metadata_expires_at=None,is_current=True,
        deleted_at=None, data_quality_status="ok",
        precision="exact",observation_time_assumed=False,
        views=120,likes=12,comments=3,shares=1,
    )
    item.update(changes)
    return item


class TestGroupCCohortRulesPure(unittest.TestCase):
    def test_creator_success_not_approved_and_frozen(self):
        result=select_compatible_peers(rules(),[peer()])
        self.assertEqual(result.post_count,1)
        self.assertEqual(result.admitted[0].snapshot_id,301)
        self.assertEqual(result.admitted[0].age_hours,Decimal("118"))
        self.assertEqual(result.rejected,())
        self.assertEqual(result.status,"candidates_only")
        self.assertFalse(result.approved)
        with self.assertRaises(FrozenInstanceError):
            result.rules.creator_id=7

    def test_explicit_purpose_strata(self):
        for spec in (
            rules(purpose="creator",creator_id=7),
            rules(purpose="niche",creator_id=None,topic_id=9),
            rules(purpose="engagement_prior",creator_id=None,topic_id=9),
            rules(purpose="engagement_prior",creator_id=7,topic_id=9),
        ):
            with self.subTest(purpose=spec.purpose):
                self.assertEqual(select_compatible_peers(spec,[peer()]).post_count,1)
        for changes in (
            {"purpose":"engagement_prior","creator_id":None,"topic_id":None},
            {"purpose":"niche","creator_id":None,"topic_id":None},
            {"purpose":"creator","creator_id":None},
            {"purpose":"mystery"}, {"platform":"reddit"}, {"content_type":"image"},
        ):
            with self.subTest(changes=changes),self.assertRaises(ContractError):
                rules(**changes)

    def test_exact_metric_source_platform_and_kind(self):
        cases = (
            ({"project_id":"b"*32},"cross_project_peer"),
            ({"source_id":"other"},"different_video_or_source"),
            ({"source_policy_version_id":5},"source_policy_unavailable"),
            ({"metadata_policy_version_id":5},"source_policy_unavailable"),
            ({"source_metric_definition":"reach"},"incompatible_metric_definition"),
            ({"source_metric_version":"v4"},"incompatible_metric_version"),
            ({"platform":"tiktok"},"incompatible_peer_kind"),
            ({"c_content_type":"carousel"},"incompatible_peer_kind"),
            ({"content_kind":"photo_carousel"},"incompatible_peer_kind"),
            ({"creator_id":8},"incompatible_peer_creator"),
        )
        for changes, reason in cases:
            with self.subTest(changes=changes):
                selected=select_compatible_peers(rules(),[peer(**changes)])
                self.assertEqual(selected.post_count,0)
                self.assertEqual(selected.rejected[0].reason,reason)

    def test_topic_and_prior_require_exact_match(self):
        r=rules(purpose="niche",creator_id=None,topic_id=9)
        self.assertEqual(select_compatible_peers(r,[peer(topic_id=10)]).rejected[0].reason,
                         "incompatible_peer_topic")
        prior=rules(purpose="engagement_prior",topic_id=9,creator_id=None)
        for changes in (
            {"views":0},{"views":None},{"likes":None},
            {"comments":None},{"shares":None},
        ):
            selection=select_compatible_peers(prior,[peer(**changes)])
            self.assertEqual(selection.post_count,0)
            self.assertEqual(selection.rejected[0].reason,"missing_raw_counter")
        self.assertEqual(select_compatible_peers(rules(),[peer(views=0)]).post_count,1)

    def test_self_exclusion_and_duplicate_prevention(self):
        selection=select_compatible_peers(rules(),[peer(video_id=99)])
        self.assertEqual(selection.rejected[0].reason,"self_inclusion")
        with self.assertRaises(ContractError) as e:
            select_compatible_peers(rules(),[peer(),peer(snapshot_id=302)])
        self.assertEqual(e.exception.code,"duplicate_peer_video")

    def test_time_and_age_windows_are_mandatory(self):
        for changes in (
            {"evaluated_as_of":datetime(2026,10,26,15)},
            {"published_from":T-timedelta(hours=1)},
            {"published_before":T+timedelta(hours=1)},
            {"min_age_hours":-1}, {"min_age_hours":float("nan")},
            {"max_age_hours":Decimal("NaN")},
            {"min_age_hours":1000}, {"max_age_hours":1},
        ):
            with self.subTest(changes=changes),self.assertRaises(ContractError):
                rules(**changes)
        for row in (
            peer(published_at=T-timedelta(days=11)),
            peer(published_at=T-timedelta(hours=1)),
            peer(observed_at=T+timedelta(minutes=1)),
        ):
            self.assertEqual(select_compatible_peers(rules(),[row]).post_count,0)
        young=peer(published_at=T-timedelta(hours=3),fetched_at=T-timedelta(hours=1))
        self.assertEqual(select_compatible_peers(
            rules(published_before=T, min_age_hours=12),[young]).post_count,0)

    def test_invalid_quality_and_temporal_evidence(self):
        cases=(
            ({"data_quality_status":"retracted"},"unverified_or_assumed_observation"),
            ({"data_quality_status":"counter_correction"},"unverified_or_assumed_observation"),
            ({"precision":"estimated"},"unverified_or_assumed_observation"),
            ({"observation_time_assumed":True},"unverified_or_assumed_observation"),
            ({"is_current":False},"peer_publication_unavailable"),
            ({"deleted_at":T},"peer_publication_unavailable"),
            ({"expires_at":T},"peer_expired"),
            ({"metadata_expires_at":T},"peer_expired"),
            ({"observed_at":T-timedelta(hours=1),
              "collected_at":T-timedelta(hours=3)},"invalid_peer_timestamps"),
        )
        for change,code in cases:
            with self.subTest(change=change):
                selection=select_compatible_peers(rules(),[peer(**change)])
                self.assertEqual(selection.rejected[0].reason,code)

    def test_explicit_bound_no_silent_truncation(self):
        with self.assertRaises(ContractError) as e:
            select_compatible_peers(rules(max_candidates=1),[
                peer(),peer(video_id=202,snapshot_id=302,metadata_id=402)])
        self.assertEqual(e.exception.code,"cohort_population_overflow")
        for invalid in (0,501,True,None,1.5):
            with self.subTest(invalid=invalid),self.assertRaises(ContractError):
                rules(max_candidates=invalid)

    def test_utc_canonical_sha_and_repeatable_sort(self):
        r=rules()
        plus=timezone(timedelta(hours=3))
        alt=replace(r,
            evaluated_as_of=r.evaluated_as_of.astimezone(plus),
            published_from=r.published_from.astimezone(plus),
            published_before=r.published_before.astimezone(plus),
            min_age_hours="12.0",max_age_hours="168.000")
        self.assertEqual(r.sha256,alt.sha256)
        first=select_compatible_peers(r,[
            peer(video_id=202,snapshot_id=302,metadata_id=402),peer()])
        second=select_compatible_peers(alt,[
            peer(),peer(video_id=202,snapshot_id=302,metadata_id=402)])
        self.assertEqual(first.selection_sha256,second.selection_sha256)
        self.assertEqual([p.video_id for p in first.admitted],[201,202])

    def test_any_missing_stratum_is_rejected_not_defaulted(self):
        for changes in (
            {"source_id":""},{"source_policy_version_id":None},
            {"metric_version":None},{"metric_definition":"impressions"},
            {"target_video_id":True},{"project_id":"A"*32},
            {"schema_version":"group-c-cohort-rules/v0"},
        ):
            with self.subTest(changes=changes),self.assertRaises(ContractError):
                rules(**changes)

    def test_reject_future_fetched_metadata_at_historical_cutoff(self):
        choice=select_compatible_peers(rules(),[
            peer(fetched_at=T+timedelta(minutes=1))])
        self.assertEqual(choice.post_count,0)
        self.assertEqual(choice.rejected[0].reason,"invalid_peer_timestamps")

    def test_fingerprint_ignores_decimal_global_context(self):
        from decimal import ROUND_DOWN, localcontext
        r=rules(min_age_hours="120.00000001",max_age_hours="168.000")
        stable=r.sha256
        with localcontext() as ctx:
            ctx.prec=4
            ctx.rounding=ROUND_DOWN
            self.assertEqual(r.sha256,stable)

    def test_no_auto_median_priors_or_c7(self):
        result=select_compatible_peers(rules(),[peer()])
        self.assertFalse(hasattr(result,"median_views"))
        self.assertFalse(hasattr(result,"prior_like_rate"))
        self.assertFalse(hasattr(result,"breakout_score"))
        self.assertEqual(result.rules.schema_version,COHORT_RULES_VERSION)

    def test_invalid_rows_and_bool_ids_block_whole_batch(self):
        for row in (None,{},peer(video_id=True),peer(snapshot_id="301")):
            with self.subTest(row=row),self.assertRaises(ContractError):
                select_compatible_peers(rules(),[row])

    def test_pure_input_rows_not_mutated(self):
        r=peer()
        copy=r.copy()
        select_compatible_peers(rules(),[r])
        self.assertEqual(r,copy)


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "requires disposable PostgreSQL17 (GC_TEST_DISPOSABLE_DB=YES)",
)
class TestGroupCCohortRulesPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.conn=psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True)
        if int(cls.conn.execute("SHOW server_version_num").fetchone()[0]) // 10000 != 17:
            raise AssertionError("Disposable PostgreSQL17 required")
        migrate.apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.project=uuid4().hex
        self.source="gc09-"+uuid4().hex
        self.conn.execute("""INSERT INTO project_registry
          (id,name,profile_revision,profile_hash,created_at,updated_at)
          VALUES(%s,'cohort test',1,%s,now(),now())""",(self.project,"f"*64))
        self.conn.execute("INSERT INTO sre_workspaces(project_id) VALUES (%s)",(self.project,))
        self.conn.execute("""INSERT INTO sources
          (id,provider,source_type,status,can_store_metadata,
           can_store_metrics,can_derive_scores,retention_days)
          VALUES(%s,'fixture','manual','enabled',TRUE,TRUE,TRUE,30)""",(self.source,))
        self.policy=self.conn.execute("""INSERT INTO source_policy_versions
          (source_id,version,is_active,permissions,retention_policy)
          VALUES(%s,1,TRUE,%s::jsonb,'{"retention_days":30}'::jsonb)
          RETURNING id""",(self.source,'{"can_store_metadata":true,"can_store_metrics":true,"can_derive_scores":true}')).fetchone()[0]
        self.creator=self.conn.execute("""INSERT INTO creators(platform,platform_creator_id)
          VALUES('youtube',%s) RETURNING id""",(uuid4().hex,)).fetchone()[0]
        self.target,self.target_snap,self.target_md=self.video(0)
        self.peer,self.peer_snap,self.peer_md=self.video(1)

    def video(self,index):
        token=uuid4().hex
        vid=self.conn.execute("""INSERT INTO videos
          (platform,platform_video_id,canonical_url,content_kind,c_content_type,creator_id)
          VALUES('youtube',%s,%s,'video','video',%s) RETURNING id""",
          (token,"https://youtube.com/watch?v="+token,self.creator)).fetchone()[0]
        self.conn.execute("""INSERT INTO sre_project_videos(project_id,video_id)
          VALUES(%s,%s)""",(self.project,vid))
        md=self.conn.execute("""INSERT INTO source_video_metadata
          (video_id,source_id,source_policy_version_id,fetched_at,published_at)
          VALUES(%s,%s,%s,now()-interval '5 hours',
                 now()-interval '5 days') RETURNING id""",(vid,self.source,self.policy)).fetchone()[0]
        sid=self.conn.execute("""INSERT INTO metric_snapshots
          (video_id,source_id,source_policy_version_id,observed_at,collected_at,
           views,likes,comments,shares,source_metric_definition,
           source_metric_version,data_quality_status,precision,observation_time_assumed)
          VALUES(%s,%s,%s,now()-interval '2 hours',now()-interval '1 hour',
                 100,10,3,2,'platform_post_views','v3','ok','exact',FALSE)
          RETURNING id""",(vid,self.source,self.policy)).fetchone()[0]
        return vid,sid,md

    def tearDown(self):
        self.conn.execute("DELETE FROM sre_workspaces WHERE project_id=%s",(self.project,))
        self.conn.execute("DELETE FROM project_registry WHERE id=%s",(self.project,))
        self.conn.execute("DELETE FROM videos WHERE creator_id=%s",(self.creator,))
        self.conn.execute("DELETE FROM creators WHERE id=%s",(self.creator,))
        self.conn.execute("DELETE FROM sources WHERE id=%s",(self.source,))

    def strata(self,**kw):
        now=datetime.now(timezone.utc)-timedelta(minutes=1)
        data=dict(project_id=self.project,target_video_id=self.target,
                  purpose="creator",creator_id=self.creator,topic_id=None,
                  platform="youtube",content_type="video",source_id=self.source,
                  source_policy_version_id=self.policy,metric_definition="platform_post_views",
                  metric_version="v3",evaluated_as_of=now,
                  published_from=now-timedelta(days=10),
                  published_before=now-timedelta(days=1),
                  min_age_hours=Decimal("0"),max_age_hours=Decimal("168"),
                  max_candidates=20)
        data.update(kw)
        return CohortRules(**data)

    def read(self,**kw):
        return PostgreSQLCohortCandidates().select(
            self.conn,rules=kw.get("rules",self.strata()),
            target_snapshot_id=self.target_snap,target_metadata_id=self.target_md)

    def test_exact_project_creator_and_source_peers(self):
        result=self.read()
        self.assertEqual(result.status,"candidates_only")
        self.assertFalse(result.approved)
        self.assertEqual([(r.video_id,r.snapshot_id,r.metadata_id) for r in result.admitted],
                         [(self.peer,self.peer_snap,self.peer_md)])
        self.assertNotEqual(result.admitted[0].video_id,self.target)

    def test_newer_retracted_or_version_changed_source_never_falls_back(self):
        extra=self.conn.execute("""INSERT INTO metric_snapshots
          (video_id,source_id,source_policy_version_id,observed_at,collected_at,
           views,likes,comments,shares,source_metric_definition,source_metric_version,
           data_quality_status,precision,observation_time_assumed)
          VALUES(%s,%s,%s,now()-interval '30 minutes',now()-interval '20 minutes',
                 110,10,3,2,'platform_post_views','v4','retracted','exact',FALSE)
          RETURNING id""",(self.peer,self.source,self.policy)).fetchone()[0]
        # Select a later cutoff, still not in the future.
        r=self.strata(evaluated_as_of=datetime.now(timezone.utc)-timedelta(minutes=1))
        chosen=self.read(rules=r)
        self.assertEqual(chosen.post_count,0)
        self.assertEqual(chosen.rejected[0].snapshot_id,extra)
        self.assertEqual(chosen.rejected[0].reason,"incompatible_metric_version")

    def test_target_must_match_age_and_publication_window(self):
        with self.assertRaises(ContractError) as err:
            self.read(rules=self.strata(max_age_hours=Decimal("60")))
        self.assertEqual(err.exception.code,"incompatible_target_age")

    def test_target_newer_raw_snapshot_invalidates_old_evidence(self):
        self.conn.execute("""INSERT INTO metric_snapshots
          (video_id,source_id,source_policy_version_id,observed_at,collected_at,
           views,likes,comments,shares,source_metric_definition,source_metric_version,
           data_quality_status,precision,observation_time_assumed)
          VALUES(%s,%s,%s,now()-interval '30 minutes',now()-interval '20 minutes',
                 110,10,3,2,'platform_post_views','v99','ok','exact',FALSE)""",
          (self.target,self.source,self.policy))
        with self.assertRaises(ContractError) as err:
            self.read()
        self.assertEqual(err.exception.code,"metric_history_changed")

    def test_source_revocation_and_other_project_block(self):
        wrong=self.strata(project_id=uuid4().hex)
        with self.assertRaises(ContractError):
            self.read(rules=wrong)
        self.conn.execute("UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",(self.source,))
        with self.assertRaises(ContractError):
            self.read()

    def test_candidate_bound_never_truncates_and_returns_unapproved(self):
        another=self.video(2)
        with self.assertRaises(ContractError) as error:
            self.read(rules=self.strata(max_candidates=1))
        self.assertEqual(error.exception.code,"cohort_population_overflow")


if __name__=="__main__":
    unittest.main()
