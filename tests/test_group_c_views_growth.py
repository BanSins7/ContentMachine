"""Скорость роста просмотров: точная арифметика и одноразовая PG17 D1."""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, localcontext
from uuid import uuid4

from intelligence.group_c.c0 import C0Pair, C0Snapshot, read_c0_pair
from intelligence.group_c.contracts import ContractError
from intelligence.group_c.views_growth import (
    VIEW_GROWTH_VERSION, ViewsGrowthResult, PostgreSQLViewsGrowth,
    compute_verified_views_growth, compute_views_growth_per_hour,
)

T = datetime(2026,10,20,12,0,tzinfo=timezone.utc)


def snapshot(ident: int, views: int | None, hours_before: int) -> C0Snapshot:
    return C0Snapshot(
        snapshot_id=ident,video_id=111,source_id="fixture",
        source_policy_version_id=4,metric_definition="platform_post_views",
        metric_version="v3",observed_at=T-timedelta(hours=hours_before),
        collected_at=T-timedelta(hours=hours_before-1),
        expires_at=None,views=views,likes=None,comments=None,shares=None,
        saves=None,followers=None,
    )


class TestViewsGrowthPure(unittest.TestCase):
    def test_reference_gold_25_views_per_hour(self):
        self.assertEqual(compute_views_growth_per_hour(
            100,200,T-timedelta(hours=4),T),
            Decimal("25.000000000000"),
        )

    def test_rate_is_zero_when_counters_unchanged(self):
        self.assertEqual(compute_views_growth_per_hour(
            100,100,T-timedelta(hours=4),T),
            Decimal("0.000000000000"),
        )

    def test_zero_views_allowed_for_growth_only(self):
        self.assertEqual(compute_views_growth_per_hour(
            0,0,T-timedelta(hours=1),T),
            Decimal("0.000000000000"),
        )
        self.assertEqual(compute_views_growth_per_hour(
            0,5,T-timedelta(hours=2),T),
            Decimal("2.500000000000"),
        )

    def test_subsecond_precision_no_total_seconds_float(self):
        old=T-timedelta(microseconds=1)
        self.assertEqual(compute_views_growth_per_hour(0,1,old,T),
                         Decimal("3600000000.000000000000"))
        self.assertEqual(compute_views_growth_per_hour(0,1,T-timedelta(seconds=1),T),
                         Decimal("3600.000000000000"))

    def test_half_up_quantization_isolated_context(self):
        expected=Decimal("0.333333333333")
        with localcontext() as ctx:
            ctx.prec=4
            ctx.rounding=ROUND_DOWN
            self.assertEqual(compute_views_growth_per_hour(
                0,1,T-timedelta(hours=3),T),expected)
        self.assertEqual(compute_views_growth_per_hour(
            0,2,T-timedelta(hours=3),T),
            Decimal("0.666666666667"))

    def test_different_timezone_same_instants(self):
        tz=timezone(timedelta(hours=5,minutes=30))
        old=(T-timedelta(hours=4)).astimezone(tz)
        current=T.astimezone(timezone(timedelta(hours=-3)))
        self.assertEqual(compute_views_growth_per_hour(100,200,old,current),
                         Decimal("25.000000000000"))

    def test_equal_reverse_and_naive_timestamps_fail_closed(self):
        cases=(
            (T,T),
            (T,T-timedelta(seconds=1)),
            (datetime(2026,10,20,10),T),
            (T-timedelta(hours=1),datetime(2026,10,20,12)),
        )
        for before,now in cases:
            with self.subTest(before=before,now=now),self.assertRaises(ContractError) as e:
                compute_views_growth_per_hour(0,1,before,now)
            self.assertEqual(e.exception.code,"invalid_observation_interval")

    def test_negative_counter_correction_is_never_zero_growth(self):
        with self.assertRaises(ContractError) as e:
            compute_views_growth_per_hour(101,100,T-timedelta(hours=4),T)
        self.assertEqual(e.exception.code,"counter_decreased")

    def test_exact_bigint_boundaries_and_no_float(self):
        for invalid in (-1,None,True,0.0,1.25,"100",2**63,float("nan")):
            with self.subTest(value=invalid),self.assertRaises(ContractError):
                compute_views_growth_per_hour(invalid,100,T-timedelta(hours=1),T)
            with self.subTest(value=invalid),self.assertRaises(ContractError):
                compute_views_growth_per_hour(0,invalid,T-timedelta(hours=1),T)

    def test_result_numeric_overflow_blocks_not_clamps(self):
        with self.assertRaises(ContractError) as e:
            compute_views_growth_per_hour(
                0,2**63-1,T-timedelta(microseconds=1),T)
        self.assertEqual(e.exception.code,"numeric_overflow")

    def test_verified_c0_pair_does_not_require_likes_or_shares(self):
        before=snapshot(1,100,5)
        current=snapshot(2,200,1)
        checked=C0Pair(previous=before,current=current)
        self.assertEqual(compute_verified_views_growth(checked),
                         Decimal("25.000000000000"))
        self.assertIsNone(checked.current.likes)

    def test_verified_pair_rejects_cross_video_source_metric_version(self):
        start=snapshot(1,100,5)
        end=snapshot(2,200,1)
        for key,value in (
            ("video_id",112),("source_id","other"),
            ("source_policy_version_id",99),
            ("metric_definition","impressions"),("metric_version","v4"),
            ("snapshot_id",1),
        ):
            with self.subTest(key=key),self.assertRaises(ContractError):
                compute_verified_views_growth(
                    C0Pair(previous=start,current=replace(end,**{key:value})))

    def test_verified_pair_rejects_missing_counter_and_reversed_time(self):
        start=snapshot(1,100,5)
        end=snapshot(2,200,1)
        for revised_start,revised_end in (
            (replace(start,views=None),end),
            (start,replace(end,views=None)),
            (start,replace(end,observed_at=start.observed_at)),
            (replace(start,views=300),end),
        ):
            with self.subTest(prev=revised_start,current=revised_end),self.assertRaises(ContractError):
                compute_verified_views_growth(C0Pair(revised_start,revised_end))

    def test_unverified_plain_mapping_cannot_impersonate_c0_pair(self):
        with self.assertRaises(ContractError) as e:
            compute_verified_views_growth(
                {"previous":snapshot(1,100,5),"current":snapshot(2,200,1)})
        self.assertEqual(e.exception.code,"missing_snapshot")

    def test_growth_not_breakout_not_persisted(self):
        self.assertEqual(VIEW_GROWTH_VERSION,"group-c-views-growth/v1")
        self.assertFalse(hasattr(PostgreSQLViewsGrowth,"persist"))
        self.assertFalse(hasattr(PostgreSQLViewsGrowth,"publish"))
        self.assertFalse(hasattr(PostgreSQLViewsGrowth,"approve"))


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "Disposable PostgreSQL17 only: CM_TEST_PG_DSN + GC_TEST_DISPOSABLE_DB=YES",
)
class TestViewsGrowthPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_group_c_c0 import TestC0Postgres
        TestC0Postgres.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from test_group_c_c0 import TestC0Postgres
        TestC0Postgres.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_c0 import TestC0Postgres
        self.fixture=TestC0Postgres()
        self.fixture.conn=self.conn
        self.fixture.setUp()
        for name in ("project","source","policy","video","metadata","snapshots"):
            setattr(self,name,getattr(self.fixture,name))
        # Anchor all baseline facts to exact-second timestamps so elapsed
        # is exactly four hours despite separate SQL INSERT statement times.
        anchor=datetime.now(timezone.utc).replace(microsecond=0)
        for sid,hours in zip(self.snapshots,(6,2)):
            self.conn.execute(
                """UPDATE metric_snapshots SET observed_at=%s WHERE id=%s""",
                (anchor-timedelta(hours=hours),sid),
            )

    def tearDown(self):
        self.fixture.tearDown()

    def derive(self,**changes):
        args=dict(
            conn=self.conn,project_id=self.project,video_id=self.video,
            source_id=self.source,source_policy_version_id=self.policy,
            previous_snapshot_id=self.snapshots[0],snapshot_id=self.snapshots[1],
            metadata_id=self.metadata,metric_version="v2",
        )
        args.update(changes)
        return PostgreSQLViewsGrowth().derive(**args)

    def test_observed_hours_exact_and_no_breakout_persistence(self):
        out=self.derive()
        self.assertIsInstance(out,ViewsGrowthResult)
        self.assertEqual(out.previous_views,20)
        self.assertEqual(out.current_views,90)
        self.assertEqual(out.views_delta,70)
        self.assertEqual(out.elapsed_microseconds,14400000000)
        self.assertEqual(out.views_growth_per_hour,Decimal("17.500000000000"))
        self.assertEqual(out.status,"growth_derived_unapproved")
        self.assertFalse(out.ready_for_breakout)
        with self.assertRaises(FrozenInstanceError):
            out.views_delta=20
        self.assertFalse(self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM calculated_metrics WHERE post_id=%s)",
            (self.video,),
        ).fetchone()[0])

    def test_views_only_when_likes_comments_shares_missing(self):
        self.conn.execute(
            """UPDATE metric_snapshots
                  SET likes=NULL,comments=NULL,shares=NULL WHERE id=%s""",
            (self.snapshots[1],),
        )
        value=self.derive()
        self.assertEqual(value.views_growth_per_hour,Decimal("17.500000000000"))
        with self.assertRaises(ContractError) as e:
            read_c0_pair(
                self.conn,project_id=self.project,video_id=self.video,
                source_id=self.source,source_policy_version_id=self.policy,
                previous_snapshot_id=self.snapshots[0],snapshot_id=self.snapshots[1],
                metadata_id=self.metadata,metric_definition="platform_post_views",
                metric_version="v2",
            )
        self.assertEqual(e.exception.code,"missing_raw_counter")

    def test_counter_decreased_never_clamped_zero(self):
        fresh=self.fixture.insert_snapshot(1,50)
        with self.assertRaises(ContractError) as e:
            self.derive(previous_snapshot_id=self.snapshots[1],
                        snapshot_id=fresh)
        self.assertEqual(e.exception.code,"counter_decreased")

    def test_newer_retracted_or_wrong_version_blocks_old_pair(self):
        self.fixture.insert_snapshot(1,101,status="retracted",version="v3")
        with self.assertRaises(ContractError) as e:
            self.derive()
        self.assertEqual(e.exception.code,"metric_history_changed")

    def test_provider_metric_version_mismatch_denied(self):
        with self.assertRaises(ContractError) as e:
            self.derive(metric_version="v4")
        self.assertEqual(e.exception.code,"incompatible_metric_version")

    def test_wrong_project_video_policy_and_metadata_denied(self):
        for case in (
            {"project_id":uuid4().hex},
            {"video_id":999999999},{"source_id":"other"},
            {"source_policy_version_id":999999999},
            {"metadata_id":999999999},
            {"previous_snapshot_id":self.snapshots[1],
             "snapshot_id":self.snapshots[0]},
        ):
            with self.subTest(case=case),self.assertRaises(ContractError):
                self.derive(**case)

    def test_same_observation_timestamp_denied(self):
        previous=self.conn.execute(
            "SELECT observed_at FROM metric_snapshots WHERE id=%s",
            (self.snapshots[0],),
        ).fetchone()[0]
        self.conn.execute(
            "UPDATE metric_snapshots SET observed_at=%s WHERE id=%s",
            (previous,self.snapshots[1]),
        )
        with self.assertRaises(ContractError) as e:
            self.derive()
        self.assertEqual(e.exception.code,"invalid_observation_interval")

    def test_expired_publication_and_source_revocation_denied(self):
        self.conn.execute(
            "UPDATE source_video_metadata SET expires_at=now()-interval '1 second' WHERE id=%s",
            (self.metadata,),
        )
        with self.assertRaises(ContractError):
            self.derive()

    def test_source_revocation_denied(self):
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.source,),
        )
        with self.assertRaises(ContractError):
            self.derive()

    def test_replay_uses_exact_same_evidence_hash(self):
        first=self.derive()
        second=self.derive()
        self.assertEqual(first,second)
        self.assertEqual(len(first.evidence_sha256),64)

    def test_real_microsecond_delta_without_float(self):
        anchor=datetime.now(timezone.utc)-timedelta(hours=3)
        anchor=anchor.replace(microsecond=10000)
        self.conn.execute(
            "UPDATE metric_snapshots SET observed_at=%s,views=20 WHERE id=%s",
            (anchor,self.snapshots[0]),
        )
        self.conn.execute(
            "UPDATE metric_snapshots SET observed_at=%s,views=21 WHERE id=%s",
            (anchor+timedelta(microseconds=1),self.snapshots[1]),
        )
        result=self.derive()
        self.assertEqual(result.elapsed_microseconds,1)
        self.assertEqual(result.views_growth_per_hour,Decimal("3600000000.000000000000"))


if __name__=="__main__":
    unittest.main()
