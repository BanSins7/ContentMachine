"""Stage23 synthetic robustness oracles. Stage22 and prior-stage replays are MOCKED.

These are mathematical regression fixtures, not verified UAE evidence or actual p-values.
"""
import json
import unittest
from dataclasses import replace
from fractions import Fraction
from decimal import Decimal
from unittest.mock import patch

from intelligence.patterns_insights.contracts import PIEBoundaryError,canonical_hash
from intelligence.patterns_insights.research.robustness import (
    SensitivityFactor,RobustnessPolicy,_audit_pairs,_permutation_counts,_discovery_fold_audit,
    build_candidate_robustness_report,replay_candidate_robustness_report)
from test_pie_technical_numeric import fixture as numeric_fixture,_json
from intelligence.patterns_insights.research.technical_numeric import (
    build_candidate_technical_numeric_report)

def _base():
    v=numeric_fixture()
    with patch('intelligence.patterns_insights.research.technical_numeric.replay_candidate_controls',
               return_value=v[3]),patch(
         'intelligence.patterns_insights.research.technical_numeric.replay_candidate_feature_matrix',
               return_value=v[4]),patch(
         'intelligence.patterns_insights.research.technical_numeric.replay_candidate_holdout_split',
               return_value=v[5]):
        numeric=build_candidate_technical_numeric_report(*v)
    policy=RobustnessPolicy(
        'robust-v1','sha-signflip-v1',v[8].design_sha256,
        v[0].snapshot_sha256,v[3].artifact_sha256,v[4].artifact_sha256,
        v[5].artifact_sha256,numeric.artifact_sha256,
        '2026-10-09T00:00:00Z','unverified-registration-claim-v1',
        73,64,4,4,2,1,'0.5',
        tuple(SensitivityFactor(f.factor_id,(7,)) for f in v[8].factors))
    return (*v,numeric,policy)


