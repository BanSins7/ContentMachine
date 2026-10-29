"""PIE-MOD-14: source replay + independent arithmetic goldens (synthetic only)."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import json
from pathlib import Path
import unittest

from intelligence.patterns_insights import PIEBoundaryError, canonical_hash
from intelligence.patterns_insights.research.effects import (
    SCHEMA, EffectEstimationPolicy, CandidateDescriptiveEffects,
    build_candidate_descriptive_effects, replay_candidate_descriptive_effects,
    _effect, _fraction,
)
from test_pie_marginal import MarginalDiscoveryTests

FIXTURE = Path(__file__).parent/'fixtures'/'pie'/'pie_effects_golden_v1.json'


def effect_policy(**change):
    d=dict(policy_version='effects/v1',design_sha256='c'*64,method_version='descriptive/v1',
           minimum_observed_per_role=1,decimal_places=6)
    d.update(change)
    return EffectEstimationPolicy(**d)


class DescriptiveEffectTests(unittest.TestCase):
    def setUp(self):
        MarginalDiscoveryTests.setUp(self)
        self.discovery=MarginalDiscoveryTests.build(self)
        self.estimation_policy=effect_policy()

    def refs(self):
        return (self.snapshot,self.strata,self.outcome,self.controls,
                self.complete_matrix,self.passed_coverage,self.discovery)

    def estimate(self,policy=None):
        return build_candidate_descriptive_effects(*self.refs(),policy or self.estimation_policy)

    def denied(self,code,fn,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as e:
            fn(*args,**kwargs)
        self.assertEqual(e.exception.code,code)

    def test_all_preregistered_hypotheses_estimated_without_selection(self):
        effects=self.estimate()
        self.assertIsInstance(effects,CandidateDescriptiveEffects)
        obj=effects.as_dict()
        self.assertEqual(obj['schema_version'],SCHEMA)
        self.assertEqual(obj['registered_hypothesis_count'],4)
        self.assertEqual(effects.effect_count,4)
        self.assertEqual([e['hypothesis_id'] for e in obj['effects']],
                         ['a_duration','a_measured','b_hooks','b_story'])
        self.assertEqual(obj['registered_hypothesis_family_sha256'],
                         self.discovery.as_dict()['hypothesis_family_sha256'])
        self.assertEqual(obj['source_population']['scanned_n'],3)
        self.assertEqual(obj['source_population']['excluded_n'],1)
        self.assertEqual(obj['source_discovery_sha256'],self.discovery.artifact_sha256)
        self.assertIsNone(obj['p_values'])
        self.assertIsNone(obj['confidence_intervals'])
        self.assertFalse(effects.verified_live)
        self.assertFalse(effects.ready_for_analysis)
        with self.assertRaises(FrozenInstanceError):effects.effect_count=999

    def test_numeric_difference_keeps_source_unit_and_original_medians(self):
        row=self.estimate().as_dict()['effects'][0]
        self.assertEqual(row['method'],'median_difference_plus_binned_total_variation')
        self.assertEqual(row['unit'],'seconds')
        self.assertEqual(row['median_high_raw'],'16')
        self.assertEqual(row['median_low_raw'],'16')
        self.assertEqual(row['median_difference_high_minus_low_raw'],'0')
        self.assertEqual(row['high_observed_n'],1)
        self.assertEqual(row['low_observed_n'],1)
        self.assertEqual(row['histogram_tv_distance'],{'numerator':0,'denominator':1,'display_decimal':'0.000000'})
        self.assertIsNone(row['risk_difference'])

    def test_boolean_frequency_has_explicit_fraction_denominators(self):
        row=self.estimate().as_dict()['effects'][1]
        self.assertEqual(row['high_hit_n'],1)
        self.assertEqual(row['low_hit_n'],1)
        self.assertEqual(row['risk_difference'],{'numerator':0,'denominator':1,'display_decimal':'0.000000'})
        self.assertEqual(row['risk_ratio'],{'numerator':1,'denominator':1,'display_decimal':'1.000000'})
        self.assertEqual(row['risk_ratio_status'],'defined')

    def test_multilabel_dedup_and_zero_control_not_infinity(self):
        row=self.estimate().as_dict()['effects'][2]
        self.assertEqual(row['high_hit_n'],1)
        self.assertEqual(row['low_hit_n'],0)
        self.assertEqual(row['high_observed_n'],1)
        self.assertEqual(row['low_observed_n'],1)
        self.assertEqual(row['risk_difference']['display_decimal'],'1.000000')
        self.assertIsNone(row['risk_ratio'])
        self.assertEqual(row['risk_ratio_status'],'undefined_zero_baseline')
        self.assertNotIn('Infinity',json.dumps(row))

    def test_b3_ordered_sequence_rate_uses_video_denominator(self):
        row=self.estimate().as_dict()['effects'][3]
        self.assertEqual(row['analysis'],'sequence_equals')
        self.assertEqual(row['high_hit_n'],1)
        self.assertEqual(row['low_hit_n'],0)
        self.assertEqual(row['risk_difference'],{'numerator':1,'denominator':1,'display_decimal':'1.000000'})

    def test_independent_hand_calculated_binary_fraction_oracle(self):
        ref=json.loads(FIXTURE.read_text())
        b=ref['binary']
        spec={'hypothesis_id':'manual_binary','family':'b','feature_name':'flag',
              'analysis':'boolean_equals','target':True,'numeric_boundaries':[]}
        def arm(values):
            return {**values,'median_raw':None,'bin_counts':None,
                    'hit_fraction_observed':None}
        row=_effect({'hypothesis':spec,'high':arm(b['high']),'low':arm(b['low'])},effect_policy())
        for key,expected in [('high_hit_rate',b['high_rate']),('low_hit_rate',b['low_rate']),
                             ('risk_difference',b['risk_difference']),('risk_ratio',b['risk_ratio'])]:
            fr=row[key]
            self.assertEqual(Fraction(fr['numerator'],fr['denominator']),Fraction(expected))
        self.assertEqual(row['risk_difference']['display_decimal'],b['risk_difference_display'])
        self.assertEqual(row['risk_ratio']['display_decimal'],b['risk_ratio_display'])
        self.assertEqual(row['high_total_n'],6)
        self.assertEqual(row['high_null_n'],1)
        self.assertEqual(ref['fixture_kind'],'synthetic_independent_hand_computed')

    def test_independent_hand_calculated_distribution_shift_oracle(self):
        ref=json.loads(FIXTURE.read_text())['numeric']
        spec={'hypothesis_id':'manual_numeric','family':'a','feature_name':'duration_sec',
              'analysis':'numeric_distribution','target':None,'numeric_boundaries':[0,5,10]}
        def arm(data):
            return {**data,'total_n':data['observed_n'],'null_n':0,'absent_n':0,
                    'hit_n':None,'hit_fraction_observed':None}
        row=_effect({'hypothesis':spec,'high':arm(ref['high']), 'low':arm(ref['low'])},effect_policy(),numeric_unit='seconds')
        self.assertEqual(row['median_difference_high_minus_low_raw'],ref['median_difference'])
        self.assertEqual(Fraction(row['histogram_tv_distance']['numerator'],
                                  row['histogram_tv_distance']['denominator']),Fraction(ref['histogram_total_variation']))
        self.assertEqual(row['histogram_tv_distance']['display_decimal'],ref['histogram_total_variation_display'])

    def test_independent_zero_baseline_oracle_and_zero_high_event(self):
        data=json.loads(FIXTURE.read_text())['zero_baseline_ratio']
        sp={'hypothesis_id':'zero','family':'b','feature_name':'flag',
            'analysis':'boolean_equals','target':True,'numeric_boundaries':[]}
        def arm(h,n):
            return {'total_n':n,'observed_n':n,'null_n':0,'absent_n':0,'hit_n':h,
                    'median_raw':None,'bin_counts':None,'hit_fraction_observed':None}
        result=_effect({'hypothesis':sp,'high':arm(2,4),'low':arm(0,5)},effect_policy())
        self.assertIsNone(result['risk_ratio'])
        self.assertEqual(result['risk_ratio_status'],data['status'])
        result=_effect({'hypothesis':sp,'high':arm(0,4),'low':arm(2,5)},effect_policy())
        self.assertEqual(result['risk_ratio']['numerator'],0)
        self.assertEqual(result['risk_difference']['display_decimal'],'-0.400000')

    def test_exact_fraction_rounding_half_even(self):
        self.assertEqual(_fraction(Fraction(1,3),6)['display_decimal'],'0.333333')
        self.assertEqual(_fraction(Fraction(1,8),2)['display_decimal'],'0.12')
        self.assertEqual(_fraction(Fraction(3,8),2)['display_decimal'],'0.38')
        self.assertEqual(_fraction(Fraction(-1,2),0)['display_decimal'],'-0')

    def test_negative_and_bad_counts_refused(self):
        src=self.discovery.as_dict()['hypotheses'][1]
        for change,code in (({'hit_n':2},'effect_invalid_count'),
                            ({'hit_n':True},'effect_invalid_count'),
                            ({'observed_n':0},'effect_invalid_denominator'),
                            ({'null_n':100},'effect_invalid_count'),
                            ({'total_n':3},'effect_insufficient_sample')):
            row=deepcopy(src)
            row['high'].update(change)
            with self.subTest(change=change):self.denied(code,_effect,row,effect_policy())

    def test_no_implicit_posthoc_changes_to_policy(self):
        for changes in ({'policy_version':''},{'design_sha256':'x'*64},
                        {'method_version':'!'}, {'minimum_observed_per_role':0},
                        {'minimum_observed_per_role':True},{'decimal_places':13},
                        {'decimal_places':True},{'direction':'low_minus_high'},
                        {'estimator':'weighted_implicit'},{'zero_baseline_ratio':'infinity'},
                        {'numeric_shift':'wasserstein_guessed'}):
            with self.subTest(changes=changes):self.denied('invalid_effect_policy',effect_policy,**changes)
        self.denied('invalid_effect_policy',build_candidate_descriptive_effects,*self.refs(),{})
        self.denied('effect_policy_mismatch',self.estimate,effect_policy(design_sha256='0'*64))
        self.denied('effect_insufficient_sample',self.estimate,effect_policy(minimum_observed_per_role=2))

    def test_same_run_reproducible_and_method_precision_changes_identity(self):
        x=self.estimate()
        self.assertEqual(x,self.estimate())
        self.assertEqual(x.artifact_sha256,canonical_hash(x.as_dict()))
        self.assertEqual(x, replay_candidate_descriptive_effects(x.to_json(),x.artifact_sha256,*self.refs()))
        self.assertEqual(x,replay_candidate_descriptive_effects(x.to_json().encode(),x.artifact_sha256,*self.refs()))
        self.assertNotEqual(self.estimate(effect_policy(method_version='descriptive/v2')).artifact_sha256,x.artifact_sha256)
        self.assertNotEqual(self.estimate(effect_policy(decimal_places=7)).artifact_sha256,x.artifact_sha256)

    def test_tamper_and_resealing_rejected(self):
        x=self.estimate()
        self.denied('effect_hash_mismatch',replay_candidate_descriptive_effects,
                    x.to_json(),'0'*64,*self.refs())
        forged=x.as_dict()
        forged['effects'][2]['risk_difference']['numerator']=999
        self.denied('effect_replay_mismatch',replay_candidate_descriptive_effects,
                    json.dumps(forged),canonical_hash(forged),*self.refs())
        self.denied('duplicate_json_key',replay_candidate_descriptive_effects,
                    '{"x":1,"x":2}','0'*64,*self.refs())
        self.denied('invalid_effect_artifact',replay_candidate_descriptive_effects,
                    '{"x":NaN}','0'*64,*self.refs())
        forged=x.as_dict();forged['approved_live']=True
        self.denied('unsupported_effect_artifact',replay_candidate_descriptive_effects,
                    json.dumps(forged),canonical_hash(forged),*self.refs())
        forged=x.as_dict();forged['effects'].pop()
        self.denied('effect_replay_mismatch',replay_candidate_descriptive_effects,
                    json.dumps(forged),canonical_hash(forged),*self.refs())

    def test_input_missing_or_cross_study_cannot_be_used(self):
        args=list(self.refs());args[6]=object()
        self.denied('invalid_effect_input',build_candidate_descriptive_effects,*args,effect_policy())
        self.denied('invalid_effect_policy',build_candidate_descriptive_effects,*self.refs(),None)
        fake=replace(self.discovery,project_id='b'*32)
        args=list(self.refs());args[6]=fake
        self.denied('effect_source_mismatch',build_candidate_descriptive_effects,*args,effect_policy())

    def test_no_claims_publication_probability_or_causality(self):
        obj=self.estimate().as_dict()
        self.assertEqual(obj['warnings'],['exploratory_descriptive_only','not_live_authorized',
            'matched_pair_dependence_not_modeled','unweighted_no_causal_inference',
            'no_uncertainty_interval','multiple_testing_uncorrected','selection_bias_unknown'])
        for prohibited in ('publish_insight','calculate_p_values','calculate_ci','claim_causal_uplift',
                           'mark_verified','approve_live','run_statistical_test'):
            self.assertFalse(hasattr(self.estimate(),prohibited))
        self.assertFalse(obj['causal_claim'])


if __name__=='__main__':unittest.main()
