"""GC-MOD-08: actual provider metadata date/retention tests, no guessed wall clock."""
from __future__ import annotations

import os
import re
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, localcontext
from uuid import uuid4

from intelligence.group_c.contracts import ContractError
from intelligence.group_c.publication_time import (
    PUBLICATION_EVIDENCE_VERSION, PublicationTimeEvidence,
    read_publication_time, validate_publication_time,
)

NOW = datetime(2026, 10, 26, 14, 0, 0, tzinfo=timezone.utc)


def fixture(**change):
    row = dict(id=21, video_id=7, source_id="source-1",
               source_policy_version_id=13,
               published_at=NOW-timedelta(days=3),
               fetched_at=NOW-timedelta(hours=3),
               expires_at=NOW+timedelta(days=5),
               is_current=True, deleted_at=None)
    row.update(change)
    return row


def check(row=None, **change):
    return validate_publication_time(
        fixture() if row is None else row,
        video_id=7, metadata_id=21, source_id="source-1",
        source_policy_version_id=13,
        previous_snapshot_id=101, snapshot_id=102,
        previous_observed_at=NOW-timedelta(hours=7),
        observed_at=NOW-timedelta(hours=2),
        as_of=NOW, **change,
    )


class TestPublicationTimePure(unittest.TestCase):
    def test_frozen_exact_time_and_observation_anchored_age(self):
        evidence = check()
        self.assertIsInstance(evidence, PublicationTimeEvidence)
        self.assertEqual(evidence.schema_version, PUBLICATION_EVIDENCE_VERSION)
        self.assertEqual(evidence.time_source, "source_video_metadata.published_at")
        self.assertEqual(evidence.metadata_id, 21)
        self.assertEqual(evidence.published_at, NOW-timedelta(days=3))
        self.assertEqual(evidence.age, timedelta(days=2, hours=22))
        self.assertEqual(evidence.age_hours, Decimal("70"))
        self.assertEqual(evidence.calculation_publication(),
                         {"published_at":NOW-timedelta(days=3)})
        with self.assertRaises(FrozenInstanceError):
            evidence.metadata_id = 11

    def test_age_is_not_wall_clock_age_and_fingerprint_stable(self):
        first = check()
        second = check(fixture(), as_of=NOW+timedelta(minutes=5))
        self.assertEqual(first.age, second.age)
        self.assertEqual(first.age_hours, second.age_hours)
        self.assertEqual(first.evidence_sha256, second.evidence_sha256)
        self.assertNotEqual(first.checked_at, second.checked_at)

    def test_source_id_metadata_and_video_mismatch_fail_closed(self):
        for change in (
            {"id":22}, {"video_id":8}, {"source_id":"other"},
            {"source_policy_version_id":15}, {"is_current":False},
            {"is_current":None}, {"deleted_at":NOW},
        ):
            with self.subTest(change=change), self.assertRaises(ContractError):
                check(fixture(**change))

    def test_missing_publication_must_not_use_legacy_video_date(self):
        for raw in (None, {}, fixture(published_at=None)):
            with self.subTest(raw=raw), self.assertRaises(ContractError):
                check(raw)
        self.assertNotIn("videos.published_at", check().time_source)

    def test_naive_or_missing_fetched_and_observed_not_accepted(self):
        for change in (
            {"published_at":datetime(2026,10,23,14)},
            {"fetched_at":datetime(2026,10,24,14)},
            {"published_at":"2026-10-23T14:00:00Z"},
            {"fetched_at":None},
            {"expires_at":datetime(2026,10,30,14)},
        ):
            with self.subTest(change=change), self.assertRaises(ContractError):
                check(fixture(**change))

    def test_publication_after_prior_observation_fails(self):
        row = fixture(published_at=NOW-timedelta(hours=5))
        with self.assertRaises(ContractError) as e:
            check(row)
        self.assertEqual(e.exception.code, "invalid_publication_timestamp")

    def test_observation_time_reversals_and_future_fail(self):
        for times in (
            (NOW-timedelta(hours=2),NOW-timedelta(hours=3)),
            (NOW-timedelta(hours=2),NOW-timedelta(hours=2)),
            (NOW-timedelta(hours=7),NOW+timedelta(minutes=1)),
        ):
            with self.subTest(times=times), self.assertRaises(ContractError):
                validate_publication_time(
                    fixture(), video_id=7, metadata_id=21,
                    source_id="source-1", source_policy_version_id=13,
                    previous_snapshot_id=101, snapshot_id=102,
                    previous_observed_at=times[0],observed_at=times[1],
                    as_of=NOW,
                )

    def test_fetched_after_checked_or_before_publication_fails(self):
        for change in (
            {"fetched_at":NOW+timedelta(seconds=1)},
            {"fetched_at":NOW-timedelta(days=4)},
            {"published_at":NOW+timedelta(minutes=1)},
        ):
            with self.subTest(change=change), self.assertRaises(ContractError):
                check(fixture(**change))

    def test_retention_expired_exact_boundary_and_deleted(self):
        for value in (NOW, NOW-timedelta(microseconds=1)):
            with self.subTest(value=value), self.assertRaises(ContractError) as e:
                check(fixture(expires_at=value))
            self.assertEqual(e.exception.code, "publication_expired")
        self.assertIsNone(check(fixture(expires_at=None)).expires_at)
        self.assertEqual(check(fixture(expires_at=NOW+timedelta(microseconds=1))).metadata_id,21)

    def test_source_policy_or_identifier_mismatch(self):
        for change in (
            {"video_id":True}, {"metadata_id":0},
            {"source_policy_version_id":False},
            {"previous_snapshot_id":102},
            {"snapshot_id":101}, {"source_id":""},
        ):
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_publication_time(
                    fixture(), video_id=change.get("video_id",7),
                    metadata_id=change.get("metadata_id",21),
                    source_id=change.get("source_id","source-1"),
                    source_policy_version_id=change.get("source_policy_version_id",13),
                    previous_snapshot_id=change.get("previous_snapshot_id",101),
                    snapshot_id=change.get("snapshot_id",102),
                    previous_observed_at=NOW-timedelta(hours=7),
                    observed_at=NOW-timedelta(hours=2),as_of=NOW)

    def test_timezone_normalization_and_microseconds(self):
        plus_three = timezone(timedelta(hours=3))
        row = fixture(
            published_at=(NOW-timedelta(days=3)).astimezone(plus_three))
        result = validate_publication_time(
            row,video_id=7,metadata_id=21,source_id="source-1",
            source_policy_version_id=13,previous_snapshot_id=101,snapshot_id=102,
            previous_observed_at=(NOW-timedelta(hours=7)).astimezone(plus_three),
            observed_at=(NOW-timedelta(hours=2)+timedelta(microseconds=5)).astimezone(plus_three),
            as_of=NOW,
        )
        self.assertEqual(result.published_at.tzinfo,timezone.utc)
        self.assertEqual(result.age_microseconds,
                         70 * 3600 * 1000000 + 5)
        with localcontext() as ctx:
            ctx.prec = 4
            ctx.rounding = ROUND_DOWN
            self.assertGreater(result.age_hours,Decimal("70"))

    def test_fingerprint_changes_if_verified_date_or_source_changes(self):
        original = check()
        updated = check(fixture(published_at=NOW-timedelta(days=3, seconds=1)))
        self.assertNotEqual(original.evidence_sha256, updated.evidence_sha256)
        self.assertRegex(original.evidence_sha256,r"^[0-9a-f]{64}$")
        self.assertNotIn("age_now",original.calculation_publication())

    def test_pure_function_makes_no_mutations(self):
        row = fixture()
        copy = row.copy()
        check(row)
        self.assertEqual(row,copy)


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "requires disposable PostgreSQL17 and GC_TEST_DISPOSABLE_DB=YES",
)
class TestPublicationTimePostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.conn=psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True)
        version=int(cls.conn.execute("SHOW server_version_num").fetchone()[0])
        if not (170000 <= version < 180000):
            raise AssertionError("PG17 required")
        migrate.apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.project=uuid4().hex
        self.source="gc-pub-"+uuid4().hex
        self.conn.execute("""INSERT INTO project_registry
          (id,name,profile_revision,profile_hash,created_at,updated_at)
          VALUES(%s,'GC08 fixture',1,%s,now(),now())""",(self.project,"f"*64))
        self.conn.execute("INSERT INTO sre_workspaces(project_id) VALUES(%s)",(self.project,))
        self.conn.execute("""INSERT INTO sources
          (id,provider,source_type,status,can_store_metadata,can_store_metrics,
           can_derive_scores,retention_days)
          VALUES(%s,'fixture','manual','enabled',TRUE,TRUE,TRUE,30)""",(self.source,))
        self.policy=self.conn.execute("""INSERT INTO source_policy_versions
          (source_id,version,is_active,permissions,retention_policy)
          VALUES(%s,1,TRUE,%s::jsonb,'{"retention_days":30}'::jsonb)
          RETURNING id""",
          (self.source,'{"can_store_metadata":true,"can_store_metrics":true,"can_derive_scores":true}')
        ).fetchone()[0]
        token=uuid4().hex
        self.video=self.conn.execute("""INSERT INTO videos
          (platform,platform_video_id,canonical_url,content_kind,c_content_type,published_at)
          VALUES('youtube',%s,%s,'video','video',now()-interval '60 days')
          RETURNING id""",(token,"https://youtube.com/watch?v="+token)).fetchone()[0]
        self.conn.execute("INSERT INTO sre_project_videos(project_id,video_id) VALUES(%s,%s)",
                          (self.project,self.video))
        self.metadata=self.conn.execute("""INSERT INTO source_video_metadata
          (video_id,source_id,source_policy_version_id,fetched_at,published_at,expires_at)
          VALUES(%s,%s,%s,now()-interval '1 hour',
                 now()-interval '5 days',now()+interval '10 days') RETURNING id""",
          (self.video,self.source,self.policy)).fetchone()[0]
        self.snaps=[]
        for hours in (8,2):
            self.snaps.append(self.conn.execute("""INSERT INTO metric_snapshots
              (video_id,source_id,source_policy_version_id,observed_at,
               collected_at,views,likes,comments,shares,source_metric_definition,
               source_metric_version,data_quality_status,precision,observation_time_assumed)
              VALUES(%s,%s,%s,now()-(%s*interval '1 hour'),now(),10,2,1,1,
                     'platform_post_views','v1','ok','exact',FALSE) RETURNING id""",
              (self.video,self.source,self.policy,hours)).fetchone()[0])

    def tearDown(self):
        self.conn.execute("DELETE FROM sre_workspaces WHERE project_id=%s",(self.project,))
        self.conn.execute("DELETE FROM project_registry WHERE id=%s",(self.project,))
        self.conn.execute("DELETE FROM videos WHERE id=%s",(self.video,))
        self.conn.execute("DELETE FROM sources WHERE id=%s",(self.source,))

    def read(self,**overrides):
        kwargs = dict(
            conn=self.conn, project_id=self.project, video_id=self.video,
            metadata_id=self.metadata, source_id=self.source,
            source_policy_version_id=self.policy,
            previous_snapshot_id=self.snaps[0],snapshot_id=self.snaps[1],
        )
        kwargs.update(overrides)
        return read_publication_time(**kwargs)

    def test_pinned_date_not_videos_published_and_age_stable(self):
        first=self.read()
        self.assertNotEqual(first.published_at,
                            self.conn.execute("SELECT published_at FROM videos WHERE id=%s",
                                              (self.video,)).fetchone()[0])
        second=self.read()
        self.assertEqual(first.evidence_sha256,second.evidence_sha256)
        self.assertEqual(first.age_microseconds,second.age_microseconds)
        self.assertGreater(first.age_hours,Decimal("100"))
        self.assertFalse(first.published_at > first.previous_observed_at)

    def test_missing_date_never_falls_back_to_video(self):
        self.conn.execute("UPDATE source_video_metadata SET published_at=NULL WHERE id=%s",
                          (self.metadata,))
        with self.assertRaises(ContractError) as e:
            self.read()
        self.assertEqual(e.exception.code,"invalid_publication_timestamp")

    def test_wrong_project_or_video_is_not_source_evidence(self):
        for kwargs in ({"project_id":uuid4().hex},
                       {"source_id":"other"},
                       {"metadata_id":999999999}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ContractError):
                self.read(**kwargs)

    def test_revoked_policy_and_expired_metadata(self):
        self.conn.execute("UPDATE source_policy_versions SET is_active=FALSE WHERE id=%s",
                          (self.policy,))
        with self.assertRaises(ContractError):
            self.read()
        self.conn.execute("UPDATE source_policy_versions SET is_active=TRUE WHERE id=%s",
                          (self.policy,))
        self.conn.execute("UPDATE source_video_metadata SET expires_at=now() WHERE id=%s",
                          (self.metadata,))
        with self.assertRaises(ContractError):
            self.read()

    def test_current_timestamp_after_publication_and_snapshot(self):
        self.conn.execute("""UPDATE source_video_metadata
          SET published_at=now()-interval '30 minutes' WHERE id=%s""",(self.metadata,))
        with self.assertRaises(ContractError) as e:
            self.read()
        self.assertEqual(e.exception.code,"invalid_publication_timestamp")

    def test_requires_top_level_read_transaction(self):
        from psycopg import connect
        with connect(os.environ["CM_TEST_PG_DSN"],autocommit=False) as conn:
            with self.assertRaises(ContractError) as e:
                read_publication_time(
                    conn,project_id=self.project,video_id=self.video,
                    metadata_id=self.metadata,source_id=self.source,
                    source_policy_version_id=self.policy,
                    previous_snapshot_id=self.snaps[0],snapshot_id=self.snaps[1])
            self.assertEqual(e.exception.code,"transaction_contract_unavailable")


if __name__ == "__main__":
    unittest.main()
