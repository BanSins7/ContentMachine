"""UAE integration regression for actual Group B25 published candidate shape.

The existing Group B branch exports UAECompatibility/HandoffDecision:
pure candidates have payload=None and uae_qualified=False. This test suite
never changes peer code, never imports Group B branch modules and never
turns such candidates into an EvidenceRef or a qualified snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError, canonical_hash
from intelligence.unified_analytics.group_b25_bridge import (
    B25_WIRE,B25_ADAPTER_SCHEMA,BRIDGE_WIRE,
    B25CandidateAudit,audit_b25_candidate,
)
from intelligence.unified_analytics.group_b_adapter import B_READER_WIRE
from test_unified_analytics_group_b_adapter import scenario


def capability(*,carousel=True,**changes):
    fields={
        "adapter_schema":B25_ADAPTER_SCHEMA,
        "reader_wire":B_READER_WIRE,
        "understands_b25_version":True,
        "understands_stale_invalidation":True,
        "understands_immutable_review":True,
        "understands_qdp_receipt":True,
        "understands_carousel_na":carousel,
    }
    fields.update(changes)
    return dict(fields,capability_sha256=canonical_hash(fields))


def candidate(pins,*,payload=None,**changes):
    obj={
        "state":"candidate","reasons":(),
        "project_id":pins.scope.project_id,
        "run_id":pins.b_run_id,
        "analysis_id":pins.b_analysis_id,
        "payload_sha256":canonical_hash(payload) if payload is not None else None,
        "payload":payload,"uae_qualified":False,
    }
    obj.update(changes)
    return obj


class State(str,Enum):
    BLOCKED="blocked"
    CANDIDATE="candidate"


@dataclass(frozen=True,slots=True)
class CompatiblePeerDataclass:
    adapter_schema:str
    reader_wire:str
    understands_b25_version:bool
    understands_stale_invalidation:bool
    understands_immutable_review:bool
    understands_qdp_receipt:bool
    understands_carousel_na:bool
    capability_sha256:str


@dataclass(frozen=True,slots=True)
class DecisionPeerDataclass:
    state:State
    reasons:tuple
    project_id:str
    run_id:str
    analysis_id:int|None
    payload_sha256:str|None
    payload:dict|None
    uae_qualified:bool=False


class B25CandidateBridgeTests(unittest.TestCase):
    def setUp(self):
        self.pins=scenario()["pins"]

    def test_b25_version_and_peer_reader_wire_exact(self):
        self.assertEqual(B25_WIRE,"group-b-uae-handoff/v1")
        self.assertEqual(B25_ADAPTER_SCHEMA,"uae-group-b-reader/v1")
        self.assertEqual(B_READER_WIRE,"uae-group-b-exact/v1")

    def test_real_pure_candidate_missing_payload_is_not_approval(self):
        obj=audit_b25_candidate(
            self.pins,capability(),candidate(self.pins))
        self.assertTrue(obj.compatible)
        self.assertFalse(obj.digest_checked)
        self.assertEqual(obj.state,"blocked")
        self.assertEqual(obj.reason_code,"b25_candidate_payload_not_emitted")
        self.assertFalse(obj.approved_for_uae_qualification)

    def test_digest_consistent_payload_is_still_unapproved(self):
        obj=audit_b25_candidate(
            self.pins,capability(),candidate(self.pins,payload={"blocks":8}))
        self.assertEqual(obj.state,"candidate_unapproved")
        self.assertTrue(obj.compatible)
        self.assertTrue(obj.digest_checked)
        self.assertFalse(obj.has_independent_producer_proof)
        self.assertFalse(obj.approved_for_uae_qualification)
        self.assertEqual(obj.reason_code,
                         "b25_independent_producer_authority_unverified")

    def test_wrong_capability_hash_rejected(self):
        obj=audit_b25_candidate(
            self.pins,dict(capability(),capability_sha256="f"*64),
            candidate(self.pins))
        self.assertEqual(obj.reason_code,"b25_contract_digest_mismatch")

    def test_unknown_peer_reader_wire_denied(self):
        obj=audit_b25_candidate(
            self.pins,capability(reader_wire="group-b-uae-handoff/v2"),
            candidate(self.pins))
        self.assertEqual(obj.reason_code,"b25_contract_not_negotiated")

    def test_forged_adapter_schema_denied(self):
        obj=audit_b25_candidate(
            self.pins,capability(adapter_schema="uae-other-reader/v1"),
            candidate(self.pins))
        self.assertEqual(obj.reason_code,"b25_contract_not_negotiated")

    def test_capability_false_required_flags_denied(self):
        for field in (
            "understands_b25_version","understands_stale_invalidation",
            "understands_immutable_review","understands_qdp_receipt"
        ):
            with self.subTest(field=field):
                obj=audit_b25_candidate(
                    self.pins,capability(**{field:False}),candidate(self.pins))
                self.assertEqual(obj.reason_code,"b25_required_capability_missing")

    def test_boollike_feature_not_bool_denied(self):
        obj=audit_b25_candidate(
            self.pins,capability(understands_qdp_receipt=1),
            candidate(self.pins))
        self.assertEqual(obj.reason_code,"b25_contract_not_negotiated")

    def test_malformed_capability_keys_denied(self):
        c=capability()
        del c["capability_sha256"]
        obj=audit_b25_candidate(self.pins,c,candidate(self.pins))
        self.assertEqual(obj.reason_code,"b25_contract_shape_invalid")

    def test_carousel_requires_explicit_na_understanding(self):
        pins=scenario("photo_carousel")["pins"]
        obj=audit_b25_candidate(
            pins,capability(carousel=False),candidate(pins))
        self.assertEqual(obj.reason_code,"b25_carousel_na_not_supported")

    def test_video_allows_na_feature_false_without_approval(self):
        obj=audit_b25_candidate(
            self.pins,capability(carousel=False),candidate(self.pins))
        self.assertTrue(obj.compatible)
        self.assertFalse(obj.approved_for_uae_qualification)

    def test_candidate_mismatched_project_blocked(self):
        obj=audit_b25_candidate(
            self.pins,capability(),candidate(self.pins,project_id="a"*32
                                             if self.pins.scope.project_id!="a"*32
                                             else "b"*32))
        self.assertEqual(obj.reason_code,"b25_lineage_scope_mismatch")

    def test_candidate_mismatched_run_blocked(self):
        obj=audit_b25_candidate(
            self.pins,capability(),
            candidate(self.pins,run_id="00000000-0000-4000-8000-000000000001"))
        self.assertEqual(obj.reason_code,"b25_lineage_scope_mismatch")

    def test_candidate_mismatched_analysis_id_blocked(self):
        obj=audit_b25_candidate(
            self.pins,capability(),candidate(self.pins,analysis_id=9876))
        self.assertEqual(obj.reason_code,"b25_lineage_scope_mismatch")

    def test_candidate_analysis_id_bool_blocked(self):
        obj=audit_b25_candidate(
            self.pins,capability(),candidate(self.pins,analysis_id=True))
        self.assertEqual(obj.reason_code,"b25_lineage_scope_mismatch")

    def test_candidate_claims_uae_qualified_forbidden(self):
        obj=audit_b25_candidate(
            self.pins,capability(),candidate(self.pins,uae_qualified=True))
        self.assertEqual(obj.reason_code,"b25_qualification_claim_forbidden")

    def test_candidate_nonempty_reasons_forbidden(self):
        obj=audit_b25_candidate(
            self.pins,capability(),
            candidate(self.pins,reasons=("source_revoked",)))
        self.assertEqual(obj.reason_code,"b25_qualification_claim_forbidden")

    def test_candidate_blocked_status_not_ready(self):
        obj=audit_b25_candidate(
            self.pins,capability(),candidate(self.pins,state="blocked"))
        self.assertEqual(obj.reason_code,"b25_candidate_not_ready")

    def test_candidate_missing_hash_and_present_payload_invalid(self):
        obj=audit_b25_candidate(
            self.pins,capability(),
            candidate(self.pins,payload={"blocks":8},payload_sha256=None))
        self.assertEqual(obj.reason_code,"b25_candidate_payload_invalid")

    def test_candidate_payload_hash_mismatch(self):
        obj=audit_b25_candidate(
            self.pins,capability(),
            candidate(self.pins,payload={"blocks":8},payload_sha256="f"*64))
        self.assertEqual(obj.reason_code,"b25_candidate_payload_hash_mismatch")

    def test_candidate_payload_not_canonical_json(self):
        malformed=candidate(self.pins)
        malformed.update(payload={"bad":object()},payload_sha256="f"*64)
        obj=audit_b25_candidate(self.pins,capability(),malformed)
        self.assertEqual(obj.reason_code,"b25_candidate_payload_invalid")

    def test_peer_dataclass_shape_without_peer_import(self):
        c=CompatiblePeerDataclass(**capability())
        d=DecisionPeerDataclass(
            **dict(candidate(self.pins),state=State.CANDIDATE))
        obj=audit_b25_candidate(self.pins,c,d)
        self.assertEqual(obj.reason_code,"b25_candidate_payload_not_emitted")
        self.assertFalse(obj.approved_for_uae_qualification)

    def test_extra_hidden_contract_fields_refused(self):
        obj=audit_b25_candidate(
            self.pins,dict(capability(),admin=True),candidate(self.pins))
        self.assertEqual(obj.reason_code,"b25_contract_shape_invalid")

    def test_wrong_pins_object_raises(self):
        with self.assertRaises(AnalyticsBoundaryError):
            audit_b25_candidate(object(),capability(),{})

    def test_contract_audit_cannot_be_manually_approved(self):
        with self.assertRaises(AnalyticsBoundaryError):
            B25CandidateAudit(BRIDGE_WIRE,"candidate_unapproved","fake",
                              True,True,False,True)

    def test_standalone_has_no_database_or_peer_coupling(self):
        from intelligence.unified_analytics import group_b25_bridge as mod
        self.assertFalse(hasattr(mod,"connect"))
        self.assertFalse(hasattr(mod,"QualifiedAggregationReader"))


if __name__=="__main__":
    unittest.main()
