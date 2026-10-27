"""GC-MOD-13: exact Decimal C1 + frozen project/creator lineage + PG17 IAM gates."""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal, ROUND_DOWN, localcontext
from uuid import uuid4

from intelligence.group_c.contracts import ContractError
from intelligence.group_c.creator_ratio import (
    C1_VERSION, CreatorRatioResult, PostgreSQLCreatorRatio,
    _approved_creator_context, compute_creator_ratio,
)

PROJECT="a"*32
SHA="b"*64


def inputs():
    freeze=dict(
        id=3,project_id=PROJECT,target_video_id=9,
        target_snapshot_id=10,target_metadata_id=11,purpose="creator",
        state="frozen_unreviewed",source_id="authorized",
        source_policy_version_id=12,
        source_metric_definition="platform_post_views",
        source_metric_version="v1",legacy_cohort_id=13,
        rules_sha256=SHA,selection_sha256=SHA,evidence_sha256=SHA,
        member_count=1,median_views=Decimal("100"),
    )
    parent=dict(id=13,purpose="creator",creator_id=18,
                source_id="authorized",source_metric_definition="platform_post_views",
                is_frozen=True,median_views=Decimal("100"),post_count=1,
                platform="youtube",content_type="video")
    peer=dict(project_id=PROJECT,video_id=28,snapshot_id=38,
              metadata_id=48,creator_id=18,platform="youtube",c_content_type="video",
              source_id="authorized",source_policy_version_id=12,
              metric_definition="platform_post_views",metric_version="v1",
              source_metric_definition="platform_post_views",
              source_metric_version="v1",views=100,raw_views=100,
              raw_snapshot_id=38,legacy_video_id=28,legacy_snapshot_id=38)
    args=dict(
        project_id=PROJECT,video_id=9,snapshot_id=10,
        metadata_id=11,creator_id=18,
        source_id="authorized",source_policy_version_id=12,
        metric_version="v1",freeze=freeze,cohort_parent=parent,
        peer_rows=[peer],
    )
    return args


