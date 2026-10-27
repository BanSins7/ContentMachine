"""UA-MOD-12 exact pinned join / deterministic rejection regression tests.

Positive fixtures simulate FUTURE reviewed Group C and completed SRE/QDP.
The real Group C port currently *always* returns MISSING for unapproved
freeze, so no mock-positive is a production qualification claim.
"""
from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from intelligence.unified_analytics import (
    AnalyticsBoundaryError, InputScope, CanonicalLineagePins, CanonicalLineage,
    SREApprovedRelevancePins, QDPQualifiedPins, GroupAVerifiedPins,
    GroupBExactPins, GroupCPersistedPins, SourceRightsPins,
    EvidenceRef, EvidenceRead, ReadState,
)
from intelligence.unified_analytics.contracts import canonical_hash
from intelligence.unified_analytics.ports import PORT_SCHEMAS, UAEPorts, PortCapability, CapabilityState
from intelligence.unified_analytics.source_policy_adapter import (
    SourcePolicyCurrentReader, CurrentSourceRights,
)
from intelligence.unified_analytics.joined_context import (
    JoinedCandidatePins, JoinedContextAssembler, JOIN_SCHEMA, MAX_CANDIDATES,
    GA_MEDIA_IDS_SQL,
)

NOW=datetime(2026,10,27,9,tzinfo=timezone.utc)
PROJECT="a"*32
SCOPE=InputScope(
    PROJECT,17,"licensed_source",9,"video",
    "00112233-4455-4677-8899-aabbccddeeff","a"*64,
)
SRE_RUN="11112222-3333-4444-8555-666677778888"
A_RUN="11223344-5566-4777-8888-99aabbccddee"
B_RUN="22223333-4444-4555-8666-777788889999"
C_RUN="33334444-5555-4666-8777-88889999aaaa"
EVENT="44445555-6666-4777-8888-9999aaaabbbb"
CATEGORY="55556666-7777-4888-8999-aaaabbbbcccc"


def bundle() -> JoinedCandidatePins:
    lineage=CanonicalLineagePins(
        scope=SCOPE,metadata_id=70,metric_snapshot_id=202,
        sre_run_id=SRE_RUN,sre_taxonomy_version_id=31,
        sre_content_version_id=41,group_a_analysis_id=1001,
        group_b_analysis_id=1002,calculated_metric_id=1003,
        b_taxonomy_version="taxonomy-v3",
        sre_decision_sha256="b"*64,
    )
    sre=SREApprovedRelevancePins(
        lineage=lineage,taxonomy_sha256="c"*64,
        content_sha256="d"*64,contexts_sha256="e"*64,
        target_category_keys=(CATEGORY,),
    )
    qdp=QDPQualifiedPins(
        scope=SCOPE,sre_run_id=SRE_RUN,sre_taxonomy_version_id=31,
        taxonomy_sha256="c"*64,qdp_manifest_sha256="f"*64,
        qdp_selected_sha256="1"*64,sre_content_version_id=41,
        sre_content_sha256="d"*64,metadata_id=70,
        preparation_shas=("2"*64,"3"*64,"4"*64),
        approved_category_keys=(CATEGORY,),sre_result_ids=(13,),
        group_c_snapshot_ids=(201,202),group_c_config_id=12,
    )
    a=GroupAVerifiedPins(
        scope=SCOPE,ga_run_id=A_RUN,ga_result_id=55,
        analysis_id=1001,qdp_a_preparation_sha256="2"*64,
        media_manifest_sha256="5"*64,result_sha256="6"*64,
        analysis_version="A3",method_version="A-method-v1",
        expected_media_items=1,
    )
    b=GroupBExactPins(
        scope=SCOPE,b_run_id=B_RUN,b_analysis_id=1002,
        qdp_b_preparation_sha256="3"*64,
        group_a_analysis_id=1001,group_a_result_sha256="6"*64,
        group_a_media_manifest_sha256="5"*64,
        config_sha256="7"*64,
        analysis_version="B2",taxonomy_version="taxonomy-v3",
        method_version="B-method-v1",
        event_id=EVENT,event_sha256="8"*64,
        block_evidence_sha256s=tuple(str(i)*64 for i in range(1,9)),
    )
    c=GroupCPersistedPins(
        scope=SCOPE,c_run_id=C_RUN,calculated_metric_id=1003,
        current_snapshot_id=202,previous_snapshot_id=201,
        metadata_id=70,config_id=12,config_version="gc-decimal/v1",
        creator_cohort_id=1,prior_cohort_id=3,niche_cohort_id=None,
        creator_freeze_sha256="9"*64,prior_freeze_sha256="a"*64,
        niche_freeze_sha256=None,min_creator_peers=5,min_prior_peers=7,
        min_niche_peers=None,source_metric_version="1.0",
        qdp_c_preparation_sha256="4"*64,
    )
    return JoinedCandidatePins(
        lineage,SourceRightsPins(SCOPE,70,(201,202),(101,)),
        sre,qdp,a,b,c,
    )


