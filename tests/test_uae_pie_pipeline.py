"""Synthetic UAE producer -> PIE signed receiver -> provenance composition.

Uses the existing UAE fake DB and both real peer contracts. NO production
qualification, live source authorization, publishing, or external APIs.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import unittest

from intelligence.uae_pie_pipeline import UAEPIETransport, UAEPIEPipeline
from intelligence.patterns_insights.contracts import PIEBoundaryError
from intelligence.patterns_insights.intake.uae import (
    HMACReceiptVerifier, UAERightsDecision,
)
from intelligence.patterns_insights.intake.provenance import (
    ScopeEligibility, RowEligibility,
)
from test_unified_analytics_descriptive_aggregation import NOW
from test_unified_analytics_pie_research_handoff import TemporaryApproval, SECRET


AT = NOW + timedelta(seconds=1)


class CurrentRights:
    def __init__(self):
        self.granted = True
        self.calls = 0

    def check_dataset(self, pointer, dataset, challenge):
        self.calls += 1
        return UAERightsDecision(
            pointer.project_id, pointer.source_id,
            pointer.source_policy_version_id, dataset.manifest_sha256,
            challenge, self.granted, AT.isoformat(),
        )


class IndependentProvenance:
    def __init__(self):
        self.current = True
        self.scopes = 0
        self.rows = 0

    def check_scope(self, pointer):
        self.scopes += 1
        return ScopeEligibility(
            pointer.project_id, pointer.source_id,
            pointer.source_policy_version_id,
            pointer.uae_manifest_sha256, pointer.uae_dataset_sha256,
            AT.isoformat(), (NOW + timedelta(days=1)).isoformat(),
            True, True, True, True, True, True, True,
        )

    def check_row(self, subject):
        self.rows += 1
        return RowEligibility(
            subject.project_id, subject.source_id,
            subject.source_policy_version_id,
            subject.manifest_sha256, subject.video_id,
            subject.uae_snapshot_id, subject.uae_snapshot_sha256,
            subject.row_sha256,
            AT.isoformat(), (NOW + timedelta(days=1)).isoformat(),
            True, True, True, True, True, True,
            self.current, True, True,
        )


class ProducerAdapter:
    """Calls real UAE exporter inside its existing patched fake PG model."""
    def __init__(self, fixture, run):
        self.fixture = fixture
        self.run = run

    def export(self, run_id, pins, challenge):
        if run_id != self.run.run_id:
            return None
        return self.fixture.export(run=self.run, pins=pins, challenge=challenge)


class UAEPIEIntegrationContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = TemporaryApproval()
        self.fixture.setUp()
        self.run = self.fixture.create()
        self.rights = CurrentRights()
        self.provenance = IndependentProvenance()
        self.reader = ProducerAdapter(self.fixture, self.run)
        self.transport = UAEPIETransport(
            self.reader, lambda _pointer: (self.run.run_id, self.fixture.pins),
            issuer=self.fixture.signer.issuer,
        )
        self.verifier = HMACReceiptVerifier({"unit-test.key.v1": SECRET})
        self.pipeline = UAEPIEPipeline(
            self.transport, verifier=self.verifier, rights=self.rights,
            provenance=self.provenance, now=lambda: AT,
        )

    def test_real_peer_contracts_repeatable_full_signed_intake(self):
        pointer = self.pipeline.prepare_pointer()
        self.assertEqual(pointer.uae_engine_version,
                         self.transport.describe().producer_version)
        first = self.pipeline.admit(pointer)
        second = self.pipeline.admit(pointer)
        self.assertEqual(first, second)
        self.assertEqual(first.sample_size, 5)
        self.assertEqual(first.manifest_sha256, pointer.uae_manifest_sha256)
        self.assertTrue(first.candidate_eligible)
        self.assertIs(self.pipeline.ports.require("uae"), self.pipeline.receiver)
        self.assertEqual(first.blockers, ())
        # No synthetic rights evidence can turn the hard safety gates on.
        self.assertFalse(first.verified_live)
        self.assertFalse(first.ready_for_analysis)
        self.assertEqual(self.rights.calls, 2)
        self.assertEqual(self.provenance.rows, 10)
        self.assertEqual(self.provenance.scopes, 4)

    def test_revoked_rights_fail_before_row_provenance(self):
        pointer = self.pipeline.prepare_pointer()
        self.rights.granted = False
        with self.assertRaises(PIEBoundaryError) as error:
            self.pipeline.admit(pointer)
        self.assertEqual(error.exception.code, "rights_denied")
        self.assertEqual(self.provenance.rows, 0)

    def test_revoked_upstream_lineage_suppresses_whole_dataset(self):
        pointer = self.pipeline.prepare_pointer()
        self.provenance.current = False
        decision = self.pipeline.admit(pointer)
        self.assertFalse(decision.candidate_eligible)
        self.assertEqual(decision.sample_size, 5)
        self.assertIn("row_c_score_persisted_complete", decision.blockers)
        self.assertFalse(decision.ready_for_analysis)

    def test_wrong_independent_receipt_key_rejected(self):
        pointer = self.pipeline.prepare_pointer()
        wrong = UAEPIEPipeline(
            self.transport,
            verifier=HMACReceiptVerifier({
                "unit-test.key.v1": b"wrong-unit-only-secret-not-production-001"
            }),
            rights=self.rights, provenance=self.provenance,
            now=lambda: AT,
        )
        with self.assertRaises(PIEBoundaryError) as error:
            wrong.admit(pointer)
        self.assertEqual(error.exception.code, "receipt_untrusted")
        self.assertEqual(self.rights.calls, 0)

    def test_changed_row_fails_pinned_identity_without_silent_reselection(self):
        pointer = self.pipeline.prepare_pointer()
        first = self.fixture.pins[0]
        self.fixture.db.a_rows[first.candidate.group_a.analysis_id]["scene_count"] = 100
        with self.assertRaises(PIEBoundaryError) as error:
            self.pipeline.admit(pointer)
        self.assertIn(error.exception.code, (
            "uae_read_failed", "uae_pinned_dataset_changed"
        ))
        self.assertEqual(self.rights.calls, 0)

    def test_missing_explicit_authority_fails_closed(self):
        with self.assertRaises(PIEBoundaryError) as error:
            UAEPIEPipeline(
                self.transport, verifier=self.verifier,
                rights=None, provenance=self.provenance,
            )
        self.assertEqual(error.exception.code, "uae_pie_authority_unconfigured")


if __name__ == "__main__":
    unittest.main()
