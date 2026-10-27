"""GC-MOD-14: optional niche relative performance, never a Breakout component."""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, ROUND_DOWN, localcontext
from uuid import uuid4

from intelligence.group_c.contracts import ContractError
from intelligence.group_c.niche_comparison import (
    NICHE_COMPARISON_VERSION, NicheComparisonResult,
    PostgreSQLNicheComparison, _approved_niche_context,
    compute_niche_comparison,
)

PROJECT="a"*32
SHA="b"*64


def context():
    freeze=dict(
        id=31,project_id=PROJECT,target_video_id=9,
        target_snapshot_id=10,target_metadata_id=11,purpose="niche",
        state="frozen_unreviewed",source_id="authorized",
        source_policy_version_id=12,
        source_metric_definition="platform_post_views",
        source_metric_version="v1",legacy_cohort_id=13,
        rules_sha256=SHA,selection_sha256=SHA,evidence_sha256=SHA,
        member_count=1,median_views=Decimal("250"),
    )
    parent=dict(id=13,purpose="niche",topic_id=77,
                platform="youtube",content_type="video",
                source_id="authorized",source_metric_definition="platform_post_views",
                is_frozen=True,median_views=Decimal("250"),post_count=1)
    peer=dict(video_id=28,snapshot_id=38,metadata_id=48,
              project_id=PROJECT,topic_id=77,
              platform="youtube",c_content_type="video",
              source_id="authorized",source_policy_version_id=12,
              metric_definition="platform_post_views",metric_version="v1",
              source_metric_definition="platform_post_views",
              source_metric_version="v1",
              views=250,raw_views=250,raw_snapshot_id=38,
              legacy_video_id=28,legacy_snapshot_id=38)
    return dict(project_id=PROJECT,video_id=9,snapshot_id=10,
                metadata_id=11,topic_id=77,source_id="authorized",
                source_policy_version_id=12,metric_version="v1",
                platform="youtube",content_type="video",
                niche_freeze=freeze,cohort_parent=parent,peer_rows=[peer])


