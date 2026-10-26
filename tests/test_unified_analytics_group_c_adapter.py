"""UA-MOD-10: persisted C1-C7 facts with strict unapproved GC057 gate.

Mocks emulate future peer schema rows; current feature 057 deliberately has
ONLY frozen_unreviewed cohorts. A stored complete number is never an
authenticated approved cohort or a UAE qualified score.
"""
from __future__ import annotations

import copy
import os
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from intelligence.unified_analytics import AnalyticsBoundaryError, InputScope
from intelligence.unified_analytics.dto import EvidenceRead, EvidenceRef, ReadState
from intelligence.unified_analytics.ports import (
    CapabilityState, PortCapability, PORT_SCHEMAS, UAEPorts,
)
from intelligence.unified_analytics.qdp_adapter import QDPQualifiedSetReader
from intelligence.unified_analytics.group_c_adapter import (
    C_READER_WIRE, GroupCPersistedPins, GroupCPersistedScoreReader,
    PersistedScoreFacts, REQUIRED_RELATIONS, PROBE_SQL, SCORE_SQL,
    FREEZE_SQL, REVISION_SQL,
)

NOW=datetime(2026,10,26,16,tzinfo=timezone.utc)
PROJECT="a"*32
QDP_RUN="00112233-4455-4677-8899-aabbccddeeff"
C_RUN="11112222-3333-4444-8555-666677778888"
SOURCE="licensed_source"
SCOPE=InputScope(PROJECT,17,SOURCE,9,"video",QDP_RUN,"c"*64)
D=lambda n:Decimal(n)