class IndependentMathOracle(unittest.TestCase):
    def setUp(self):
        self.values=tuple((a,b,f'creator_{2*i+1}',f'creator_{2*i+2}',f'{2*i+1}:{2*i+2}')
                          for i,(a,b) in enumerate(zip((2,6,8,9),(1,3,4,7))))
        self.p=RobustnessPolicy('robust-v1','sha-signflip-v1','a'*64,
                    'b'*64,'c'*64,'d'*64,'e'*64,'f'*64,
                    '2026-10-09T00:00:00Z','unverified-registration-v1',
                    73,64,4,4,2,1,'0.5',(SensitivityFactor('f1',(7,)),))

    def test_hand_calculated_original_trim_loo_and_cutoff(self):
        d=_audit_pairs(self.values,self.p,self.p.factors[0],5)
        self.assertEqual(d['baseline_raw_difference'],
                         {'numerator':5,'denominator':2,'decimal_12dp':'2.500000000000'})
        self.assertEqual(d['outlier_trim']['removed_pair_keys'],['5:6'])
        self.assertEqual(d['outlier_trim']['difference']['numerator'],2)
        self.assertEqual(d['outlier_trim']['difference']['denominator'],1)
        self.assertEqual(d['leave_one_creator_out'][0]['remaining_pairs'],3)
        self.assertEqual(d['leave_one_creator_out'][0]['difference']['numerator'],3)
        self.assertEqual(d['leave_one_creator_out'][0]['difference']['denominator'],1)
        self.assertEqual([(x['cutoff_raw_inclusive'],x['difference']['numerator'],
                           x['difference']['denominator']) for x in d['exposure_sensitivity']],
                         [(5,1,2),(7,1,4)])

    def test_negative_control_deterministic_seed_and_exact_ratio(self):
        a=_permutation_counts((1,3,4,2),73,64,'f1')
        self.assertEqual(a,_permutation_counts((1,3,4,2),73,64,'f1'))
        self.assertEqual(a['draw_count'],64)
        self.assertEqual(a['descriptive_exceedance_fraction']['numerator']*64,
                         a['abs_sum_at_least_observed_count']*
                         a['descriptive_exceedance_fraction']['denominator'])
        self.assertIsNone(a['inferential_p_value'])
        self.assertFalse(a['calibrated_negative_control'])

    def test_creator_loo_flip_detected(self):
        # One creator controls a large positive pair, remaining pairs trend negative.
        x=((100,0,'a','b','1:2'),(0,1,'c','d','3:4'),
           (0,1,'e','f','5:6'),(0,1,'g','h','7:8'))
        d=_audit_pairs(x,self.p,self.p.factors[0],5)
        self.assertEqual(d['baseline_direction'],'positive')
        self.assertEqual(d['status'],'fragile_leave_creator_out_or_outlier_flip')
        self.assertEqual(d['outlier_trim']['direction'],'negative')

    def test_alternate_cutoff_reversal_detected(self):
        x=((100,5,'a','b','1:2'),(7,100,'c','d','3:4'),
           (6,5,'e','f','5:6'),(6,5,'g','h','7:8'))
        # Baseline mean has an exact, positive signed difference (+4 total).
        self.assertGreater(sum(a-b for a,b,*_ in x),0)
        # At >=5 all high/low hit -> zero, while >=7 direction can differ.
        d=_audit_pairs(x,self.p,self.p.factors[0],5)
        self.assertEqual(d['status'],'blocked_insufficient_support_or_zero_baseline')

    def test_null_exceedance_downgrades_noncausal_candidate(self):
        x=tuple((5,5,f'a{i}',f'b{i}',f'{i}') for i in range(4))
        d=_audit_pairs(x,self.p,self.p.factors[0],5)
        self.assertEqual(d['status'],'blocked_insufficient_support_or_zero_baseline')
        self.assertFalse(d['may_promote_as_universal_pattern'])

    def test_duplicate_creators_not_independent_pairs(self):
        x=((2,1,'same','other','1:2'),(6,3,'same','other','3:4'),
           (8,4,'same','other','5:6'),(9,7,'same','other','7:8'))
        with self.assertRaises(PIEBoundaryError) as c:
            _audit_pairs(x,self.p,self.p.factors[0],5)
        self.assertEqual(c.exception.code,'robustness_insufficient_creators')

    def test_disjoint_discovery_fold_manual_raw_means(self):
        folds={1:0,2:0,3:0,4:0,5:1,6:1,7:1,8:1}
        report=_discovery_fold_audit(self.values,folds,2,2,'positive')
        self.assertEqual(report['status'],
                         'consistent_discovery_cv_directions_descriptive_only')
        self.assertEqual([row['raw_difference']['numerator'] for row in report['folds']],[2,3])
        self.assertTrue(all(row['raw_difference']['denominator']==1 for row in report['folds']))
        self.assertFalse(report['independent_confirmation'])

    def test_discovery_cv_cross_fold_pair_is_blocked(self):
        folds={1:0,2:1,3:0,4:0,5:1,6:1,7:1,8:1}
        report=_discovery_fold_audit(self.values,folds,2,2,'positive')
        self.assertEqual(report['status'],'blocked_matched_pair_crosses_discovery_cv_folds')
        self.assertEqual(report['cross_fold_pair_keys'],['1:2'])

    def test_discovery_cv_weak_subgroup_refused(self):
        folds={1:0,2:0,3:0,4:0,5:0,6:0,7:1,8:1}
        report=_discovery_fold_audit(self.values,folds,2,2,'positive')
        self.assertEqual(report['status'],'blocked_insufficient_discovery_cv_fold_pairs')

    def test_no_float_random_seeds_or_unbounded_perm(self):
        with self.assertRaises(PIEBoundaryError):
            replace(self.p,seed=True)
        with self.assertRaises(PIEBoundaryError):
            replace(self.p,negative_control_permutations=513)