class TestNicheComparisonPure(unittest.TestCase):
    def test_reference_numeric_ratio_is_independent_of_breakout(self):
        self.assertEqual(compute_niche_comparison(200,250,5),
                         Decimal("0.803921568627"))
        self.assertEqual(NICHE_COMPARISON_VERSION,"group-c-niche-outlier/v1")

    def test_optional_unconfigured_is_null_only(self):
        self.assertIsNone(compute_niche_comparison(200,None,5))
        self.assertIsNone(compute_niche_comparison(0,None,5))

    def test_zero_views_and_zero_median_exact(self):
        self.assertEqual(compute_niche_comparison(0,0,5),
                         Decimal("1.000000000000"))
        self.assertEqual(compute_niche_comparison(200,0,5),
                         Decimal("41.000000000000"))

    def test_denominator_smoothing_and_half_up(self):
        self.assertEqual(compute_niche_comparison(1,2,1),
                         Decimal("0.666666666667"))
        self.assertEqual(compute_niche_comparison(100,Decimal("100.5"),Decimal("0.5")),
                         Decimal("0.995049504950"))

    def test_context_precision_does_not_change_niche(self):
        expected=compute_niche_comparison(200,250,5)
        with localcontext() as c:
            c.prec=5
            c.rounding=ROUND_DOWN
            self.assertEqual(compute_niche_comparison(200,250,5),expected)
            self.assertIsNone(compute_niche_comparison(200,None,5))

    def test_undefined_niche_cannot_skip_alpha_validation(self):
        for alpha in (0,-1,None,True,0.5,float("inf"),"NaN",
                      "0.00000000000001",Decimal("1e18")):
            with self.subTest(alpha=alpha),self.assertRaises(ContractError):
                compute_niche_comparison(5,None,alpha)

    def test_invalid_views_rejected_even_without_niche(self):
        for views in (-1,True,2**63,None,1.25,"200"):
            with self.subTest(views=views),self.assertRaises(ContractError):
                compute_niche_comparison(views,None,5)

    def test_bad_configured_median_is_error_not_null(self):
        for median in (-1,True,"NaN",float("inf"),0.1,
                       Decimal("250.0000000000005"),Decimal("1e18")):
            with self.subTest(median=median),self.assertRaises(ContractError):
                compute_niche_comparison(200,median,5)

    def test_niche_cannot_use_target_or_duplicate_peer(self):
        for bad in ({"video_id":9},{"project_id":"c"*32}):
            p=context()
            p["peer_rows"]=[{**p["peer_rows"][0],**bad}]
            with self.subTest(bad=bad),self.assertRaises(ContractError):
                _approved_niche_context(**p)
        p=context()
        p["niche_freeze"]["member_count"]=2
        p["cohort_parent"]["post_count"]=2
        p["peer_rows"]=p["peer_rows"]*2
        with self.assertRaises(ContractError) as ex:
            _approved_niche_context(**p)
        self.assertEqual(ex.exception.code,"self_inclusion")

    def test_niche_allows_different_creators_with_exact_same_topic(self):
        p=context()
        _approved_niche_context(**p)
        # No equality check on creator_id: peers may be different creators
        # within one exact canonical niche topic.

    def test_niche_target_project_policy_snapshot_version_fail_closed(self):
        for key,value in (
            ("project_id","c"*32),("video_id",19),("snapshot_id",20),
            ("metadata_id",21),("topic_id",78),
            ("source_id","other"),("source_policy_version_id",44),
            ("metric_version","v9"),("content_type","carousel"),
            ("platform","tiktok"),
        ):
            p=context()
            with self.subTest(key=key),self.assertRaises(ContractError):
                _approved_niche_context(**{**p,key:value})

    def test_niche_peer_topic_source_metric_and_raw_lineage_fail_closed(self):
        for bad in (
            {"topic_id":78},{"source_id":"other"},
            {"metric_version":"v2"},{"source_metric_version":"v3"},
            {"metric_definition":"reach"},{"source_metric_definition":"impressions"},
            {"source_policy_version_id":99},{"raw_views":999},
            {"raw_snapshot_id":99},{"legacy_snapshot_id":100},
            {"legacy_video_id":999},{"c_content_type":"carousel"},
        ):
            p=context()
            p["peer_rows"]=[{**p["peer_rows"][0],**bad}]
            with self.subTest(bad=bad),self.assertRaises(ContractError):
                _approved_niche_context(**p)

    def test_legacy_topic_and_median_must_be_exact(self):
        for bad in (
            {"topic_id":78},{"is_frozen":False},{"median_views":251},
            {"post_count":4},{"purpose":"creator"},{"platform":"instagram"},
            {"source_metric_definition":"reach"},
        ):
            p=context()
            p["cohort_parent"]={**p["cohort_parent"],**bad}
            with self.subTest(bad=bad),self.assertRaises(ContractError):
                _approved_niche_context(**p)

    def test_frozen_cohort_cannot_be_unreviewed_or_version_drifted(self):
        for bad in (
            {"state":"approved"},{"purpose":"creator"},
            {"source_metric_version":"v4"},{"evidence_sha256":"BAD"},
            {"member_count":2},{"target_snapshot_id":99},
            {"source_metric_definition":"reach"},
        ):
            p=context()
            p["niche_freeze"]={**p["niche_freeze"],**bad}
            with self.subTest(bad=bad),self.assertRaises(ContractError):
                _approved_niche_context(**p)

    def test_pure_niche_has_no_breakout_or_worker_side_effects(self):
        self.assertFalse(hasattr(PostgreSQLNicheComparison,"persist"))
        self.assertFalse(hasattr(PostgreSQLNicheComparison,"publish"))


