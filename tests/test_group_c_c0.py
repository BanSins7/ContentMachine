"""GC-MOD-06 C0 exact raw counters, timestamps, source versions and timeline.

PG17 tests use only an explicitly-disposable database; independent pure
tests do not connect to providers, QDP or a production store.
"""
from __future__ import annotations

import os
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from intelligence.group_c.contracts import ContractError
from intelligence.group_c.c0 import (
    C0Pair, C0Snapshot, C0_SCHEMA_VERSION, MAX_COUNTER, VIEW_DEFINITIONS,
    read_c0_pair, validate_c0_pair, validate_c0_snapshot,
)
from intelligence.group_c.d1 import PostgreSQLD1FactsReader

NOW = datetime.now(timezone.utc)


def raw(**update):
    data = dict(
        id=29, video_id=11, source_id="official",
        source_policy_version_id=7, observed_at=NOW-timedelta(hours=2),
        collected_at=NOW-timedelta(hours=1), expires_at=NOW+timedelta(days=1),
        views=100, likes=0, comments=3, shares=2, saves=None, followers=None,
        source_metric_definition="platform_post_views",
        source_metric_version="v1",
        data_quality_status="ok", precision="exact",
        observation_time_assumed=False,
    )
    data.update(update)
    return data


def check(data=None, *, now=NOW, require_score_fields=False,
          metric_definition="platform_post_views", metric_version="v1"):
    return validate_c0_snapshot(
        data if data is not None else raw(), video_id=11, source_id="official",
        source_policy_version_id=7, metric_definition=metric_definition,
        metric_version=metric_version, now=now,
        require_score_fields=require_score_fields,
    )


