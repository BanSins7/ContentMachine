"""UA-MOD-13: every unavailable/missing peer, status and spoofing case.

StubAssembler models JoinedSelection only to test pure decision mapping.
It is NEVER a real source-backed completed UAE read, approval or production
qualified response. Existing UA03 tests retain conditional offline qualified.
"""
from __future__ import annotations
import os
import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from types import SimpleNamespace

from intelligence.unified_analytics import (
    AnalyticsBoundaryError, InputScope, EvidenceRef,
)
from intelligence.unified_analytics.contracts import canonical_hash
from intelligence.unified_analytics.joined_context import (
    JoinedContextAssembler, JoinedSelection, JoinedInputContext,
    CandidateExclusion, REQUIRED_PORTS,
)
from intelligence.unified_analytics.qualification_orchestrator import (
    DECISION_SCHEMA, QualificationOrchestrator,
    QualificationStatus, BlockerSeverity, QualificationDecision,
    QualificationBlocker, PortQualification,
)
from intelligence.unified_analytics.qualification import (
    QualificationObservation, QualificationInput,
    ObservationState, canonical_gate_hash, evaluate_qualification,
)

AS_OF=datetime(2026,10,27,11,tzinfo=timezone.utc)
SCOPE=InputScope(
    "a"*32,17,"licensed_source",9,"video",
    "00112233-4455-4677-8899-aabbccddeeff","b"*64,
)
FP_A="1"*64
FP_B="2"*64
HASH="e"*64


def context(scope=SCOPE,*,fp=FP_A,as_of=AS_OF):
    refs=tuple(EvidenceRef(
        scope,port,port+":version:1","uae-mock-reference/v1",
        "future-mock-v1","c"*64,
    ) for port in REQUIRED_PORTS)
    return JoinedInputContext(
        scope=scope,as_of=as_of,fingerprint=fp,
        lineage_sha256="d"*64,source_rights_sha256=HASH,
        evidence=refs,binding_sha256="f"*64,
    )


def selected(*,fp=FP_A):
    return JoinedSelection(
        status="complete_claims_unapproved",selected=context(fp=fp),
        excluded=(),attempted=1,
    )


def rejected(*reasons):
    return JoinedSelection(
        status="blocked",selected=None,
        excluded=tuple(CandidateExclusion(FP_A if i%2==0 else FP_B,code)
                       for i,code in enumerate(reasons)),
        attempted=len(reasons),
    )


class StubAssembler(JoinedContextAssembler):
    """No SQL. This subclass is ONLY for testing status decisions."""
    def __init__(self, selection, *, scope=SCOPE, as_of=AS_OF,
                 hashes=(FP_A,),):
        self._scope=scope
        self._as_of=as_of
        self._candidates=tuple(SimpleNamespace(fingerprint=h) for h in hashes)
        self.selection=selection
        self.calls=0
    def select(self):
        self.calls+=1
        return self.selection


def decision(selection, **kw):
    asm=StubAssembler(selection,**kw)
    return QualificationOrchestrator(asm).evaluate()