class TestCreatorRatioPure(unittest.TestCase):
    def test_reference_c1_gold_decimal(self):
        self.assertEqual(compute_creator_ratio(200,Decimal("100"),Decimal("5")),
                         Decimal("1.952380952381"))

    def test_zero_is_exact_observation_and_not_null(self):
        self.assertEqual(compute_creator_ratio(0,0,5),Decimal("1.000000000000"))
        self.assertEqual(compute_creator_ratio(0,100,5),Decimal("0.047619047619"))

    def test_even_cohort_median_and_half_up(self):
        self.assertEqual(compute_creator_ratio(100,Decimal("100.5"),Decimal("0.5")),
                         Decimal("0.995049504950"))
        self.assertEqual(compute_creator_ratio(1,2,1),
                         Decimal("0.666666666667"))

    def test_decimal_context_is_isolated(self):
        expected=compute_creator_ratio(200,100,5)
        with localcontext() as ctx:
            ctx.prec=4
            ctx.rounding=ROUND_DOWN
            self.assertEqual(compute_creator_ratio(200,100,5),expected)

    def test_views_exact_bigint_no_float_or_bool(self):
        for bad in (-1,True,0.0,"1",None,2**63,float("inf")):
            with self.subTest(bad=bad),self.assertRaises(ContractError):
                compute_creator_ratio(bad,100,5)

    def test_median_exact_nonnegative_required(self):
        for bad in (-1,None,True,0.5,float("nan"),"NaN","Infinity"):
            with self.subTest(bad=bad),self.assertRaises(ContractError):
                compute_creator_ratio(5,bad,5)

    def test_alpha_explicit_positive_exact(self):
        for bad in (-1,None,True,0,0.5,float("inf"),"NaN","Infinity"):
            with self.subTest(bad=bad),self.assertRaises(ContractError):
                compute_creator_ratio(5,10,bad)

    def test_numeric_storage_overflow_guard(self):
        with self.assertRaises(ContractError):
            compute_creator_ratio(200,Decimal("1e18"),5)
        with self.assertRaises(ContractError):
            compute_creator_ratio(200,100,Decimal("1e18"))
        self.assertEqual(compute_creator_ratio(200,Decimal("0"),Decimal("5")),
                         Decimal("41.000000000000"))

    def test_creator_project_snapshot_source_policy_and_version(self):
        base=inputs()
        _approved_creator_context(**base)
        for key,value in (
            ("project_id","c"*32),("video_id",99),("snapshot_id",20),
            ("metadata_id",20),("source_id","different"),
            ("source_policy_version_id",15),("metric_version","v2"),
            ("creator_id",0),
        ):
            with self.subTest(field=key),self.assertRaises(ContractError):
                _approved_creator_context(**{**base,key:value})

    def test_target_self_and_cross_project_peer_rejected(self):
        for item in (
            {"video_id":9},{"project_id":"c"*32},{"creator_id":19},
            {"platform":"tiktok"},{"c_content_type":"carousel"},
            {"source_id":"other"},{"source_policy_version_id":99},
            {"metric_version":"v2"},{"source_metric_version":"v2"},
            {"raw_views":999},{"raw_snapshot_id":99},
            {"legacy_snapshot_id":39},{"legacy_video_id":999},
            {"source_metric_definition":"reach"},
        ):
            args=inputs()
            args["peer_rows"]=[{**args["peer_rows"][0],**item}]
            with self.subTest(item=item),self.assertRaises(ContractError):
                _approved_creator_context(**args)

    def test_parent_must_be_immutable_and_same_creator(self):
        for item in (
            {"creator_id":20},{"platform":"instagram"},{"is_frozen":False},
            {"median_views":Decimal("101")},{"post_count":2},
            {"purpose":"niche"},{"source_id":"other"},
            {"source_metric_definition":"reach"},
        ):
            args=inputs()
            args["cohort_parent"]={**args["cohort_parent"],**item}
            with self.subTest(item=item),self.assertRaises(ContractError):
                _approved_creator_context(**args)

    def test_frozen_cohort_must_have_original_provenance(self):
        for item in (
            {"state":"approved"},{"purpose":"niche"},
            {"source_metric_version":"v0"},
            {"member_count":2},{"rules_sha256":"not-sha"},
            {"evidence_sha256":None},{"target_snapshot_id":100},
            {"source_policy_version_id":None},
        ):
            args=inputs()
            args["freeze"]={**args["freeze"],**item}
            with self.subTest(item=item),self.assertRaises(ContractError):
                _approved_creator_context(**args)

    def test_duplicate_members_not_allowed(self):
        args=inputs()
        args["freeze"]={**args["freeze"],"member_count":2}
        args["cohort_parent"]={**args["cohort_parent"],"post_count":2}
        args["peer_rows"]=args["peer_rows"]*2
        with self.assertRaises(ContractError) as err:
            _approved_creator_context(**args)
        self.assertEqual(err.exception.code,"self_inclusion")

    def test_c1_is_not_complete_or_c7(self):
        self.assertEqual(C1_VERSION,"group-c-creator-ratio/v1")
        self.assertFalse(hasattr(PostgreSQLCreatorRatio,"persist"))
        self.assertFalse(hasattr(PostgreSQLCreatorRatio,"publish"))