class TestC0Pure(unittest.TestCase):
    def test_null_is_not_zero_and_raw_is_frozen(self):
        s=check()
        self.assertIsInstance(s,C0Snapshot)
        self.assertEqual(s.schema_version,C0_SCHEMA_VERSION)
        self.assertEqual(s.views,100)
        self.assertEqual(s.likes,0)
        self.assertIsNone(s.saves)
        self.assertIsNone(s.followers)
        self.assertEqual(s.missing_score_fields,())
        self.assertTrue(s.ready_for_rate_math)
        with self.assertRaises(Exception):
            s.views=10

    def test_missing_optional_values_preserved(self):
        s=check(raw(views=None,likes=None,comments=None,shares=None))
        self.assertIsNone(s.views)
        self.assertFalse(s.ready_for_rate_math)
        self.assertEqual(s.missing_score_fields,("views","likes","comments","shares"))

    def test_mandatory_rate_inputs_incomplete(self):
        for name in ("views","likes","comments","shares"):
            with self.subTest(name=name),self.assertRaises(ContractError) as e:
                check(raw(**{name:None}),require_score_fields=True)
            self.assertEqual(e.exception.code,"missing_raw_counter")

    def test_zero_views_is_valid_fact_not_a_rate(self):
        val=check(raw(views=0))
        self.assertEqual(val.views,0)
        self.assertFalse(val.ready_for_rate_math)
        with self.assertRaises(ContractError) as e:
            check(raw(views=0),require_score_fields=True)
        self.assertEqual(e.exception.code,"zero_views_rate_undefined")

    def test_postgres_bigint_and_boolean_rejections(self):
        for field in ("views","likes","comments","shares","saves","followers"):
            for value in (-1,True,1.0,"0",MAX_COUNTER+1):
                with self.subTest(field=field,value=value),self.assertRaises(ContractError) as e:
                    check(raw(**{field:value}))
                self.assertEqual(e.exception.code,"invalid_raw_counter")
        self.assertEqual(check(raw(views=MAX_COUNTER)).views,MAX_COUNTER)

    def test_no_missing_columns_silently_coerced(self):
        for field in ("views","likes","comments","shares","saves","followers"):
            rec=raw()
            rec.pop(field)
            with self.subTest(field=field),self.assertRaises(ContractError) as e:
                check(rec)
            self.assertEqual(e.exception.code,"invalid_raw_counter")

    def test_view_is_not_reach_or_impressions(self):
        self.assertIn("platform_post_views",VIEW_DEFINITIONS)
        for name in ("reach","impressions","platform_post_reach","video_impressions",""):
            with self.subTest(name=name),self.assertRaises(ContractError) as e:
                check(raw(source_metric_definition=name),metric_definition=name)
            self.assertEqual(e.exception.code,"incompatible_metric_definition")

    def test_exact_version_and_policy_video_source(self):
        for update,code in (
            ({"video_id":12},"different_video_or_source"),
            ({"source_id":"elsewhere"},"different_video_or_source"),
            ({"source_policy_version_id":8},"source_policy_unavailable"),
            ({"source_metric_definition":"reach"},"incompatible_metric_definition"),
            ({"source_metric_version":"v2"},"incompatible_metric_version"),
        ):
            with self.subTest(update=update),self.assertRaises(ContractError) as e:
                check(raw(**update))
            self.assertEqual(e.exception.code,code)

    def test_invalid_quality_estimates_assumed_and_retraction(self):
        for change,code in (
            ({"data_quality_status":"partial"},"unverified_or_assumed_observation"),
            ({"data_quality_status":"unverified"},"unverified_or_assumed_observation"),
            ({"data_quality_status":"retracted"},"source_expired_or_retracted"),
            ({"data_quality_status":"counter_correction"},"counter_decreased"),
            ({"precision":"rounded"},"unverified_or_assumed_observation"),
            ({"precision":"estimated"},"unverified_or_assumed_observation"),
            ({"observation_time_assumed":True},"unverified_or_assumed_observation"),
            ({"observation_time_assumed":None},"unverified_or_assumed_observation"),
        ):
            with self.subTest(change=change),self.assertRaises(ContractError) as e:
                check(raw(**change))
            self.assertEqual(e.exception.code,code)

    def test_timestamp_boundaries(self):
        for change,code in (
            ({"observed_at":NOW+timedelta(seconds=1)},"invalid_observation_interval"),
            ({"observed_at":NOW+timedelta(hours=2),
              "collected_at":NOW+timedelta(hours=3)},"invalid_observation_interval"),
            ({"observed_at":NOW,"collected_at":NOW-timedelta(seconds=1)},"invalid_observation_interval"),
            ({"observed_at":datetime(2020,1,1)},"invalid_observation_interval"),
            ({"collected_at":None},"invalid_observation_interval"),
            ({"expires_at":NOW},"source_expired_or_retracted"),
            ({"expires_at":NOW-timedelta(seconds=1)},"source_expired_or_retracted"),
        ):
            with self.subTest(change=change),self.assertRaises(ContractError) as e:
                check(raw(**change))
            self.assertEqual(e.exception.code,code)
        self.assertEqual(check(raw(expires_at=None)).expires_at,None)

    def test_same_instant_different_timezone_is_valid(self):
        tz=timezone(timedelta(hours=3))
        snap=check(raw(observed_at=(NOW-timedelta(hours=2)).astimezone(tz)))
        self.assertEqual(snap.observed_at.tzinfo,timezone.utc)

    def test_c0_pair_growth_requires_exact_identity_and_time(self):
        prior=check(raw(id=28,observed_at=NOW-timedelta(hours=5),
                        collected_at=NOW-timedelta(hours=4),views=55))
        current=check()
        pair=validate_c0_pair(prior,current)
        self.assertIsInstance(pair,C0Pair)
        self.assertEqual(pair.elapsed,timedelta(hours=3))
        with self.assertRaises(ContractError) as e:
            validate_c0_pair(current,prior)
        self.assertEqual(e.exception.code,"invalid_observation_interval")
        with self.assertRaises(ContractError) as e:
            validate_c0_pair(replace(prior,observed_at=current.observed_at),current)
        self.assertEqual(e.exception.code,"invalid_observation_interval")
        with self.assertRaises(ContractError) as e:
            validate_c0_pair(replace(prior,snapshot_id=current.snapshot_id),current)
        self.assertEqual(e.exception.code,"missing_prior_snapshot")

    def test_pair_never_clamps_negative_view_correction(self):
        prior=check(raw(id=28,observed_at=NOW-timedelta(hours=5),
                        collected_at=NOW-timedelta(hours=4),views=150))
        with self.assertRaises(ContractError) as e:
            validate_c0_pair(prior,check())
        self.assertEqual(e.exception.code,"counter_decreased")

    def test_pair_missing_previous_view_is_not_a_zero(self):
        prior=check(raw(id=28,observed_at=NOW-timedelta(hours=5),
                        collected_at=NOW-timedelta(hours=4),views=None))
        with self.assertRaises(ContractError) as e:
            validate_c0_pair(prior,check())
        self.assertEqual(e.exception.code,"missing_raw_counter")

    def test_pair_current_optional_engagement_is_incomplete(self):
        prior=check(raw(id=28,observed_at=NOW-timedelta(hours=5),
                        collected_at=NOW-timedelta(hours=4),views=55))
        current=check(raw(comments=None))
        self.assertIsNotNone(validate_c0_pair(
            prior,current,require_score_fields=False))
        with self.assertRaises(ContractError) as e:
            validate_c0_pair(prior,current)
        self.assertEqual(e.exception.code,"missing_raw_counter")

    def test_reader_guards_do_not_mutate_source_snapshot(self):
        r=raw()
        clone=r.copy()
        check(r)
        self.assertEqual(r,clone)
        self.assertEqual(C0_SCHEMA_VERSION,"group-c-c0/v1")


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "requires disposable PostgreSQL17 CM_TEST_PG_DSN / GC_TEST_DISPOSABLE_DB=YES",
)
class TestC0Postgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.conn=psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True)
        version=int(cls.conn.execute("SHOW server_version_num").fetchone()[0])
        if not 170000<=version<180000:
            raise AssertionError("C0 DB tests require disposable PostgreSQL 17")
        migrate.apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.project=uuid4().hex
        self.source="gc-c0-"+uuid4().hex
        self.conn.execute("""INSERT INTO project_registry
          (id,name,profile_revision,profile_hash,created_at,updated_at)
          VALUES (%s,'C0 fixture',1,%s,now(),now())""",(self.project,"f"*64))
        self.conn.execute("INSERT INTO sre_workspaces(project_id) VALUES (%s)",(self.project,))
        self.conn.execute("""INSERT INTO sources
          (id,provider,source_type,status,can_store_metadata,
           can_store_metrics,can_derive_scores)
          VALUES (%s,'gc_test','manual','enabled',TRUE,TRUE,TRUE)""",(self.source,))
        self.policy=self.conn.execute("""INSERT INTO source_policy_versions
          (source_id,version,is_active,permissions)
          VALUES(%s,1,TRUE,%s::jsonb) RETURNING id""",
          (self.source,'{"can_store_metadata":true,"can_store_metrics":true,"can_derive_scores":true}')
        ).fetchone()[0]
        token=uuid4().hex
        self.video=self.conn.execute("""INSERT INTO videos
          (platform,platform_video_id,canonical_url,content_kind,c_content_type)
          VALUES('youtube',%s,%s,'video','video') RETURNING id""",
          (token,"https://youtube.com/watch?v="+token)).fetchone()[0]
        self.conn.execute("INSERT INTO sre_project_videos(project_id,video_id) VALUES(%s,%s)",
                          (self.project,self.video))
        self.metadata=self.conn.execute("""INSERT INTO source_video_metadata
          (video_id,source_id,source_policy_version_id,fetched_at,published_at)
          VALUES(%s,%s,%s,now()-interval '1 hour',now()-interval '5 days') RETURNING id""",
          (self.video,self.source,self.policy)).fetchone()[0]
        self.snapshots=[]
        for hours,views in ((6,20),(2,90)):
            self.snapshots.append(self.insert_snapshot(hours,views))

    def insert_snapshot(self,hours,views, *, status="ok",version="v2",likes=0):
        return self.conn.execute("""INSERT INTO metric_snapshots
          (video_id,source_id,source_policy_version_id,
           observed_at,collected_at,views,likes,comments,shares,
           source_metric_definition,source_metric_version,
           data_quality_status,precision,observation_time_assumed)
          VALUES(%s,%s,%s,now()-(%s*interval '1 hour'),now(),
                 %s,%s,2,1,'platform_post_views',%s,%s,'exact',FALSE)
          RETURNING id""",
          (self.video,self.source,self.policy,hours,views,likes,version,status)).fetchone()[0]

    def tearDown(self):
        self.conn.execute("DELETE FROM sre_workspaces WHERE project_id=%s",(self.project,))
        self.conn.execute("DELETE FROM project_registry WHERE id=%s",(self.project,))
        self.conn.execute("DELETE FROM videos WHERE id=%s",(self.video,))
        self.conn.execute("DELETE FROM sources WHERE id=%s",(self.source,))

    def pair(self,**change):
        data=dict(conn=self.conn,project_id=self.project,video_id=self.video,
                  source_id=self.source,source_policy_version_id=self.policy,
                  previous_snapshot_id=self.snapshots[0],snapshot_id=self.snapshots[1],
                  metadata_id=self.metadata,metric_definition="platform_post_views",
                  metric_version="v2")
        data.update(change)
        return read_c0_pair(**data)

    def test_db_pair_and_exact_nullable_raw_counters(self):
        pair=self.pair()
        self.assertEqual(pair.previous.views,20)
        self.assertEqual(pair.current.views,90)
        self.assertEqual(pair.current.likes,0)
        self.assertIsNone(pair.current.followers)
        self.assertIsNone(pair.current.saves)
        self.assertTrue(pair.current.ready_for_rate_math)
        self.assertEqual([r["id"] for r in
            PostgreSQLD1FactsReader().read_recent_raw_observations(
                self.conn,self.video,self.source,3)],list(reversed(self.snapshots)))

    def test_db_retracted_newer_snapshot_blocks_old_pair(self):
        extra=self.insert_snapshot(1,100,status="retracted",version="v3")
        with self.assertRaises(ContractError) as e:
            self.pair()
        self.assertEqual(e.exception.code,"metric_history_changed")

    def test_db_version_change_blocks_old_pair(self):
        self.insert_snapshot(1,120,version="v3")
        with self.assertRaises(ContractError) as e:
            self.pair()
        self.assertEqual(e.exception.code,"metric_history_changed")

    def test_db_counter_correction_blocks_growth(self):
        newer=self.insert_snapshot(1,50)
        with self.assertRaises(ContractError) as e:
            self.pair(previous_snapshot_id=self.snapshots[1],snapshot_id=newer)
        self.assertEqual(e.exception.code,"counter_decreased")

    def test_db_expired_or_missing_project_denied(self):
        with self.assertRaises(ContractError):
            self.pair(project_id=uuid4().hex)
        self.conn.execute("""UPDATE source_video_metadata
          SET expires_at=now()-interval '1 second' WHERE id=%s""",
          (self.metadata,))
        with self.assertRaises(ContractError):
            self.pair()


if __name__=="__main__":
    unittest.main()
