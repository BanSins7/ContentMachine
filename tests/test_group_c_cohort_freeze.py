"""GC-MOD-10: immutable median/prior evidence + serializable PG17 freeze tests."""
from __future__ import annotations

import os
import re
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, localcontext
from pathlib import Path
from uuid import uuid4

from intelligence.group_c.cohort_rules import (
    CohortRules, select_compatible_peers,
)
from intelligence.group_c.cohort_freeze import (
    ESTIMATOR_VERSION, CohortFreezeReceipt, PostgreSQLCohortFreezeWriter,
    build_frozen_evidence,
)
from intelligence.group_c.contracts import ContractError

HERE = Path(__file__).resolve().parents[1]
MIGRATION = HERE / "intelligence" / "migrations" / "057_group_c_cohort_freezes.sql"
T = datetime(2026,10,26,14,tzinfo=timezone.utc)


def rules(**update):
    data = dict(
        project_id="a"*32,target_video_id=99,purpose="creator",
        platform="youtube",content_type="video",
        source_id="fixture",source_policy_version_id=13,
        metric_definition="platform_post_views",metric_version="v3",
        evaluated_as_of=T,published_from=T-timedelta(days=10),
        published_before=T-timedelta(days=1),
        min_age_hours=Decimal("0"),max_age_hours=Decimal("168"),
        creator_id=8,topic_id=None,max_candidates=10,
    )
    data.update(update)
    return CohortRules(**data)


def peer(n=1, views=100, **override):
    value=dict(
        video_id=200+n,snapshot_id=300+n,metadata_id=400+n,
        project_id="a"*32,platform="youtube",content_kind="video",
        c_content_type="video",creator_id=8,topic_id=77,
        source_id="fixture",source_policy_version_id=13,
        metadata_policy_version_id=13,
        source_metric_definition="platform_post_views",
        source_metric_version="v3",
        observed_at=T-timedelta(hours=2),
        collected_at=T-timedelta(hours=1),
        published_at=T-timedelta(days=5),
        fetched_at=T-timedelta(hours=4),
        expires_at=None,metadata_expires_at=None,
        is_current=True,deleted_at=None,precision="exact",
        observation_time_assumed=False,data_quality_status="ok",
        views=views, likes=views//10, comments=views//50, shares=views//100,
    )
    value.update(override)
    return value


def selection(*rows, rule=None):
    return select_compatible_peers(rule or rules(),list(rows))