def fixture(*,niche=False):
    pins=GroupCPersistedPins(
        scope=SCOPE,c_run_id=C_RUN,calculated_metric_id=51,
        current_snapshot_id=202,previous_snapshot_id=201,
        metadata_id=70,config_id=12,config_version="gc-decimal/v1",
        creator_cohort_id=301,prior_cohort_id=303,
        niche_cohort_id=302 if niche else None,
        creator_freeze_sha256="1"*64,prior_freeze_sha256="3"*64,
        niche_freeze_sha256="2"*64 if niche else None,
        min_creator_peers=5,min_prior_peers=7,
        min_niche_peers=4 if niche else None,
        source_metric_version="1.0",
        qdp_c_preparation_sha256="b"*64,
    )
    data=dict(
        c_run_id=C_RUN,project_id=PROJECT,video_id=17,source_id=SOURCE,
        run_policy_id=9,qdp_input_sha256="b"*64,
        run_state="completed",reason_code=None,
        run_snapshot_id=202,run_previous_id=201,run_metadata_id=70,
        run_config_id=12,run_creator_id=301,
        run_prior_id=303,run_niche_id=302 if niche else None,
        run_calculated_id=51,
        completed_at=NOW-timedelta(hours=1),
        qdp_run_id=QDP_RUN,inbox_state="accepted",
        analysis_group="C",inbox_schema="qdp-handoff/v1",
        inbox_input_sha="b"*64,
        calculated_id=51,post_id=17,
        snapshot_id=202,previous_snapshot_id=201,published_metadata_id=70,
        calculation_config_id=12,creator_cohort_id=301,
        prior_cohort_id=303,niche_cohort_id=302 if niche else None,
        calculation_status="complete",score_status="complete",
        calculated_at=NOW-timedelta(hours=2),
        creator_median_views=D("100"),niche_median_views=D("140") if niche else None,
        alpha_used=D("5"),k_used=D("20"),p_l_used=D("0.1"),
        p_c_used=D("0.02"),p_s_used=D("0.03"),
        creator_ratio=D("2"),niche_outlier=D("1.5") if niche else None,
        views_growth_per_hour=D("40"),weighted_engagement_smoothed=D("50"),
        freshness_score=D("80"),
        score_creator_component=D("0.9"),
        score_growth_component=D("12"),
        score_engagement_component=D("7.5"),
        score_freshness_component=D("8"),
        breakout_score=D("28.4"),
        calculation_version="gc-decimal/v1",
        config_active=True,configured_alpha=D("5"),
        configured_k=D("20"),
        weights_json=dict(creator_ratio=0.45,
            views_growth_per_hour=0.30,weighted_engagement_smoothed=0.15,
            freshness_score=0.10),
        freshness_half_life_h=D("48"),normalization_enabled=False,
        prior_source_policy={},cohort_selection_rules={},growth_policy={},
        metadata_id=70,metadata_video_id=17,
        metadata_source_id=SOURCE,metadata_policy_id=9,
        metadata_current=True,metadata_deleted_at=None,
        metadata_expires_at=NOW+timedelta(days=2),
        published_at=NOW-timedelta(days=6),
        current_id=202,current_video_id=17,current_source_id=SOURCE,
        current_policy_id=9,current_metric_definition="platform_post_views",
        current_metric_version="1.0",
        current_observed_at=NOW-timedelta(hours=3),
        current_collected_at=NOW-timedelta(hours=2,minutes=45),
        current_expires_at=NOW+timedelta(days=1),
        current_quality="ok",current_time_assumed=False,
        current_precision="exact",
        current_views=200,current_likes=15,current_comments=3,
        current_shares=5,
        previous_id=201,previous_video_id=17,
        previous_source_id=SOURCE,previous_policy_id=9,
        previous_metric_definition="platform_post_views",
        previous_metric_version="1.0",
        previous_observed_at=NOW-timedelta(days=1),
        previous_collected_at=NOW-timedelta(days=1)+timedelta(minutes=3),
        previous_expires_at=NOW+timedelta(days=1),
        previous_quality="ok",previous_time_assumed=False,
        previous_precision="exact",previous_views=80,
        source_state="enabled",can_store_metrics=True,
        can_derive_scores=True,source_retention_days=30,
        policy_active=True,
        policy_reviewed_at=NOW-timedelta(days=4),
        policy_permissions={"can_store_metrics":True,"can_derive_scores":True},
        retention_policy={"retention_days":30},
    )
    cohorts={}
    for purpose,cohort_id,evidence,count,minimum in (
        ("creator",301,"1"*64,5,5),
        ("engagement_prior",303,"3"*64,7,7),
        ("niche",302,"2"*64,4,4),
    ):
        cohorts[cohort_id]=dict(
            freeze_id=cohort_id+100,project_id=PROJECT,
            legacy_cohort_id=cohort_id,cohort_id=cohort_id,
            target_video_id=17,target_snapshot_id=202,
            target_metadata_id=70,purpose=purpose,legacy_purpose=purpose,
            source_id=SOURCE,source_policy_version_id=9,
            source_metric_definition="platform_post_views",
            source_metric_version="1.0",
            rules_version="group-c-cohort-rules/v1",
            estimator_version="group-c-cohort-estimator/v1",
            rules_sha256="4"*64,selection_sha256="5"*64,
            evidence_sha256=evidence,member_count=count,
            is_frozen=True,freeze_state="frozen_unreviewed",
            sealed_at=NOW-timedelta(hours=5),
            evaluated_as_of=NOW-timedelta(hours=6),
            post_count=count,frozen_members=count,legacy_members=count,
            distinct_videos=count,invalid_members=0,
            median_views=D("100") if purpose=="creator" else
                         D("140") if purpose=="niche" else None,
            legacy_median=D("100") if purpose=="creator" else
                          D("140") if purpose=="niche" else None,
            prior_like_rate=D("0.1") if purpose=="engagement_prior" else None,
            legacy_like_rate=D("0.1") if purpose=="engagement_prior" else None,
            prior_comment_rate=D("0.02") if purpose=="engagement_prior" else None,
            legacy_comment_rate=D("0.02") if purpose=="engagement_prior" else None,
            prior_share_rate=D("0.03") if purpose=="engagement_prior" else None,
            legacy_share_rate=D("0.03") if purpose=="engagement_prior" else None,
        )
    return dict(pins=pins,score=data,freezes=cohorts,revisions=[])


