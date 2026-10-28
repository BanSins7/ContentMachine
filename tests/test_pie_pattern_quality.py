"""PIE-MOD-24 synthetic quality gate and replay tests; upstream replay MOCKED.

Code executes real source-status mapping and full 11-axis gate. Not UAE
integration, not executed regression QA or genuine reviewer authorization.
"""
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from intelligence.patterns_insights.contracts import PIEBoundaryError,canonical_hash
from intelligence.patterns_insights.research.pattern_quality import (
    PatternQualityPolicy,_RUBRIC,_OUTCOMES,_grade_factor,
    build_candidate_pattern_quality_report,replay_candidate_pattern_quality_report)
from intelligence.patterns_insights.research.robustness import build_candidate_robustness_report
from test_pie_robustness import _base,_json


def _fixture():
    sources=list(_base())              # stage22 source; stages10/11/17 replay mocked in _base
    with patch('intelligence.patterns_insights.research.robustness.replay_candidate_technical_numeric_report',
               return_value=sources[9]):
        robustness=build_candidate_robustness_report(*sources)
    sources.append(robustness)          # stage23 candidate report at index11
    policy=PatternQualityPolicy(
        'pattern-quality-v1','explicit-gates-v1',sources[8].design_sha256,
        sources[0].snapshot_sha256,sources[3].artifact_sha256,
        sources[4].artifact_sha256,sources[5].artifact_sha256,
        sources[9].artifact_sha256,robustness.artifact_sha256,
        '2026-10-09T00:00:00Z','unverified-registration-v1',
        'unverified-review-protocol-v1')
    sources.append(policy)              # stage24 policy at index12
    return tuple(sources)


class QualityDecisionPureTests(unittest.TestCase):
    def setUp(self):
        self.v=_fixture()
        self.policy=self.v[12]
        self.numeric=self.v[9].as_dict()['factors'][0]
        self.robustness=self.v[11].as_dict()['factor_sensitivities'][0]
        self.n=self.v[9].as_dict()['discovery_pair_count']

    def evaluate(self,robust_status,numeric=None,sensitivity=None):
        record={**self.robustness,'status':robust_status,
                'sensitivity':(self.robustness.get('sensitivity')
                               if sensitivity is None else sensitivity)}
        return _grade_factor(self.numeric if numeric is None else numeric,
                             record,self.n,self.policy)

    def reject(self,code,fn,*args,**kw):
        with self.assertRaises(PIEBoundaryError) as c:fn(*args,**kw)
        self.assertEqual(c.exception.code,code)

    def test_stable_descriptive_is_exploratory_not_supported(self):
        output=self.evaluate('directionally_robust_descriptive_only')
        self.assertEqual(output['decision'],'exploratory')
        self.assertEqual(output['transition']['from'],'unassessed')
        self.assertEqual(output['transition']['to'],'exploratory')
        self.assertFalse(output['eligible_for_supported'])
        self.assertFalse(output['eligible_for_release'])
        self.assertIsNone(output['quality_score'])

    def test_negative_control_failure_rejected(self):
        result=self.evaluate('fragile_negative_control_high_exceedance')
        self.assertEqual(result['decision'],'rejected')
        self.assertEqual(result['recommended_action'],'exclude_as_fragile')

    def test_alternate_exposure_direction_flip_rejected(self):
        result=self.evaluate('fragile_alternate_exposure_definition')
        self.assertEqual(result['decision'],'rejected')
        self.assertEqual(result['transition']['reason_code'],'predeclared_robustness_failure')

    def test_leave_creator_out_and_fold_flip_rejected(self):
        for status in ('fragile_leave_creator_out_or_outlier_flip',
                       'fragile_discovery_cv_fold_direction'):
            self.assertEqual(self.evaluate(status)['decision'],'rejected')

    def test_cross_cv_fold_and_insufficient_creators_needs_review(self):
        for status in ('blocked_matched_pair_crosses_discovery_cv_folds',
                       'blocked_insufficient_support_or_zero_baseline',
                       'blocked_insufficient_discovery_cv_fold_pairs'):
            self.assertEqual(self.evaluate(status)['decision'],'needs_review')

    def test_blocked_upstream_numeric_needs_review(self):
        numeric={**self.numeric,'status':'blocked_incomplete_or_sparse_bin',
                 'matched_mean_raw_high_minus_low':None}
        record={**self.robustness,'status':'blocked_upstream_numeric_or_missing',
                'sensitivity':None}
        self.assertEqual(_grade_factor(numeric,record,self.n,self.policy)['decision'],
                         'needs_review')

    def test_11_axes_complete_and_inferential_axes_blocked(self):
        x=self.evaluate('directionally_robust_descriptive_only')
        dimensions=x['quality_dimensions']
        self.assertEqual(tuple(d['axis'] for d in dimensions),_RUBRIC)
        states={d['axis']:d['state'] for d in dimensions}
        self.assertEqual(states['sample_support'],'descriptive_pass')
        self.assertEqual(states['effect_magnitude_and_direction'],'descriptive_pass')
        self.assertEqual(states['calibrated_uncertainty'],'blocked')
        self.assertEqual(states['multiple_testing'],'blocked')
        self.assertEqual(states['holdout_confirmation'],'blocked')
        self.assertEqual(states['source_identity_and_rights'],'unverified')
        self.assertEqual(states['reviewer_authorization'],'unverified')
        self.assertEqual(states['confounding_and_c7_growth'],'unverified')

    def test_no_arbitrary_quality_weights_or_score(self):
        d=self.evaluate('directionally_robust_descriptive_only')
        self.assertIsNone(d['quality_score'])
        self.assertEqual(d['quality_dimensions'][1]['evidence']['minimum_effect_threshold'],
                         'none_no_post_selection')
        self.reject('invalid_quality_policy',replace,self.policy,
                    weighting='weighted_0_100')

    def test_forged_marginal_significance_rejected(self):
        for field,value in (('p_value','0.01'),('adjusted_q_value','0.02'),
                            ('uncertainty_interval',{'low':'1','high':'2'}),
                            ('confirmed_effect',True)):
            n={**self.numeric,field:value}
            self.reject('quality_inference_forgery',self.evaluate,
                        'directionally_robust_descriptive_only',n)

    def test_forged_supported_source_stage23_rejected(self):
        r={**self.robustness,'status':'supported'}
        self.reject('quality_factor_mismatch',_grade_factor,self.numeric,r,self.n,self.policy)
        r={**self.robustness,'status':'directionally_robust_descriptive_only',
           'may_promote_as_universal_pattern':True}
        self.reject('quality_publication_forgery',_grade_factor,self.numeric,r,self.n,self.policy)

    def test_wrong_factor_or_pair_count_refused(self):
        r={**self.robustness,'factor_id':'invented'}
        self.reject('quality_factor_mismatch',_grade_factor,self.numeric,r,self.n,self.policy)
        self.reject('quality_factor_mismatch',_grade_factor,self.numeric,self.robustness,0,self.policy)

    def test_candidate_only_taxonomy_contains_reserved_supported(self):
        self.assertEqual(_OUTCOMES,('exploratory','supported','rejected','needs_review'))
        self.assertEqual(self.policy.decision_taxonomy,_OUTCOMES)
        self.reject('invalid_quality_policy',replace,self.policy,
                    release_gate='auto_publish_if_stable')
        self.reject('invalid_quality_policy',replace,self.policy,
                    evidence_authentication='assume_verified')

    def test_changed_axis_order_or_claim_does_not_give_authority(self):
        self.reject('invalid_quality_policy',replace,self.policy,
                    rubric_axes=tuple(reversed(_RUBRIC)))
        changed=replace(self.policy,review_protocol_claim_id='signed-off-says-approved')
        d=_grade_factor(self.numeric,
                        {**self.robustness,'status':'directionally_robust_descriptive_only'},
                        self.n,changed)
        self.assertFalse(d['eligible_for_supported'])


