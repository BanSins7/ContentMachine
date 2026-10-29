"""PIE-MOD-22 synthetic numeric technical factor oracle; upstream replay MOCKED.

Stage22 arithmetic, policy, missingness, identity and canonical replay are real.
Stage07/10/11/17 fixtures are deliberately not a qualified live UAE source.
"""
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from intelligence.patterns_insights.contracts import PIEBoundaryError, canonical_hash
from intelligence.patterns_insights.research.technical_numeric import (
    TechnicalNumericFactor, TechnicalNumericPolicy,
    build_candidate_technical_numeric_report, replay_candidate_technical_numeric_report)
from test_pie_adjustment import _make_source, _json

METRICS=(
    ('duration','a','duration_ms','milliseconds',1),
    ('speech_coverage','a','speech_coverage_pct_x100','pct_x100',100),
    ('speech_wpm','a','speech_wpm_x100','wpm_x100',100),
    ('cuts_per_minute','b','cuts_per_minute_x100','cuts_per_minute_x100',100),
    ('first_5_seconds_cuts','b','first_5_seconds_cuts','count',1),
)
HIGH=(2,6,8,9)
LOW=(1,3,4,7)


def fixture():
    source=list(_make_source())  # 16 synthetic videos, first 8 discovery, 4 early matched pairs.
    frozen=source[0]
    snapshot=frozen.as_dict()
    for member in snapshot['selection']['included']:
        i=member['video_id']
        member['row']['group_a_sha256']=f'{i+100:064x}'
        member['row']['group_b_sha256']=f'{i+200:064x}'
    source[0]=replace(frozen,_canonical_json=_json(snapshot))
    m=source[4]
    data=m.as_dict()
    data['columns']=[
        {'family':family,'name':name,'representation':'numeric_int','unit':unit}
        for _,family,name,unit,_ in METRICS
    ]
    for row in data['rows']:
        i=row['video_id']
        v=(HIGH[(i-1)//2] if i%2 else LOW[(i-2)//2]) if i<=8 else 7
        row['features']=[{
            'family':family,'name':name,'representation':'numeric_int',
            'analysis_sha256':f'{i+(100 if family=="a" else 200):064x}',
            'observation':{'state':'present','value':v}
        } for _,family,name,unit,scale in METRICS]
    source[4]=replace(m,column_count=len(METRICS),_json=_json(data))
    factors=tuple(TechnicalNumericFactor(
        factor_id=f'factor_{idx:02d}_{kind}',metric_kind=kind,family=family,
        feature_name=name,source_unit=unit,value_scale=scale,
        bin_cutoffs_raw=(5,),min_per_role_per_bin=1,min_creators_per_bin=2)
        for idx,(kind,family,name,unit,scale) in enumerate(METRICS))
    pol=TechnicalNumericPolicy('numeric-v1','integer-bins-v1',source[8].design_sha256,
        source[0].snapshot_sha256,source[3].artifact_sha256,
        source[4].artifact_sha256,source[5].artifact_sha256,
        '2026-10-09T00:00:00Z','unverified-preregistration-v1',4,factors)
    return (*source[:8],pol)


class NumericTechnicalSyntheticTests(unittest.TestCase):
    def setUp(self): self.value=fixture()

    def edit(self,index,item,source=None):
        a=list(self.value if source is None else source)
        a[index]=item
        return tuple(a)

    def build(self,source=None):
        v=self.value if source is None else source
        with patch('intelligence.patterns_insights.research.technical_numeric.replay_candidate_controls',
                   return_value=v[3]),patch(
             'intelligence.patterns_insights.research.technical_numeric.replay_candidate_feature_matrix',
                   return_value=v[4]),patch(
             'intelligence.patterns_insights.research.technical_numeric.replay_candidate_holdout_split',
                   return_value=v[5]):
            return build_candidate_technical_numeric_report(*v)

    def replay(self,raw,sha,source=None):
        v=self.value if source is None else source
        with patch('intelligence.patterns_insights.research.technical_numeric.replay_candidate_controls',
                   return_value=v[3]),patch(
             'intelligence.patterns_insights.research.technical_numeric.replay_candidate_feature_matrix',
                   return_value=v[4]),patch(
             'intelligence.patterns_insights.research.technical_numeric.replay_candidate_holdout_split',
                   return_value=v[5]):
            return replay_candidate_technical_numeric_report(raw,sha,*v[:8])

    def reject(self,code,fun,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as c: fun(*args,**kwargs)
        self.assertEqual(c.exception.code,code)

    def test_golden_pairwise_raw_mean_and_bins(self):
        d=self.build().as_dict()
        self.assertEqual(d['registered_factor_count'],5)
        self.assertEqual(d['report_status'],'candidate_descriptive_numeric_only')
        self.assertEqual(d['discovery_pair_count'],4)
        self.assertEqual(len(d['reserved_pairs']),4)
        self.assertEqual(d['unmatched_discovery_video_ids'],[])
        f=d['factors'][0]
        self.assertEqual(f['status'],'candidate_descriptive_only')
        self.assertEqual(f['monotonicity'],'nondecreasing_descriptive')
        self.assertEqual([(b['high_n'],b['low_n']) for b in f['bins']],[(1,3),(3,1)])
        self.assertEqual(f['bins'][0]['high_share_within_bin']['numerator'],1)
        self.assertEqual(f['bins'][0]['high_share_within_bin']['denominator'],4)
        self.assertEqual(f['bins'][1]['high_share_within_bin']['numerator'],3)
        self.assertEqual(f['bins'][1]['high_share_within_bin']['denominator'],4)
        self.assertEqual(f['transition_matrix_high_bin_by_low_bin'],[[1,0],[2,1]])
        self.assertEqual(f['paired_raw_direction_counts'],{'high_greater':4,'low_greater':0,'equal':0})
        self.assertEqual(f['matched_mean_raw_high_minus_low'],
                         {'numerator':5,'denominator':2,'decimal_12dp':'2.500000000000'})
        self.assertEqual(f['observed_raw_ranges']['high'],{'minimum':2,'maximum':9})
        self.assertEqual(f['observed_raw_ranges']['low'],{'minimum':1,'maximum':7})

    def test_all_five_explicit_source_family_metric_kinds(self):
        d=self.build().as_dict()
        self.assertEqual([f['metric_kind'] for f in d['factors']],[
            'duration','speech_coverage','speech_wpm','cuts_per_minute','first_5_seconds_cuts'])
        self.assertEqual([f['feature_family'] for f in d['factors']],['a','a','a','b','b'])
        self.assertTrue(all(f['status']=='candidate_descriptive_only' for f in d['factors']))
        self.assertEqual(d['registered_factor_family_sha256'],self.value[8].family_sha256)

    def test_scaled_fixed_point_division_explicit(self):
        f=self.build().as_dict()['factors'][1]
        self.assertEqual(f['source_unit'],'pct_x100')
        self.assertEqual(f['value_scale'],100)
        self.assertEqual(f['matched_mean_raw_high_minus_low']['numerator'],5)
        self.assertEqual(f['matched_mean_raw_high_minus_low']['denominator'],2)
        self.assertEqual(f['matched_mean_scaled_high_minus_low'],
                         {'numerator':1,'denominator':40,'decimal_12dp':'0.025000000000'})

    def test_no_inference_or_calibrated_uncertainty(self):
        report=self.build()
        d=report.as_dict()
        self.assertFalse(report.ready_for_analysis)
        self.assertFalse(report.verified_live)
        self.assertIsNone(d['confirmed_discoveries'])
        self.assertFalse(d['heldout_confirmation_used'])
        self.assertFalse(d['can_publish_significance'])
        self.assertEqual(d['valid_inferential_p_value_count'],0)
        self.assertEqual(d['uncertainty_status'],'blocked_no_calibrated_cluster_uncertainty')
        self.assertTrue(all(x['uncertainty_interval'] is None and x['p_value'] is None
                            and x['adjusted_q_value'] is None and not x['confirmed_effect']
                            for x in d['factors']))

    def test_minimum_cell_positivity_does_not_silently_drop_bins(self):
        p=self.value[8]
        alt=replace(p,factors=(replace(p.factors[0],min_per_role_per_bin=2),))
        d=self.build(self.edit(8,alt)).as_dict()
        self.assertEqual(d['registered_factor_count'],1)
        self.assertEqual(d['report_status'],'blocked_incomplete_registered_numeric_family')
        self.assertEqual(d['factors'][0]['supported_bin_count'],0)
        self.assertIsNone(d['factors'][0]['matched_mean_raw_high_minus_low'])
        self.assertIsNone(d['factors'][0]['matched_mean_scaled_high_minus_low'])
        self.assertTrue(all(not x['supported'] for x in d['factors'][0]['bins']))

    def test_missing_numeric_blocks_whole_factor_and_preserves_family(self):
        m=self.value[4];raw=m.as_dict()
        raw['rows'][0]['features'][0]['observation']={'state':'null','value':None}
        d=self.build(self.edit(4,replace(m,_json=_json(raw)))).as_dict()
        self.assertEqual(d['registered_factor_count'],5)
        self.assertEqual(len(d['factors']),5)
        self.assertEqual(d['factors'][0]['status'],'blocked_incomplete_or_sparse_bin')
        self.assertEqual(d['factors'][0]['missing_observations'],
                         [{'video_id':1,'role':'high','reason_code':'null_or_absent_numeric'}])
        self.assertEqual(d['factors'][0]['bins'][0]['high_n'],0)
        self.assertIsNone(d['factors'][0]['matched_mean_raw_high_minus_low'])
        self.assertEqual(d['report_status'],'blocked_incomplete_registered_numeric_family')
        self.assertEqual(d['factors'][1]['status'],'candidate_descriptive_only')

    def test_both_pair_cells_missing_counted_once_not_twice(self):
        m=self.value[4];raw=m.as_dict()
        for idx in (0,1):
            raw['rows'][idx]['features'][0]['observation']={'state':'absent','value':None}
        d=self.build(self.edit(4,replace(m,_json=_json(raw)))).as_dict()
        f=d['factors'][0]
        self.assertEqual(len(f['missing_observations']),2)
        self.assertEqual(sum(sum(x) for x in f['transition_matrix_high_bin_by_low_bin']),3)
        self.assertEqual(f['status'],'blocked_incomplete_or_sparse_bin')

    def test_float_bool_negative_and_overflow_fails_closed(self):
        for bad in (False,1.5,-1,2**63):
            m=self.value[4];raw=m.as_dict()
            raw['rows'][0]['features'][0]['observation']['value']=bad
            self.reject('numeric_invalid_value',self.build,
                        self.edit(4,replace(m,_json=_json(raw))))

    def test_mismatched_unit_and_type_forbidden(self):
        p=self.value[8]
        alt=replace(p,factors=(replace(p.factors[0],source_unit='seconds'),))
        self.reject('numeric_feature_unavailable',self.build,self.edit(8,alt))
        m=self.value[4];raw=m.as_dict()
        raw['columns'][0]['representation']='category'
        self.reject('numeric_feature_unavailable',self.build,
                    self.edit(4,replace(m,_json=_json(raw))))

    def test_forged_analysis_lineage_rejected(self):
        m=self.value[4];raw=m.as_dict()
        raw['rows'][0]['features'][0]['analysis_sha256']='f'*64
        self.reject('numeric_cell_mismatch',self.build,
                    self.edit(4,replace(m,_json=_json(raw))))

    def test_missing_column_blocked_not_reassigned(self):
        p=self.value[8]
        alt=replace(p,factors=(replace(p.factors[0],feature_name='duration_unknown'),))
        self.reject('numeric_feature_unavailable',self.build,self.edit(8,alt))

    def test_same_numeric_feature_cannot_be_explored_under_two_ids(self):
        p=self.value[8];first=p.factors[0]
        self.reject('duplicate_numeric_feature',replace,p,factors=(
            first,replace(first,factor_id='zz_duplicate')))

    def test_invalid_family_and_metric_guards(self):
        f=self.value[8].factors[0]
        self.reject('invalid_numeric_factor',replace,f,family='b')
        self.reject('invalid_numeric_factor',replace,f,metric_kind='c7_score')
        self.reject('invalid_numeric_factor',replace,f,source_origin_claim='official_uae_verified')
        self.reject('invalid_numeric_factor',replace,f,value_scale=0)

    def test_bad_cutoff_order_and_infinite_resources_fail(self):
        f=self.value[8].factors[0]
        for cutoffs in ((0,),(-1,),(5,5),(7,5),(),tuple(range(1,17)),(True,)):
            self.reject('invalid_numeric_factor',replace,f,bin_cutoffs_raw=cutoffs)
        self.reject('invalid_numeric_factor',replace,f,min_per_role_per_bin=0)
        self.reject('invalid_numeric_factor',replace,f,min_creators_per_bin=0)

    def test_policy_requires_complete_fixed_family_and_registration(self):
        p=self.value[8]
        self.reject('invalid_numeric_policy',replace,p,factors=tuple(reversed(p.factors)))
        self.reject('invalid_numeric_policy',replace,p,factors=())
        self.reject('invalid_numeric_policy',replace,p,min_discovery_pairs=True)
        self.reject('invalid_numeric_policy',replace,p,sample_scope='confirmation')
        self.reject('invalid_numeric_policy',replace,p,inference='significant_by_default')
        self.reject('invalid_numeric_policy',replace,p,treatment_of_missing='impute_zero')
        self.reject('invalid_numeric_policy',replace,p,sample_weighting='c7_weighted')

    def test_post_selection_registration_claim_rejected(self):
        p=self.value[8]
        self.reject('numeric_post_selection',self.build,self.edit(8,replace(
            p,registered_at='2026-10-16T00:00:00Z')))

    def test_source_hash_mismatch_refused(self):
        p=self.value[8]
        for name in ('snapshot_sha256','design_sha256','controls_sha256','matrix_sha256','holdout_sha256'):
            self.reject('numeric_lineage_mismatch',self.build,self.edit(8,replace(p,**{name:'f'*64})))

    def test_pair_crossing_discovery_confirmation_refused(self):
        m=self.value[3];data=m.as_dict()
        data['pairs'][0]['control_video_id']=10
        self.reject('numeric_cross_arm_pair',self.build,
                    self.edit(3,replace(m,_json=_json(data))))

    def test_reused_matched_video_rejected(self):
        m=self.value[3];data=m.as_dict()
        data['pairs'][1]['case_video_id']=1
        self.reject('numeric_insufficient_pairs',self.build,
                    self.edit(3,replace(m,_json=_json(data))))

    def test_insufficient_discovery_pair_minimum(self):
        p=self.value[8]
        self.reject('numeric_insufficient_pairs',self.build,self.edit(8,replace(p,min_discovery_pairs=5)))

    def test_nonmonotone_three_bins_independent_golden(self):
        # High bins: [2,1,1], low bins [1,2,1] => high shares 2/3,1/3,1/2.
        m=self.value[4];raw=m.as_dict()
        high=[1,2,5,8];low=[3,4,6,9]
        for row in raw['rows'][:8]:
            i=row['video_id']
            val=high[(i-1)//2] if i%2 else low[(i-2)//2]
            row['features'][0]['observation']['value']=val
        p=self.value[8]
        alt=replace(p,factors=(replace(p.factors[0],bin_cutoffs_raw=(4,7)),))
        d=self.build(self.edit(8,alt,self.edit(4,replace(m,_json=_json(raw))))).as_dict()
        f=d['factors'][0]
        self.assertEqual(f['status'],'candidate_descriptive_only')
        self.assertEqual(f['monotonicity'],'nonmonotone_descriptive')
        self.assertEqual([(v['high_n'],v['low_n']) for v in f['bins']],
                         [(2,1),(1,2),(1,1)])

    def test_replay_sha_and_canonical_bytes_identical(self):
        report=self.build()
        self.assertEqual(report.to_json(),self.build().to_json())
        self.assertEqual(self.replay(report.to_json(),report.artifact_sha256),report)
        self.assertEqual(self.replay(report.to_json().encode(),report.artifact_sha256),report)

    def test_rehashed_forgery_and_changed_source_refused(self):
        a=self.build();fake=a.as_dict()
        fake['factors'][0]['matched_mean_raw_high_minus_low']['numerator']=999
        self.reject('numeric_replay_mismatch',self.replay,_json(fake),canonical_hash(fake))
        m=self.value[4];raw=m.as_dict()
        raw['rows'][0]['features'][0]['observation']['value']=3
        changed=self.edit(4,replace(m,_json=_json(raw)))
        self.reject('numeric_replay_mismatch',self.replay,a.to_json(),a.artifact_sha256,changed)

    def test_replay_noncanonical_malformed_duplicate_nonfinite_and_false_live(self):
        a=self.build()
        self.reject('numeric_replay_mismatch',self.replay,json.dumps(a.as_dict(),indent=2),a.artifact_sha256)
        self.reject('duplicate_json_key',self.replay,'{"x":1,"x":2}','0'*64)
        self.reject('invalid_numeric_artifact',self.replay,'{"x":NaN}','0'*64)
        self.reject('invalid_numeric_artifact',self.replay,b'\xff','0'*64)
        self.reject('numeric_hash_mismatch',self.replay,a.to_json(),'f'*64)
        fake=a.as_dict();fake['approved_live']=True
        self.reject('unsupported_numeric_artifact',self.replay,_json(fake),canonical_hash(fake))

if __name__=='__main__':
    unittest.main()
