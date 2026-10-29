"""PIE-MOD-19 synthetic direction/coverage oracle; prior-stage replay is MOCKED.

No genuine producer source, preregistration or held-out confirmatory evaluation.
"""
import json
import unittest
from dataclasses import replace
from fractions import Fraction
from unittest.mock import patch

from intelligence.patterns_insights.contracts import PIEBoundaryError, canonical_hash
from intelligence.patterns_insights.research.adjustment import (
    build_candidate_adjustment_report)
from intelligence.patterns_insights.research.stability import (
    StabilityWindow, StabilityPolicy, _classify_groups,
    build_candidate_stability_report, replay_candidate_stability_report)
from test_pie_adjustment import _make_source


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def _adjustment(source):
    # The real stage18 code executes, but replay of earlier stage10/11/17 is stubbed.
    with patch('intelligence.patterns_insights.research.adjustment.replay_candidate_controls',
               return_value=source[3]), patch(
         'intelligence.patterns_insights.research.adjustment.replay_candidate_feature_matrix',
               return_value=source[4]), patch(
         'intelligence.patterns_insights.research.adjustment.replay_candidate_holdout_split',
               return_value=source[5]):
        return build_candidate_adjustment_report(*source)


def _fixture(*, niche_by_pair=True, matrix_mutation=None):
    source=list(_make_source())
    if niche_by_pair:
        # Freeze all 16 claims, and ensure each discovery pair shares a niche.
        source[9]=tuple(replace(x,niche=('alpha' if x.video_id<=4 else 'beta'))
                         for x in source[9])
    if matrix_mutation:
        matrix=source[4]
        payload=matrix.as_dict()
        for row in payload['rows']:
            if row['video_id'] in matrix_mutation:
                row['features'][0]['observation']['value']=matrix_mutation[row['video_id']]
        source[4]=replace(matrix,_json=_json(payload))
    source=tuple(source)
    report=_adjustment(source)
    holdout=source[5]
    policy=StabilityPolicy('stability-v1','paired-fraction-v1',
        source[8].design_sha256,source[0].snapshot_sha256,
        source[3].artifact_sha256,source[4].artifact_sha256,
        holdout.artifact_sha256,report.artifact_sha256,
        '2026-10-09T00:00:00Z',
        (StabilityWindow('earlier','2026-10-01T00:00:00Z','2026-10-11T00:00:00Z'),
         StabilityWindow('later','2026-10-11T00:00:00Z','2026-10-15T00:00:00Z')),
        ('publication_window','creator','niche','topic','age','posting_time',
         'platform','source_provider'),
        2,2,2,'0.1','1')
    return (*source,report,policy)


class PureDirectionOracle(unittest.TestCase):
    """Independent handcrafted group statistics, no connected prior stages."""
    def setUp(self):
        self.positive=[(1,0,'a','b'),(1,0,'c','d')]
        self.negative=[(0,1,'e','f'),(0,1,'g','h')]

    def check(self,groups,expected=4,minimum_pairs=2,minimum_creators=2,minimum_groups=2):
        from decimal import Decimal
        return _classify_groups(groups,expected_pairs=expected,
            minimum_pairs=minimum_pairs,minimum_creators=minimum_creators,
            minimum_groups=minimum_groups,floor=Decimal('0.1'),coverage=Decimal('1'))

    def test_manual_stable_positive(self):
        value=self.check({'early':self.positive,'late':self.positive})
        self.assertEqual(value['status'],'consistent_direction_descriptive_only')
        self.assertEqual(value['creator_reuse_across_groups_count'],4)
        self.assertFalse(value['cross_group_creator_independence_established'])
        self.assertEqual([(v['difference']['numerator'],v['difference']['denominator'])
                          for v in value['groups']],[(1,1),(1,1)])

    def test_manual_stable_negative(self):
        value=self.check({'early':self.negative,'late':self.negative})
        self.assertEqual(value['status'],'consistent_direction_descriptive_only')
        self.assertTrue(all(x['direction']=='negative' for x in value['groups']))

    def test_manual_reversal_is_unstable(self):
        value=self.check({'early':self.positive,'late':self.negative})
        self.assertEqual(value['status'],'unstable_direction_reversal')

    def test_manual_near_zero_is_unstable(self):
        weak=[(1,0,'e','f'),(0,1,'g','h')]
        value=self.check({'early':self.positive,'late':weak})
        self.assertEqual(value['status'],'unstable_weak_or_mixed_direction')
        self.assertEqual(value['groups'][1]['direction'],'near_zero')

    def test_manual_missing_group_is_not_stable(self):
        value=self.check({'early':self.positive},expected=4)
        self.assertEqual(value['status'],'blocked_incomplete_or_cross_group_coverage')
        self.assertEqual(value['covered_pairs'],2)

    def test_manual_positivity_and_creators_gates(self):
        value=self.check({'early':self.positive,'late':self.positive},minimum_pairs=3)
        self.assertEqual(value['status'],'blocked_insufficient_subgroup_support')
        repeated=[(1,0,'only','other'),(1,0,'only','other')]
        value=self.check({'early':repeated,'late':repeated},minimum_creators=3)
        self.assertEqual(value['status'],'blocked_insufficient_subgroup_support')

    def test_exact_half_fraction_and_denominator(self):
        mixed=[(1,0,'a','b'),(0,0,'c','d')]
        val=self.check({'early':mixed,'late':mixed})
        for entry in val['groups']:
            self.assertEqual(entry['difference']['numerator'],1)
            self.assertEqual(entry['difference']['denominator'],2)
            self.assertEqual(entry['difference']['decimal_12dp'],'0.500000000000')