class Cursor:
    def __init__(self,rows): self.rows=rows
    def fetchall(self): return self.rows


class FakeDB:
    def __init__(self):
        self.media=(101,)
        self.calls=0
    def execute(self,sql,args=()):
        if sql!=GA_MEDIA_IDS_SQL:
            raise AssertionError("Only authoritative GA media IDs read in mock")
        self.calls+=1
        return Cursor([{"slide_index":i,"media_asset_id":asset}
                       for i,asset in enumerate(self.media,1)])


class CurrentRightsMock(SourcePolicyCurrentReader):
    def __init__(self,conn,pins,*,as_of,flags):
        super().__init__(conn,pins,as_of=as_of)
        self.flags=flags
    def capability(self):
        return PortCapability("source_policy",CapabilityState.DECLARED,
                              PORT_SCHEMAS["source_policy"],"uae-source-rights/v1")
    def check_current_rights(self,scope):
        if self.flags.get("revoked"):
            raise AnalyticsBoundaryError(
                "source_permission_intersection_denied","Rights revoked",
            )
        if self.flags.get("expiry"):
            raise AnalyticsBoundaryError("source_retention_expired","expired")
        serial=self.flags.get("rights_revision",0)
        sha=canonical_hash({
            "scope":scope.scope_sha256,
            "revision":serial,
            "metadata_id":self._pins.metadata_id,
            "as_of":self._as_of.isoformat(),
        })
        return CurrentSourceRights(
            scope,self._pins.metadata_id,self._pins.metric_snapshot_ids,
            self._pins.media_asset_ids,self._as_of,
            self._as_of+timedelta(days=1),7,sha,
        )


class PeerMock:
    """Protocol-compliant, fake port for conditional future-positive tests."""
    def __init__(self,port,flags):
        self.port,self.flags=port,flags
    def capability(self):
        return PortCapability(self.port,CapabilityState.DECLARED,
                              PORT_SCHEMAS[self.port],
                              {"sre":"sre.classifications_ready/v1",
                               "qdp":"qdp-handoff/v1",
                               "group_a":"group-a-to-b/v1"}.get(self.port))
    def _read(self,scope):
        counts=self.flags.setdefault("calls",{})
        counts[self.port]=counts.get(self.port,0)+1
        if self.flags.get("revoked_during")==self.port:
            self.flags["revoked"]=True
        if self.flags.get("right_change_during")==self.port:
            self.flags["rights_revision"]=self.flags.get("rights_revision",0)+1
        if self.flags.get("missing")==self.port:
            return EvidenceRead(self.port,scope,ReadState.MISSING,
                                reason_code="future_peer_unapproved")
        if self.flags.get("unavailable")==self.port:
            return EvidenceRead(self.port,scope,ReadState.UNAVAILABLE,
                                reason_code="peer_schema_missing")
        return EvidenceRead(self.port,scope,ReadState.LOCATED,reference=EvidenceRef(
            scope,self.port,self.port+":mock",
            {"sre":"sre.classifications_ready/v1",
             "qdp":"qdp-handoff/v1",
             "group_a":"group-a-to-b/v1",
             "group_b":"uae-group-b-exact/v1",
             "group_c":"uae-group-c-persisted/v1"}[self.port],
            "mock-future-v1","c"*64,
        ))
    def read_current_relevance(self,scope): return self._read(scope)
    def read_qualified_membership(self,scope): return self._read(scope)
    def read_completed_analysis(self,scope): return self._read(scope)
    def read_persisted_metric(self,scope): return self._read(scope)


