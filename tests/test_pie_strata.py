"""PIE-MOD-08: deterministic descriptive strata only, synthetic UAE fixture."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import json
import unittest

from intelligence.patterns_insights import PIEBoundaryError, PIEDatasetPointer, canonical_hash, validate_dataset_manifest, build_candidate_dataset
from intelligence.patterns_insights.intake.provenance import SourceEligibilityGate
from intelligence.patterns_insights.research.strata import (
    SCHEMA, AgeBucket, StrataPolicy, CandidateStrata,
    build_candidate_strata, replay_candidate_strata, require_one_stratum,
)
from test_pie_research_dataset import SOURCE, READ, NOW, Authority, design


def policy(*, creator='cohort', niche=None, missing='isolate', pub=None, lag=None, version='strat-v1'):
    return StrataPolicy(
        policy_version=version,
        publication_age_buckets=pub or (AgeBucket('day0', 0, 86400), AgeBucket('day1plus', 86400, None)),
        observation_lag_buckets=lag or (AgeBucket('fresh', 0, 7200), AgeBucket('aged', 7200, None)),
        creator_mode=creator, niche_feature=niche, niche_missing=missing,
    )


class ComparableStrataTests(unittest.TestCase):
    def setUp(self):
        self.authority = Authority()
        self.gate = SourceEligibilityGate(self.authority, now=lambda: NOW)
        self.snapshot = build_candidate_dataset(READ, self.gate, design())

    def expected_error(self, code, fn, *args):
        with self.assertRaises(PIEBoundaryError) as error:
            fn(*args)
        self.assertEqual(error.exception.code, code)

    def new_snapshot(self, mutate):
        """Synthetic checksum-correct, separately reconstructed source manifest."""
        fixture = deepcopy(SOURCE)
        mutate(fixture)
        for row in fixture['manifest']['observations']:
            row['features_sha256'] = canonical_hash({'a': row['features_a'], 'b': row['features_b']})
        fixture['pointer']['uae_dataset_sha256'] = canonical_hash({
            'schema_version': 'pie-row-dataset/v1',
            'observations': fixture['manifest']['observations'],
        })
        fixture['pointer']['uae_manifest_sha256'] = canonical_hash(fixture['manifest'])
        pointer = PIEDatasetPointer(**fixture['pointer'])
        dataset = validate_dataset_manifest(pointer, fixture['manifest'])
        read = replace(READ, dataset=dataset)
        return build_candidate_dataset(read, SourceEligibilityGate(Authority(), now=lambda: NOW), design())

    def test_partition_is_deterministic_and_candidate_only(self):
        a = build_candidate_strata(self.snapshot, policy())
        b = build_candidate_strata(self.snapshot, policy())
        self.assertEqual(a, b)
        self.assertIsInstance(a, CandidateStrata)
        self.assertFalse(a.verified_live)
        self.assertFalse(a.ready_for_analysis)
        self.assertEqual(a.included_video_ids, (101, 102))
        self.assertEqual(a.excluded_video_ids, (103,))
        self.assertEqual(a.stratum_count, 2)  # different content kind
        self.assertEqual([g['sample_size'] for g in a.as_dict()['strata']], [1, 1])
        self.assertEqual(a.as_dict()['schema_version'], SCHEMA)
        self.assertEqual(a.as_dict()['evidence_state'], 'unverified')
        self.assertEqual(a, replay_candidate_strata(a.to_json(), a.artifact_sha256, self.snapshot))
        with self.assertRaises(FrozenInstanceError):
            a.stratum_count = 200

    def test_exact_source_metric_config_taxonomy_and_age_pins(self):
        artifact = build_candidate_strata(self.snapshot, policy())
        data = artifact.as_dict()
        self.assertEqual(data['source_snapshot_sha256'], self.snapshot.snapshot_sha256)
        for group in data['strata']:
            key = group['key']
            self.assertEqual(key['source_id'], SOURCE['pointer']['source_id'])
            self.assertEqual(key['source_metric_definition'], SOURCE['pointer']['source_metric_definition'])
            self.assertEqual(key['source_metric_version'], SOURCE['pointer']['source_metric_version'])
            self.assertEqual(key['calculation_config_id'], SOURCE['pointer']['calculation_config_id'])
            self.assertEqual(key['group_b_taxonomy_version'], SOURCE['manifest']['versions']['group_b_taxonomy_version'])
            self.assertEqual(key['sre_taxonomy_version'], SOURCE['manifest']['versions']['sre_taxonomy_version'])
            self.assertEqual(key['creator_cohort_version'], SOURCE['manifest']['versions']['creator_cohort_version'])
            self.assertEqual(key['niche_status'], 'missing')
            self.assertEqual(key['publication_age_bucket'], 'day1plus')
            self.assertEqual(key['observation_lag_bucket'], 'aged')
            self.assertEqual(group['key_sha256'], canonical_hash(key))

    def test_mixed_platform_and_kind_never_pooled(self):
        a = build_candidate_strata(self.snapshot, policy())
        self.expected_error('incompatible_strata', require_one_stratum, a, (101, 102))
        self.expected_error('unknown_video', require_one_stratum, a, (101, 103))
        self.expected_error('invalid_strata_selection', require_one_stratum, a, (102, 101))
        self.expected_error('invalid_strata_selection', require_one_stratum, a, (101, 101))
        self.expected_error('invalid_strata_selection', require_one_stratum, a, (True,))
        self.assertIsInstance(require_one_stratum(a, (101,)), str)

    def test_same_kind_creator_cohort_merges_only_under_explicit_policy(self):
        snapshot = self.new_snapshot(lambda x: x['manifest']['observations'][1].update({'content_kind': 'video'}))
        a = build_candidate_strata(snapshot, policy())
        self.assertEqual(a.stratum_count, 1)
        self.assertEqual(a.as_dict()['strata'][0]['sample_size'], 2)
        self.assertEqual(a.as_dict()['strata'][0]['members'][0]['analysis_weight'], '1.000')
        self.assertEqual(a.as_dict()['strata'][0]['members'][1]['analysis_weight'], '0.50')
        self.assertIsInstance(require_one_stratum(a, (101, 102)), str)
        b = build_candidate_strata(snapshot, policy(creator='individual'))
        self.assertEqual(b.stratum_count, 2)
        self.assertNotEqual(a.artifact_sha256, b.artifact_sha256)

    def test_niche_unknown_must_explicitly_isolate_or_reject(self):
        self.expected_error('missing_niche', build_candidate_strata, self.snapshot,
                            policy(niche='topic_id', missing='reject'))
        sample = self.new_snapshot(lambda x: x['manifest']['observations'][0]['features_b'].update({'topic_id': 51}))
        a = build_candidate_strata(sample, policy(niche='topic_id'))
        statuses = sorted(s['key']['niche_status'] for s in a.as_dict()['strata'])
        self.assertEqual(statuses, ['known', 'missing'])
        known = next(s for s in a.as_dict()['strata'] if s['key']['niche_status'] == 'known')
        self.assertEqual(known['key']['niche_value'], 'i:51')
        self.assertNotEqual(a.artifact_sha256, build_candidate_strata(sample, policy()).artifact_sha256)

    def test_multilabel_niche_refused_not_split_into_more_samples(self):
        sample = self.new_snapshot(lambda x: x['manifest']['observations'][0]['features_b'].update({'topic_id': ['niche-a', 'niche-b']}))
        self.expected_error('ambiguous_niche', build_candidate_strata, sample, policy(niche='topic_id'))

    def test_observation_age_boundaries_are_half_open_and_timezone_safe(self):
        samekind = self.new_snapshot(lambda x: x['manifest']['observations'][1].update({'content_kind': 'video', 'published_at': '2026-10-25T07:00:00+00:00'}))
        result = build_candidate_strata(samekind, policy())
        self.assertEqual(result.stratum_count, 2)  # exactly 24h → day1plus, 23h → day0
        age = {m['video_id']: group['key']['publication_age_bucket'] for group in result.as_dict()['strata'] for m in group['members']}
        self.assertEqual(age, {101:'day0', 102:'day1plus'})

    def test_issuer_versions_and_policy_changes_force_different_hash(self):
        baseline = build_candidate_strata(self.snapshot, policy())
        self.assertNotEqual(baseline.artifact_sha256, build_candidate_strata(self.snapshot, policy(version='strat-v2')).artifact_sha256)
        self.assertNotEqual(baseline.artifact_sha256, build_candidate_strata(self.snapshot, policy(creator='individual')).artifact_sha256)
        for mutation in (
            lambda x: x['pointer'].update({'source_metric_version': 'provider-test/v2'}) or x['manifest']['scope'].update({'source_metric_version': 'provider-test/v2'}),
            lambda x: x['pointer'].update({'calculation_config_id': 18}) or x['manifest']['scope'].update({'calculation_config_id': 18}),
            lambda x: x['manifest']['versions'].update({'group_b_taxonomy_version': 'group-b-taxonomy-test/v2'}) or [r.update({'group_b_taxonomy_version': 'group-b-taxonomy-test/v2'}) for r in x['manifest']['observations']],
        ):
            snap = self.new_snapshot(mutation)
            self.assertNotEqual(baseline.artifact_sha256, build_candidate_strata(snap, policy()).artifact_sha256)

    def test_bad_policy_fail_closed(self):
        for args in (
            (AgeBucket('a', 0, 10), AgeBucket('b', 9, None)),
            (AgeBucket('a', 0, 10), AgeBucket('b', 11, None)),
            (AgeBucket('a', 1, None),),
            (AgeBucket('a', 0, 10), AgeBucket('a', 10, None)),
            (AgeBucket('a', 0, None), AgeBucket('b', 100, None)),
        ):
            with self.subTest(args=args):
                self.expected_error('invalid_strata_policy', StrataPolicy, 'x/v1', args,
                                    (AgeBucket('all',0,None),), 'cohort', None, 'isolate')
        for a,b in ((0,0), (-1,2), (10,9), (10,10.3)):
            self.expected_error('invalid_strata_policy', AgeBucket, 'bad', a, b)
        self.expected_error('invalid_strata_policy', StrataPolicy, 'x/v1',
                            (AgeBucket('all',0,None),), (AgeBucket('all',0,None),), 'individual', None, 'reject')
        self.expected_error('invalid_strata_policy', build_candidate_strata, self.snapshot, None)

    def test_replay_rejects_resealed_tampering(self):
        artifact = build_candidate_strata(self.snapshot, policy())
        change_cases = (
            lambda p: p['strata'][0]['members'][0].update({'analysis_weight':'100000'}),
            lambda p: p['strata'][0]['key'].update({'source_metric_version':'other/v5'}),
            lambda p: p['excluded'].clear(),
            lambda p: p.update({'evidence_state':'verified'}),
            lambda p: p['strata'][0].update({'sample_size':999}),
        )
        for mutation in change_cases:
            p = artifact.as_dict()
            mutation(p)
            raw = json.dumps(p, sort_keys=True, separators=(',', ':'))
            with self.subTest(mutation=mutation):
                self.expected_error('strata_replay_mismatch', replay_candidate_strata, raw, canonical_hash(p), self.snapshot)
        self.expected_error('strata_hash_mismatch', replay_candidate_strata,
                            artifact.to_json(), '0'*64, self.snapshot)
        self.expected_error('duplicate_json_key', replay_candidate_strata,
                            '{"x":1,"x":2}', '0'*64, self.snapshot)
        self.expected_error('invalid_strata', replay_candidate_strata,
                            '{"x":NaN}', '0'*64, self.snapshot)

    def test_forged_candidate_wrapper_rejected(self):
        self.expected_error('invalid_candidate', build_candidate_strata, object(), policy())
        fake = replace(self.snapshot, project_id='b'*32)
        self.expected_error('invalid_candidate', build_candidate_strata, fake, policy())

    def test_source_not_re_read_and_no_statistics_leak(self):
        before=(self.authority.scope_count,self.authority.row_count)
        a=build_candidate_strata(self.snapshot,policy())
        self.assertEqual(before,(self.authority.scope_count,self.authority.row_count))
        for method in ('compute_p_values', 'declare_significant', 'publish_insight', 'verify_live'):
            self.assertFalse(hasattr(a,method))
        self.assertFalse(a.ready_for_analysis)


if __name__=='__main__':
    unittest.main()
