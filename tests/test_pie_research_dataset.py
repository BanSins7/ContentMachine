"""PIE-MOD-07 immutable research candidate: mock source data only, NOT live UAE."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import unittest

from intelligence.patterns_insights import (
    PIEBoundaryError, PIEDatasetPointer, canonical_hash, validate_dataset_manifest,
    ResearchSampleWeight, ResearchBuildSpec, CandidateResearchSnapshot,
    build_candidate_dataset, replay_candidate_snapshot,
)
from intelligence.patterns_insights.intake.uae import UAEReadResult
from intelligence.patterns_insights.intake.provenance import (
    SourceEligibilityGate, ScopeEligibility, RowEligibility,
)
from intelligence.patterns_insights.research.dataset_builder import SNAPSHOT_SCHEMA

ROOT = Path(__file__).parent / 'fixtures' / 'pie'
SOURCE = json.loads((ROOT / 'uae_to_pie_v1_positive.json').read_text(encoding='utf8'))
PTR = PIEDatasetPointer(**SOURCE['pointer'])
DATA = validate_dataset_manifest(PTR, SOURCE['manifest'])
NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
READ = UAEReadResult(DATA, 'uae-synthetic', 'e' * 64,
                     (NOW - timedelta(minutes=1)).isoformat())


def design(*, weights=None, version='replay/v1', reason='approved_candidate'):
    return ResearchBuildSpec(
        design_sha256='c' * 64,
        algorithm_version=version,
        weight_rule_version='explicit-v1',
        selection_reason_code=reason,
        frozen_as_of=PTR.as_of,
        weights=weights or (ResearchSampleWeight(101, '1.000'),
                            ResearchSampleWeight(102, '0.50')),
    )


class Authority:
    """Clearly fake positive fixtures; no production-current attestations."""
    def __init__(self):
        self.scope_count = 0
        self.row_count = 0
        self.deny_scope = False
        self.deny_video = None
        self.revoke_after_scan = False
        self.row_sha_mismatch = False

    def check_scope(self, pointer):
        self.scope_count += 1
        return ScopeEligibility(
            pointer.project_id, pointer.source_id, pointer.source_policy_version_id,
            pointer.uae_manifest_sha256, pointer.uae_dataset_sha256,
            NOW.isoformat(), (NOW + timedelta(hours=1)).isoformat(),
            True, True, not (self.deny_scope or (self.revoke_after_scan and self.scope_count >= 2)),
            True, True, True, True,
        )

    def check_row(self, subject):
        self.row_count += 1
        return RowEligibility(
            subject.project_id, subject.source_id, subject.source_policy_version_id,
            subject.manifest_sha256, subject.video_id, subject.uae_snapshot_id,
            subject.uae_snapshot_sha256,
            'b' * 64 if self.row_sha_mismatch else subject.row_sha256,
            NOW.isoformat(), (NOW + timedelta(hours=1)).isoformat(),
            True, True, subject.video_id != self.deny_video,
            True, True, True, True, True, True,
        )


class PIEReproducibleDatasetTests(unittest.TestCase):
    def setUp(self):
        self.authority = Authority()
        self.gate = SourceEligibilityGate(self.authority, now=lambda: NOW)
        self.spec = design()

    def build(self, read=READ, spec=None):
        return build_candidate_dataset(read, self.gate, spec or self.spec)

    def rejected(self, code, fn, *args):
        with self.assertRaises(PIEBoundaryError) as err:
            fn(*args)
        self.assertEqual(err.exception.code, code)

    def test_candidate_is_fully_frozen_and_never_analysis_ready(self):
        result = self.build()
        self.assertIsInstance(result, CandidateResearchSnapshot)
        self.assertEqual(result.project_id, PTR.project_id)
        self.assertEqual(result.sample_size, 2)
        self.assertEqual(result.selection_count, 3)
        self.assertEqual(result.included_video_ids, (101, 102))
        self.assertEqual(result.excluded_video_ids, (103,))
        self.assertFalse(result.verified_live)
        self.assertFalse(result.ready_for_analysis)
        self.assertEqual(self.authority.scope_count, 2)
        self.assertEqual(self.authority.row_count, 2)
        with self.assertRaises(FrozenInstanceError):
            result.selection_count = 300

    def test_deterministic_repeat_and_offline_replay(self):
        a = self.build()
        b = self.build()
        self.assertEqual(a, b)
        self.assertEqual(a.to_json(), b.to_json())
        self.assertEqual(a.snapshot_sha256, sha256(a.to_json().encode()).hexdigest())
        self.assertTrue(a.verify_offline_replay())
        self.assertEqual(replay_candidate_snapshot(a.to_json().encode(), a.snapshot_sha256), a)
        self.assertEqual(replay_candidate_snapshot(a.to_json(), a.snapshot_sha256), a)
        self.assertEqual(self.authority.scope_count, 4)
        self.assertEqual(self.authority.row_count, 4)  # replay never contacts authority

    def test_pinned_golden_identity_across_runs(self):
        golden = json.loads((ROOT / 'pie_research_candidate_identity_v1.json').read_text(encoding='utf8'))
        snapshot = self.build()
        self.assertEqual(golden['fixture_kind'], 'synthetic_non_authoritative')
        self.assertEqual(golden['expected_snapshot_sha256'], snapshot.snapshot_sha256)
        self.assertEqual(golden['expected_dictionary_sha256'], snapshot.feature_dictionary_sha256)
        self.assertEqual(golden['upstream_manifest_sha256'], snapshot.upstream_manifest_sha256)
        self.assertEqual(golden['upstream_dataset_sha256'], snapshot.upstream_dataset_sha256)
        self.assertEqual(golden['included_video_ids'], list(snapshot.included_video_ids))
        self.assertEqual(golden['excluded_video_ids'], list(snapshot.excluded_video_ids))
        self.assertEqual(golden['scanned_count'], snapshot.selection_count)

    def test_snapshot_member_lineage_and_unbounded_raw_C7(self):
        s = self.build().as_dict()
        self.assertEqual(s['schema_version'], SNAPSHOT_SCHEMA)
        self.assertEqual(s['evidence_state'], 'unverified')
        self.assertEqual(s['source']['pointer'], SOURCE['pointer'])
        self.assertEqual(s['source']['manifest'], SOURCE['manifest'])
        self.assertEqual(s['source']['receipt_sha256'], READ.receipt_sha256)
        self.assertEqual(s['selection']['included_count'], 2)
        self.assertEqual(s['selection']['excluded_count'], 1)
        self.assertEqual(s['selection']['scanned_count'], 3)
        first, second = s['selection']['included']
        self.assertEqual(first['row_sha256'], DATA.observations[0].row_sha256)
        self.assertEqual(first['row']['breakout_score'], '250.50')  # NOT normalized to [0,100]
        self.assertEqual(second['row']['breakout_score'], '-3.25')
        self.assertEqual(first['analysis_weight'], '1.000')
        self.assertEqual(second['analysis_weight'], '0.50')
        self.assertEqual(s['selection']['excluded'], [{'video_id': 103, 'reason_code': 'policy_unavailable'}])

    def test_no_mutable_aliases_to_manifest_weights_or_dictionary(self):
        frozen = self.build()
        obj = frozen.as_dict()
        obj['source']['manifest']['observations'][0]['breakout_score'] = '99999'
        obj['selection']['included'][0]['row']['features_b']['hook_mechanisms'].append('fabricated')
        obj['dictionary']['entries'][:] = []
        self.assertEqual(frozen.as_dict()['source']['manifest']['observations'][0]['breakout_score'], '250.50')
        self.assertEqual(frozen.as_dict()['selection']['included'][0]['row']['features_b']['hook_mechanisms'], ['question', 'question'])
        self.assertTrue(frozen.as_dict()['dictionary']['entries'])
        self.assertTrue(frozen.verify_offline_replay())

    def test_immutable_dictionary_preserves_absent_null_false_zero_and_sequences(self):
        s = self.build().as_dict()
        d = s['dictionary']
        self.assertEqual(d['score']['arithmetic'], 'raw_decimal_unbounded')
        self.assertEqual(s['dictionary_sha256'], canonical_hash(d))
        field = next(x for x in d['entries'] if x['family'] == 'features_b' and x['name'] == 'cta_has_cta')
        self.assertEqual(field['null_count'], 1)
        self.assertIn('null', field['observed_types'])
        self.assertIn('bool', field['observed_types'])
        hook = next(x for x in d['entries'] if x['family'] == 'features_b' and x['name'] == 'hook_mechanisms')
        self.assertEqual(hook['observed_types'], ['ordered_text_sequence'])
        self.assertEqual(s['selection']['included'][1]['row']['features_b']['hook_mechanisms'], [])

    def test_weight_population_must_be_exact(self):
        cases = [
            (ResearchSampleWeight(101, '1'),),
            (ResearchSampleWeight(101, '1'), ResearchSampleWeight(104, '1')),
            (ResearchSampleWeight(102, '1'), ResearchSampleWeight(103, '1')),
        ]
        for weights in cases:
            with self.subTest(weights=weights):
                self.rejected('weight_population_mismatch', self.build, READ, design(weights=weights))

    def test_weight_is_strict_finite_positive_and_canonical(self):
        for value in ('0', '0.000', '-1', 'NaN', 'Infinity', '1e3', '01', '1.1234567890123',
                      '9999999999999', 1.0, True, None, ''):
            with self.subTest(value=value):
                self.rejected('invalid_weight', ResearchSampleWeight, 101, value)
        self.rejected('invalid_weight', ResearchSampleWeight, False, '1')
        self.rejected('duplicate_weight', ResearchBuildSpec, 'c'*64, 'replay/v1', 'explicit-v1',
                      'approved_candidate', PTR.as_of,
                      (ResearchSampleWeight(101, '1'), ResearchSampleWeight(101, '2')))
        self.rejected('duplicate_weight', ResearchBuildSpec, 'c'*64, 'replay/v1', 'explicit-v1',
                      'approved_candidate', PTR.as_of,
                      (ResearchSampleWeight(102, '1'), ResearchSampleWeight(101, '2')))

    def test_explicit_build_spec_no_default_statistical_policy(self):
        self.rejected('invalid_build_spec', ResearchBuildSpec, 'c'*64, 'replay/v1',
                      'explicit-v1', 'approved_candidate', PTR.as_of, ())
        self.rejected('invalid_build_spec', ResearchBuildSpec, 'c'*64, 'replay/v1',
                      'explicit-v1', 'approved_candidate', PTR.as_of, [ResearchSampleWeight(101, '1')])
        self.rejected('invalid_build_spec', ResearchBuildSpec, 'bad', 'replay/v1',
                      'explicit-v1', 'approved_candidate', PTR.as_of, self.spec.weights)
        self.rejected('invalid_build_spec', ResearchBuildSpec, 'c'*64, 'replay/v1',
                      'explicit-v1', 'approved_candidate', 'no timezone', self.spec.weights)
        self.rejected('frozen_time_mismatch', self.build, READ,
                      replace(self.spec, frozen_as_of='2026-10-26T09:00:00Z'))
        self.rejected('invalid_build_spec', build_candidate_dataset, READ, self.gate, {})

    def test_any_denied_row_blocks_all_samples_without_silent_exclusion(self):
        self.authority.deny_video = 102
        self.rejected('eligibility_blocked', self.build)
        self.assertEqual(self.authority.row_count, 2)
        self.assertEqual(self.authority.scope_count, 2)

    def test_revoked_scope_prevents_row_processing(self):
        self.authority.deny_scope = True
        self.rejected('eligibility_blocked', self.build)
        self.assertEqual(self.authority.row_count, 0)

    def test_revocation_during_dataset_scan_blocks_final_snapshot(self):
        self.authority.revoke_after_scan = True
        self.rejected('eligibility_blocked', self.build)
        self.assertEqual(self.authority.scope_count, 2)
        self.assertEqual(self.authority.row_count, 2)

    def test_row_fingerprint_mismatch_denied(self):
        self.authority.row_sha_mismatch = True
        self.rejected('provenance_lineage_mismatch', self.build)

    def test_missing_authority_or_raw_unverified_manifest_rejected(self):
        self.rejected('capability_unavailable', build_candidate_dataset,
                      READ, SourceEligibilityGate(), self.spec)
        self.rejected('capability_unavailable', build_candidate_dataset,
                      READ, object(), self.spec)
        self.rejected('invalid_read', build_candidate_dataset,
                      DATA, self.gate, self.spec)

    def test_forged_dataclass_rows_revalidated_before_eligibility_read(self):
        forged = replace(DATA, scanned_count=300)
        self.rejected('manifest_replay_mismatch', self.build, replace(READ, dataset=forged))
        self.assertEqual(self.authority.scope_count, 0)
        self.assertEqual(self.authority.row_count, 0)

    def test_version_and_weight_mutations_force_new_snapshot_hash(self):
        base = self.build()
        self.assertNotEqual(self.build(spec=design(version='replay/v2')).snapshot_sha256, base.snapshot_sha256)
        changed = replace(self.spec, weights=(ResearchSampleWeight(101, '1.000'),
                                              ResearchSampleWeight(102, '1.00')))
        self.assertNotEqual(self.build(spec=changed).snapshot_sha256, base.snapshot_sha256)
        changed = replace(READ, receipt_sha256='d'*64)
        self.assertNotEqual(self.build(read=changed).snapshot_sha256, base.snapshot_sha256)

    def test_new_source_snapshot_version_forces_new_identity(self):
        obj = deepcopy(SOURCE)
        obj['manifest']['observations'][0]['uae_snapshot_id'] = 2999
        obj['pointer']['uae_dataset_sha256'] = canonical_hash({
            'schema_version': 'pie-row-dataset/v1', 'observations': obj['manifest']['observations']})
        obj['pointer']['uae_manifest_sha256'] = canonical_hash(obj['manifest'])
        new_pointer = PIEDatasetPointer(**obj['pointer'])
        new_data = validate_dataset_manifest(new_pointer, obj['manifest'])
        new_read = replace(READ, dataset=new_data)
        self.assertNotEqual(self.build(new_read).snapshot_sha256, self.build().snapshot_sha256)

    def test_replay_detects_byte_tamper_and_nonmatching_hash(self):
        snapshot = self.build()
        obj = snapshot.as_dict()
        obj['selection']['included'][0]['analysis_weight'] = '99999'
        self.rejected('snapshot_hash_mismatch', replay_candidate_snapshot,
                      json.dumps(obj), snapshot.snapshot_sha256)
        self.rejected('snapshot_hash_mismatch', replay_candidate_snapshot,
                      snapshot.to_json(), '0'*64)

    def test_replay_detects_resealed_structural_forgery(self):
        snapshot = self.build()
        changes = [
            lambda p: p['selection']['included'][0].update({'row_sha256':'1'*64}),
            lambda p: p['selection']['excluded'].clear(),
            lambda p: p['dictionary']['entries'].clear(),
            lambda p: p['selection']['included'][1].update({'analysis_weight':'4.00'}),
            lambda p: p['source']['manifest']['observations'][0].update({'breakout_score': '999'}),
            lambda p: p.update({'evidence_state':'verified'}),
        ]
        for idx, change in enumerate(changes):
            obj = snapshot.as_dict()
            change(obj)
            raw = json.dumps(obj, sort_keys=True, separators=(',', ':'))
            digest = canonical_hash(obj)
            with self.subTest(idx=idx):
                with self.assertRaises(PIEBoundaryError):
                    replay_candidate_snapshot(raw, digest)

    def test_replay_rejects_duplicate_keys_nonfinite_and_unsupported_schema(self):
        snap = self.build()
        self.rejected('duplicate_json_key', replay_candidate_snapshot,
                      '{"schema_version":"foo","schema_version":"bar"}', '0'*64)
        self.rejected('invalid_snapshot', replay_candidate_snapshot, '{"x":NaN}', '0'*64)
        self.rejected('invalid_snapshot', replay_candidate_snapshot, 123, '0'*64)
        obj = snap.as_dict()
        obj['schema_version'] = 'pie-research-candidate/v2'
        self.rejected('unsupported_snapshot', replay_candidate_snapshot,
                      json.dumps(obj), canonical_hash(obj))

    def test_no_untrusted_dataset_inference_or_publication_methods(self):
        s = self.build()
        for method in ('compute_p_values', 'discover_patterns', 'publish_insight', 'authorize', 'mark_verified'):
            self.assertFalse(hasattr(s, method))
        self.assertFalse(s.ready_for_analysis)


if __name__ == '__main__':
    unittest.main()
