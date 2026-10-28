"""GC-MOD-30 independent C0-C7 Decimal gold vectors and failure parity.

The math oracle and frozen JSON vectors do NOT import the product calculator.
These tests cannot authorize a score or substitute real signed D1/C7 E2E.
"""
from __future__ import annotations

import copy
import json
import os
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, localcontext
from pathlib import Path
from uuid import uuid4

from group_c_independent_oracle import oracle_case,OracleInputError
from intelligence.group_c.c0 import validate_c0_snapshot,validate_c0_pair
from intelligence.group_c.contracts import ContractError
from intelligence.group_c.creator_ratio import compute_creator_ratio
from intelligence.group_c.niche_comparison import compute_niche_comparison
from intelligence.group_c.views_growth import compute_views_growth_per_hour
from intelligence.group_c.raw_engagement import compute_raw_engagement
from intelligence.group_c.bayesian_engagement import compute_bayesian_engagement
from intelligence.group_c.freshness import compute_freshness_score
from intelligence.group_c.breakout_score import compute_breakout_components
from intelligence.group_c.pure_math import compute_c_metrics
from intelligence.group_c.cohort_freeze import build_frozen_evidence
from test_group_c_cohort_freeze import rules,peer,selection


GOLD_PATH=Path(__file__).with_name("fixtures")/"group_c_math_gold_v1.json"


def _fixture():
    o=json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    assert o["schema_version"]=="group-c-gold-v1"
    return o["cases"]


def _time(raw):
    return datetime.fromisoformat(raw)


def _raw(case,*,previous=False,**override):
    time=_time(case["prev_at" if previous else "observed_at"])
    row={
        "id":10 if previous else 11,
        "video_id":7,"source_id":"gold-official",
        "source_policy_version_id":13,
        "observed_at":time,"collected_at":time+timedelta(minutes=5),
        "expires_at":time+timedelta(days=365),
        "views":case["previous_views"] if previous else case["views"],
        "likes":0 if previous else case["likes"],
        "comments":0 if previous else case["comments"],
        "shares":0 if previous else case["shares"],
        "saves":None,"followers":None,
        "source_metric_definition":"platform_post_views",
        "source_metric_version":"v3","data_quality_status":"ok",
        "precision":"exact","observation_time_assumed":False,
    }
    row.update(override)
    return row


def _verify_snapshot(raw):
    return validate_c0_snapshot(
        raw,video_id=7,source_id="gold-official",
        source_policy_version_id=13,
        metric_definition="platform_post_views",metric_version="v3",
        now=datetime(2026,10,28,tzinfo=timezone.utc),
    )


def _standalone(case):
    ratio=compute_creator_ratio(
        case["views"],case["creator_median"],case["alpha"])
    niche=compute_niche_comparison(
        case["views"],case["niche_median"],case["alpha"])
    growth=compute_views_growth_per_hour(
        case["previous_views"],case["views"],
        _time(case["prev_at"]),_time(case["observed_at"]),
    )
    raw=compute_raw_engagement(
        case["views"],case["likes"],case["comments"],case["shares"])
    smooth=compute_bayesian_engagement(
        case["views"],case["likes"],case["comments"],case["shares"],
        case["k"],case["p_l"],case["p_c"],case["p_s"])
    freshness=compute_freshness_score(
        _time(case["published_at"]),_time(case["observed_at"]))
    score=compute_breakout_components(ratio,growth,
                                     smooth.weighted_engagement_smoothed,
                                     freshness)
    return {
        "creator":ratio,"niche":niche,"growth":growth,
        "like_rate":raw.like_rate_fraction,
        "comment_rate":raw.comment_rate_fraction,
        "share_rate":raw.share_rate_fraction,
        "engagement_rate":raw.engagement_rate_fraction,
        "weighted_raw":raw.weighted_engagement_raw_fraction,
        "like_smoothed":smooth.like_rate_smoothed,
        "comment_smoothed":smooth.comment_rate_smoothed,
        "share_smoothed":smooth.share_rate_smoothed,
        "weighted_smoothed":smooth.weighted_engagement_smoothed,
        "freshness":freshness,
        "creator_component":score.score_creator_component,
        "growth_component":score.score_growth_component,
        "engagement_component":score.score_engagement_component,
        "freshness_component":score.score_freshness_component,
        "breakout":score.breakout_score,
    }