class TestGroupCCohortFreezePure(unittest.TestCase):
    def test_odd_exact_median_preserves_raw_counts(self):
        proof=build_frozen_evidence(selection(
            peer(1,101),peer(2,205),peer(3,503)))
        self.assertEqual(proof.median_views,Decimal("205.000000000000"))
        self.assertIsNone(proof.prior_like_rate)
        self.assertEqual(proof.selection.post_count,3)
        self.assertEqual(proof.estimator_version,ESTIMATOR_VERSION)

    def test_even_median_half_unit_postgres_scale(self):
        proof=build_frozen_evidence(selection(peer(1,100),peer(2,101)))
        self.assertEqual(proof.median_views,Decimal("100.500000000000"))

    def test_zero_views_median_is_real_not_missing(self):
        proof=build_frozen_evidence(selection(peer(1,0),peer(2,0)))
        self.assertEqual(proof.median_views,Decimal("0.000000000000"))

    def test_niche_same_median_and_no_prior(self):
        config=rules(purpose="niche",creator_id=None,topic_id=77)
        proof=build_frozen_evidence(selection(peer(1,100),peer(2,400),rule=config))
        self.assertEqual(proof.median_views,Decimal("250.000000000000"))
        self.assertIsNone(proof.prior_share_rate)

    def test_pooled_prior_exact_not_mean_of_post_rates(self):
        cfg=rules(purpose="engagement_prior",creator_id=8,topic_id=None)
        s=selection(peer(1,100,likes=15,comments=1,shares=2),
                    peer(2,300,likes=15,comments=7,shares=2),rule=cfg)
        proof=build_frozen_evidence(s)
        self.assertIsNone(proof.median_views)
        self.assertEqual(proof.prior_like_rate,Decimal("0.075000000000"))
        self.assertEqual(proof.prior_comment_rate,Decimal("0.020000000000"))
        self.assertEqual(proof.prior_share_rate,Decimal("0.010000000000"))

    def test_fractional_prior_round_half_up(self):
        cfg=rules(purpose="engagement_prior")
        s=selection(peer(1,3,likes=1,comments=0,shares=0),rule=cfg)
        proof=build_frozen_evidence(s)
        self.assertEqual(proof.prior_like_rate,Decimal("0.333333333333"))

    def test_zero_denominator_is_not_default_prior(self):
        cfg=rules(purpose="engagement_prior")
        selected=selection(peer(1,0),rule=cfg)
        self.assertEqual(selected.post_count,0)
        with self.assertRaises(ContractError) as e:
            build_frozen_evidence(selected)
        self.assertEqual(e.exception.code,"empty_cohort")

    def test_empty_cohort_cannot_be_frozen(self):
        result=selection(peer(1,100,data_quality_status="retracted"))
        self.assertEqual(result.post_count,0)
        with self.assertRaises(ContractError) as e:
            build_frozen_evidence(result)
        self.assertEqual(e.exception.code,"empty_cohort")

    def test_tamper_source_counters_changes_freeze_digest(self):
        a=build_frozen_evidence(selection(peer(1,100),peer(2,300)))
        b=build_frozen_evidence(selection(peer(1,100),peer(2,301)))
        self.assertNotEqual(a.evidence_sha256,b.evidence_sha256)
        self.assertEqual(a.selection.rules.sha256,b.selection.rules.sha256)

    def test_order_and_global_decimal_context_do_not_change_evidence(self):
        a=build_frozen_evidence(selection(peer(1,100),peer(2,301)))
        with localcontext() as ctx:
            ctx.prec=6
            ctx.rounding=ROUND_DOWN
            b=build_frozen_evidence(selection(peer(2,301),peer(1,100)))
        self.assertEqual(a.evidence_sha256,b.evidence_sha256)
        self.assertEqual(a.median_views,b.median_views)

    def test_frozen_pure_structure_and_never_approval(self):
        proof=build_frozen_evidence(selection(peer(1,100)))
        with self.assertRaises(FrozenInstanceError):
            proof.median_views=Decimal(0)
        self.assertEqual(proof.estimator_version,"group-c-cohort-estimator/v1")
        self.assertFalse(hasattr(proof,"approved"))
        self.assertFalse(hasattr(proof,"score_status"))

    def test_missing_median_prior_do_not_turn_into_default(self):
        for config in (rules(purpose="creator"),rules(purpose="engagement_prior")):
            choice=selection(peer(1,100,source_metric_version="v4"),rule=config)
            with self.subTest(purpose=config.purpose),self.assertRaises(ContractError):
                build_frozen_evidence(choice)

    def test_migration_static_immutability_and_enforcement(self):
        sql=MIGRATION.read_text(encoding="utf-8").lower()
        self.assertIn("create table group_c_cohort_freezes",sql)
        self.assertIn("create table group_c_cohort_freeze_members",sql)
        self.assertIn("deferrable initially deferred",sql)
        self.assertIn("source_metric_version text not null",sql)
        self.assertIn("on delete restrict",sql)
        self.assertIn("frozen group c cohort evidence is immutable",sql)
        self.assertNotRegex(sql,r"\b(?:drop table|truncate table)\b")
        for old in ("metric_snapshots","source_video_metadata","comparison_cohorts",
                    "comparison_cohort_members"):
            self.assertNotRegex(sql,r"(?m)^\s*alter table\s+"+old+r"\b")


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "requires disposable PG17 and GC_TEST_DISPOSABLE_DB=YES",
)
class TestGroupCCohortFreezePostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.psycopg=psycopg
        cls.conn=psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True)
        if int(cls.conn.execute("SHOW server_version_num").fetchone()[0])//10000 !=17:
            raise AssertionError("PostgreSQL17 disposable required")
        migrate.apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.project=uuid4().hex
        self.source="gc10-"+uuid4().hex
        self.conn.execute("""INSERT INTO project_registry
          (id,name,profile_revision,profile_hash,created_at,updated_at)
          VALUES(%s,'cohort freeze',1,%s,now(),now())""",
          (self.project,"c"*64))
        self.conn.execute("INSERT INTO sre_workspaces(project_id) VALUES(%s)",(self.project,))
        self.conn.execute("""INSERT INTO sources
          (id,provider,source_type,status,can_store_metadata,
           can_store_metrics,can_derive_scores,retention_days)
          VALUES(%s,'fixture','manual','enabled',TRUE,TRUE,TRUE,30)""",(self.source,))
        self.policy=self.conn.execute("""INSERT INTO source_policy_versions
          (source_id,version,is_active,permissions,retention_policy)
          VALUES(%s,1,TRUE,%s::jsonb,'{"retention_days":30}'::jsonb)
          RETURNING id""",(self.source,
          '{"can_store_metadata":true,"can_store_metrics":true,"can_derive_scores":true}')).fetchone()[0]
        self.creator=self.conn.execute("""INSERT INTO creators(platform,platform_creator_id)
          VALUES('youtube',%s) RETURNING id""",(uuid4().hex,)).fetchone()[0]
        self.target=self.video(100)
        self.peers=[self.video(101),self.video(301)]

    def video(self,views):
        token=uuid4().hex
        vid=self.conn.execute("""INSERT INTO videos
          (platform,platform_video_id,canonical_url,content_kind,c_content_type,creator_id)
          VALUES('youtube',%s,%s,'video','video',%s) RETURNING id""",
          (token,"https://youtube.com/watch?v="+token,self.creator)).fetchone()[0]
        self.conn.execute("INSERT INTO sre_project_videos(project_id,video_id) VALUES(%s,%s)",
                          (self.project,vid))
        md=self.conn.execute("""INSERT INTO source_video_metadata
          (video_id,source_id,source_policy_version_id,fetched_at,published_at)
          VALUES(%s,%s,%s,now()-interval '3 hours',now()-interval '5 days')
          RETURNING id""",(vid,self.source,self.policy)).fetchone()[0]
        sid=self.conn.execute("""INSERT INTO metric_snapshots
          (video_id,source_id,source_policy_version_id,observed_at,collected_at,
           views,likes,comments,shares,source_metric_definition,
           source_metric_version,precision,data_quality_status,observation_time_assumed)
          VALUES(%s,%s,%s,now()-interval '2 hours',now()-interval '1 hour',
                 %s,10,3,2,'platform_post_views','v1','exact','ok',FALSE)
          RETURNING id""",(vid,self.source,self.policy,views)).fetchone()[0]
        return vid,sid,md

    def tearDown(self):
        # Explicitly delete frozen tables only after a test-specific
        # fixture cleanup is possible: immutable freezes prohibit DELETE.
        # Each test uses unique data; destructive cleanup is intentionally
        # confined to disposable PG17 databases only.
        pass

    def rule(self,**changes):
        as_of=datetime.now(timezone.utc)-timedelta(minutes=1)
        fields=dict(
            project_id=self.project,target_video_id=self.target[0],
            purpose="creator",platform="youtube",content_type="video",
            source_id=self.source,source_policy_version_id=self.policy,
            metric_definition="platform_post_views",metric_version="v1",
            evaluated_as_of=as_of,
            published_from=as_of-timedelta(days=9),
            published_before=as_of-timedelta(days=1),
            min_age_hours=Decimal("0"),max_age_hours=Decimal("168"),
            creator_id=self.creator,topic_id=None,max_candidates=20,
        )
        fields.update(changes)
        return CohortRules(**fields)

    def freeze(self, rule=None):
        return PostgreSQLCohortFreezeWriter().freeze(
            self.conn,rules=rule or self.rule(),
            target_snapshot_id=self.target[1],target_metadata_id=self.target[2])

    def test_freeze_persists_exact_members_and_even_median(self):
        frozen=self.freeze()
        self.assertEqual(frozen.status,"frozen_unreviewed")
        self.assertFalse(frozen.approved)
        self.assertEqual(frozen.member_count,2)
        row=self.conn.execute("""SELECT is_frozen,post_count,median_views
          FROM comparison_cohorts WHERE id=%s""",(frozen.legacy_cohort_id,)).fetchone()
        self.assertEqual(row,(True,2,Decimal("201.000000000000")))
        members=self.conn.execute("""SELECT video_id,snapshot_id,metadata_id
          FROM group_c_cohort_freeze_members WHERE freeze_id=%s ORDER BY video_id""",
          (frozen.id,)).fetchall()
        self.assertEqual(members,sorted(self.peers))
        self.assertEqual(self.conn.execute("""SELECT count(*) FROM comparison_cohort_members
          WHERE cohort_id=%s""",(frozen.legacy_cohort_id,)).fetchone()[0],2)

    def test_same_rule_replay_is_idempotent(self):
        exact=self.rule()
        a=self.freeze(exact)
        b=self.freeze(exact)
        self.assertEqual(a,b)
        self.assertEqual(self.conn.execute("""SELECT count(*)
          FROM group_c_cohort_freezes WHERE rules_sha256=%s AND project_id=%s""",
          (exact.sha256,self.project)).fetchone()[0],1)

    def test_changed_evidence_same_rule_denied(self):
        r=self.rule()
        self.freeze(r)
        self.conn.execute("""INSERT INTO metric_snapshots
          (video_id,source_id,source_policy_version_id,observed_at,collected_at,
           views,likes,comments,shares,source_metric_definition,source_metric_version,
           precision,data_quality_status,observation_time_assumed)
          VALUES(%s,%s,%s,now()-interval '30 minutes',now()-interval '20 minutes',
                 350,20,4,3,'platform_post_views','v1','exact','ok',FALSE)""",
          (self.peers[1][0],self.source,self.policy))
        with self.assertRaises(ContractError) as err:
            self.freeze(r)
        self.assertEqual(err.exception.code,"cohort_selection_conflict")

    def test_prior_pooled_empirical_rates_no_assumed_config(self):
        r=self.rule(purpose="engagement_prior")
        receipt=self.freeze(r)
        row=self.conn.execute("""SELECT median_views,prior_like_rate,
              prior_comment_rate,prior_share_rate,post_count
            FROM comparison_cohorts WHERE id=%s""",
            (receipt.legacy_cohort_id,)).fetchone()
        self.assertEqual(row,(None,Decimal("0.049751243781"),
                              Decimal("0.014925373134"),
                              Decimal("0.009950248756"),2))
        self.assertFalse(receipt.approved)

    def test_immutable_freeze_member_and_old_parent(self):
        receipt=self.freeze()
        for stmt,args in (
            ("UPDATE group_c_cohort_freezes SET state='frozen_unreviewed' WHERE id=%s",
             (receipt.id,)),
            ("DELETE FROM group_c_cohort_freeze_members WHERE freeze_id=%s",
             (receipt.id,)),
            ("UPDATE comparison_cohorts SET post_count=77 WHERE id=%s",
             (receipt.legacy_cohort_id,)),
            ("DELETE FROM comparison_cohort_members WHERE cohort_id=%s",
             (receipt.legacy_cohort_id,)),
        ):
            with self.subTest(stmt=stmt),self.assertRaises(self.psycopg.Error):
                self.conn.execute(stmt,args)

    def test_empty_or_revoked_source_rolls_back_without_partial_freeze(self):
        self.conn.execute("UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",(self.source,))
        with self.assertRaises(ContractError):
            self.freeze()
        self.assertEqual(self.conn.execute("""SELECT count(*) FROM group_c_cohort_freezes
          WHERE project_id=%s""",(self.project,)).fetchone()[0],0)

    def test_no_approved_sample_from_target_only(self):
        self.conn.execute("DELETE FROM sre_project_videos WHERE video_id=%s",
                          (self.peers[0][0],))
        self.conn.execute("DELETE FROM sre_project_videos WHERE video_id=%s",
                          (self.peers[1][0],))
        with self.assertRaises(ContractError) as e:
            self.freeze()
        self.assertEqual(e.exception.code,"empty_cohort")
        self.assertEqual(self.conn.execute("""SELECT count(*) FROM group_c_cohort_freezes
          WHERE project_id=%s""",(self.project,)).fetchone()[0],0)

    def test_schema_checksum_migration_reentry(self):
        import hashlib
        from intelligence import migrate
        expected=hashlib.sha256(MIGRATION.read_bytes()).hexdigest()
        self.assertEqual(self.conn.execute("""SELECT sha256 FROM schema_migrations
          WHERE version='057_group_c_cohort_freezes.sql'""").fetchone()[0],expected)
        self.assertEqual(migrate.apply_migrations(self.conn),[])


if __name__=="__main__":
    unittest.main()