class MockAssembler(JoinedContextAssembler):
    def __init__(self,conn,scope,candidates,*,as_of=NOW,flags=None):
        super().__init__(conn,scope,candidates,as_of=as_of)
        self.flags={} if flags is None else flags
    def _canonical(self,p):
        if self.flags.get("canonical_stale"):
            version="wrong"
        else:
            version=p.group_b.analysis_version
        return CanonicalLineage(
            pins=p.lineage,as_of=self._as_of,
            a_analysis_version=p.group_a.analysis_version,
            b_analysis_version=version,b_taxonomy_version=p.group_b.taxonomy_version,
            sre_taxonomy_checksum=p.qdp.taxonomy_sha256,
            sre_content_revision=2,metric_source_definition="platform_post_views",
            metric_source_version=p.group_c.source_metric_version,
            metric_snapshot_id=p.group_c.current_snapshot_id,
            calculated_metric_id=p.group_c.calculated_metric_id,
            score_config_id=p.group_c.config_id,lineage_sha256="e"*64,
        )
    def _readers(self,p):
        rights=CurrentRightsMock(self._conn,p.rights,as_of=self._as_of,
                                 flags=self.flags)
        ports=UAEPorts(
            source_policy=rights,
            qdp=PeerMock("qdp",self.flags),sre=PeerMock("sre",self.flags),
            group_a=PeerMock("group_a",self.flags),
            group_b=PeerMock("group_b",self.flags),
            group_c=PeerMock("group_c",self.flags),
        )
        return rights,ports