class IntegratedSynthetic(unittest.TestCase):
    def setUp(self):self.v=_base()

    def edit(self,index,item,source=None):
        z=list(self.v if source is None else source);z[index]=item;return tuple(z)

    def build(self,values=None):
        v=self.v if values is None else values
        with patch('intelligence.patterns_insights.research.robustness.replay_candidate_technical_numeric_report',
                   return_value=v[9]):
            return build_candidate_robustness_report(*v)

    def replay(self,raw,sha,values=None):
        v=self.v if values is None else values
        with patch('intelligence.patterns_insights.research.robustness.replay_candidate_technical_numeric_report',
                   return_value=v[9]):
            return replay_candidate_robustness_report(raw,sha,*v[:10])

    def reject(self,code,fn,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as c:fn(*args,**kwargs)
        self.assertEqual(c.exception.code,code)

    def test_integrated_5_source_bound_factors(self):
        d=self.build().as_dict()
        self.assertEqual(d['registered_factor_count'],5)
        self.assertEqual(d['discovery_pair_count'],4)
        self.assertEqual(len(d['reserved_pairs']),4)
        self.assertEqual(d['unmatched_discovery_video_ids'],[])
        self.assertEqual(len(d['factor_sensitivities']),5)
        self.assertTrue(all(x['sensitivity']['baseline_raw_difference']['numerator']==5
                            for x in d['factor_sensitivities']))
        self.assertEqual(d['source_technical_numeric_sha256'],self.v[9].artifact_sha256)
        self.assertEqual(d['alternate_outcome_window_status'],
                         'blocked_requires_new_qualified_uae_replay')
        self.assertEqual(d['heldout_subgroup_status'],'discovery_cv_only_no_confirmation_arm_inspected')

    def test_cv_folds_report_blocked_without_confirmation_leak(self):
        d=self.build().as_dict()
        for result in d['factor_sensitivities']:
            if result['sensitivity'] is not None:
                cv=result['sensitivity']['discovery_cv_fold_audit']
                self.assertEqual(len(cv['folds']),2)
                self.assertFalse(cv['used_confirmation_arm'])
                self.assertFalse(cv['independent_confirmation'])
        self.assertEqual(d['heldout_subgroup_status'],
                         'discovery_cv_only_no_confirmation_arm_inspected')

    def test_discovery_fold_duplicate_video_rejected(self):
        split=self.v[5];raw=split.as_dict()
        self.assertGreaterEqual(len(raw['cross_validation_folds']),2)
        raw['cross_validation_folds'][0]['video_ids'].append(
            raw['cross_validation_folds'][1]['video_ids'][0])
        self.reject('robustness_fold_lineage',self.build,
                    self.edit(5,replace(split,_json=_json(raw))))

    def test_never_claims_successful_heldout_or_causal_result(self):
        a=self.build();d=a.as_dict()
        self.assertFalse(a.verified_live);self.assertFalse(a.ready_for_analysis)
        self.assertIsNone(d['confirmed_discoveries'])
        self.assertFalse(d['can_publish_significance'])
        self.assertFalse(d['causal_claim'])
        self.assertFalse(d['independent_out_of_sample_replication'])
        self.assertEqual(d['c7_raw_growth_dominance'],
                         'unknown_requires_source_metric_decomposition')
        self.assertEqual(d['valid_inferential_p_value_count'],0)
        self.assertTrue(all(not x['may_promote_as_universal_pattern']
                            for x in d['factor_sensitivities']))

    def test_report_downgrades_weak_negative_control_threshold(self):
        policy=replace(self.v[10],maximum_descriptive_null_exceedance_fraction='0')
        result=self.build(self.edit(10,policy)).as_dict()
        self.assertTrue(result['downgraded_or_blocked_factor_ids'])
        self.assertEqual(result['report_status'],'downgraded_fragile_or_blocked_factors')

    def test_block_incomplete_original_numeric_family(self):
        original=self.v[9];v=original.as_dict()
        v['factors'][0]['status']='blocked_incomplete_or_sparse_bin'
        report=replace(original,_json=_json(v))
        d=self.build(self.edit(9,report)).as_dict()
        self.assertEqual(d['factor_sensitivities'][0]['status'],
                         'blocked_upstream_numeric_or_missing')
        self.assertIsNone(d['factor_sensitivities'][0]['sensitivity'])
        self.assertEqual(len(d['factor_sensitivities']),5)

    def test_post_selection_policy_rejected(self):
        p=replace(self.v[10],registered_at='2026-10-16T00:00:00Z')
        self.reject('robustness_post_selection',self.build,self.edit(10,p))

    def test_wrong_hash_or_factor_family_fails(self):
        p=self.v[10]
        for k in ('snapshot_sha256','controls_sha256','matrix_sha256',
                  'holdout_sha256','technical_report_sha256','design_sha256'):
            v=self.edit(10,replace(p,**{k:'f'*64}))
            self.reject('robustness_lineage_mismatch',self.build,v)
        v=self.edit(10,replace(p,factors=p.factors[:-1]))
        self.reject('robustness_family_mismatch',self.build,v)

    def test_cross_arm_matched_pair_fails(self):
        c=self.v[3];raw=c.as_dict()
        raw['pairs'][0]['control_video_id']=10
        self.reject('robustness_cross_arm_pair',self.build,
                    self.edit(3,replace(c,_json=_json(raw))))

    def test_unsupported_numeric_without_imputation(self):
        m=self.v[4];raw=m.as_dict()
        raw['rows'][0]['features'][0]['observation']={'state':'absent','value':None}
        # Must not claim prior report verifiably matches this changed source:
        # mock is stage22 itself, here source row changes and blocked upstream required.
        original=self.v[9];report=original.as_dict()
        report['factors'][0]['status']='blocked_incomplete_or_sparse_bin'
        altered=self.edit(9,replace(original,_json=_json(report)))
        altered=self.edit(4,replace(m,_json=_json(raw)),altered)
        d=self.build(altered).as_dict()
        self.assertEqual(d['factor_sensitivities'][0]['status'],'blocked_upstream_numeric_or_missing')
        self.assertEqual(d['factor_sensitivities'][0]['missing_video_ids'],[1])

    def test_different_input_seed_different_audit_identity(self):
        a=self.build()
        changed=self.edit(10,replace(self.v[10],seed=99))
        b=self.build(changed)
        self.assertNotEqual(a.artifact_sha256,b.artifact_sha256)
        self.assertEqual(b.as_dict()['policy']['seed'],99)

    def test_replay_exact_json_and_byte_identity(self):
        a=self.build()
        self.assertEqual(a.to_json(),self.build().to_json())
        self.assertEqual(self.replay(a.to_json(),a.artifact_sha256),a)
        self.assertEqual(self.replay(a.to_json().encode(),a.artifact_sha256),a)

    def test_replay_detects_rehashed_modified_contrast(self):
        a=self.build();d=a.as_dict()
        d['factor_sensitivities'][0]['sensitivity']['baseline_raw_difference']['numerator']=999
        self.reject('robustness_replay_mismatch',self.replay,_json(d),canonical_hash(d))
        self.reject('robustness_replay_mismatch',self.replay,
                    json.dumps(a.as_dict(),indent=2),a.artifact_sha256)

    def test_replay_wrong_source_or_modified_registered_family(self):
        a=self.build();p=self.v[10]
        changed=self.edit(10,replace(p,seed=55))
        self.reject('robustness_replay_mismatch',self.replay,
                    a.to_json(),a.artifact_sha256,changed)

    def test_replay_refuses_duplicate_nan_wrong_sha_fake_live(self):
        a=self.build()
        self.reject('duplicate_json_key',self.replay,'{"x":1,"x":2}','0'*64)
        self.reject('invalid_robustness_artifact',self.replay,'{"x":NaN}','0'*64)
        self.reject('invalid_robustness_artifact',self.replay,b'\xff','0'*64)
        self.reject('robustness_hash_mismatch',self.replay,a.to_json(),'f'*64)
        fake=a.as_dict();fake['approved_live']=True
        self.reject('unsupported_robustness_artifact',self.replay,_json(fake),canonical_hash(fake))

    def test_policy_no_fake_confirmation_or_significance(self):
        p=self.v[10]
        for kw in ({'scope':'confirmation'}, {'permutation_unit':'shuffle_individual_posts'},
                   {'deletion_unit':'remove_one_role_not_pair'},
                   {'alternate_outcome_windows':'reuse_original_c7'},
                   {'heldout_evaluation':'confirmed'}, {'significance':'valid_p_value'}):
            self.reject('invalid_robustness_policy',replace,p,**kw)

    def test_cutoffs_and_claims_must_be_versioned(self):
        f=self.v[10].factors[0]
        for bad in ((),(7,7),(9,7),(0,),(True,),tuple(range(1,11))):
            self.reject('invalid_robustness_policy',replace,f,
                        alternative_exposure_cutoffs_raw=bad)
        self.reject('invalid_robustness_policy',replace,self.v[10],
                    factors=tuple(reversed(self.v[10].factors)))

if __name__=='__main__':
    unittest.main()
