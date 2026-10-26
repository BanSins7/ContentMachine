"""PIE-MOD-06 independent current-rights/provenance gate: SYNTHETIC ONLY."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import unittest

from intelligence.patterns_insights import (
    PIEDatasetPointer, PIEBoundaryError, validate_dataset_manifest,
)
from intelligence.patterns_insights.intake.uae import UAEReadResult
from intelligence.patterns_insights.intake.provenance import (
    SourceEligibilityGate, ScopeEligibility, RowEligibility, EligibilityReport,
    RowProvenanceSubject,
)

DATA = json.loads((Path(__file__).parent / 'fixtures' / 'pie' /
                   'uae_to_pie_v1_positive.json').read_text('utf8'))
PTR = PIEDatasetPointer(**DATA['pointer'])
DATASET = validate_dataset_manifest(PTR, DATA['manifest'])
NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
RIGHTS_AT = (NOW - timedelta(minutes=1)).isoformat()
READ = UAEReadResult(DATASET, 'uae-test-fixture', 'f' * 64, RIGHTS_AT)


class Authority:
    def __init__(self):
        self.scope_calls = 0
        self.row_calls = 0
        self.scope_mutate = lambda value: value
        self.row_mutate = lambda value: value
        self.fail_scope = False
        self.fail_row = False
        self.scope_flags = {}
        self.row_flags = {}

    def check_scope(self, pointer):
        self.scope_calls += 1
        if self.fail_scope:
            raise RuntimeError('CREDENTIAL_SHOULD_NEVER_LEAK')
        result = ScopeEligibility(
            pointer.project_id, pointer.source_id,
            pointer.source_policy_version_id, pointer.uae_manifest_sha256,
            pointer.uae_dataset_sha256, NOW.isoformat(),
            (NOW + timedelta(hours=1)).isoformat(),
            True, True, True, True, True, True, True,
        )
        return self.scope_mutate(replace(result, **self.scope_flags))

    def check_row(self, subject):
        self.row_calls += 1
        if self.fail_row:
            raise RuntimeError('SECRET_PROVIDER_CONNECTION')
        result = RowEligibility(
            subject.project_id, subject.source_id,
            subject.source_policy_version_id, subject.manifest_sha256,
            subject.video_id, subject.uae_snapshot_id,
            subject.uae_snapshot_sha256, subject.row_sha256,
            NOW.isoformat(), (NOW + timedelta(hours=1)).isoformat(),
            True, True, True, True, True, True, True, True, True,
        )
        return self.row_mutate(replace(result, **self.row_flags))


class PIEProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.authority = Authority()
        self.gate = SourceEligibilityGate(self.authority, now=lambda: NOW)

    def rejected(self, code, func, *args):
        with self.assertRaises(PIEBoundaryError) as error:
            func(*args)
        self.assertEqual(error.exception.code, code)

    def test_positive_mock_candidate_is_not_live_certification(self):
        report = self.gate.evaluate(READ)
        self.assertEqual(report, EligibilityReport(DATASET.manifest_sha256, 2, 2, 0, ()))
        self.assertTrue(report.candidate_eligible)
        self.assertFalse(report.verified_live)
        self.assertFalse(report.ready_for_analysis)
        self.assertEqual(self.authority.scope_calls, 2)
        self.assertEqual(self.authority.row_calls, 2)
        with self.assertRaises(FrozenInstanceError):
            report.total_rows = 999

    def test_missing_authority_and_missing_method_are_unavailable(self):
        for source in (None, object(), type('Broken', (), {'check_scope': lambda self, p: None})()):
            self.rejected('capability_unavailable', SourceEligibilityGate(source).evaluate, READ)

    def test_does_not_consume_raw_pointer_or_untrusted_dataset(self):
        self.rejected('invalid_read', self.gate.evaluate, DATASET)
        self.rejected('invalid_read', self.gate.evaluate, DATA['manifest'])
        self.assertEqual(self.authority.scope_calls, 0)

    def test_missing_or_forged_receipt_field(self):
        self.rejected('invalid_read', self.gate.evaluate, replace(READ, receipt_sha256='not-sha'))
        self.rejected('invalid_read', self.gate.evaluate, replace(READ, issuer=''))
        self.assertEqual(self.authority.scope_calls, 0)

    def test_scope_mismatch_cross_project_source_policy_hash(self):
        for patch in ({'project_id': 'b'*32}, {'source_id': 'other-source'},
                      {'source_policy_version_id': 8}, {'manifest_sha256': 'a'*64},
                      {'dataset_sha256': 'b'*64}):
            self.authority.scope_mutate = lambda scope, patch=patch: replace(scope, **patch)
            with self.subTest(patch=patch):
                self.rejected('provenance_scope_mismatch', self.gate.evaluate, READ)
        self.assertEqual(self.authority.row_calls, 0)

    def test_scope_revocation_blocks_all_rows_and_avoids_evidence_reads(self):
        for flag in ('project_membership_current', 'source_enabled', 'policy_active_and_current',
                     'can_store_metadata', 'can_analyze_media', 'can_store_metrics', 'can_derive_scores'):
            self.authority.scope_flags = {flag: False}
            with self.subTest(flag=flag):
                report = self.gate.evaluate(READ)
                self.assertFalse(report.candidate_eligible)
                self.assertEqual(report.structurally_eligible_rows, 0)
                self.assertEqual(report.suppressed_rows, 2)
                self.assertIn('scope_'+flag, report.blockers)
                self.assertEqual(self.authority.row_calls, 0)
        self.authority.scope_flags = {}

    def test_retention_boundary_and_expired_rights_suppression(self):
        self.authority.scope_mutate = lambda s: replace(s, retention_until=NOW.isoformat())
        report = self.gate.evaluate(READ)
        self.assertEqual(report.blockers, ('scope_expired',))
        self.assertEqual(self.authority.row_calls, 0)

    def test_stale_future_scope_proof_refused(self):
        for instant in (NOW-timedelta(minutes=2), NOW+timedelta(seconds=1)):
            self.authority.scope_mutate = lambda s, instant=instant: replace(s, checked_at=instant.isoformat())
            self.rejected('provenance_stale', self.gate.evaluate, READ)
        self.assertEqual(self.authority.row_calls, 0)

    def test_rights_receipt_cannot_claim_future(self):
        bad = replace(READ, rights_checked_at=(NOW+timedelta(seconds=1)).isoformat())
        self.rejected('rights_stale', self.gate.evaluate, bad)
        self.assertEqual(self.authority.scope_calls, 0)

    def test_invalid_clock_and_naive_clock(self):
        for clock in (lambda: datetime(2026,10,26,12), lambda: 'not-clock'):
            self.rejected('invalid_clock', SourceEligibilityGate(self.authority, now=clock).evaluate, READ)
        self.assertEqual(self.authority.scope_calls, 0)

    def test_row_scope_snapshot_and_hash_mismatch_refused(self):
        for patch in ({'project_id': 'b'*32}, {'source_id': 'elsewhere'},
                      {'source_policy_version_id': 99}, {'manifest_sha256': 'b'*64},
                      {'video_id': 999}, {'uae_snapshot_id': 9999},
                      {'uae_snapshot_sha256': 'a'*64}, {'row_sha256': 'b'*64}):
            self.authority.row_mutate = lambda row, patch=patch: replace(row, **patch)
            with self.subTest(patch=patch):
                self.rejected('provenance_lineage_mismatch', self.gate.evaluate, READ)
        self.assertEqual(self.authority.row_calls, 8)

    def test_every_unverified_row_component_blocks_full_dataset(self):
        for flag in ('project_video_member', 'uae_qualified_current',
                     'qdp_qualified_current', 'sre_approved_live', 'group_a_complete',
                     'group_b_complete', 'c_score_persisted_complete',
                     'lineage_exact_current', 'source_metric_healthy'):
            self.authority.row_flags = {flag: False}
            with self.subTest(flag=flag):
                report = self.gate.evaluate(READ)
                self.assertEqual(report.suppressed_rows, 2)
                self.assertEqual(report.structurally_eligible_rows, 0)
                self.assertEqual(report.blockers, ('row_'+flag,))
        self.authority.row_flags = {}

    def test_single_invalid_row_cannot_silently_reduce_n(self):
        def mutate(row):
            return replace(row, qdp_qualified_current=False) if row.video_id == 102 else row
        self.authority.row_mutate = mutate
        report = self.gate.evaluate(READ)
        self.assertEqual(report.total_rows, 2)
        self.assertEqual(report.suppressed_rows, 2)
        self.assertFalse(report.candidate_eligible)

    def test_derived_expiry_at_boundary_suppresses(self):
        self.authority.row_mutate = lambda r: replace(r, derived_expires_at=NOW.isoformat())
        self.assertEqual(self.gate.evaluate(READ).blockers, ('row_expired',))

    def test_stale_or_future_row_check_suppresses(self):
        for instant in (NOW-timedelta(minutes=2), NOW+timedelta(seconds=1)):
            self.authority.row_mutate=lambda row, instant=instant: replace(row, checked_at=instant.isoformat())
            self.assertEqual(self.gate.evaluate(READ).blockers, ('row_stale',))

    def test_revoked_between_two_reads_is_not_cached(self):
        self.assertTrue(self.gate.evaluate(READ).candidate_eligible)
        self.authority.scope_flags={'policy_active_and_current': False}
        denied=self.gate.evaluate(READ)
        self.assertEqual(denied.blockers, ('scope_policy_active_and_current',))
        self.assertEqual(self.authority.scope_calls, 3)
        self.assertEqual(self.authority.row_calls, 2)

    def test_source_revoked_during_row_iteration_is_suppressed(self):
        first = self.authority.check_scope
        def flip_scope(pointer):
            result = first(pointer)
            if self.authority.scope_calls == 2:
                return replace(result, policy_active_and_current=False)
            return result
        self.authority.check_scope = flip_scope
        report = self.gate.evaluate(READ)
        self.assertEqual(report.suppressed_rows, 2)
        self.assertEqual(report.blockers, ('scope_policy_active_and_current',))
        self.assertFalse(report.ready_for_analysis)
        self.assertEqual(self.authority.row_calls, 2)

    def test_upstream_errors_do_not_leak_secret_text(self):
        self.authority.fail_scope=True
        with self.assertRaises(PIEBoundaryError) as caught:
            self.gate.evaluate(READ)
        self.assertEqual(caught.exception.code, 'provenance_check_failed')
        self.assertNotIn('CREDENTIAL', str(caught.exception))
        self.authority.fail_scope=False
        self.authority.fail_row=True
        with self.assertRaises(PIEBoundaryError) as caught:
            self.gate.evaluate(READ)
        self.assertEqual(caught.exception.code, 'provenance_check_failed')
        self.assertNotIn('SECRET', str(caught.exception))

    def test_malformed_or_missing_proofs_do_not_fail_open(self):
        self.authority.scope_mutate=lambda x: None
        self.rejected('invalid_provenance_response', self.gate.evaluate, READ)
        self.authority.scope_mutate=lambda x: x
        self.authority.row_mutate=lambda x: {'qualified': True}
        self.rejected('invalid_provenance_response', self.gate.evaluate, READ)

    def test_boolean_truthy_int_is_rejected(self):
        good=self.authority.check_scope(PTR)
        with self.assertRaises(PIEBoundaryError) as error:
            replace(good, can_store_metrics=1)
        self.assertEqual(error.exception.code, 'invalid_provenance')
        subject=self._subject()
        good_row=self.authority.check_row(subject)
        with self.assertRaises(PIEBoundaryError) as error:
            replace(good_row, sre_approved_live=1)
        self.assertEqual(error.exception.code, 'invalid_provenance')

    def test_no_raw_evidence_pii_or_scores_in_report_or_subject(self):
        subjects=[]
        self.authority.row_mutate=lambda row: (subjects.append(row), row)[1]
        report=self.gate.evaluate(READ)
        self.assertNotIn('breakout_score', str(report))
        self.assertFalse(any('features_a' in s.__dataclass_fields__ or
                             'breakout_score' in s.__dataclass_fields__ for s in subjects))
        sub=self._subject()
        self.assertNotIn('creator_key', sub.__dataclass_fields__)
        self.assertNotIn('features_b', sub.__dataclass_fields__)

    def test_reference_tuple_contains_all_source_identity_versions(self):
        x=self._subject()
        source=DATA['manifest']['observations'][0]
        for key in ('qdp_run_id','qdp_preparation_sha256', 'sre_decision_sha256',
                    'group_a_analysis_id','group_b_analysis_id','metric_snapshot_id',
                    'calculated_metric_id','group_a_sha256','group_b_sha256',
                    'metric_snapshot_sha256','calculated_metric_sha256'):
            self.assertEqual(getattr(x,key), source[key])
        self.assertEqual(x.row_sha256, DATASET.observations[0].row_sha256)

    def _subject(self):
        subject_list=[]
        saved=self.authority.check_row
        def check(row):
            subject_list.append(row)
            return saved(row)
        self.authority.check_row=check
        try:
            self.gate.evaluate(READ)
        finally:
            self.authority.check_row=saved
        return subject_list[0]


if __name__ == '__main__':
    unittest.main()