class Cursor:
    def __init__(self,rows): self.rows=rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows


class FakeDB:
    def __init__(self,world):
        self.world=world
        self.schema=True
        self.fail=None
        self.calls=[]
    def execute(self,sql,args=()):
        self.calls.append((sql,args))
        if sql==self.fail:
            raise RuntimeError("host=postgres password=never-print")
        if sql==PROBE_SQL:
            return Cursor([{n:n if self.schema else None for n in REQUIRED_RELATIONS}])
        if sql==SCORE_SQL:
            return Cursor([self.world["score"]] if self.world["score"] else [])
        if sql==FREEZE_SQL:
            cohort=self.world["freezes"].get(args[2])
            return Cursor([cohort] if cohort else [])
        if sql==REVISION_SQL:
            return Cursor(self.world["revisions"])
        raise AssertionError("Unexpected Group C SQL")


class QDPStub(QDPQualifiedSetReader):
    """Mock only; not an external attested QDP qualified membership."""
    def __init__(self,db,pins):
        self._conn=db
        self._pins=SimpleNamespace(
            scope=pins.scope,
            preparation_shas=("1"*64,"2"*64,pins.qdp_c_preparation_sha256),
            group_c_snapshot_ids=(pins.previous_snapshot_id,pins.current_snapshot_id),
            group_c_config_id=pins.config_id,
        )
        self.declared=True
        self.located=True
    def capability(self):
        return (PortCapability("qdp",CapabilityState.DECLARED,
                PORT_SCHEMAS["qdp"],"qdp-handoff/v1")
            if self.declared else PortCapability(
                "qdp",CapabilityState.UNAVAILABLE,reason_code="not_connected"))
    def read_qualified_membership(self,scope):
        if not self.located:
            return EvidenceRead("qdp",scope,ReadState.MISSING,
                                reason_code="not_qualified")
        return EvidenceRead("qdp",scope,ReadState.LOCATED,reference=EvidenceRef(
            scope=scope,port="qdp",record_key="mock:qualified",
            producer_schema="qdp-handoff/v1",
            producer_version="fixture-v1",payload_sha256="1"*64,
        ))


