"""PIE-MOD-15 synthetic independent bootstrap oracles and fail-closed cases.

Synthetic 8-pair fixtures use patched replay solely to test numerical mechanics.
They NEVER demonstrate trusted UAE, reviewer authorization or end-to-end E2E.
"""
import json
import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from intelligence.patterns_insights.contracts import PIEBoundaryError, canonical_hash
from intelligence.patterns_insights.research.effects import (
    CandidateDescriptiveEffects, EffectEstimationPolicy, _effect,
)
from intelligence.patterns_insights.research.uncertainty import (
    SCHEMA, UncertaintyPolicy, CandidateUncertaintyIntervals,
    build_candidate_uncertainty_intervals, replay_candidate_uncertainty_intervals,
    _draw_indices, _binary_stats, _numeric_stats, _bounds,
)
from fractions import Fraction
from decimal import Decimal


class View:
    def __init__(self, data):
        self.data=data
        self.project_id='a'*32
        self.artifact_sha256=canonical_hash(data)
        self.included_video_ids=tuple(x['video_id'] for x in data.get('selection',{}).get('included',[]))
    def as_dict(self):
        return deepcopy(self.data)


def synthetic_fixture(*, repeat_creator=False, missing=False, vary=False):
    """Not signed: isolated arithmetic fixture, never eligible live evidence."""
    project='a'*32
    included=[]; pairs=[]; matrix=[]
    high_durations=[];low_durations=[];high_flags=[];low_flags=[]
    for i in range(8):
        hi=101+i*2;lo=hi+1
        creator_hi=('repeat' if repeat_creator else f'h{i}')
        for video_id, creator in ((hi,creator_hi),(lo,f'l{i}')):
            included.append({'video_id':video_id,'row':{'creator_key':creator}})
        pairs.append({'case_video_id':hi,'control_video_id':lo})
        numeric_hi=20+i if vary else 20
        numeric_lo=10+i if vary else 10
        flag_hi=(i<6 if vary else True)
        flag_lo=(i==0 if vary else False)
        high_durations.append(numeric_hi);low_durations.append(numeric_lo)
        high_flags.append(flag_hi);low_flags.append(flag_lo)
        for video,role,num,flag in ((hi,'high',numeric_hi,flag_hi),(lo,'low',numeric_lo,flag_lo)):
            obs=[{'observation':{'state':'present','value':num}},
                 {'observation':{'state':'null' if missing and role=='low' and i==0 else 'present',
                                 'value':None if missing and role=='low' and i==0 else flag}}]
            matrix.append({'video_id':video,'role':role,'pair_index':i,'features':obs})
    boundaries=[15]
    def arm_numeric(seq):
        s=sorted(seq)
        median=(Decimal(s[3])+Decimal(s[4]))/2
        bins=[sum(x<15 for x in seq),sum(x>=15 for x in seq)]
        return {'total_n':8,'observed_n':8,'null_n':0,'absent_n':0,
                'bin_counts':bins,'hit_n':None,'median_raw':format(median,'f')}
    def arm_binary(seq):
        return {'total_n':8,'observed_n':8,'null_n':0,'absent_n':0,
                'bin_counts':None,'hit_n':sum(seq),'median_raw':None}
    numeric={'hypothesis_id':'a_duration','family':'a','feature_name':'duration_sec',
             'analysis':'numeric_distribution','target':None,'numeric_boundaries':boundaries}
    flag={'hypothesis_id':'a_measured','family':'a','feature_name':'measured',
          'analysis':'boolean_equals','target':True,'numeric_boundaries':[]}
    hypotheses=[{'hypothesis':numeric,'high':arm_numeric(high_durations),'low':arm_numeric(low_durations)},
                {'hypothesis':flag,'high':arm_binary(high_flags),'low':arm_binary(low_flags)}]
    effect_policy=EffectEstimationPolicy('eff/v1','c'*64,'desc/v1',1,6)
    effects=[_effect(h,effect_policy,numeric_unit='seconds' if i==0 else None) for i,h in enumerate(hypotheses)]
    d={'hypotheses':hypotheses,'hypothesis_family_sha256':'b'*64,'registered_hypothesis_count':2}
    e={'project_id':project,'evidence_state':'unverified','approved_live':False,
       'policy':{'design_sha256':'c'*64,'decimal_places':6},'effect_count':2,'effects':effects}
    raw=json.dumps(e,sort_keys=True,separators=(',',':'))
    effect_obj=CandidateDescriptiveEffects(sha256(raw.encode()).hexdigest(),project,'d'*64,'e'*64,2,raw)
    return (View({'selection':{'included':included}}),None,None,View({'pairs':pairs}),
            View({'columns':[{'family':'a','name':'duration_sec'},
                              {'family':'a','name':'measured'}],'rows':matrix}),None,View(d),effect_obj)


