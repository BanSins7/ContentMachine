"""UA-MOD-20 gold Decimal arithmetic and private current-rights aggregation.

All future-positive in-memory snapshots here are deliberately FAKE, not
claims of actual approved SRE/Group C data. Production UA19/085 denies all.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime,timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError,VisibleSnapshot
from intelligence.unified_analytics import descriptive_aggregation as agg

PROJECT="a"*32
OTHER="b"*32
SHA="c"*64
NOW=datetime(2026,10,27,20,0,tzinfo=timezone.utc)


def stub(**kwargs):
    return SimpleNamespace(**kwargs)


class Pins:
    def __init__(self,video_id:int,*,project:str=PROJECT,config:int=11,
                 source:str="trusted.source"):
        self.snapshot_id=video_id+1000
        self.candidate=stub(
            scope=stub(project_id=project,video_id=video_id,source_id=source,
                       content_kind="video"),
            group_a=stub(analysis_version="A-v1"),
            group_b=stub(analysis_version="B-v1",taxonomy_version="B-tax-v1"),
            lineage=stub(sre_taxonomy_version_id=3),
            group_c=stub(calculated_metric_id=video_id+2000,
                         current_snapshot_id=video_id+3000,
                         config_id=config,config_version="C-v1",
                         creator_cohort_id=41,prior_cohort_id=42,
                         niche_cohort_id=None,
                         creator_freeze_sha256=SHA,
                         prior_freeze_sha256=SHA,
                         niche_freeze_sha256=None,
                         source_metric_version="source-v1"),
        )
    @property
    def project_id(self):
        return self.candidate.scope.project_id


def dbrow(pins:Pins,breakout:Decimal,views:int)->dict:
    c=pins.candidate
    scope=c.scope
    return dict(
        project_id=scope.project_id,snapshot_id=pins.snapshot_id,
        video_id=scope.video_id,source_id=scope.source_id,
        content_kind=scope.content_kind,platform="youtube",
        calculated_metric_id=c.group_c.calculated_metric_id,
        metric_snapshot_id=c.group_c.current_snapshot_id,
        calculation_config_id=c.group_c.config_id,
        calculation_version=c.group_c.config_version,
        creator_cohort_id=c.group_c.creator_cohort_id,
        prior_cohort_id=c.group_c.prior_cohort_id,
        niche_cohort_id=c.group_c.niche_cohort_id,
        creator_definition_version="creator-v1",
        prior_definition_version="prior-v1",
        niche_definition_version=None,
        score_status="complete",calculation_status="complete",
        breakout_score=breakout,views=views,
        source_metric_definition="platform_post_views",
        source_metric_version="source-v1",
        data_quality_status="ok",precision="exact",
        observation_time_assumed=False,
    )


class Cursor:
    def __init__(self,items):self.items=items
    def fetchone(self):return self.items[0] if self.items else None
    def fetchall(self):return self.items


class FakeDB:
    def __init__(self,pins,values):
        self.info=stub(transaction_status=0)
        self.rows={p.snapshot_id:dbrow(p,value,views)
                   for p,(value,views) in zip(pins,values)}
        self.denied=set()
        self.query_count=0
        self.auth_count=0
        self.sql=[]
    @contextmanager
    def transaction(self):
        yield
    def execute(self,sql,args=()):
        self.sql.append(sql)
        if sql=="SET TRANSACTION ISOLATION LEVEL REPEATABLE READ":
            return Cursor([])
        if sql==agg.CLOCK_SQL:
            return Cursor([dict(db_now=NOW)])
        if sql==agg.QUALIFIED_METRIC_SQL:
            self.query_count+=1
            row=self.rows.get(args[1])
            return Cursor([row] if row is not None else [])
        raise AssertionError("Unapproved query: "+sql[:80])


class MockVisible:
    def __init__(self,db):self.db=db
    def _read(self,db,p,at):
        db.auth_count+=1
        if p.snapshot_id in db.denied:
            return None
        return VisibleSnapshot(p.project_id,p.snapshot_id,
                               p.candidate.scope.video_id)


def fixture(values=None):
    pins=tuple(Pins(n) for n in range(1,6))
    vals=values or tuple((Decimal(n),10*n) for n in range(1,6))
    db=FakeDB(pins,vals)
    return pins,db


class GoldenDecimalTests(unittest.TestCase):
    def test_independent_four_value_percentiles(self):
        values=tuple(map(Decimal,("1","2","3","4")))
        self.assertEqual(agg._percentile_linear(values,1,4),Decimal("1.75"))
        self.assertEqual(agg._percentile_linear(values,1,2),Decimal("2.5"))
        self.assertEqual(agg._percentile_linear(values,3,4),Decimal("3.25"))

    def test_type_seven_endpoints_and_single_math(self):
        values=(Decimal("9.5"),)
        self.assertEqual(agg._percentile_linear(values,0,4),Decimal("9.5"))
        self.assertEqual(agg._percentile_linear(values,3,4),Decimal("9.5"))
        v=tuple(map(Decimal,("1","2","4")))
        self.assertEqual(agg._percentile_linear(v,0,4),Decimal(1))
        self.assertEqual(agg._percentile_linear(v,4,4),Decimal(4))

    def test_exact_means_no_float_rounding(self):
        v=tuple(Decimal(x) for x in ("0.1","0.2","0.3","0.4","0.5"))
        stat=agg._statistic(v)
        self.assertEqual(Decimal(stat.mean),Decimal("0.3"))
        self.assertEqual(Decimal(stat.median),Decimal("0.3"))

    def test_no_normalization_of_group_c_score(self):
        v=tuple(Decimal(x) for x in ("-8","0","100","203","444"))
        stat=agg._statistic(v)
        self.assertEqual(stat.minimum,"-8")
        self.assertEqual(stat.maximum,"444")
        self.assertEqual(stat.p75,"203")
        self.assertEqual(stat.median,"100")

    def test_quantile_repeat_values_deterministic(self):
        stat=agg._statistic((Decimal(7),)*5)
        self.assertEqual(stat.p25,"7")
        self.assertEqual(stat.median,"7")
        self.assertEqual(stat.p75,"7")
        self.assertEqual(stat.mean,"7")

    def test_pure_stats_disallow_subthreshold_or_nan(self):
        for values in ((),(Decimal(1),),
                       (Decimal(1),)*4,
                       (Decimal("NaN"),)*5,
                       (Decimal("Infinity"),)*5,
                       tuple(Decimal(i) for i in range(51))):
            with self.assertRaises(AnalyticsBoundaryError):
                agg._statistic(values)

    def test_no_binary_float_or_unbounded_decimal_exponent(self):
        self.assertIsNone(agg._number(0.4))
        self.assertIsNone(agg._number(True))
        self.assertIsNone(agg._number(Decimal("1e99999")))
        self.assertIsNone(agg._number(Decimal("NaN")))
        self.assertEqual(agg._number(9223372036854775807),
                         Decimal(9223372036854775807))

    def test_reject_unsorted_percentile_input(self):
        with self.assertRaises(AnalyticsBoundaryError):
            agg._percentile_linear((Decimal(2),Decimal(1)),1,4)


class AuthorizedAggregationTests(unittest.TestCase):
    @contextmanager
    def mocked_visibility(self):
        with patch.object(agg,"VisibilityPins",Pins),\
             patch.object(agg,"VisibleSnapshotReader",MockVisible):
            yield

    def test_golden_baseline_uses_actual_views_and_score(self):
        pins,db=fixture()
        with self.mocked_visibility():
            result=agg.QualifiedAggregationReader(db).summarize_qualified(pins)
        self.assertIsInstance(result,agg.DescriptiveAggregate)
        self.assertEqual(result.qualified_video_count,5)
        self.assertEqual(result.breakout.median,"3")
        self.assertEqual(result.breakout.p25,"2")
        self.assertEqual(result.breakout.p75,"4")
        self.assertEqual(result.breakout.mean,"3")
        self.assertEqual(result.views.median,"30")
        self.assertEqual(result.views.mean,"30")
        self.assertEqual(db.auth_count,5)
        self.assertEqual(db.query_count,5)
        self.assertTrue(result.descriptive_only)
        self.assertFalse(result.causal_claims_allowed)
        self.assertFalse(result.confidence_intervals_computed)

    def test_single_hidden_snapshot_suppresses_entire_result(self):
        pins,db=fixture()
        db.denied.add(pins[2].snapshot_id)
        with self.mocked_visibility():
            result=agg.QualifiedAggregationReader(db).summarize_qualified(pins)
        self.assertIsNone(result)
        self.assertEqual(db.query_count,2)

    def test_small_zero_and_one_sample_suppress_without_storage_query(self):
        pins,db=fixture()
        with self.mocked_visibility():
            reader=agg.QualifiedAggregationReader(db)
            self.assertIsNone(reader.summarize_qualified(()))
            self.assertIsNone(reader.summarize_qualified(pins[:1]))
            self.assertIsNone(reader.summarize_qualified(pins[:4]))
        self.assertFalse(db.sql)
        self.assertEqual(db.auth_count,0)

    def test_unknown_views_does_not_become_zero_or_leak_counts(self):
        pins,db=fixture()
        db.rows[pins[3].snapshot_id]["views"]=None
        with self.mocked_visibility():
            self.assertIsNone(
                agg.QualifiedAggregationReader(db).summarize_qualified(pins)
            )

    def test_incomplete_score_does_not_become_zero(self):
        pins,db=fixture()
        db.rows[pins[1].snapshot_id]["breakout_score"]=None
        with self.mocked_visibility():
            self.assertIsNone(
                agg.QualifiedAggregationReader(db).summarize_qualified(pins)
            )

    def test_float_score_is_refused_not_coerced(self):
        pins,db=fixture()
        db.rows[pins[0].snapshot_id]["breakout_score"]=0.12
        with self.mocked_visibility():
            self.assertIsNone(
                agg.QualifiedAggregationReader(db).summarize_qualified(pins)
            )

    def test_breakout_has_no_fixed_range_clamp(self):
        pins,db=fixture(tuple(
            (Decimal(x),100) for x in ("101","120","240","400","2000")
        ))
        with self.mocked_visibility():
            res=agg.QualifiedAggregationReader(db).summarize_qualified(pins)
        self.assertEqual(res.breakout.maximum,"2000")

    def test_cross_platform_mixing_is_denied(self):
        pins,db=fixture()
        db.rows[pins[4].snapshot_id]["platform"]="instagram"
        with self.mocked_visibility():
            self.assertIsNone(
                agg.QualifiedAggregationReader(db).summarize_qualified(pins)
            )

    def test_cross_source_or_metric_version_mixing_denied(self):
        pins,db=fixture()
        db.rows[pins[4].snapshot_id]["source_metric_version"]="source-v2"
        with self.mocked_visibility():
            self.assertIsNone(
                agg.QualifiedAggregationReader(db).summarize_qualified(pins)
            )

    def test_group_c_config_mismatch_denied(self):
        pins,db=fixture()
        db.rows[pins[2].snapshot_id]["calculation_config_id"]=77
        with self.mocked_visibility():
            self.assertIsNone(
                agg.QualifiedAggregationReader(db).summarize_qualified(pins)
            )

    def test_cohort_version_mismatch_denied(self):
        pins,db=fixture()
        db.rows[pins[3].snapshot_id]["prior_definition_version"]="prior-v2"
        with self.mocked_visibility():
            self.assertIsNone(
                agg.QualifiedAggregationReader(db).summarize_qualified(pins)
            )

    def test_duplicate_video_snapshots_are_rejected_before_io(self):
        pins,db=fixture()
        dupe=Pins(pins[1].candidate.scope.video_id)
        dupe.snapshot_id=999999
        requested=pins[:1]+(dupe,)+pins[2:]
        with self.mocked_visibility():
            with self.assertRaises(AnalyticsBoundaryError):
                agg.QualifiedAggregationReader(db).summarize_qualified(requested)
        self.assertFalse(db.sql)

    def test_duplicate_snapshot_id_and_cross_project_denied(self):
        pins,db=fixture()
        with self.mocked_visibility():
            reader=agg.QualifiedAggregationReader(db)
            with self.assertRaises(AnalyticsBoundaryError):
                reader.summarize_qualified((pins[0],pins[0])+pins[2:])
            other=Pins(6,project=OTHER)
            with self.assertRaises(AnalyticsBoundaryError):
                reader.summarize_qualified(pins[:4]+(other,))
        self.assertFalse(db.sql)

    def test_min_sample_floor_bool_and_limit_validation(self):
        pins,db=fixture()
        with self.mocked_visibility():
            reader=agg.QualifiedAggregationReader(db)
            for bad in (0,1,4,51,True,False,"5",5.0):
                with self.assertRaises(AnalyticsBoundaryError):
                    reader.summarize_qualified(pins,min_sample=bad)
        self.assertFalse(db.sql)

    def test_higher_sample_floor_suppresses_without_io(self):
        pins,db=fixture()
        with self.mocked_visibility():
            res=agg.QualifiedAggregationReader(db).summarize_qualified(
                pins,min_sample=6
            )
        self.assertIsNone(res)
        self.assertFalse(db.sql)

    def test_nonidle_psycopg_session_denied(self):
        pins,db=fixture()
        db.info.transaction_status=2
        with self.mocked_visibility():
            with self.assertRaises(AnalyticsBoundaryError) as ctx:
                agg.QualifiedAggregationReader(db).summarize_qualified(pins)
        self.assertEqual(ctx.exception.code,"aggregation_fresh_transaction_required")

    def test_wrapper_requires_concrete_reader(self):
        pins,db=fixture()
        with self.mocked_visibility():
            with self.assertRaises(AnalyticsBoundaryError):
                agg.summarize_qualified(object(),pins)

    def test_unqualified_not_selected_even_with_complete_c_table_row(self):
        pins,db=fixture()
        db.denied.add(pins[0].snapshot_id)
        with self.mocked_visibility():
            self.assertIsNone(
                agg.QualifiedAggregationReader(db).summarize_qualified(pins)
            )
        self.assertEqual(db.query_count,0)

    def test_postgres_sql_is_pinned_not_source_metrics_calculation(self):
        sql=agg.QUALIFIED_METRIC_SQL
        for field in ("uae_readside_visibility_candidates",
                      "cm.score_status='complete'",
                      "cm.calculation_status='complete'",
                      "cm.snapshot_id=sn.metric_snapshot_id",
                      "cfg.normalization_enabled IS FALSE",
                      "comparison_cohorts","eligible.project_id=%s",
                      "ms.data_quality_status='ok'"):
            self.assertIn(field,sql)
        self.assertNotIn("compute_c_metrics",sql)
        self.assertNotIn("GROUP BY",sql)
        self.assertNotIn("SUM(",sql)
        self.assertNotIn("UPDATE ",sql)


if __name__=="__main__":
    unittest.main()