class _SyntheticNicheIAM:
    """Never use outside disposable test cases; not real IAM."""
    def __init__(self, allow=True):
        self.allow=allow
    def verify_niche_use(self,conn,**kw):
        return bool(self.allow is True and kw["subject_sha256"]
                    and kw["decision_id"] and kw["activation_event_id"]
                    and kw["activation_assertion_sha256"])


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "requires disposable PG17 with GC_TEST_DISPOSABLE_DB=YES",
)
class TestNicheComparisonPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_group_c_config_activation import TestActivationPG17
        TestActivationPG17.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from test_group_c_config_activation import TestActivationPG17
        TestActivationPG17.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_config_activation import TestActivationPG17
        self.fixture=TestActivationPG17()
        self.fixture.conn=self.conn
        self.fixture.psycopg=self.psycopg
        self.fixture.setUp()
        for name in ("project","source","policy","creator","target","peers","config"):
            setattr(self,name,getattr(self.fixture,name))
        self.topic=self.conn.execute(
            "INSERT INTO topics(name) VALUES(%s) RETURNING id",
            ("gc-niche-"+uuid4().hex,),
        ).fetchone()[0]
        self.conn.execute("UPDATE creators SET topic_id=%s WHERE id=%s",
                          (self.topic,self.creator))

    def tearDown(self):
        self.fixture.tearDown()

    def prepare(self,with_niche):
        from intelligence.group_c.cohort_freeze import PostgreSQLCohortFreezeWriter
        from intelligence.group_c.human_review import PostgreSQLHumanReview
        from test_group_c_human_review import _TestVerifier
        creator,prior=self.fixture.freeze()
        niche_id=None
        if with_niche:
            rules=replace(self.fixture._fixture.rule("creator"),
                          purpose="niche",creator_id=None,topic_id=self.topic)
            niche=PostgreSQLCohortFreezeWriter().freeze(
                self.conn,rules=rules,target_snapshot_id=self.target[1],
                target_metadata_id=self.target[2])
            niche_id=niche.id
        request=PostgreSQLHumanReview().create_request(
            self.conn,project_id=self.project,config_id=self.config,
            creator_freeze_id=creator.id,prior_freeze_id=prior.id,
            niche_freeze_id=niche_id,
            minimum_creator_posts=2,minimum_prior_posts=2,
            minimum_niche_posts=2 if with_niche else None,
        )
        decision=PostgreSQLHumanReview().record_decision(
            self.conn,request_id=request.request_id,decision="approved",
            signed_assertion="test-signed-human-assertion-token",
            note="Reviewed the exact niche composition and median",
            reviewer_verifier=_TestVerifier(),
        )
        self.assertTrue(decision.approved)
        self.fixture.activate(request)
        return request,niche_id

    def derive(self,request,niche_id,**changes):
        kwargs=dict(
            conn=self.conn,project_id=self.project,
            video_id=self.target[0],snapshot_id=self.target[1],
            metadata_id=self.target[2],source_id=self.source,
            source_policy_version_id=self.policy,metric_version="v1",
            config_id=self.config,review_request_id=request.request_id,
            niche_freeze_id=niche_id,approval_verifier=_SyntheticNicheIAM(),
        )
        kwargs.update(changes)
        return PostgreSQLNicheComparison().derive(**kwargs)

    def test_approved_niche_uses_exact_topic_median_and_never_breakout(self):
        request,niche_id=self.prepare(with_niche=True)
        result=self.derive(request,niche_id)
        self.assertIsInstance(result,NicheComparisonResult)
        self.assertEqual(result.topic_id,self.topic)
        self.assertEqual(result.niche_median_views,Decimal("200.000000000000"))
        self.assertEqual(result.niche_outlier,Decimal("0.512195121951"))
        self.assertFalse(result.contributes_to_breakout)
        self.assertFalse(result.ready_for_breakout)
        self.assertEqual(result.status,"niche_derived_unpersisted")
        with self.assertRaises(FrozenInstanceError):
            result.niche_outlier=0

    def test_approved_no_niche_returns_exact_null_not_zero(self):
        request,niche_id=self.prepare(with_niche=False)
        result=self.derive(request,niche_id)
        self.assertIsNone(result.niche_outlier)
        self.assertIsNone(result.niche_median_views)
        self.assertIsNone(result.niche_freeze_id)
        self.assertEqual(result.status,"niche_not_configured")
        self.assertFalse(result.contributes_to_breakout)

    def test_mismatched_or_missing_claimed_niche_is_not_silently_null(self):
        request,niche_id=self.prepare(with_niche=True)
        with self.assertRaises(ContractError):
            self.derive(request,None)
        with self.assertRaises(ContractError):
            self.derive(request,999999999)
        request_none,_=self.prepare_new_no_niche_request()
        with self.assertRaises(ContractError):
            self.derive(request_none,niche_id)

    def prepare_new_no_niche_request(self):
        # No new global activation here: this method should be used only
        # for read-time approved niche mismatch in the existing config.
        # A second request with another subject cannot activate the same
        # config version, so instead use it for negative preactivation proof.
        from intelligence.group_c.human_review import PostgreSQLHumanReview
        c,p=self.fixture.freeze()
        pending=PostgreSQLHumanReview().create_request(
            self.conn,project_id=self.project,config_id=self.config,
            creator_freeze_id=c.id,prior_freeze_id=p.id,
            minimum_creator_posts=2,minimum_prior_posts=2,
        )
        return pending,None

    def test_source_revoked_after_approval_blocks_ratio_and_null(self):
        request,niche_id=self.prepare(with_niche=True)
        self.conn.execute("UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
                          (self.source,))
        with self.assertRaises(ContractError):
            self.derive(request,niche_id)

    def test_different_target_topic_fails_not_fallback_to_another_niche(self):
        request,niche_id=self.prepare(with_niche=True)
        other=self.conn.execute("INSERT INTO topics(name) VALUES(%s) RETURNING id",
                                ("other-"+uuid4().hex,)).fetchone()[0]
        self.conn.execute("UPDATE creators SET topic_id=%s WHERE id=%s",
                          (other,self.creator))
        with self.assertRaises(ContractError) as err:
            self.derive(request,niche_id)
        self.assertEqual(err.exception.code,"incompatible_peer_topic")

    def test_topic_disabled_blocks_approved_niche(self):
        request,niche_id=self.prepare(with_niche=True)
        self.conn.execute("UPDATE topics SET enabled=FALSE WHERE id=%s",
                          (self.topic,))
        with self.assertRaises(ContractError) as err:
            self.derive(request,niche_id)
        self.assertEqual(err.exception.code,"niche_topic_unavailable")

    def test_wrong_metric_version_project_or_iam_denied(self):
        request,niche_id=self.prepare(with_niche=True)
        for changes in (
            {"metric_version":"v9"},{"source_id":"other"},
            {"project_id":uuid4().hex},{"config_id":999999999},
            {"approval_verifier":None},
            {"approval_verifier":_SyntheticNicheIAM(False)},
        ):
            with self.subTest(changes=changes),self.assertRaises(ContractError):
                self.derive(request,niche_id,**changes)

    def test_raw_latest_changed_cannot_use_old_approved_niche(self):
        request,niche_id=self.prepare(with_niche=True)
        self.conn.execute(
            """INSERT INTO metric_snapshots
                (video_id,source_id,source_policy_version_id,
                 observed_at,collected_at,views,likes,comments,shares,
                 source_metric_definition,source_metric_version,precision,
                 data_quality_status,observation_time_assumed)
               VALUES(%s,%s,%s,now()-interval '30 minutes',
                      now()-interval '20 minutes',888,10,3,2,
                      'platform_post_views','v99','exact','ok',FALSE)""",
            (self.target[0],self.source,self.policy),
        )
        with self.assertRaises(ContractError) as err:
            self.derive(request,niche_id)
        self.assertEqual(err.exception.code,"metric_history_changed")

    def test_repeated_niche_read_is_identical_and_no_score_persistence(self):
        request,niche_id=self.prepare(with_niche=True)
        a=self.derive(request,niche_id)
        b=self.derive(request,niche_id)
        self.assertEqual(a,b)
        self.assertEqual(len(a.evidence_sha256),64)
        self.assertFalse(self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM calculated_metrics WHERE post_id=%s)",
            (self.target[0],),
        ).fetchone()[0])


if __name__=="__main__":
    unittest.main()