class JoinedTests(unittest.TestCase):
    def make(self,candidates=None,flags=None,db=None):
        conn=FakeDB() if db is None else db
        c=(bundle(),) if candidates is None else candidates
        return MockAssembler(conn,SCOPE,c,flags=flags),conn

    def rejected(self,mutator,reason):
        c=bundle()
        c=mutator(c)
        selector,_=self.make((c,))
        selected=selector.select()
        self.assertEqual(selected.status,"blocked")
        self.assertIsNone(selected.selected)
        self.assertEqual(selected.excluded[0].reason_code,reason)
        self.assertFalse(selected.qualified)

    def test_future_positive_assembled_claims_never_claim_qualified(self):
        selector,db=self.make()
        got=selector.select()
        self.assertEqual(got.status,"complete_claims_unapproved")
        self.assertIsNotNone(got.selected)
        self.assertEqual(got.attempted,1)
        self.assertEqual(tuple(x.port for x in got.selected.evidence),
            ("source_policy","qdp","sre","group_a","group_b","group_c"))
        self.assertEqual(len(got.selected.binding_sha256),64)
        self.assertEqual(got,selector.select())
        self.assertEqual(db.calls,2)
        self.assertFalse(got.selected.qualified)
        self.assertFalse(got.qualified)

    def test_structural_cross_scope_sre_and_qdp_mismatch(self):
        self.rejected(
            lambda c:replace(c,rights=replace(
                c.rights,scope=replace(SCOPE,source_id="other"))),
            "join_scope_or_sre_lineage_mismatch")
        self.rejected(lambda c:replace(
            c,qdp=replace(c.qdp,sre_run_id=C_RUN)),
            "join_sre_qdp_version_mismatch")
        self.rejected(lambda c:replace(
            c,qdp=replace(c.qdp,sre_content_sha256="f"*64)),
            "join_sre_qdp_version_mismatch")

    def test_exact_project_metadata_selection_across_every_stage(self):
        for key in ("qdp","group_c","rights"):
            def mutate(c):
                if key=="qdp": return replace(c,qdp=replace(c.qdp,metadata_id=71))
                if key=="group_c": return replace(c,group_c=replace(c.group_c,metadata_id=71))
                return replace(c,rights=replace(c.rights,metadata_id=71))
            with self.subTest(key=key):
                self.rejected(mutate,"join_metadata_version_mismatch")

    def test_qdp_three_independent_preparation_hashes_mandatory(self):
        for key in ("group_a","group_b","group_c"):
            def mutate(c):
                old=getattr(c,key)
                field={"group_a":"qdp_a_preparation_sha256",
                       "group_b":"qdp_b_preparation_sha256",
                       "group_c":"qdp_c_preparation_sha256"}[key]
                return replace(c,**{key:replace(old,**{field:"f"*64})})
            with self.subTest(key=key):
                self.rejected(mutate,"join_abc_preparation_sha_mismatch")

    def test_group_B_cannot_inherit_unrelated_group_A_result(self):
        self.rejected(lambda c:replace(c,group_b=replace(
            c.group_b,group_a_result_sha256="f"*64)),
            "join_b_wrong_group_a_parent")
        self.rejected(lambda c:replace(c,group_b=replace(
            c.group_b,group_a_analysis_id=99)),
            "join_b_wrong_group_a_parent")

    def test_group_C_snapshot_pair_and_config_are_exact_QDP_approved_inputs(self):
        self.rejected(lambda c:replace(c,group_c=replace(
            c.group_c,config_id=99)),
            "join_c_config_or_snapshot_changed")
        self.rejected(lambda c:replace(c,qdp=replace(
            c.qdp,group_c_snapshot_ids=(200,202))),
            "join_c_config_or_snapshot_changed")
        self.rejected(lambda c:replace(c,rights=replace(
            c.rights,metric_snapshot_ids=(200,202))),
            "join_metric_snapshot_pair_mismatch")

    def test_wrong_C_metric_or_B_analysis_not_shadow_selection(self):
        self.rejected(lambda c:replace(c,group_c=replace(
            c.group_c,calculated_metric_id=103)),
            "join_abc_analysis_id_mismatch")
        self.rejected(lambda c:replace(c,group_b=replace(
            c.group_b,b_analysis_id=999)),
            "join_abc_analysis_id_mismatch")
        self.rejected(lambda c:replace(c,group_b=replace(
            c.group_b,taxonomy_version="wrong")),
            "join_b_taxonomy_version_mismatch")

    def test_project_video_source_rights_and_GA_media_facts_are_bound(self):
        db=FakeDB()
        db.media=(999,)
        selector,_=self.make(db=db)
        got=selector.select()
        self.assertIsNone(got.selected)
        self.assertEqual(got.excluded[0].reason_code,"join_ga_source_media_mismatch")
        db.media=(101,102)
        got=selector.select()
        self.assertEqual(got.excluded[0].reason_code,"join_ga_source_media_mismatch")
        db.media=(101,)
        self.assertIsNotNone(selector.select().selected)

    def test_current_expiry_or_revocation_cannot_return_partial_payload(self):
        for setting,reason in (
            ({"revoked":True},"source_permission_intersection_denied"),
            ({"expiry":True},"source_retention_expired"),
            ({"revoked_during":"group_b"},"join_group_b_not_current"),
            ({"right_change_during":"group_c"},"join_group_c_not_current"),
        ):
            with self.subTest(setting=setting):
                selector,_=self.make(flags=setting)
                got=selector.select()
                self.assertIsNone(got.selected)
                self.assertEqual(got.excluded[0].reason_code,reason)

    def test_missing_or_unavailable_C_never_becomes_qualified(self):
        for key in ("missing","unavailable"):
            selector,_=self.make(flags={key:"group_c"})
            got=selector.select()
            self.assertIsNone(got.selected)
            self.assertEqual(got.excluded[0].reason_code,"join_group_c_not_current")
            self.assertFalse(got.qualified)

    def test_canonical_D1_version_mismatch_not_covered_by_good_mock_peers(self):
        selector,_=self.make(flags={"canonical_stale":True})
        got=selector.select()
        self.assertIsNone(got.selected)
        self.assertEqual(got.excluded[0].reason_code,"join_canonical_version_changed")

    def test_duplicate_candidate_exclusion_deterministic(self):
        p=bundle()
        selector,_=self.make((p,p))
        first=selector.select()
        self.assertIsNotNone(first.selected)
        self.assertEqual(first.attempted,1)
        # duplicate behind first complete choice is not inspected; when the
        # first is blocked it is classified deterministically.
        selector,_=self.make((p,p),flags={"missing":"group_c"})
        result=selector.select()
        self.assertIsNone(result.selected)
        self.assertEqual([x.reason_code for x in result.excluded],
                         ["join_group_c_not_current","join_duplicate_candidate"])

    def test_deterministic_order_and_fallback_only_for_current_compatible(self):
        old=bundle()
        newc=replace(old,
            lineage=replace(old.lineage,metric_snapshot_id=203),
            rights=replace(old.rights,metric_snapshot_ids=(201,203)),
            qdp=replace(old.qdp,group_c_snapshot_ids=(201,203)),
            group_c=replace(old.group_c,current_snapshot_id=203),
        )
        selector,_=self.make((old,newc))
        got=selector.select()
        self.assertEqual(got.selected.fingerprint,newc.fingerprint)
        selector,_=self.make((newc,old))
        self.assertEqual(selector.select().selected.fingerprint,newc.fingerprint)
        bad=replace(newc,group_b=replace(
            newc.group_b,group_a_result_sha256="f"*64))
        selector,_=self.make((old,bad))
        got=selector.select()
        self.assertEqual(got.selected.fingerprint,old.fingerprint)
        self.assertEqual(got.excluded[0].reason_code,"join_b_wrong_group_a_parent")

    def test_missing_approval_across_all_candidates_never_selects_latest(self):
        base=bundle()
        newer=replace(base,
            lineage=replace(base.lineage,metric_snapshot_id=203),
            rights=replace(base.rights,metric_snapshot_ids=(201,203)),
            qdp=replace(base.qdp,group_c_snapshot_ids=(201,203)),
            group_c=replace(base.group_c,current_snapshot_id=203),
        )
        selector,_=self.make((base,newer),flags={"missing":"group_c"})
        got=selector.select()
        self.assertIsNone(got.selected)
        self.assertEqual(got.attempted,2)
        self.assertEqual(len(got.excluded),2)
        self.assertTrue(all(x.reason_code=="join_group_c_not_current"
                            for x in got.excluded))

    def test_bounded_candidates_and_scope_validation(self):
        with self.assertRaises(AnalyticsBoundaryError):
            MockAssembler(FakeDB(),SCOPE,(),as_of=NOW)
        with self.assertRaises(AnalyticsBoundaryError):
            MockAssembler(FakeDB(),SCOPE,(bundle(),)*(MAX_CANDIDATES+1),as_of=NOW)
        with self.assertRaises(AnalyticsBoundaryError):
            MockAssembler(FakeDB(),SCOPE,(bundle(),),as_of=datetime(2026,10,27))
        with self.assertRaises(AnalyticsBoundaryError):
            MockAssembler(FakeDB(),SCOPE,("not-pins",),as_of=NOW)


if __name__=="__main__":
    unittest.main()