def policy(**kw):
    data={'policy_version':'boot/v1','method_version':'paired-sha256/v1',
          'design_sha256':'c'*64,'seed':20261027,'resamples':400,
          'minimum_independent_pairs':8}
    data.update(kw)
    return UncertaintyPolicy(**data)


class UncertaintyTests(unittest.TestCase):
    def assertCode(self,code,fn,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as e:fn(*args,**kwargs)
        self.assertEqual(e.exception.code,code)

    def build(self, args=None, selected=None):
        args=args or synthetic_fixture()
        with patch('intelligence.patterns_insights.research.uncertainty.replay_candidate_descriptive_effects',
                   return_value=args[-1]):
            return build_candidate_uncertainty_intervals(*args,policy() if selected is None else selected)

    def test_synthetic_constant_pair_oracle_and_all_hypotheses(self):
        x=self.build()
        self.assertIsInstance(x,CandidateUncertaintyIntervals)
        self.assertEqual(x.interval_count,2)
        data=x.as_dict();self.assertEqual(data['schema_version'],SCHEMA)
        self.assertEqual(data['replicate_count'],400)
        self.assertEqual(data['registered_hypothesis_count'],2)
        a,b=data['all_hypotheses']
        self.assertEqual(a['median_difference_interval'],{'lower_raw':'10','upper_raw':'10','unit':'seconds'})
        self.assertEqual(a['histogram_tv_interval']['lower']['numerator'],1)
        self.assertEqual(a['histogram_tv_interval']['upper']['numerator'],1)
        self.assertEqual(b['risk_difference_interval']['lower']['numerator'],1)
        self.assertEqual(b['risk_difference_interval']['upper']['numerator'],1)
        self.assertIsNone(b['risk_ratio_interval'])
        self.assertEqual(b['risk_ratio_interval_status'],'undefined_bootstrap_zero_baseline')
        self.assertFalse(x.verified_live);self.assertFalse(x.ready_for_analysis)
        self.assertIs(data['approved_live'],False)
        self.assertIsNone(data['p_values']);self.assertFalse(data['causal_claim'])
        with self.assertRaises(FrozenInstanceError):x.interval_count=4

    def test_independent_eight_pair_golden(self):
        golden=json.loads((Path(__file__).parent/'fixtures'/'pie'/'pie_uncertainty_golden_v1.json').read_text())
        a=self.build()
        self.assertEqual(golden['fixture_kind'],'synthetic_non_authoritative_eight_pairs_mocked_upstream_replay')
        self.assertEqual(golden['expected_artifact_sha256'],a.artifact_sha256)
        self.assertEqual(golden['expected_new_seed_42_sha256'],self.build(selected=policy(seed=42)).artifact_sha256)
        self.assertEqual(golden['hypothesis_count'],a.interval_count)

    def test_full_prior_chain_single_pair_is_too_small(self):
        try:
            from test_pie_effects import DescriptiveEffectTests
        except ImportError:
            self.skipTest('Full PIE test chain absent in isolated runner')
        case=DescriptiveEffectTests('test_all_preregistered_hypotheses_estimated_without_selection')
        case.setUp()
        effect=case.estimate()
        self.assertCode('uncertainty_insufficient_pairs',build_candidate_uncertainty_intervals,
                        *case.refs(),effect,policy())

    def test_deterministic_repeat_and_changed_seed(self):
        a=self.build();self.assertEqual(a,self.build())
        self.assertEqual(a.artifact_sha256,canonical_hash(a.as_dict()))
        self.assertNotEqual(a.artifact_sha256,self.build(selected=policy(seed=42)).artifact_sha256)
        self.assertNotEqual(a.artifact_sha256,self.build(selected=policy(method_version='paired-sha256/v2')).artifact_sha256)

    def test_independent_pair_counter_rng_stable_golden(self):
        draws=list(_draw_indices(123,4,3))
        self.assertEqual(draws,[(1,2,1,0),(3,3,0,1),(3,2,0,1)])
        self.assertEqual(len(list(_draw_indices(123,8,400))),400)
        self.assertEqual(draws,list(_draw_indices(123,4,3)))

    def test_exact_binary_numeric_and_percentile_oracles(self):
        self.assertEqual(_binary_stats([True,True,False,False],[True,False,False,False]),
                         (Fraction(1,4),Fraction(2,1)))
        self.assertEqual(_binary_stats([False]*8,[False]*8),(Fraction(0),None))
        self.assertEqual(_numeric_stats([10,20,30,40],[0,10,20,30],[15,25]),
                         (Decimal('10'),Fraction(1,4)))
        nums=list(range(1,401))
        self.assertEqual(_bounds(nums,6,'decimal'),{'lower_raw':'10.000000','upper_raw':'390.000000'})
        self.assertEqual(_bounds([Fraction(x,400) for x in nums],6,'fraction')['lower']['numerator'],1)

    def test_missing_pair_refuses_silent_complete_case_dropping(self):
        self.assertCode('uncertainty_missing_pair',self.build,synthetic_fixture(missing=True))

    def test_repeated_creator_across_pairs_blocks_false_independence(self):
        self.assertCode('uncertainty_creator_dependence',self.build,synthetic_fixture(repeat_creator=True))

    def test_mixed_pair_values_risk_ratio_stable_zero_handling(self):
        x=self.build(synthetic_fixture(vary=True))
        b=x.as_dict()['all_hypotheses'][1]
        self.assertIsNone(b['risk_ratio_interval'])
        self.assertEqual(b['risk_ratio_interval_status'],'undefined_bootstrap_zero_baseline')

    def test_policy_must_be_explicit_and_bounded(self):
        for change in ({'seed':-1},{'seed':True},{'seed':2**64},{'resamples':399},
                       {'resamples':401},{'resamples':3000},{'minimum_independent_pairs':7},
                       {'minimum_independent_pairs':True},{'confidence_level':'0.99'},
                       {'resampling_unit':'individual_video'},
                       {'creator_dependence':'ignore'}, {'missingness':'drop_missing'},
                       {'interval_method':'gaussian_normal'}, {'ratio_zero_rule':'infinity'},
                       {'policy_version':''},{'design_sha256':'wrong'}):
            with self.subTest(change=change):
                self.assertCode('invalid_uncertainty_policy',policy,**change)
        args=synthetic_fixture()
        self.assertCode('invalid_uncertainty_policy',self.build,args,{})

    def test_wrong_design_and_small_pairs_block(self):
        self.assertCode('uncertainty_lineage_mismatch',self.build,synthetic_fixture(),policy(design_sha256='a'*64))
        self.assertCode('uncertainty_insufficient_pairs',self.build,synthetic_fixture(),policy(minimum_independent_pairs=9))

    def test_resampling_operation_limit_fail_closed(self):
        self.assertCode('uncertainty_insufficient_pairs',self.build,synthetic_fixture(),policy(resamples=2000,minimum_independent_pairs=1000))
        with patch('intelligence.patterns_insights.research.uncertainty._MAX_OPERATIONS',8):
            self.assertCode('uncertainty_insufficient_pairs',self.build)

    def test_replay_rejects_hash_tampering_and_resealed_forgery(self):
        args=synthetic_fixture();a=self.build(args)
        with patch('intelligence.patterns_insights.research.uncertainty.replay_candidate_descriptive_effects',return_value=args[-1]):
            self.assertEqual(replay_candidate_uncertainty_intervals(a.to_json(),a.artifact_sha256,*args),a)
            self.assertCode('uncertainty_hash_mismatch',replay_candidate_uncertainty_intervals,a.to_json(),'0'*64,*args)
            forged=a.as_dict();forged['all_hypotheses'][0]['median_difference_interval']['lower_raw']='-999'
            self.assertCode('uncertainty_replay_mismatch',replay_candidate_uncertainty_intervals,
                       json.dumps(forged),canonical_hash(forged),*args)
            forged=a.as_dict();forged['approved_live']=True
            self.assertCode('unsupported_uncertainty_artifact',replay_candidate_uncertainty_intervals,
                       json.dumps(forged),canonical_hash(forged),*args)
            self.assertCode('duplicate_json_key',replay_candidate_uncertainty_intervals,'{"a":1,"a":2}','0'*64,*args)
            self.assertCode('invalid_uncertainty_artifact',replay_candidate_uncertainty_intervals,'{"a":NaN}','0'*64,*args)

    def test_no_production_statistical_or_publication_methods(self):
        result=self.build()
        for name in ('publish_insights','claim_significance','authorize_live','mark_verified',
                     'compute_p_values','adjust_multiple_testing','claim_causal_effect'):
            self.assertFalse(hasattr(result,name))

if __name__=='__main__':unittest.main()