class _SyntheticC1IAM:
    """Test-only authorization fixture; NOT a production IAM connector."""
    def __init__(self,ok=True):
        self.ok=ok
        self.calls=0
    def verify_c1_use(self,conn,**kwargs):
        self.calls+=1
        return (self.ok is True and kwargs["project_id"]
                and kwargs["video_id"]>0 and kwargs["snapshot_id"]>0
                and kwargs["activation_assertion_sha256"]
                and kwargs["creator_freeze_id"]>0)


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "requires disposable PostgreSQL17 and GC_TEST_DISPOSABLE_DB=YES",
)
class TestCreatorRatioPG17(unittest.TestCase):
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

    def tearDown(self):
        self.fixture.tearDown()

    def signed_and_active(self):
        request=self.fixture.signed()
        self.fixture.activate(request)
        row=self.conn.execute(
            "SELECT creator_freeze_id FROM group_c_review_requests WHERE id=%s",
            (request.request_id,),
        ).fetchone()
        return request,row[0]

    def derive(self,request,freeze_id,**changes):
        data=dict(conn=self.conn,project_id=self.project,
                  video_id=self.target[0],snapshot_id=self.target[1],
                  metadata_id=self.target[2],source_id=self.source,
                  source_policy_version_id=self.policy,metric_version="v1",
                  config_id=self.config,creator_freeze_id=freeze_id,
                  review_request_id=request.request_id,
                  approval_verifier=_SyntheticC1IAM())
        data.update(changes)
        return PostgreSQLCreatorRatio().derive(**data)

    def test_gold_approved_same_creator_two_exact_peers(self):
        request,freeze=self.signed_and_active()
        result=self.derive(request,freeze)
        self.assertIsInstance(result,CreatorRatioResult)
        self.assertEqual(result.creator_id,self.creator)
        self.assertEqual(result.creator_median_views,Decimal("200.000000000000"))
        self.assertEqual(result.alpha_used,Decimal("5.000000000000"))
        self.assertEqual(result.creator_ratio,Decimal("0.512195121951"))
        self.assertEqual(result.status,"c1_derived_unpersisted")
        self.assertFalse(result.ready_for_c7)
        with self.assertRaises(FrozenInstanceError):
            result.views=0

    def test_no_review_approval_cannot_derive(self):
        from intelligence.group_c.human_review import PostgreSQLHumanReview
        c,p=self.fixture.freeze()
        request=PostgreSQLHumanReview().create_request(
            self.conn,project_id=self.project,config_id=self.config,
            creator_freeze_id=c.id,prior_freeze_id=p.id,
            minimum_creator_posts=2,minimum_prior_posts=2,
        )
        with self.assertRaises(ContractError):
            self.derive(request,c.id)

    def test_missing_external_iam_or_refusal_denied(self):
        request,freeze=self.signed_and_active()
        for verifier in (None,_SyntheticC1IAM(False)):
            with self.subTest(verifier=verifier),self.assertRaises(ContractError):
                self.derive(request,freeze,approval_verifier=verifier)

    def test_wrong_project_config_or_freeze_denied(self):
        request,freeze=self.signed_and_active()
        for change in (
            {"project_id":uuid4().hex},{"config_id":999999999},
            {"creator_freeze_id":999999999},{"source_id":"other"},
            {"source_policy_version_id":999999999},
            {"metric_version":"v2"},{"video_id":self.peers[0][0]},
        ):
            with self.subTest(change=change),self.assertRaises(ContractError):
                self.derive(request,freeze,**change)

    def test_revoked_source_prevents_using_historical_approval(self):
        request,freeze=self.signed_and_active()
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.source,),
        )
        with self.assertRaises(ContractError):
            self.derive(request,freeze)

    def test_newer_raw_snapshot_prevents_old_approved_ratio(self):
        request,freeze=self.signed_and_active()
        self.conn.execute(
            """INSERT INTO metric_snapshots (
                 video_id,source_id,source_policy_version_id,
                 observed_at,collected_at,views,likes,comments,shares,
                 source_metric_definition,source_metric_version,
                 precision,data_quality_status,observation_time_assumed)
               VALUES(%s,%s,%s,now()-interval '30 minutes',
                      now()-interval '20 minutes',999,10,3,2,
                      'platform_post_views','v2','exact','ok',FALSE)""",
            (self.target[0],self.source,self.policy),
        )
        with self.assertRaises(ContractError) as err:
            self.derive(request,freeze)
        self.assertEqual(err.exception.code,"metric_history_changed")

    def test_creator_peer_change_blocked_by_lineage(self):
        request,freeze=self.signed_and_active()
        other=self.conn.execute(
            "INSERT INTO creators(platform,platform_creator_id) VALUES('youtube',%s) RETURNING id",
            (uuid4().hex,),
        ).fetchone()[0]
        self.conn.execute(
            "UPDATE videos SET creator_id=%s WHERE id=%s",
            (other,self.peers[0][0]),
        )
        with self.assertRaises(ContractError) as err:
            self.derive(request,freeze)
        self.assertEqual(err.exception.code,"incompatible_peer_creator")

    def test_derivation_is_read_only_and_repeatable(self):
        request,freeze=self.signed_and_active()
        one=self.derive(request,freeze)
        two=self.derive(request,freeze)
        self.assertEqual(one,two)
        self.assertEqual(len(one.evidence_sha256),64)
        self.assertFalse(self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM calculated_metrics WHERE post_id=%s)",
            (self.target[0],),
        ).fetchone()[0])


if __name__=="__main__":
    unittest.main()
