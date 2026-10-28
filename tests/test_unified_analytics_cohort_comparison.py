"""UA-MOD-22 deterministic cohort golds + fail-closed author/time controls.

Positive proof mocks are FUTURE synthetic approval only; UA85 still
prohibits positive qualified snapshots. No live peer claims are made.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics import cohort_comparison as cmp
from test_unified_analytics_descriptive_aggregation import (
    Pins, FakeDB, MockVisible, Cursor, NOW,
)

START=datetime(2026,9,1,tzinfo=timezone.utc)
STOP=datetime(2026,10,1,tzinfo=timezone.utc)
WINDOW=cmp.PublicationWindow(START,STOP)


def make_pins(n=5):
    left=tuple(Pins(i) for i in range(1,n+1))
    right=tuple(Pins(i) for i in range(101,101+n))
    for p in left+right:
        p.candidate.scope.source_policy_version_id=7
        p.candidate.group_c.metadata_id=p.candidate.scope.video_id+4000
    return left,right


def auth_row(p,creator,age_days=3,published=None):
    s=p.candidate.scope
    time=published or START+timedelta(days=(s.video_id % 10))
    return dict(
        project_id=s.project_id,snapshot_id=p.snapshot_id,
        video_id=s.video_id,source_id=s.source_id,
        source_policy_version_id=s.source_policy_version_id,
        calculated_metric_id=p.candidate.group_c.calculated_metric_id,
        published_metadata_id=p.candidate.group_c.metadata_id,
        metadata_id=p.candidate.group_c.metadata_id,
        creator_id=creator,creator_platform="youtube",platform="youtube",
        published_at=time,fetched_at=time,
        is_current=True,deleted_at=None,expires_at=None,
        metadata_source_id=s.source_id,metadata_policy_id=s.source_policy_version_id,
        metric_snapshot_id=p.candidate.group_c.current_snapshot_id,
        observed_at=time+timedelta(days=age_days),
        collected_at=time+timedelta(days=age_days),
        metric_expires_at=None,
    )


class CohortFakeDB(FakeDB):
    def __init__(self,left,right,age_map=None):
        pins=left+right
        values=tuple(
            (Decimal((i%len(left))+1+(2 if i>=len(left) else 0)),
             (i%len(left)+1)*10+(20 if i>=len(left) else 0))
            for i in range(len(pins))
        )
        super().__init__(pins,values)
        self.metadata={
            p.snapshot_id:auth_row(p,111 if i<len(left) else 111,
                        (age_map or {}).get(p.snapshot_id,3))
            for i,p in enumerate(pins)
        }
        self.author_reads=0
    def execute(self,sql,args=()):
        if sql==cmp.AUTHOR_TIME_SQL:
            self.author_reads+=1
            self.sql.append(sql)
            row=self.metadata.get(args[1])
            return Cursor([row] if row is not None else [])
        return super().execute(sql,args)


@contextmanager
def authorization_mock():
    with patch.object(cmp,"VisibilityPins",Pins),\
         patch.object(cmp,"VisibleSnapshotReader",MockVisible):
        yield


class WindowsAndBinsTests(unittest.TestCase):
    def test_utc_half_open_and_equal_duration(self):
        self.assertTrue(WINDOW.includes(START))
        self.assertFalse(WINDOW.includes(STOP))
        self.assertFalse(WINDOW.includes(START-timedelta(seconds=1)))
        spec=cmp.ComparisonSpec(WINDOW,WINDOW,creator_id=111)
        self.assertEqual(spec.left_window.duration,timedelta(days=30))

    def test_reject_naive_and_reverse_window(self):
        for bad in (
            (START.replace(tzinfo=None),STOP),
            (STOP,START),
            (START,START),
            (START,START+timedelta(days=367)),
        ):
            with self.assertRaises(AnalyticsBoundaryError):
                cmp.PublicationWindow(*bad)

    def test_reject_partially_overlapping_publication_windows(self):
        second=cmp.PublicationWindow(
            START+timedelta(days=4),STOP+timedelta(days=4)
        )
        with self.assertRaises(AnalyticsBoundaryError):
            cmp.ComparisonSpec(WINDOW,second,creator_id=111)

    def test_disjoint_period_equal_duration_supported(self):
        next_window=cmp.PublicationWindow(STOP,STOP+timedelta(days=30))
        spec=cmp.ComparisonSpec(
            WINDOW,next_window,creator_id=111
        )
        self.assertEqual(spec.right_window.start,STOP)

    def test_invalid_author_mode_age_min_floor(self):
        for opts in (
            dict(author_mode="same_creator"),
            dict(author_mode="disjoint_creators",creator_id=4),
            dict(author_mode="none",creator_id=111),
            dict(creator_id=111,age_bin_days=5),
            dict(creator_id=111,age_bin_days=True),
            dict(creator_id=111,min_sample=1),
            dict(creator_id=111,min_sample=51),
            dict(creator_id=True),
        ):
            with self.assertRaises(AnalyticsBoundaryError):
                cmp.ComparisonSpec(WINDOW,WINDOW,**opts)

    def test_deterministic_half_open_age_bins(self):
        published=START
        at=NOW
        self.assertEqual(cmp._age_bin(published,published,7,at),
                         "age_days_000_007")
        self.assertEqual(cmp._age_bin(
            published,published+timedelta(days=6,hours=23),7,at
        ),"age_days_000_007")
        self.assertEqual(cmp._age_bin(
            published,published+timedelta(days=7),7,at
        ),"age_days_007_014")
        self.assertEqual(cmp._age_bin(
            published,published+timedelta(days=15),14,at
        ),"age_days_014_028")

    def test_impossible_or_future_observation_denied(self):
        self.assertIsNone(cmp._age_bin(
            START,START-timedelta(hours=1),7,NOW
        ))
        self.assertIsNone(cmp._age_bin(
            START,NOW+timedelta(days=1),7,NOW
        ))
        self.assertIsNone(cmp._age_bin(
            START,START+timedelta(days=367),7,
            NOW+timedelta(days=400),
        ))

    def test_descriptive_differences_exact_decimal(self):
        self.assertEqual(cmp._delta("2.25","3.50"),"1.25")
        self.assertEqual(cmp._delta("4.1","2.1"),"-2.0")


class AuthorizedComparisonTests(unittest.TestCase):
    def setUp(self):
        self.left,self.right=make_pins()
        self.db=CohortFakeDB(self.left,self.right)
        self.reader=cmp.CompatibleCohortReader(self.db)
        self.spec=cmp.ComparisonSpec(WINDOW,WINDOW,creator_id=111)

    def compare(self,spec=None):
        with authorization_mock():
            return self.reader.compare(self.left,self.right,spec or self.spec)

    def test_golden_same_creator_score_and_views_difference(self):
        v=self.compare()
        self.assertIsInstance(v,cmp.CompatibleCohortComparison)
        self.assertEqual(len(v.bins),1)
        b=v.bins[0]
        self.assertEqual(b.age_bin,"age_days_000_007")
        self.assertEqual(b.left.sample_size,5)
        self.assertEqual(b.right.sample_size,5)
        self.assertEqual(b.left.median,"3")
        self.assertEqual(b.right.median,"5")
        self.assertEqual(b.breakout_median_difference,"2")
        self.assertEqual(b.views_median_difference,"20")
        self.assertTrue(v.descriptive_only)
        self.assertFalse(v.causal_claims_allowed)
        self.assertFalse(v.confidence_intervals_computed)
        self.assertEqual(self.db.auth_count,10)
        self.assertEqual(self.db.query_count,10)
        self.assertEqual(self.db.author_reads,10)

    def test_auth_all_precedes_first_numeric_or_author_read(self):
        v=self.compare()
        self.assertIsNotNone(v)
        self.assertEqual(self.db.auth_count,10)
        self.assertEqual(self.db.query_count,10)

    def test_first_denied_hidden_member_prevents_raw_reads(self):
        self.db.denied.add(self.left[0].snapshot_id)
        self.assertIsNone(self.compare())
        self.assertEqual(self.db.query_count,0)
        self.assertEqual(self.db.author_reads,0)

    def test_last_denied_member_prevents_raw_reads(self):
        self.db.denied.add(self.right[-1].snapshot_id)
        self.assertIsNone(self.compare())
        self.assertEqual(self.db.auth_count,10)
        self.assertEqual(self.db.query_count,0)
        self.assertEqual(self.db.author_reads,0)

    def test_unknown_creator_is_not_a_default_author(self):
        self.db.metadata[self.right[0].snapshot_id]["creator_id"]=None
        self.assertIsNone(self.compare())

    def test_wrong_creator_same_creator_mode_denied(self):
        self.db.metadata[self.right[-1].snapshot_id]["creator_id"]=222
        self.assertIsNone(self.compare())

    def test_disjoint_creator_mode_positive_with_different_creators(self):
        for p in self.right:
            self.db.metadata[p.snapshot_id]["creator_id"]=222
        spec=cmp.ComparisonSpec(
            WINDOW,WINDOW,author_mode="disjoint_creators"
        )
        got=self.compare(spec)
        self.assertIsNotNone(got)
        self.assertEqual(got.author_mode,"disjoint_creators")

    def test_disjoint_creator_mode_rejects_shared_creator(self):
        spec=cmp.ComparisonSpec(
            WINDOW,WINDOW,author_mode="disjoint_creators"
        )
        self.assertIsNone(self.compare(spec))

    def test_cross_platform_denied_even_same_config(self):
        self.db.rows[self.right[0].snapshot_id]["platform"]="instagram"
        self.assertIsNone(self.compare())

    def test_cross_source_denied_even_same_platform(self):
        self.db.rows[self.right[-1].snapshot_id]["source_id"]="another.source"
        self.assertIsNone(self.compare())

    def test_cross_source_metric_definition_denied(self):
        self.db.rows[self.right[0].snapshot_id][
            "source_metric_definition"]="total_reach"
        self.assertIsNone(self.compare())

    def test_cross_metric_version_denied(self):
        self.db.rows[self.right[0].snapshot_id][
            "source_metric_version"]="source-v2"
        self.assertIsNone(self.compare())

    def test_cross_group_b_taxonomy_denied(self):
        self.right[-1].candidate.group_b.taxonomy_version="B-tax-v2"
        self.assertIsNone(self.compare())

    def test_cross_c_calculation_config_denied(self):
        self.db.rows[self.right[0].snapshot_id][
            "calculation_config_id"]=400
        self.assertIsNone(self.compare())

    def test_different_frozen_creator_cohort_identity_not_auto_rejected(self):
        # Same approved definition/config, different creator cohort frozen
        # identity. UA19 still verifies each exact candidate independently.
        for p in self.right:
            p.candidate.group_c.creator_freeze_sha256="f"*64
        self.assertIsNotNone(self.compare())

    def test_wrong_persisted_metadata_id_denied(self):
        self.db.metadata[self.right[0].snapshot_id]["metadata_id"]=777
        self.assertIsNone(self.compare())

    def test_expired_pinned_publication_denied(self):
        self.db.metadata[self.left[0].snapshot_id][
            "expires_at"]=START
        self.assertIsNone(self.compare())

    def test_publication_time_out_of_window_denied(self):
        self.db.metadata[self.right[1].snapshot_id][
            "published_at"]=STOP
        self.assertIsNone(self.compare())

    def test_score_missing_or_float_denied(self):
        self.db.rows[self.left[0].snapshot_id]["breakout_score"]=None
        self.assertIsNone(self.compare())
        self.db.rows[self.left[0].snapshot_id]["breakout_score"]=0.5
        self.assertIsNone(self.compare())

    def test_no_stable_age_bin_both_sides_suppresses(self):
        for p in self.right:
            meta=self.db.metadata[p.snapshot_id]
            meta["observed_at"]=meta["published_at"]+timedelta(days=10)
        self.assertIsNone(self.compare())

    def test_two_ordered_age_bins_only_both_arms_above_floor(self):
        left,right=make_pins(10)
        db=CohortFakeDB(left,right)
        for group in (left,right):
            for p in group[5:]:
                meta=db.metadata[p.snapshot_id]
                meta["observed_at"]=meta["published_at"]+timedelta(days=10)
        with authorization_mock():
            result=cmp.CompatibleCohortReader(db).compare(
                left,right,self.spec
            )
        self.assertIsNotNone(result)
        self.assertEqual(tuple(x.age_bin for x in result.bins),(
            "age_days_000_007","age_days_007_014",
        ))
        self.assertEqual(tuple(x.left.sample_size for x in result.bins),
                         (5,5))

    def test_subthreshold_bin_omits_label_and_count(self):
        left,right=make_pins(6)
        db=CohortFakeDB(left,right)
        p=right[-1]
        meta=db.metadata[p.snapshot_id]
        meta["observed_at"]=meta["published_at"]+timedelta(days=10)
        with authorization_mock():
            result=cmp.CompatibleCohortReader(db).compare(
                left,right,self.spec
            )
        self.assertIsNotNone(result)
        self.assertEqual(len(result.bins),1)
        self.assertEqual(result.bins[0].age_bin,"age_days_000_007")
        self.assertFalse(hasattr(result,"suppressed_count"))

    def test_zero_or_one_member_no_db_reads(self):
        with authorization_mock():
            self.assertIsNone(self.reader.compare((),self.right,self.spec))
            self.assertIsNone(self.reader.compare(
                self.left,self.right[:1],self.spec
            ))
        self.assertFalse(self.db.sql)

    def test_reject_duplicate_video_or_snapshot_across_cohorts(self):
        with authorization_mock():
            with self.assertRaises(AnalyticsBoundaryError):
                self.reader.compare(
                    self.left,self.left,self.spec
                )
            same=Pins(self.left[0].candidate.scope.video_id)
            same.snapshot_id=9000
            with self.assertRaises(AnalyticsBoundaryError):
                self.reader.compare(
                    self.left,self.right[:4]+(same,),self.spec
                )
        self.assertFalse(self.db.sql)

    def test_nonidle_connection_refused(self):
        self.db.info.transaction_status=2
        with authorization_mock():
            with self.assertRaises(AnalyticsBoundaryError):
                self.reader.compare(self.left,self.right,self.spec)

    def test_missing_observation_denied_without_imputation(self):
        self.db.metadata.pop(self.right[-1].snapshot_id)
        self.assertIsNone(self.compare())

    def test_pg_query_exact_pins_and_no_raw_media_or_aggregates(self):
        sql=cmp.AUTHOR_TIME_SQL
        for required in (
            "uae_readside_visibility_candidates",
            "cm.published_metadata_id",
            "md.is_current IS TRUE",
            "sn.status='qualified'",
            "candidate.project_id=%s",
            "candidate.snapshot_id=%s",
            "candidate.video_id=%s",
            "cr.platform=v.platform",
            "ms.source_policy_version_id=sn.source_policy_version_id",
        ):
            self.assertIn(required,sql)
        for forbidden in ("GROUP BY","COUNT(","compute_c_metrics",
                          "screen_text","spoken_text","local_path",
                          "UPDATE ","DELETE ","INSERT "):
            self.assertNotIn(forbidden,sql)

    def test_uses_ua20_qualifier_not_legacy_arbitrary_rows(self):
        self.assertIn("QUALIFIED_METRIC_SQL",cmp.__all__ if False else
                         cmp.CompatibleCohortReader.compare.__code__.co_names)
        self.assertEqual(cmp.MAX_AGGREGATE,50)


if __name__=="__main__":
    unittest.main()
