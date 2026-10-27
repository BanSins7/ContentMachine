"""PIE-MOD-13 synthetic full-chain regressions: candidate hypotheses, NOT discoveries."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
import unittest

from intelligence.patterns_insights import PIEBoundaryError, canonical_hash
from intelligence.patterns_insights.research.marginal import (
    SCHEMA, MarginalHypothesis, MarginalDiscoveryPolicy, CandidateMarginalDiscovery,
    build_candidate_marginal_discovery, replay_candidate_marginal_discovery,
)
from intelligence.patterns_insights.research.feature_matrix import build_candidate_feature_matrix
from intelligence.patterns_insights.research.coverage import (
    build_candidate_coverage_audit, CoveragePolicy,
)
from test_pie_coverage import CoverageAuditTests, coverage_policy
from test_pie_feature_matrix import FeatureMatrixTests, policy as matrix_policy


def hypotheses():
    return (
        MarginalHypothesis('a_duration', 'a', 'duration_sec', 'numeric_distribution', None, (0,20)),
        MarginalHypothesis('a_measured', 'a', 'measured', 'boolean_equals', True),
        MarginalHypothesis('b_hooks', 'b', 'hook_mechanisms', 'multilabel_contains', 'question'),
        MarginalHypothesis('b_story', 'b', 'story_roles', 'sequence_equals', ('hook','proof','proof')),
    )


def discovery_policy(**updates):
    items=dict(policy_version='marginal/v1', design_sha256='c'*64,
        registered_at='2026-10-25T19:00:00+00:00', registration_claim_id='test-claim-only',
        minimum_observed_per_role=1, hypotheses=hypotheses())
    items.update(updates)
    return MarginalDiscoveryPolicy(**items)


class MarginalDiscoveryTests(unittest.TestCase):
    def setUp(self):
        CoverageAuditTests.setUp(self)
        self.complete_cols=tuple(c for c in self.selected.features if c.name!='cta_has_cta')
        self.complete_matrix=FeatureMatrixTests.matrix(self,policy_=matrix_policy(features=self.complete_cols))
        self.passed_coverage=CoverageAuditTests.audit(self,matrix=self.complete_matrix)
        self.assertEqual(self.passed_coverage.offline_coverage_status,'pass')
        self.disc_policy=discovery_policy()

    def build(self, policy=None, matrix=None, coverage=None):
        return build_candidate_marginal_discovery(self.snapshot,self.strata,self.outcome,
            self.controls, matrix if matrix is not None else self.complete_matrix,
            coverage if coverage is not None else self.passed_coverage,
            policy if policy is not None else self.disc_policy)

    def rejected(self, code, func, *args, **kwargs):
        with self.assertRaises(PIEBoundaryError) as captured:
            func(*args,**kwargs)
        self.assertEqual(captured.exception.code, code)

    def test_exact_registered_search_space_and_denominators(self):
        artifact=self.build();obj=artifact.as_dict()
        self.assertIsInstance(artifact,CandidateMarginalDiscovery)
        self.assertEqual(obj['schema_version'],SCHEMA)
        self.assertEqual(obj['registered_hypothesis_count'],4)
        self.assertEqual(artifact.hypothesis_count,4)
        self.assertEqual(obj['pairs_n'],1)
        self.assertEqual(obj['matched_n'],2)
        self.assertEqual(obj['source_population']['scanned_n'],3)
        self.assertEqual(obj['source_population']['included_n'],2)
        self.assertEqual(obj['source_population']['excluded_n'],1)
        self.assertEqual(obj['source_population']['excluded_by_reason'],{'policy_unavailable':1})
        self.assertEqual([x['hypothesis']['hypothesis_id'] for x in obj['hypotheses']],
                         ['a_duration','a_measured','b_hooks','b_story'])
        self.assertFalse(obj['causal_claim']); self.assertIsNone(obj['p_values'])
        self.assertIsNone(obj['adjusted_p_values']);self.assertFalse(obj['approved_live'])
        self.assertFalse(artifact.ready_for_analysis);self.assertFalse(artifact.verified_live)
        with self.assertRaises(FrozenInstanceError):artifact.matched_n=999

    def test_numeric_histogram_and_independent_median_oracles(self):
        entry=self.build().as_dict()['hypotheses'][0]
        byrole={row['role']: next(cell['observation']['value'] for cell in row['features']
                   if cell['name']=='duration_sec') for row in self.complete_matrix.as_dict()['rows']}
        for role in ('high','low'):
            self.assertEqual(entry[role]['median_raw'],str(byrole[role]))
            self.assertEqual(sum(entry[role]['bin_counts']),1)
            self.assertEqual(entry[role]['bin_counts'],[
                1 if byrole[role]<0 else 0,
                1 if 0<=byrole[role]<20 else 0,
                1 if byrole[role]>=20 else 0])
            self.assertEqual(entry[role]['observed_n'],1)
            self.assertIsNone(entry[role]['hit_n'])
        # Pure numeric median correctness independent from one-row fixture.
        from intelligence.patterns_insights.research.marginal import _median
        self.assertEqual(_median([0,20]),'10')
        self.assertEqual(_median([-3,-2]),'-2.5')
        self.assertEqual(_median([-3,0,2]),'0')

    def test_b1_repeated_hooks_count_once_per_video(self):
        entry=self.build().as_dict()['hypotheses'][2]
        self.assertEqual((entry['high']['hit_n'],entry['low']['hit_n']),(1,0))
        self.assertEqual((entry['high']['observed_n'],entry['low']['observed_n']),(1,1))
        self.assertEqual((entry['high']['hit_fraction_observed'],entry['low']['hit_fraction_observed']),('1.000000','0.000000'))

    def test_b3_exact_sequence_retains_duplicates_and_order(self):
        entry=self.build().as_dict()['hypotheses'][3]
        self.assertEqual((entry['high']['hit_n'],entry['low']['hit_n']),(1,0))
        changed=replace(self.disc_policy,hypotheses=(MarginalHypothesis(
            'story_order','b','story_roles','sequence_equals',('hook','proof')),))
        self.assertEqual(self.build(changed).as_dict()['hypotheses'][0]['high']['hit_n'],0)

    def test_false_zero_empty_are_observed_not_missing(self):
        artifact=self.build(discovery_policy(hypotheses=(MarginalHypothesis(
            'hook_empty','b','hook_mechanisms','multilabel_contains','absent_value'),)))
        entry=artifact.as_dict()['hypotheses'][0]
        self.assertEqual(entry['high']['observed_n'],1)
        self.assertEqual(entry['low']['observed_n'],1)
        self.assertEqual(entry['low']['hit_n'],0)
        self.assertEqual(entry['low']['null_n'],0)

    def test_coverage_failure_blocks_discovery_no_silent_cherrypicking(self):
        self.rejected('coverage_blocked',self.build,coverage=CoverageAuditTests.audit(self))
        policy=coverage_policy(min_matched_rows=4,min_rows_per_role=2)
        audit=build_candidate_coverage_audit(self.snapshot,self.strata,self.outcome,
                            self.controls,self.complete_matrix,policy)
        self.rejected('coverage_blocked',self.build,coverage=audit)

    def test_minimum_observed_n_blocks_not_estimated_from_rows(self):
        self.rejected('insufficient_observed_n',self.build,
            discovery_policy(minimum_observed_per_role=2))

    def test_reject_unknown_features_and_wrong_types(self):
        self.rejected('unsupported_feature',self.build,discovery_policy(hypotheses=(
            MarginalHypothesis('not_found','b','made_up','boolean_equals',True),)))
        self.rejected('feature_type_mismatch',self.build,discovery_policy(hypotheses=(
            MarginalHypothesis('wrong_kind','a','duration_sec','category_equals','16'),)))
        self.rejected('unsupported_feature',self.build,discovery_policy(hypotheses=(
            MarginalHypothesis('no_score','b','breakout_score','category_equals','250.50'),)))

    def test_policy_requires_preregistration_before_outcome_window(self):
        self.rejected('late_preregistration',self.build,discovery_policy(
            registered_at='2026-10-26T07:00:00+00:00'))
        self.rejected('invalid_discovery_policy',discovery_policy,registered_at='2026-10-25')

    def test_bounded_unique_canonical_registered_hypotheses(self):
        self.rejected('invalid_discovery_policy',discovery_policy,hypotheses=())
        self.rejected('invalid_discovery_policy',discovery_policy,hypotheses=tuple(
            MarginalHypothesis('h%03d'%i,'a','duration_sec','numeric_distribution',None,(0,)) for i in range(65)))
        self.rejected('invalid_discovery_policy',discovery_policy,hypotheses=(hypotheses()[1],hypotheses()[0]))
        self.rejected('invalid_discovery_policy',discovery_policy,hypotheses=(hypotheses()[0],hypotheses()[0]))
        self.rejected('duplicate_hypothesis',discovery_policy,hypotheses=(
            MarginalHypothesis('a','a','measured','boolean_equals',True),
            MarginalHypothesis('b','a','measured','boolean_equals',True)))

    def test_numeric_bins_and_predicate_types_are_explicit(self):
        for boundaries in ((1,1),(10,0),(),(True,1),[1,2]):
            with self.subTest(boundaries=boundaries):
                self.rejected('invalid_hypothesis',MarginalHypothesis,'num','a',
                              'duration_sec','numeric_distribution',None,boundaries)
        self.rejected('invalid_hypothesis',MarginalHypothesis,'cat','b','story_roles','sequence_equals',['hook'])
        self.rejected('invalid_hypothesis',MarginalHypothesis,'cat','b','story_roles','sequence_equals',('hook',), (1,))
        self.rejected('invalid_hypothesis',MarginalHypothesis,'zero','a','measured','boolean_equals',0)

    def test_policy_version_and_registered_family_change_identity(self):
        base=self.build()
        self.assertNotEqual(base.artifact_sha256,self.build(discovery_policy(policy_version='marginal/v2')).artifact_sha256)
        self.assertNotEqual(base.artifact_sha256,self.build(discovery_policy(registration_claim_id='different_claim')).artifact_sha256)
        self.assertNotEqual(base.artifact_sha256,self.build(discovery_policy(hypotheses=(hypotheses()[0],))).artifact_sha256)
        self.rejected('discovery_design_mismatch',self.build,discovery_policy(design_sha256='f'*64))

    def test_deterministic_full_replay_and_input_pins(self):
        a=self.build();b=self.build()
        self.assertEqual(a,b);self.assertEqual(a.to_json(),b.to_json())
        self.assertEqual(a.artifact_sha256,canonical_hash(a.as_dict()))
        self.assertEqual(replay_candidate_marginal_discovery(a.to_json(),a.artifact_sha256,
               self.snapshot,self.strata,self.outcome,self.controls,self.complete_matrix,self.passed_coverage),a)
        self.assertEqual(a.source_matrix_sha256,self.complete_matrix.artifact_sha256)
        self.assertEqual(a.source_coverage_sha256,self.passed_coverage.artifact_sha256)

    def test_resealed_mutation_is_not_a_verified_candidate(self):
        a=self.build();payload=a.as_dict()
        payload['hypotheses'][0]['high']['median_raw']='999'
        raw=json.dumps(payload,sort_keys=True,separators=(',',':'))
        self.rejected('discovery_replay_mismatch',replay_candidate_marginal_discovery,
            raw,canonical_hash(payload),self.snapshot,self.strata,self.outcome,
            self.controls,self.complete_matrix,self.passed_coverage)
        self.rejected('discovery_hash_mismatch',replay_candidate_marginal_discovery,
            a.to_json(),'0'*64,self.snapshot,self.strata,self.outcome,
            self.controls,self.complete_matrix,self.passed_coverage)

    def test_bad_json_duplicate_keys_nan_and_fake_live_status_fail_closed(self):
        a=self.build();refs=(self.snapshot,self.strata,self.outcome,self.controls,
                             self.complete_matrix,self.passed_coverage)
        self.rejected('duplicate_json_key',replay_candidate_marginal_discovery,
            '{"x":1,"x":2}','0'*64,*refs)
        self.rejected('invalid_discovery',replay_candidate_marginal_discovery,
            '{"x":NaN}','0'*64,*refs)
        payload=a.as_dict();payload['approved_live']=True
        self.rejected('unsupported_discovery',replay_candidate_marginal_discovery,
            json.dumps(payload),canonical_hash(payload),*refs)

    def test_independent_hand_computed_reference_with_null_and_absent(self):
        from intelligence.patterns_insights.research.marginal import _group
        from pathlib import Path
        expected=json.loads((Path(__file__).parent/'fixtures'/'pie'/'pie_marginal_golden_v1.json').read_text())
        numeric=MarginalHypothesis('ref_numeric','a','duration_sec','numeric_distribution',None,(0,5,10))
        def samples(items):
            return [{'features':[{'observation':{'state':state,'value':value}}]}
                    for state,value in items]
        high=samples([('present',0),('present',4),('present',6),('present',20),('null',None),('absent',None)])
        low=samples([('present',-3),('present',0),('present',10),('present',10)])
        self.assertEqual(_group(high,0,numeric),expected['high'])
        self.assertEqual(_group(low,0,numeric),expected['low'])
        self.assertEqual(expected['fixture_kind'],'synthetic_independent_hand_computed')

    def test_no_statistics_no_inference_no_payment_or_unsafe_claims(self):
        a=self.build()
        for action in ('publish_insight','calculate_p_values','calculate_significance',
                       'calculate_effect_size','rank_by_effect','authorize','mark_verified'):
            self.assertFalse(hasattr(a,action))
        self.assertEqual(a.as_dict()['warnings'],['exploratory_only','no_statistical_significance',
            'multiple_testing_uncorrected','not_live_authorized',
            'selection_bias_unknown','not_run_partial_unknown'])


if __name__=='__main__':
    unittest.main()