class QualityArtifactSyntheticTests(unittest.TestCase):
    def setUp(self): self.v=_fixture()

    def edit(self,index,item,values=None):
        a=list(self.v if values is None else values)
        a[index]=item
        return tuple(a)

    def build(self,values=None):
        v=self.v if values is None else values
        with patch('intelligence.patterns_insights.research.pattern_quality.replay_candidate_robustness_report',
                   return_value=v[11]):
            return build_candidate_pattern_quality_report(*v)

    def replay(self,raw,sha,values=None):
        v=self.v if values is None else values
        with patch('intelligence.patterns_insights.research.pattern_quality.replay_candidate_robustness_report',
                   return_value=v[11]):
            return replay_candidate_pattern_quality_report(raw,sha,*v[:12])

    def reject(self,code,fun,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as c:fun(*args,**kwargs)
        self.assertEqual(c.exception.code,code)

    def test_full_frozen_family_and_never_supported(self):
        result=self.build()
        data=result.as_dict()
        self.assertEqual(data['registered_factor_count'],5)
        self.assertEqual(data['registered_factor_count'],len(data['decisions']))
        self.assertEqual(data['decision_counts']['supported'],0)
        self.assertEqual(sum(data['decision_counts'].values()),5)
        self.assertEqual(len(data['rubric_axes']),11)
        self.assertFalse(data['supported_state_reachable_with_current_source'])
        self.assertFalse(data['can_release_to_registry_as_supported'])
        self.assertFalse(data['can_publish_patterns'])
        self.assertFalse(data['source_rights_authenticated'])
        self.assertFalse(data['authentic_reviewer_approval'])
        self.assertFalse(data['independent_holdout_confirmed'])
        self.assertFalse(data['calibrated_multiple_testing_p_values_available'])
        self.assertFalse(result.verified_live)
        self.assertFalse(result.ready_for_analysis)

    def test_no_unverified_external_p_values_or_promotion(self):
        result=self.build().as_dict()
        for decision in result['decisions']:
            self.assertIn(decision['decision'],('exploratory','rejected','needs_review'))
            self.assertFalse(decision['eligible_for_release'])
            self.assertIsNone(decision['quality_score'])
            self.assertEqual(decision['transition']['to'],decision['decision'])
        self.assertIsNone(result['confidence_intervals'])
        self.assertFalse(result['causal_claim'])

    def test_rejected_and_blocked_family_members_are_not_dropped(self):
        raw=self.v[11].as_dict()
        raw['factor_sensitivities'][0]['status']='fragile_negative_control_high_exceedance'
        raw['factor_sensitivities'][1]['status']='blocked_insufficient_discovery_cv_fold_pairs'
        r=replace(self.v[11],_json=_json(raw))
        vals=self.edit(11,r)
        d=self.build(vals).as_dict()
        self.assertEqual(d['decisions'][0]['decision'],'rejected')
        self.assertEqual(d['decisions'][1]['decision'],'needs_review')
        self.assertEqual(d['registered_factor_count'],5)
        self.assertEqual(d['decision_counts']['rejected'],1)
        self.assertGreaterEqual(d['decision_counts']['needs_review'],1)

    def test_source_and_policy_pins_checked(self):
        p=self.v[12]
        for field in ('design_sha256','snapshot_sha256','controls_sha256','matrix_sha256',
                      'holdout_sha256','technical_report_sha256','robustness_report_sha256'):
            self.reject('quality_lineage_mismatch',self.build,
                        self.edit(12,replace(p,**{field:'f'*64})))

    def test_fake_p_values_rejected_even_with_mocked_stage23_replay(self):
        raw=self.v[11].as_dict()
        raw['valid_inferential_p_value_count']=1
        altered=self.edit(11,replace(self.v[11],_json=_json(raw)))
        self.reject('quality_inference_forgery',self.build,altered)

    def test_fake_stage23_promotion_is_detected(self):
        raw=self.v[11].as_dict()
        raw['factor_sensitivities'][0]['may_promote_as_universal_pattern']=True
        altered=self.edit(11,replace(self.v[11],_json=_json(raw)))
        self.reject('quality_publication_forgery',self.build,altered)

    def test_reordered_source_hypotheses_rejected(self):
        raw=self.v[11].as_dict()
        raw['factor_sensitivities']=list(reversed(raw['factor_sensitivities']))
        altered=self.edit(11,replace(self.v[11],_json=_json(raw)))
        self.reject('quality_family_mismatch',self.build,altered)

    def test_reg_after_discovery_refused(self):
        p=replace(self.v[12],registered_at='2026-10-16T00:00:00Z')
        self.reject('quality_post_selection',self.build,self.edit(12,p))

    def test_immutable_deterministic_raw_bytes_replay(self):
        r=self.build()
        self.assertEqual(r.to_json(),self.build().to_json())
        self.assertEqual(self.replay(r.to_json(),r.artifact_sha256),r)
        self.assertEqual(self.replay(r.to_json().encode(),r.artifact_sha256),r)

    def test_tamper_rehashed_decision_replay_rejected(self):
        a=self.build();data=a.as_dict()
        data['decisions'][0]['decision']='supported'
        self.reject('quality_replay_mismatch',self.replay,_json(data),canonical_hash(data))

    def test_original_source_change_replay_rejected(self):
        a=self.build();raw=self.v[11].as_dict()
        first=raw['factor_sensitivities'][0]
        first['status']='fragile_negative_control_high_exceedance'
        changed=self.edit(11,replace(self.v[11],_json=_json(raw)))
        self.reject('quality_replay_mismatch',self.replay,a.to_json(),a.artifact_sha256,changed)

    def test_duplicate_json_nan_invalid_utf8_replay_refused(self):
        self.reject('duplicate_json_key',self.replay,'{"a":1,"a":2}','0'*64)
        self.reject('invalid_quality_artifact',self.replay,'{"x":NaN}','0'*64)
        self.reject('invalid_quality_artifact',self.replay,b'\xff','0'*64)

    def test_wrong_hash_invalid_schema_and_false_live_rejected(self):
        a=self.build()
        self.reject('quality_hash_mismatch',self.replay,a.to_json(),'f'*64)
        fake=a.as_dict()
        fake['approved_live']=True
        self.reject('unsupported_quality_artifact',self.replay,_json(fake),canonical_hash(fake))
        fake=a.as_dict();fake['schema_version']='pie-pattern-quality-gates-candidate/v9'
        self.reject('unsupported_quality_artifact',self.replay,_json(fake),canonical_hash(fake))

    def test_replay_mismatched_canonical_whitespace_rejected(self):
        a=self.build()
        self.reject('quality_replay_mismatch',self.replay,
                    json.dumps(a.as_dict(),indent=2),a.artifact_sha256)

if __name__=='__main__':
    unittest.main()