class Stage19IntegratedSynthetic(unittest.TestCase):
    def setUp(self):
        self.fixture=_fixture()

    def build(self,fixture=None):
        f=self.fixture if fixture is None else fixture
        with patch('intelligence.patterns_insights.research.stability.replay_candidate_adjustment_report',
                   return_value=f[10]):
            return build_candidate_stability_report(*f)

    def replay(self,raw,sha,fixture=None):
        f=self.fixture if fixture is None else fixture
        with patch('intelligence.patterns_insights.research.stability.replay_candidate_adjustment_report',
                   return_value=f[10]):
            return replay_candidate_stability_report(raw,sha,*f)

    def swap(self,index,value,fixture=None):
        x=list(self.fixture if fixture is None else fixture)
        x[index]=value
        return tuple(x)

    def reject(self,code,fn,*args):
        with self.assertRaises(PIEBoundaryError) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.code,code)

    def test_niche_association_consistent_only_in_discovery(self):
        report=self.build()
        data=report.as_dict()
        results={x['dimension']:x for x in data['dimensions']}
        niche=results['niche']
        self.assertEqual(niche['status'],'consistent_direction_descriptive_only')
        self.assertEqual([(x['group'],x['pair_count']) for x in niche['groups']],
                         [('alpha',2),('beta',2)])
        self.assertEqual([(x['difference']['numerator'],x['difference']['denominator'])
                          for x in niche['groups']],[(1,2),(1,2)])
        self.assertEqual(data['discovery_pairs'],4)
        self.assertTrue(data['confirmation_sample_touched'] is False)
        self.assertIsNone(data['confirmed_discoveries'])
        self.assertFalse(data['eligible_for_pattern_promotion'])
        self.assertFalse(report.ready_for_analysis)

    def test_single_provider_not_cross_provider_replication(self):
        x=self.build().as_dict()
        result=next(g for g in x['dimensions'] if g['dimension']=='source_provider')
        self.assertEqual(result['status'],'blocked_insufficient_subgroup_support')
        self.assertEqual(result['group_count'],1)
        self.assertFalse(x['validated_multi_provider_stability'])

    def test_creator_pair_incomparability_reported_not_removed_silently(self):
        x=self.build().as_dict()
        result=next(g for g in x['dimensions'] if g['dimension']=='creator')
        self.assertEqual(result['status'],'blocked_incomplete_or_cross_group_coverage')
        self.assertEqual(result['cross_group_pair_count'],4)
        self.assertEqual(result['covered_pairs'],0)

    def test_temporal_group_not_claimed_stable_if_one_window(self):
        g=next(g for g in self.build().as_dict()['dimensions']
               if g['dimension']=='publication_window')
        self.assertEqual(g['status'],'blocked_insufficient_subgroup_support')
        self.assertEqual(g['group_count'],1)

    def test_actual_niche_crossing_not_cherry_picked(self):
        f=_fixture(niche_by_pair=False)
        g=next(g for g in self.build(f).as_dict()['dimensions'] if g['dimension']=='niche')
        self.assertEqual(g['cross_group_pair_count'],2)
        self.assertEqual(g['status'],'blocked_incomplete_or_cross_group_coverage')

    def test_opposite_niche_direction_unstable(self):
        # Synthetically reverse the beta (5,6,7,8) role difference.
        f=_fixture(matrix_mutation={5:False,6:True,7:False,8:True})
        g=next(g for g in self.build(f).as_dict()['dimensions'] if g['dimension']=='niche')
        self.assertEqual(g['status'],'unstable_direction_reversal')
        self.assertEqual([x['direction'] for x in g['groups']],['positive','negative'])

    def test_replay_exact_bytes_and_policy_change(self):
        report=self.build()
        self.assertEqual(report.to_json(),self.build().to_json())
        self.assertEqual(self.replay(report.to_json(),report.artifact_sha256),report)
        self.assertEqual(self.replay(report.to_json().encode(),report.artifact_sha256),report)
        f=self.swap(11,replace(self.fixture[11],direction_floor='0.2'))
        self.reject('stability_replay_mismatch',self.replay,
                    report.to_json(),report.artifact_sha256,f)

    def test_rehashed_forgery_and_noncanonical_json(self):
        a=self.build()
        fake=a.as_dict()
        fake['dimensions'][2]['status']='consistent_direction_descriptive_only'
        self.reject('stability_replay_mismatch',self.replay,_json(fake),canonical_hash(fake))
        self.reject('stability_replay_mismatch',self.replay,
                    json.dumps(a.as_dict(),indent=2),a.artifact_sha256)

    def test_duplicate_json_nan_and_fake_live_rejected(self):
        a=self.build()
        self.reject('duplicate_json_key',self.replay,'{"x":1,"x":2}','0'*64)
        self.reject('invalid_stability_artifact',self.replay,'{"x":NaN}','0'*64)
        fake=a.as_dict()
        fake['approved_live']=True
        self.reject('unsupported_stability_artifact',self.replay,_json(fake),canonical_hash(fake))

    def test_blocked_stage18_cannot_be_promoted(self):
        a=self.fixture[10]
        raw=a.as_dict()
        raw['report_status']='blocked_insufficient_joint_support'
        f=self.swap(10,replace(a,_json=_json(raw)))
        self.reject('stability_adjustment_blocked',self.build,f)

    def test_stability_registered_after_discovery_refused(self):
        f=self.swap(11,replace(self.fixture[11],registered_at='2026-10-16T00:00:00Z'))
        self.reject('stability_post_selection',self.build,f)

    def test_wrong_design_or_adjustment_sha_blocks(self):
        p=self.fixture[11]
        for k in ('design_sha256','adjustment_sha256','snapshot_sha256'):
            f=self.swap(11,replace(p,**{k:'f'*64}))
            self.reject('stability_lineage_mismatch',self.build,f)

    def test_policy_rejects_fake_confirmation_or_partial_picking(self):
        p=self.fixture[11]
        for updates in ({'sample_scope':'confirmation'},
                        {'pair_boundary':'drop_unsupported_pairs'},
                        {'classification':'causal_effect'},
                        {'minimum_pairs_per_group':0},
                        {'minimum_groups_per_dimension':1},
                        {'direction_floor':'0'},
                        {'minimum_covered_pair_fraction':'0'},
                        {'dimensions':('niche','creator')}):
            self.reject('invalid_stability_policy',replace,p,**updates)

    def test_windows_require_no_gaps_or_duplicates(self):
        p=self.fixture[11]
        a,b=p.windows
        self.reject('invalid_stability_policy',replace,p,windows=(b,a))
        self.reject('invalid_stability_policy',replace,p,
                    windows=(a,StabilityWindow('later','2026-10-12T00:00:00Z',
                                             '2026-10-15T00:00:00Z')))
        self.reject('invalid_stability_policy',replace,p,windows=(a,replace(b,label=a.label)))

    def test_timestamp_scope_must_cover_discovery(self):
        p=self.fixture[11]
        a,b=p.windows
        shortened=replace(p,windows=(StabilityWindow('a','2026-10-02T00:00:00Z',
                        '2026-10-04T00:00:00Z'),
                        StabilityWindow('b','2026-10-04T00:00:00Z','2026-10-06T00:00:00Z')))
        self.reject('stability_window_coverage',self.build,self.swap(11,shortened))

if __name__=='__main__':
    unittest.main()