class GroupCScoreTests(unittest.TestCase):
    def setup(self,world=None,*,niche=False):
        data=copy.deepcopy(world or fixture(niche=niche))
        db=FakeDB(data)
        qdp=QDPStub(db,data["pins"])
        reader=GroupCPersistedScoreReader(db,data["pins"],qdp,as_of=NOW)
        return reader,db,qdp,data

    def expect_rejected(self,world,code):
        r,_,_,_=self.setup(world)
        with self.assertRaises(AnalyticsBoundaryError) as e:
            r.inspect_persisted_score(r._pins.scope)
        self.assertEqual(e.exception.code,code)

    def test_stored_score_positive_inspection_not_authorized_approval(self):
        reader,db,_,d=self.setup()
        self.assertIs(reader.capability().state,CapabilityState.DECLARED)
        score=reader.inspect_persisted_score(SCOPE)
        self.assertIsInstance(score,PersistedScoreFacts)
        self.assertEqual(score.breakout_score,D("28.4"))
        self.assertEqual(score.creator_count,5)
        self.assertEqual(score.prior_count,7)
        self.assertIsNone(score.niche_count)
        self.assertFalse(score.approved)
        self.assertEqual(score.status,"integrity_checked_unapproved")
        self.assertEqual(len(score.lineage_sha256),64)
        self.assertEqual(score,reader.inspect_persisted_score(SCOPE))
        self.assertNotIn("compute_c_metrics",str(db.calls))
        final=reader.read_persisted_metric(SCOPE)
        self.assertIs(final.state,ReadState.MISSING)
        self.assertEqual(final.reason_code,"c_effective_approval_unavailable")
        self.assertFalse(UAEPorts(group_c=reader).status("group_c").qualified)

    def test_optional_niche_present_and_missing_cases(self):
        r,_,_,d=self.setup(niche=True)
        got=r.inspect_persisted_score(SCOPE)
        self.assertEqual(got.niche_count,4)
        self.assertEqual(got.breakout_score,D("28.4"))
        self.assertFalse(got.approved)
        d=fixture()
        self.assertIsNone(self.setup(d)[0].inspect_persisted_score(SCOPE).niche_count)

    def test_breakout_score_not_capped_at_100(self):
        d=fixture()
        d["score"]["score_growth_component"]=D("200")
        d["score"]["breakout_score"]=D("216.4")
        score=self.setup(d)[0].inspect_persisted_score(SCOPE)
        self.assertEqual(score.breakout_score,D("216.4"))

    def test_raw_score_or_calc_status_partial_unknown_denied(self):
        for k,v in (
            ("run_state","partial"),("run_state","cancelled"),
            ("inbox_state","received"),("score_status","insufficient_data"),
            ("calculation_status","pending_configuration"),
            ("config_active",False),("normalization_enabled",True),
            ("freshness_half_life_h",D("24")),("reason_code","blocked"),
        ):
            with self.subTest(field=k):
                d=fixture()
                d["score"][k]=v
                self.expect_rejected(d,"c_unapproved_or_incomplete_score")

    def test_project_video_policy_and_cross_QDP_run_denied(self):
        for k,v in (
            ("project_id","f"*32),("video_id",88),
            ("run_policy_id",19),("qdp_input_sha256","9"*64),
            ("qdp_run_id",C_RUN),("inbox_input_sha","a"*64),
            ("run_creator_id",999),("run_prior_id",999),
            ("run_metadata_id",44),("post_id",999),
            ("calculated_id",777),("previous_snapshot_id",203),
            ("calculation_version","other"),
        ):
            with self.subTest(field=k):
                d=fixture()
                d["score"][k]=v
                self.expect_rejected(d,"c_persisted_lineage_mismatch")

    def test_score_cannot_lose_any_persisted_component(self):
        for k,v in (
            ("creator_ratio",None),("views_growth_per_hour",None),
            ("weighted_engagement_smoothed",None),
            ("freshness_score",None),
            ("score_creator_component",None),
            ("score_growth_component",None),
            ("score_freshness_component",D("-1")),
            ("breakout_score",D("28.5")),
        ):
            d=fixture()
            d["score"][k]=v
            with self.subTest(field=k):
                self.expect_rejected(d,(
                    "c_persisted_components_inconsistent"
                    if k=="breakout_score" else "c_persisted_component_invalid"
                ))

    def test_source_and_policy_rights_must_be_current(self):
        for k,v in (
            ("source_state","revoked"),
            ("can_store_metrics",False),("can_derive_scores",False),
            ("policy_active",False),("policy_permissions",{}),
        ):
            d=fixture()
            d["score"][k]=v
            with self.subTest(field=k):
                self.expect_rejected(d,"c_source_score_rights_revoked")

    def test_snapshots_exact_source_metric_unit_and_quality(self):
        for k,v in (
            ("current_source_id","other"),("previous_policy_id",99),
            ("current_metric_definition","impressions"),
            ("previous_metric_version","2"),
            ("current_quality","unverified"),
            ("current_precision","estimated"),
            ("current_time_assumed",True),("previous_quality","partial"),
        ):
            d=fixture()
            d["score"][k]=v
            with self.subTest(field=k):
                self.expect_rejected(d,"c_snapshot_source_or_version_mismatch")

    def test_snapshots_retracted_expired_and_time_order(self):
        d=fixture();d["score"]["current_expires_at"]=NOW
        self.expect_rejected(d,"c_snapshot_expired")
        d=fixture();d["score"]["previous_observed_at"]=NOW+timedelta(minutes=1)
        d["score"]["previous_collected_at"]=NOW+timedelta(minutes=2)
        self.expect_rejected(d,"c_snapshot_timestamps_invalid")
        d=fixture();d["score"]["previous_observed_at"]=NOW-timedelta(hours=2)
        d["score"]["previous_collected_at"]=NOW-timedelta(hours=1)
        self.expect_rejected(d,"c_snapshot_time_not_increasing")

    def test_correction_and_negative_views_are_not_score_facts(self):
        for k,v in (
            ("current_views",0),("current_views",25),
            ("current_comments",None),("previous_views",None),
        ):
            d=fixture()
            d["score"][k]=v
            with self.subTest(field=k):
                self.expect_rejected(d,"c_raw_counters_invalid")

    def test_missing_unverified_publication_or_future_metadata_denied(self):
        for k,v,code in (
            ("metadata_current",False,"c_publication_unavailable"),
            ("metadata_deleted_at",NOW,"c_publication_unavailable"),
            ("metadata_policy_id",4,"c_publication_unavailable"),
            ("metadata_expires_at",NOW,"c_publication_expired"),
            ("published_at",NOW+timedelta(days=1),"c_publication_date_invalid"),
        ):
            d=fixture()
            d["score"][k]=v
            with self.subTest(field=k):
                self.expect_rejected(d,code)

    def test_config_alpha_k_and_weights_are_exact(self):
        for k,v in (
            ("configured_alpha",None),
            ("configured_k",D("0")),
        ):
            d=fixture();d["score"][k]=v
            self.expect_rejected(d,"c_config_parameters_missing")
        for k,v in (
            ("configured_alpha",D("9")),
            ("weights_json",{"creator_ratio":0.75}),
        ):
            d=fixture();d["score"][k]=v
            self.expect_rejected(d,"c_score_config_mismatch")

    def test_minimum_peer_sample_explicit_not_inferred(self):
        for cohort,minimum in ((301,5),(303,7)):
            d=fixture()
            d["freezes"][cohort]["member_count"]=minimum-1
            self.expect_rejected(d,"c_cohort_insufficient_or_poisoned")
        d=fixture(niche=True)
        d["freezes"][302]["member_count"]=3
        self.expect_rejected(d,"c_cohort_insufficient_or_poisoned")

    def test_cohort_poisoning_cross_project_target_and_snapshot(self):
        for k,v in (
            ("project_id","f"*32),("target_video_id",99),
            ("target_snapshot_id",201),("source_id","other"),
            ("source_policy_version_id",5),("source_metric_version","other"),
            ("legacy_cohort_id",45),("evidence_sha256","a"*64),
        ):
            d=fixture()
            d["freezes"][301][k]=v
            with self.subTest(field=k):
                self.expect_rejected(d,"c_cohort_wrong_lineage")

    def test_freeze_immutability_and_member_consistency(self):
        d=fixture();d["freezes"][301]["is_frozen"]=False
        self.expect_rejected(d,"c_cohort_not_frozen")
        d=fixture();d["freezes"][301]["freeze_state"]="approved"
        self.expect_rejected(d,"c_cohort_not_frozen")
        d=fixture();d["freezes"][301]["sealed_at"]=NOW+timedelta(minutes=1)
        self.expect_rejected(d,"c_cohort_not_frozen")
        for field in ("frozen_members","legacy_members","distinct_videos"):
            d=fixture();d["freezes"][301][field]-=1
            self.expect_rejected(d,"c_cohort_insufficient_or_poisoned")
        d=fixture();d["freezes"][301]["invalid_members"]=1
        self.expect_rejected(d,"c_cohort_insufficient_or_poisoned")

    def test_baseline_and_prior_rates_frozen_consistency(self):
        d=fixture();d["freezes"][301]["legacy_median"]=D("101")
        self.expect_rejected(d,"c_cohort_baseline_invalid")
        d=fixture();d["score"]["creator_median_views"]=D("105")
        self.expect_rejected(d,"c_cohort_baseline_mismatch")
        d=fixture();d["freezes"][303]["prior_comment_rate"]=D("0.04")
        self.expect_rejected(d,"c_cohort_priors_mismatch")

    def test_superseding_revisions_veto_even_completed_score(self):
        d=fixture()
        d["revisions"]=[dict(revision=1,revision_kind="source_revoked",
                             evidence_sha256="f"*64)]
        self.expect_rejected(d,"c_score_superseded_or_review_pending")
        d=fixture()
        d["revisions"]=[dict(revision=2,revision_kind="source_revoked",
                             evidence_sha256="f"*64)]
        self.expect_rejected(d,"c_revision_lineage_invalid")

    def test_wrong_reader_scope_and_qdp_dependency(self):
        d=fixture()
        r,db,q,_=self.setup(d)
        with self.assertRaises(AnalyticsBoundaryError):
            r.read_persisted_metric(replace(SCOPE,project_id="f"*32))
        q.located=False
        with self.assertRaises(AnalyticsBoundaryError) as x:
            r.inspect_persisted_score(SCOPE)
        self.assertEqual(x.exception.code,"c_qdp_membership_unverified")
        q.located=True
        q.declared=False
        self.assertIs(r.read_persisted_metric(SCOPE).state,ReadState.UNAVAILABLE)
        q.declared=True
        db.schema=False
        self.assertEqual(r.capability().reason_code,"c_peer_schema_missing")

    def test_invalid_pins_and_same_connection_requirement(self):
        p=fixture()["pins"]
        for kw in (
            {"c_run_id":"NOT_UUID"},{"calculated_metric_id":True},
            {"current_snapshot_id":p.previous_snapshot_id},
            {"niche_cohort_id":302},
            {"creator_freeze_sha256":"F"*64},
            {"min_creator_peers":0},
            {"config_version":""},
        ):
            with self.subTest(kw=kw),self.assertRaises(AnalyticsBoundaryError):
                replace(p,**kw)
        r,db,q,_=self.setup()
        with self.assertRaises(AnalyticsBoundaryError):
            GroupCPersistedScoreReader(object(),p,q,as_of=NOW)
        with self.assertRaises(AnalyticsBoundaryError):
            GroupCPersistedScoreReader(db,p,q,as_of=datetime(2026,10,26))

    def test_database_errors_are_sanitized(self):
        r,db,_,_=self.setup()
        db.fail=SCORE_SQL
        with self.assertRaises(AnalyticsBoundaryError) as e:
            r.inspect_persisted_score(SCOPE)
        self.assertEqual(e.exception.code,"c_dependency_unavailable")
        self.assertNotIn("password",str(e.exception))
        self.assertIs(r.read_persisted_metric(SCOPE).state,ReadState.UNAVAILABLE)


@unittest.skipUnless(os.environ.get("CM_TEST_PG_DSN"),"Disposable PG17 only")
class GroupCBaselinePG17(unittest.TestCase):
    def test_unmerged_peer_056_057_remains_unavailable(self):
        import psycopg
        from psycopg.rows import dict_row
        from intelligence import migrate
        if os.environ["CM_TEST_PG_DSN"]==os.environ.get("CM_DB_DSN"):
            self.fail("Refusing production DB")
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True) as conn:
            v=int(conn.execute("SHOW server_version_num").fetchone()[0])
            self.assertTrue(170000<=v<180000)
            migrate.apply_migrations(conn)
            conn.row_factory=dict_row
            data=fixture()
            q=QDPStub(conn,data["pins"])
            r=GroupCPersistedScoreReader(conn,data["pins"],q,as_of=NOW)
            self.assertIs(r.capability().state,CapabilityState.UNAVAILABLE)
            self.assertEqual(r.capability().reason_code,"c_peer_schema_missing")
            self.assertIs(r.read_persisted_metric(SCOPE).state,ReadState.UNAVAILABLE)


if __name__=="__main__":
    unittest.main()