class OrchestratorTests(unittest.TestCase):
    def test_unapproved_seven_located_claims_are_never_qualified(self):
        out=decision(selected())
        self.assertIs(out.status,QualificationStatus.PARTIAL)
        self.assertFalse(out.qualified)
        self.assertEqual([x.code for x in out.blockers],
                         ["qualification_effective_approval_unavailable"])
        self.assertEqual(out.blockers[0].stage,"group_c")
        self.assertIs(out.blockers[0].severity,BlockerSeverity.INCOMPLETE)
        self.assertTrue(all(x.state=="located_unapproved" for x in out.readiness))
        self.assertEqual(tuple(x.port for x in out.readiness),REQUIRED_PORTS)
        self.assertEqual(out.candidate_sha256,FP_A)
        self.assertEqual(out.schema_version,DECISION_SCHEMA)
        self.assertEqual(len(out.decision_sha256),64)
        self.assertEqual(out,out)
        self.assertEqual(out.as_dict()["qualified"],False)

    def test_missing_every_peer_port_is_a_typed_denial(self):
        table={
            "source_policy":"join_source_policy_not_current",
            "d1":"join_d1_not_current",
            "qdp":"join_qdp_not_current",
            "sre":"join_sre_not_current",
            "group_a":"join_group_a_not_current",
            "group_b":"join_group_b_not_current",
            "group_c":"join_group_c_not_current",
        }
        for stage,code in table.items():
            with self.subTest(stage=stage):
                d=decision(rejected(code))
                self.assertIsNone(d.candidate_sha256)
                self.assertEqual(d.blockers[0].code,code)
                self.assertEqual(d.blockers[0].stage,stage)
                self.assertEqual(d.as_dict()["readiness"][stage],(
                    "incomplete" if stage.startswith("group_") else "blocked"
                ))
                expected=(QualificationStatus.PARTIAL if stage.startswith("group_")
                          else QualificationStatus.BLOCKED)
                self.assertIs(d.status,expected)

    def test_hard_project_qdp_sre_source_and_lineage_mismatches(self):
        for code in (
            "join_scope_or_sre_lineage_mismatch",
            "join_metadata_version_mismatch",
            "join_metric_snapshot_pair_mismatch",
            "join_sre_qdp_version_mismatch",
            "join_abc_analysis_id_mismatch",
            "join_abc_preparation_sha_mismatch",
            "join_b_wrong_group_a_parent",
            "join_c_config_or_snapshot_changed",
            "join_b_taxonomy_version_mismatch",
            "join_group_a_media_count_mismatch",
            "join_ga_source_media_mismatch",
            "join_canonical_version_changed",
            "join_current_rights_changed",
        ):
            with self.subTest(code=code):
                x=decision(rejected(code))
                self.assertIs(x.status,QualificationStatus.BLOCKED)
                self.assertIs(x.blockers[0].severity,BlockerSeverity.HARD)

    def test_rights_revocation_and_expired_evidence_override_any_soft(self):
        for code in (
            "source_policy_missing",
            "source_permission_intersection_denied",
            "source_policy_revoked_or_superseded",
            "source_retention_expired",
            "source_project_link_revoked",
            "source_derived_retention_unapproved",
            "source_read_failed",
        ):
            d=decision(rejected("join_group_c_not_current",code),
                       hashes=(FP_A,FP_B))
            self.assertIs(d.status,QualificationStatus.BLOCKED)
            self.assertFalse(d.qualified)
            self.assertIn("group_c",d.as_dict()["readiness"])
            self.assertIs(d.blockers[1].severity,BlockerSeverity.HARD)

    def test_pending_group_a_b_c_are_partial_not_qualified(self):
        for reason in (
            "ga_schema_missing","gb_schema_missing","c_peer_schema_missing",
            "c_effective_approval_unavailable",
            "ga_result_missing_or_revoked",
            "gb_completed_run_missing",
            "c_persisted_score_missing",
        ):
            with self.subTest(reason=reason):
                d=decision(rejected(reason))
                self.assertIs(d.status,QualificationStatus.PARTIAL)
                self.assertEqual(d.blockers[0].severity,BlockerSeverity.INCOMPLETE)

    def test_unknown_errors_fail_closed_hard(self):
        for code in ("peer_authentication_failed","unexpected_db_exception",
                     "group_c_missing_new_wire","totally_unknown"):
            d=decision(rejected(code))
            self.assertIs(d.status,QualificationStatus.BLOCKED)
            self.assertIs(d.blockers[0].severity,BlockerSeverity.HARD)

    def test_duplicates_and_uninspected_are_informational_only(self):
        d=decision(rejected("join_duplicate_candidate",
                            "join_not_inspected_after_selection"),
                   hashes=(FP_A,FP_B))
        self.assertIs(d.status,QualificationStatus.BLOCKED)
        self.assertEqual([b.code for b in d.blockers],
                         ["qualification_no_eligible_candidate"])
        self.assertEqual(d.attempted,2)

    def test_previous_incompatible_bundle_does_not_veto_selected_unapproved(self):
        result=JoinedSelection(
            "complete_claims_unapproved",context(fp=FP_B),
            (CandidateExclusion(FP_A,"join_b_wrong_group_a_parent"),),
            2,
        )
        d=decision(result,hashes=(FP_A,FP_B))
        self.assertIs(d.status,QualificationStatus.PARTIAL)
        self.assertEqual(d.candidate_sha256,FP_B)
        self.assertEqual([b.code for b in d.blockers],
                         ["qualification_effective_approval_unavailable"])

    def test_selected_context_must_come_from_input_candidate_pool(self):
        fake=selected(fp=FP_B)
        with self.assertRaises(AnalyticsBoundaryError) as e:
            decision(fake,hashes=(FP_A,))
        self.assertEqual(e.exception.code,"qualification_joined_context_invalid")

    def test_wrong_joined_context_scope_rejected_without_payload(self):
        alt=replace(SCOPE,source_id="different")
        x=JoinedSelection("complete_claims_unapproved",
                          context(alt),(),1)
        with self.assertRaises(AnalyticsBoundaryError):
            decision(x)

    def test_unknown_partial_cannot_be_promoted_from_checked_flag(self):
        original_facts={"status":"complete","checked":True}
        ref=EvidenceRef(
            SCOPE,"group_c","mock:1","local-claim/v1","v1",
            canonical_gate_hash(original_facts),
        )
        forged=QualificationObservation(
            "group_c",ObservationState.COMPLETE,ref,original_facts,True,True,
        )
        self.assertTrue(forged.checked)
        for arg in (
            forged,{"group_c":forged},
            QualificationInput(SCOPE,AS_OF,{"group_c":forged}),
        ):
            with self.subTest(arg=type(arg).__name__):
                with self.assertRaises(AnalyticsBoundaryError) as e:
                    QualificationOrchestrator(arg)
                self.assertEqual(e.exception.code,
                                 "qualification_joined_assembler_required")

    def test_manual_qualified_decision_forgery_is_rejected(self):
        block=(QualificationBlocker(
            "qualification_effective_approval_unavailable",
            "group_c",BlockerSeverity.INCOMPLETE,FP_A,
        ),)
        readiness=tuple(PortQualification(p,"located_unapproved")
                        for p in REQUIRED_PORTS)
        with self.assertRaises(AnalyticsBoundaryError) as e:
            QualificationDecision(
                scope=SCOPE,as_of=AS_OF,status=QualificationStatus.QUALIFIED,
                blockers=block,readiness=readiness,attempted=1,
                candidate_sha256=FP_A,input_sha256="d"*64,
                decision_sha256="f"*64,
            )
        self.assertEqual(e.exception.code,
                         "qualification_effective_approval_unavailable")

    def test_partial_cannot_include_hard_blockers(self):
        hard=QualificationBlocker("source_retention_expired","source_policy",
                                  BlockerSeverity.HARD,FP_A)
        readiness=tuple(PortQualification(p,"unverified")
                        for p in REQUIRED_PORTS)
        with self.assertRaises(AnalyticsBoundaryError):
            QualificationDecision(
                scope=SCOPE,as_of=AS_OF,status=QualificationStatus.PARTIAL,
                blockers=(hard,),readiness=readiness,attempted=1,
                candidate_sha256=None,input_sha256="d"*64,
                decision_sha256="f"*64,
            )

    def test_determinism_identical_state_yields_identical_digest(self):
        a=decision(rejected("join_group_c_not_current"))
        b=decision(rejected("join_group_c_not_current"))
        self.assertEqual(a,b)
        self.assertEqual(a.decision_sha256,b.decision_sha256)
        self.assertEqual(a.input_sha256,b.input_sha256)

    def test_changed_candidate_version_changes_input_and_decision_hash(self):
        a=decision(rejected("join_group_c_not_current"),hashes=(FP_A,))
        b=decision(rejected("join_group_c_not_current"),hashes=(FP_B,))
        self.assertNotEqual(a.input_sha256,b.input_sha256)
        self.assertNotEqual(a.decision_sha256,b.decision_sha256)

    def test_changed_as_of_changes_hash_and_does_not_allow_naive(self):
        first=decision(rejected("join_group_c_not_current"))
        future=AS_OF+timedelta(hours=2)
        second=decision(rejected("join_group_c_not_current"),as_of=future)
        self.assertNotEqual(first.decision_sha256,second.decision_sha256)
        with self.assertRaises(AnalyticsBoundaryError):
            context(as_of=datetime(2026,10,27))

    def test_missing_readiness_stays_unverified_not_fabricated_zero(self):
        d=decision(rejected("join_group_b_not_current"))
        self.assertEqual(d.as_dict()["readiness"]["group_b"],"incomplete")
        for port in REQUIRED_PORTS:
            if port!="group_b":
                self.assertEqual(d.as_dict()["readiness"][port],"unverified")

    def test_invalid_typed_blocker_and_readiness_are_rejected(self):
        for code in ("UPPER_CASE","a","invalid status","e"*81):
            with self.subTest(code=code),self.assertRaises(AnalyticsBoundaryError):
                QualificationBlocker(code,"group_c",BlockerSeverity.HARD)
        with self.assertRaises(AnalyticsBoundaryError):
            PortQualification("group_c","qualified")
        with self.assertRaises(AnalyticsBoundaryError):
            PortQualification("imaginary","unverified")

    def test_no_eligible_candidate_no_fake_qualified(self):
        d=decision(rejected())
        self.assertIs(d.status,QualificationStatus.BLOCKED)
        self.assertEqual(d.attempted,0)
        self.assertEqual(d.blockers[0].code,"qualification_no_eligible_candidate")

    def test_invalid_selector_result_rejected(self):
        asm=StubAssembler("qualified")
        with self.assertRaises(AnalyticsBoundaryError) as e:
            QualificationOrchestrator(asm).evaluate()
        self.assertEqual(e.exception.code,
                         "qualification_invalid_joined_selection")


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable PostgreSQL 17 needed")
class OrchestratorStandalonePG17(unittest.TestCase):
    def test_peer_absence_is_hard_blocked_without_promoting_qualified(self):
        import psycopg
        from psycopg.rows import dict_row
        from intelligence import migrate
        from intelligence.unified_analytics.joined_context import JoinedContextAssembler
        if os.environ["CM_TEST_PG_DSN"]==os.environ.get("CM_DB_DSN"):
            self.fail("Refuse production DB")
        # Structural test cannot invent authoritative candidate identity.
        # Only verifies a source row cannot be guessed into existence, whereas
        # the actual staged PG test of JoinedContextAssembler is UA-MOD-12.
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True) as db:
            version=int(db.execute("SHOW server_version_num").fetchone()[0])
            self.assertTrue(170000<=version<180000)
            migrate.apply_migrations(db)
            db.row_factory=dict_row
            count=db.execute(
                "SELECT count(*) FROM uae_snapshot_current_rights_candidates"
            ).fetchone()["count"]
            self.assertEqual(count,0)
            d=decision(rejected("source_policy_missing"))
            self.assertEqual(d.status,QualificationStatus.BLOCKED)
            self.assertFalse(d.qualified)


if __name__=="__main__":
    unittest.main()