def _legacy_inputs(case):
    previous=_raw(case,previous=True)
    current=_raw(case)
    config={
        "alpha":case["alpha"],"bayesian_k":case["k"],
        "freshness_half_life_h":"48","normalization_enabled":False,
        "weights_json":{
            "creator_ratio":"0.45","views_growth_per_hour":"0.30",
            "weighted_engagement_smoothed":"0.15","freshness_score":"0.10",
        },
    }
    creator={
        "is_frozen":True,"purpose":"creator","post_count":2,
        "median_views":case["creator_median"],
    }
    prior={
        "is_frozen":True,"purpose":"engagement_prior","post_count":2,
        "prior_like_rate":case["p_l"],
        "prior_comment_rate":case["p_c"],
        "prior_share_rate":case["p_s"],
    }
    niche=(None if case["niche_median"] is None else {
        "is_frozen":True,"purpose":"niche","post_count":2,
        "median_views":case["niche_median"],
    })
    return [current,previous,
            {"published_at":_time(case["published_at"])},
            config,creator,prior,niche]


class TestGoldSetC0ToC7(unittest.TestCase):
    def test_fixture_has_distinct_signed_off_corpus(self):
        cases=_fixture()
        self.assertEqual(len(cases),3)
        self.assertEqual(len({row["name"] for row in cases}),3)
        self.assertGreater(Decimal(cases[1]["expected"]["breakout"]),100)
        self.assertEqual(cases[2]["likes"],0)
        self.assertIsNone(cases[1]["niche_median"])
        self.assertFalse(any("approval" in case for case in cases))

    def test_external_oracle_matches_all_frozen_reference_decimals(self):
        for case in _fixture():
            with self.subTest(case=case["name"]):
                actual=oracle_case(case)
                for key,expect in case["expected"].items():
                    with self.subTest(key=key):
                        self.assertEqual(actual[key],
                                         Decimal(expect) if expect is not None else None)

    def test_all_stage_kernels_match_independent_oracle_per_output(self):
        for case in _fixture():
            with self.subTest(case=case["name"]):
                actual=_standalone(case)
                oracle=oracle_case(case)
                self.assertEqual(set(actual),set(oracle))
                for key in oracle:
                    with self.subTest(field=key):
                        self.assertEqual(actual[key],oracle[key])

    def test_pure_legacy_c1_c7_matches_independent_oracle(self):
        mapping={
            "creator":"creator_ratio","niche":"niche_outlier",
            "growth":"views_growth_per_hour",
            "weighted_raw":"weighted_engagement_raw",
            "like_smoothed":"like_rate_smoothed",
            "comment_smoothed":"comment_rate_smoothed",
            "share_smoothed":"share_rate_smoothed",
            "weighted_smoothed":"weighted_engagement_smoothed",
            "freshness":"freshness_score",
            "creator_component":"score_creator_component",
            "growth_component":"score_growth_component",
            "engagement_component":"score_engagement_component",
            "freshness_component":"score_freshness_component",
            "breakout":"breakout_score",
        }
        for case in _fixture():
            with self.subTest(case=case["name"]):
                legacy=compute_c_metrics(*_legacy_inputs(case))
                expected=oracle_case(case)
                for field,key in mapping.items():
                    with self.subTest(field=field):
                        self.assertEqual(legacy[key],expected[field])
                self.assertEqual(legacy["score_status"],"complete")
                self.assertEqual(legacy["calculation_status"],"complete")

    def test_c0_exact_pair_and_nullable_rate_inputs(self):
        for case in _fixture():
            with self.subTest(case=case["name"]):
                prior=_verify_snapshot(_raw(case,previous=True))
                latest=_verify_snapshot(_raw(case))
                pair=validate_c0_pair(prior,latest,require_score_fields=True)
                self.assertEqual(pair.previous.views,case["previous_views"])
                self.assertEqual(pair.current.views,case["views"])
                self.assertTrue(pair.current.ready_for_rate_math)
        base=_fixture()[0]
        incomplete=_verify_snapshot(_raw(base,likes=None))
        self.assertFalse(incomplete.ready_for_rate_math)
        with self.assertRaises(ContractError) as error:
            validate_c0_pair(_verify_snapshot(_raw(base,previous=True)),
                             incomplete,require_score_fields=True)
        self.assertEqual(error.exception.code,"missing_raw_counter")

    def test_c0_wrong_metric_version_quality_and_counter_correction_codes(self):
        case=_fixture()[0]
        old=_verify_snapshot(_raw(case,previous=True))
        current=_verify_snapshot(_raw(case))
        with self.assertRaises(ContractError) as error:
            validate_c0_pair(old,replace(current,metric_version="v4"))
        self.assertEqual(error.exception.code,"incompatible_metric_version")
        for change,code in [
            ({"source_metric_version":"v4"},"incompatible_metric_version"),
            ({"data_quality_status":"retracted"},"source_expired_or_retracted"),
            ({"data_quality_status":"counter_correction"},"counter_decreased"),
            ({"observation_time_assumed":True},"unverified_or_assumed_observation"),
            ({"source_policy_version_id":14},"source_policy_unavailable"),
            ({"likes":-1},"invalid_raw_counter"),
        ]:
            with self.subTest(change=change),self.assertRaises(ContractError) as err:
                _verify_snapshot(_raw(case,**change))
            self.assertEqual(err.exception.code,code)
        with self.assertRaises(ContractError) as error:
            validate_c0_pair(old,replace(current,views=1))
        self.assertEqual(error.exception.code,"counter_decreased")

    def test_zero_views_undefined_rate_not_invented_zero(self):
        case=copy.deepcopy(_fixture()[0])
        case["views"]=0
        case["previous_views"]=0
        with self.assertRaises(ContractError) as err:
            compute_raw_engagement(0,0,0,0)
        self.assertEqual(err.exception.code,"zero_views_rate_undefined")
        with self.assertRaises(ContractError) as err:
            compute_bayesian_engagement(0,0,0,0,"2","0","0","0")
        self.assertEqual(err.exception.code,"zero_views_rate_undefined")
        with self.assertRaises(ContractError) as err:
            validate_c0_pair(_verify_snapshot(_raw(case,previous=True)),
                             _verify_snapshot(_raw(case)),require_score_fields=True)
        self.assertEqual(err.exception.code,"zero_views_rate_undefined")
        with self.assertRaises(OracleInputError) as err:
            oracle_case(case)
        self.assertEqual(err.exception.code,"zero_views_rate_undefined")

    def test_negative_correction_is_not_clamped_to_zero_growth(self):
        case=_fixture()[0]
        t0,t1=_time(case["prev_at"]),_time(case["observed_at"])
        with self.assertRaises(ContractError) as err:
            compute_views_growth_per_hour(400,300,t0,t1)
        self.assertEqual(err.exception.code,"counter_decreased")
        correction={**case,"previous_views":400,"views":300}
        with self.assertRaises(OracleInputError) as err:
            oracle_case(correction)
        self.assertEqual(err.exception.code,"counter_decreased")

    def test_dst_offsets_are_instants_not_elapsed_wall_clock(self):
        # Amsterdam DST transition (29 Mar 2026): 01:30+01 -> 03:30+02
        before=datetime.fromisoformat("2026-03-29T01:30:00+01:00")
        after=datetime.fromisoformat("2026-03-29T03:30:00+02:00")
        self.assertEqual(
            compute_views_growth_per_hour(100,220,before,after),
            Decimal("120.000000000000"),
        )
        self.assertEqual(
            compute_freshness_score(before,after),
            compute_freshness_score(
                datetime(2026,3,29,0,30,tzinfo=timezone.utc),
                datetime(2026,3,29,1,30,tzinfo=timezone.utc),
            ),
        )
        base=copy.deepcopy(_fixture()[0])
        base.update(previous_views=100,views=220,likes=22,comments=8,shares=3,
                    prev_at=before.isoformat(),observed_at=after.isoformat(),
                    published_at="2026-03-28T01:30:00+01:00")
        self.assertEqual(
            _standalone(base)["growth"],oracle_case(base)["growth"],
        )

    def test_microsecond_elapsed_does_not_use_float_or_zero_division(self):
        c=_fixture()[2]
        self.assertEqual(
            compute_views_growth_per_hour(
                0,7,_time(c["prev_at"]),_time(c["observed_at"])),
            Decimal("25200000000.000000000000"),
        )
        self.assertEqual(oracle_case(c)["freshness"],
                         Decimal("99.999999999599"))

    def test_half_up_individual_components_not_round_final_only(self):
        r=compute_breakout_components(
            Decimal("0.000000000010"),Decimal("0.000000000005"),
            Decimal("0.000000000010"),Decimal("0.000000000005"))
        self.assertEqual(r.score_creator_component,Decimal("0.000000000005"))
        self.assertEqual(r.score_growth_component,Decimal("0.000000000002"))
        self.assertEqual(r.score_engagement_component,Decimal("0.000000000002"))
        self.assertEqual(r.score_freshness_component,Decimal("0.000000000001"))
        self.assertEqual(r.breakout_score,Decimal("0.000000000010"))

    def test_c2_niche_is_never_fifth_c7_component(self):
        c=copy.deepcopy(_fixture()[0])
        a=_standalone(c)
        c["niche_median"]=None
        b=_standalone(c)
        self.assertIsNone(b["niche"])
        self.assertEqual(a["breakout"],b["breakout"])
        self.assertEqual(a["creator_component"],b["creator_component"])

    def test_missing_and_unapproved_decimal_config_fail_closed(self):
        c=_fixture()[0]
        for key,bad in [
            ("alpha",None),("k",None),("alpha","0"),
            ("k","0"),("p_l","1.1"),("p_s",None),
            ("alpha","2.0000000000001"),
        ]:
            with self.subTest(field=key,bad=bad),self.assertRaises(
                (ContractError,OracleInputError)):
                oracle_case({**c,key:bad})
        for args,code in [
            ((10,None,"3"),"missing_creator_median"),
            ((10,"4","0"),"invalid_alpha"),
        ]:
            with self.subTest(args=args),self.assertRaises(ContractError) as err:
                compute_creator_ratio(*args)
            self.assertEqual(err.exception.code,code)

    def test_frozen_peer_median_and_pooled_prior_independent(self):
        median_selection=selection(peer(1,101),peer(2,205),peer(3,503))
        proof=build_frozen_evidence(median_selection)
        self.assertEqual(proof.median_views,Decimal("205.000000000000"))
        prior_rules=rules(purpose="engagement_prior")
        prior_selection=selection(
            peer(1,100,likes=15,comments=1,shares=2),
            peer(2,300,likes=15,comments=7,shares=2),rule=prior_rules)
        frozen=build_frozen_evidence(prior_selection)
        # pooled 30/400, 8/400, 4/400 (not mean of per-video rates).
        self.assertEqual(
            (frozen.prior_like_rate,frozen.prior_comment_rate,frozen.prior_share_rate),
            (Decimal("0.075000000000"),Decimal("0.020000000000"),
             Decimal("0.010000000000")),
        )
        self.assertIsNone(frozen.median_views)
        self.assertNotEqual(proof.evidence_sha256,
                            build_frozen_evidence(
                                selection(peer(1,101),peer(2,205),peer(3,504))
                            ).evidence_sha256)

    def test_no_global_decimal_context_drift_and_replay_idempotence(self):
        c=_fixture()[0]
        first=_standalone(c)
        with localcontext() as ctx:
            ctx.prec=8
            ctx.rounding=ROUND_DOWN
            second=_standalone(c)
            oracle=oracle_case(c)
        self.assertEqual(first,second)
        self.assertEqual(first,oracle)
        self.assertEqual(_standalone(c),first)

    def test_exact_error_parity_for_version_boundaries(self):
        c=_fixture()[0]
        # Monolithic C0-C7 validation and strict C0 both reject version drift
        # with the same canonical error code rather than selecting old views.
        legacy=_legacy_inputs(c)
        legacy[1]["source_metric_version"]="v4"
        with self.assertRaises(ContractError) as err:
            compute_c_metrics(*legacy)
        self.assertEqual(err.exception.code,"incompatible_metric_version")
        with self.assertRaises(ContractError) as err:
            _verify_snapshot(_raw(c,source_metric_version="v4"))
        self.assertEqual(err.exception.code,"incompatible_metric_version")
        # A corrected counter is blocked in all stages; standalone C3 uses
        # source-level counter_decreased, legacy aggregate code differs.
        legacy=_legacy_inputs(c)
        legacy[1]["views"]=10000
        with self.assertRaises(ContractError) as err:
            compute_c_metrics(*legacy)
        self.assertEqual(err.exception.code,"counter_correction")
        with self.assertRaises(ContractError) as err:
            compute_views_growth_per_hour(10000,c["views"],
                _time(c["prev_at"]),_time(c["observed_at"]))
        self.assertEqual(err.exception.code,"counter_decreased")
        # Missing or wrong frozen cohorts cannot turn into approved results.
        legacy=_legacy_inputs(c)
        legacy[4]["is_frozen"]=False
        with self.assertRaises(ContractError) as err:
            compute_c_metrics(*legacy)
        self.assertEqual(err.exception.code,"creator_cohort_not_approved")

    def test_peer_version_mismatch_cannot_reuse_frozen_median(self):
        baseline=build_frozen_evidence(
            selection(peer(1,100),peer(2,300)))
        wrong=selection(peer(1,100,source_metric_version="v4"),
                        peer(2,300,source_metric_version="v4"))
        self.assertEqual(wrong.post_count,0)
        with self.assertRaises(ContractError) as error:
            build_frozen_evidence(wrong)
        self.assertEqual(error.exception.code,"empty_cohort")
        self.assertEqual(baseline.median_views,Decimal("200.000000000000"))

    def test_freshness_missing_naive_and_future_errors(self):
        t=datetime(2026,1,1,tzinfo=timezone.utc)
        for born,seen in [
            (t+timedelta(hours=2),t),
            (t.replace(tzinfo=None),t),
            (t,None),
        ]:
            with self.subTest(born=born,seen=seen),self.assertRaises(ContractError):
                compute_freshness_score(born,seen)

    def test_invalid_c7_score_not_probability_or_0_to_100(self):
        inputs=(Decimal("2"),Decimal("500"),Decimal("0.1"),Decimal("50"))
        r=compute_breakout_components(*inputs)
        self.assertGreater(r.breakout_score,100)
        self.assertFalse(r.normalized)
        for bad in (Decimal("-1"),Decimal("NaN"),Decimal("Infinity"),None,1.1):
            with self.subTest(bad=bad),self.assertRaises(ContractError):
                compute_breakout_components(bad,*inputs[1:])


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "Requires explicit disposable PG17; no production score authorizations",
)
class TestGoldSetPersistedPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        TestDurableWorkerPG17.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        TestDurableWorkerPG17.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        self.base=TestDurableWorkerPG17()
        self.base.conn=self.conn
        self.base.psycopg=self.psycopg
        self.base.setUp()

    def tearDown(self):
        self.base.tearDown()

    def test_original_signed_fixture_c7_exactly_matches_independent_oracle(self):
        from test_group_c_durable_worker import _SyntheticC7IAM
        request=self.base.request()
        lease=self.base.claim()
        receipt=self.base.worker.execute_score(
            self.conn,lease=lease,review_request_id=request.request_id,
            approval_verifier=_SyntheticC7IAM(),
        )
        row=self.conn.execute(
            """SELECT creator_median_views,niche_median_views,alpha_used,
                      k_used,p_l_used,p_c_used,p_s_used,
                      score_creator_component,score_growth_component,
                      score_engagement_component,score_freshness_component,
                      breakout_score,score_status,post_id,snapshot_id,
                      previous_snapshot_id,calculation_config_id
                 FROM calculated_metrics WHERE id=%s""",
            (receipt.score.calculated_metric_id,),
        )
        names=[desc.name for desc in row.description]
        db=dict(zip(names,row.fetchone()))
        self.assertEqual(db["score_status"],"complete")
        observed=self.conn.execute(
            """SELECT id,observed_at,views,likes,comments,shares
               FROM metric_snapshots WHERE id=%s""",
            (db["snapshot_id"],),
        ).fetchone()
        previous=self.conn.execute(
            """SELECT observed_at,views FROM metric_snapshots WHERE id=%s""",
            (db["previous_snapshot_id"],),
        ).fetchone()
        published=self.conn.execute(
            """SELECT published_at FROM source_video_metadata
               WHERE id=%s""",(self.base.target[2],),
        ).fetchone()[0]
        case={
            "views":observed[2],"previous_views":previous[1],
            "likes":observed[3],"comments":observed[4],"shares":observed[5],
            "observed_at":observed[1].isoformat(),
            "prev_at":previous[0].isoformat(),
            "published_at":published.isoformat(),
            "creator_median":str(db["creator_median_views"]),
            "niche_median":(str(db["niche_median_views"])
                             if db["niche_median_views"] is not None else None),
            "alpha":str(db["alpha_used"]),"k":str(db["k_used"]),
            "p_l":str(db["p_l_used"]),"p_c":str(db["p_c_used"]),
            "p_s":str(db["p_s_used"]),
        }
        expected=oracle_case(case)
        for stored,oracle in (
            ("score_creator_component","creator_component"),
            ("score_growth_component","growth_component"),
            ("score_engagement_component","engagement_component"),
            ("score_freshness_component","freshness_component"),
            ("breakout_score","breakout"),
        ):
            with self.subTest(stored=stored):
                self.assertEqual(db[stored],expected[oracle])
        self.assertEqual(db["snapshot_id"],self.base.target[1])
        self.assertEqual(db["previous_snapshot_id"],self.base.previous)
        self.assertEqual(db["calculation_config_id"],self.base.config)

    def test_immutable_original_c7_not_mutated_by_gold_oracle(self):
        from test_group_c_durable_worker import _SyntheticC7IAM
        request=self.base.request()
        lease=self.base.claim()
        receipt=self.base.worker.execute_score(
            self.conn,lease=lease,review_request_id=request.request_id,
            approval_verifier=_SyntheticC7IAM(),
        )
        before=self.conn.execute(
            "SELECT breakout_score FROM calculated_metrics WHERE id=%s",
            (receipt.score.calculated_metric_id,),
        ).fetchone()[0]
        # Oracle only works on local numeric values; no UPDATE or recompute
        # of certified original C7 rows is allowed for QA.
        self.assertEqual(before,receipt.score.breakout_score)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM calculated_metrics WHERE id=%s",
            (receipt.score.calculated_metric_id,),
        ).fetchone()[0],1)


if __name__=="__main__":
    unittest.main()
