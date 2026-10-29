"""PIE-MOD-21 synthetic B3 role motif oracle; prior-stage replay MOCKED.

The toy row adaptation itself is not a real UAE snapshot/authorized B3 export.
Actual stage21 algorithm, geometry checks, sample accounting and replay run.
"""
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from intelligence.patterns_insights.contracts import PIEBoundaryError, canonical_hash
from intelligence.patterns_insights.research.story_motifs import (
    StoryMotif, StoryMotifPolicy, StoryRoleCoordinates,
    count_story_ngram, build_candidate_story_motifs, replay_candidate_story_motifs)
from test_pie_adjustment import _make_source, _json

ROLES={
    1:['hook','problem','cta','hook','problem','cta'],
    2:['intro','cta'],
    3:['hook','problem','cta'],
    4:['hook','problem','cta'],
    5:['hook','problem','cta'],
    6:['problem','cta','hook'],
    7:['intro','cta'],
    8:['intro','cta'],
}


def fixture(*, alignment='sequence_only_no_geometry'):
    source=list(_make_source())       # Mocked lineage from stage07–17 only.
    snapshot=source[0]
    payload=snapshot.as_dict()
    for member in payload['selection']['included']:
        member['row']['group_b_sha256']=f'{member["video_id"]+200:064x}'
    source[0]=replace(snapshot,_canonical_json=_json(payload))
    matrix=source[4]
    matrix_data=matrix.as_dict()
    matrix_data['columns']=[
        {'family':'b','name':'story_roles','representation':'ordered_sequence'}]
    for r in matrix_data['rows']:
        i=r['video_id']
        r['features']=[{
            'family':'b','name':'story_roles','representation':'ordered_sequence',
            'analysis_sha256':f'{i+200:064x}',
            'observation':{'state':'present','value':ROLES.get(i,['intro','cta'])},
        }]
    source[4]=replace(matrix,_json=_json(matrix_data))
    motifs=(
        StoryMotif('m01_hook_problem',('hook','problem')),
        StoryMotif('m02_problem_cta',('problem','cta')),
        StoryMotif('m03_hook_problem_cta',('hook','problem','cta')),
    )
    policy=StoryMotifPolicy('story-v1','ngram-count-v1',source[8].design_sha256,
        source[0].snapshot_sha256,source[3].artifact_sha256,
        source[4].artifact_sha256,source[5].artifact_sha256,
        '2026-10-09T00:00:00Z','unverified-preregistration-claim-v1',
        'story_roles',4,1,motifs,alignment_mode=alignment)
    coordinates=()
    if alignment=='full_unverified_b3_role_coordinates':
        coordinates=tuple(StoryRoleCoordinates(
            i,f'{i:064x}',f'{i+200:064x}','video_timestamp_ms',
            tuple(1000*j for j in range(len(ROLES[i])))) for i in range(1,9))
    return (*source[:8],policy,coordinates)


class StoryNgramIndependentOracle(unittest.TestCase):
    def test_repeated_role_occurrences_counted_without_dedup(self):
        self.assertEqual(count_story_ngram(('hook','hook','hook'),('hook','hook')),2)

    def test_contiguous_vs_subsequence_and_reverse(self):
        self.assertEqual(count_story_ngram(('hook','intro','cta'),('hook','cta')),0)
        self.assertEqual(count_story_ngram(('hook','cta'),('cta','hook')),0)
        self.assertEqual(count_story_ngram(('hook','cta'),('hook','cta')),1)

    def test_overlapping_ngrams_have_distinct_offsets(self):
        self.assertEqual(count_story_ngram(('a','b','a','b','a'),('a','b','a')),2)

    def test_empty_or_short_sequence_is_observed_zero(self):
        self.assertEqual(count_story_ngram((),('hook','cta')),0)
        self.assertEqual(count_story_ngram(('hook',),('hook','cta')),0)

    def test_invalid_sequence_never_cast_string_to_roles(self):
        with self.assertRaises(PIEBoundaryError):
            count_story_ngram('hook,cta',('hook','cta'))
        with self.assertRaises(PIEBoundaryError):
            count_story_ngram(('hook',None),('hook','cta'))
        with self.assertRaises(PIEBoundaryError):
            count_story_ngram(('hook','cta'),('hook',))


class StoryMotifSyntheticTests(unittest.TestCase):
    def setUp(self):
        self.values=fixture()

    def edit(self,ix,value,values=None):
        v=list(self.values if values is None else values)
        v[ix]=value
        return tuple(v)

    def build(self,values=None):
        v=self.values if values is None else values
        with patch('intelligence.patterns_insights.research.story_motifs.replay_candidate_controls',
                   return_value=v[3]),patch(
             'intelligence.patterns_insights.research.story_motifs.replay_candidate_feature_matrix',
                   return_value=v[4]),patch(
             'intelligence.patterns_insights.research.story_motifs.replay_candidate_holdout_split',
                   return_value=v[5]):
            return build_candidate_story_motifs(*v)

    def replay(self,raw,sha,values=None):
        v=self.values if values is None else values
        with patch('intelligence.patterns_insights.research.story_motifs.replay_candidate_controls',
                   return_value=v[3]),patch(
             'intelligence.patterns_insights.research.story_motifs.replay_candidate_feature_matrix',
                   return_value=v[4]),patch(
             'intelligence.patterns_insights.research.story_motifs.replay_candidate_holdout_split',
                   return_value=v[5]):
            return replay_candidate_story_motifs(raw,sha,*v[:8],v[9])

    def reject(self,code,fn,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as ctx:
            fn(*args,**kwargs)
        self.assertEqual(ctx.exception.code,code)

    def test_golden_exact_paired_role_motifs_and_occurrence_counts(self):
        report=self.build()
        d=report.as_dict()
        self.assertEqual(d['registered_motif_count'],3)
        self.assertEqual(d['report_status'],'candidate_descriptive_motifs_only')
        self.assertEqual(d['discovery_pair_count'],4)
        self.assertEqual(len(d['reserved_pairs']),4)
        self.assertEqual(d['unmatched_discovery_video_ids'],[])
        self.assertEqual(d['geometry_status'],'not_available_no_temporal_or_slide_claim')
        self.assertFalse(d['group_a_technical_scene_geometry_used'])
        self.assertTrue(d['b3_role_repetitions_preserved'])
        first,second,third=d['motifs']
        self.assertEqual(first['high'],{'observed_posts':4,'hit_posts':3,'total_occurrences':4})
        self.assertEqual(first['low'],{'observed_posts':4,'hit_posts':1,'total_occurrences':1})
        self.assertEqual(first['paired_discordance'],
                         {'both_hit':1,'high_only':2,'low_only':0,'neither_hit':1})
        self.assertEqual(first['matched_post_prevalence_difference'],
                         {'numerator':1,'denominator':2,'decimal_12dp':'0.500000000000'})
        self.assertEqual(second['matched_post_prevalence_difference']['numerator'],1)
        self.assertEqual(second['matched_post_prevalence_difference']['denominator'],4)
        self.assertEqual(third['high']['hit_posts'],3)
        self.assertEqual(third['low']['hit_posts'],1)

    def test_no_inference_and_no_promotion(self):
        out=self.build()
        d=out.as_dict()
        self.assertFalse(out.ready_for_analysis)
        self.assertFalse(out.verified_live)
        self.assertEqual(d['inferential_hypotheses_tested'],0)
        self.assertEqual(d['multiplicity_status'],'blocked_no_valid_p_values')
        self.assertIsNone(d['confirmed_discoveries'])
        self.assertFalse(d['can_publish_significance'])
        self.assertFalse(d['causal_claim'])
        self.assertTrue(all(x['p_value'] is None and x['confidence_interval'] is None
                            and not x['confirmed'] for x in d['motifs']))

    def test_sparse_motif_remains_with_full_registered_family(self):
        p=self.values[8]
        sparse=StoryMotif('m04_rare',('cta','hook','intro'))
        variant=self.edit(8,replace(p,motifs=(*p.motifs,sparse)))
        report=self.build(variant).as_dict()
        self.assertEqual(report['registered_motif_count'],4)
        self.assertEqual(len(report['motifs']),4)
        self.assertEqual(report['motifs'][3]['status'],
                         'blocked_missing_sparse_or_incomparable_geometry')
        self.assertIsNone(report['motifs'][3]['matched_post_prevalence_difference'])
        self.assertEqual(report['report_status'],'blocked_incomplete_registered_motif_family')

    def test_missing_b3_blocks_no_imputation(self):
        matrix=self.values[4]
        data=matrix.as_dict()
        data['rows'][0]['features'][0]['observation']={'state':'null','value':None}
        alt=self.edit(4,replace(matrix,_json=_json(data)))
        d=self.build(alt).as_dict()
        self.assertEqual(d['missing_b3_roles'],[{'video_id':1,'role':'high',
                                                 'reason_code':'b3_absent_or_null'}])
        self.assertTrue(all(m['matched_post_prevalence_difference'] is None for m in d['motifs']))
        self.assertEqual(d['report_status'],'blocked_incomplete_registered_motif_family')

    def test_wrong_a_family_or_multilabel_representation_refused(self):
        p=self.values[8]
        self.reject('invalid_story_policy',replace,p,source_feature_family='a')
        m=self.values[4]
        data=m.as_dict()
        data['columns'][0]['representation']='multi_label'
        self.reject('story_b3_feature_unavailable',self.build,
                    self.edit(4,replace(m,_json=_json(data))))

    def test_original_b_analysis_sha_must_match_semantic_source(self):
        matrix=self.values[4]
        data=matrix.as_dict()
        data['rows'][0]['features'][0]['analysis_sha256']='f'*64
        self.reject('story_b3_lineage_mismatch',self.build,
                    self.edit(4,replace(matrix,_json=_json(data))))

    def test_video_timestamp_positions_exact_and_unverified(self):
        f=fixture(alignment='full_unverified_b3_role_coordinates')
        d=self.build(f).as_dict()
        self.assertEqual(d['coordinate_kinds'],['video_timestamp_ms'])
        self.assertEqual(d['geometry_status'],'unverified_external_coordinates_no_authentication')
        self.assertIsNotNone(d['b3_role_coordinate_claim_sha256'])
        self.assertFalse(d['mixed_media_pooled_comparison_blocked'])
        self.assertEqual(d['report_status'],'candidate_descriptive_motifs_only')

    def test_slide_number_not_timestamp_and_no_mixed_media_pooling(self):
        f=fixture(alignment='full_unverified_b3_role_coordinates')
        coords=tuple(replace(x,coordinate_kind='carousel_slide_number',
                positions=tuple(j+1 for j in range(len(x.positions)))) for x in f[9])
        d=self.build(self.edit(9,coords,f)).as_dict()
        self.assertEqual(d['coordinate_kinds'],['carousel_slide_number'])
        self.assertFalse(d['mixed_media_pooled_comparison_blocked'])
        split=tuple(replace(x,coordinate_kind='carousel_slide_number',
                     positions=tuple(j+1 for j in range(len(x.positions))))
                     if x.video_id==2 else x for x in f[9])
        crossed=self.build(self.edit(9,split,f)).as_dict()
        self.assertTrue(crossed['mixed_media_pooled_comparison_blocked'])
        self.assertEqual(crossed['cross_media_matched_pairs'][0]['case_video_id'],1)
        self.assertEqual(crossed['report_status'],'blocked_incomplete_registered_motif_family')
        self.assertTrue(all(m['matched_post_prevalence_difference'] is None for m in crossed['motifs']))

    def test_incomplete_or_misaligned_geometry_refused(self):
        f=fixture(alignment='full_unverified_b3_role_coordinates')
        self.reject('story_geometry_population_mismatch',self.build,self.edit(9,f[9][:-1],f))
        corrupted=list(f[9])
        corrupted[0]=replace(corrupted[0],positions=(0,1000))
        self.reject('story_coordinate_lineage_mismatch',self.build,
                    self.edit(9,tuple(corrupted),f))
        corrupted[0]=replace(corrupted[0],row_sha256='f'*64)
        self.reject('story_coordinate_lineage_mismatch',self.build,
                    self.edit(9,tuple(corrupted),f))

    def test_wrong_geometry_kind_repeated_and_zero_slide_invalid(self):
        c=fixture(alignment='full_unverified_b3_role_coordinates')[9][0]
        self.reject('invalid_story_coordinates',replace,c,coordinate_kind='technical_a_scene')
        self.reject('invalid_story_coordinates',replace,c,positions=(0,0,1000))
        self.reject('invalid_story_coordinates',replace,c,coordinate_kind='carousel_slide_number')
        self.reject('invalid_story_coordinates',replace,c,provenance='official_verified')

    def test_unrequested_geometry_cannot_be_silently_used(self):
        f=fixture(alignment='full_unverified_b3_role_coordinates')
        self.reject('story_geometry_policy_mismatch',self.build,self.edit(9,f[9]))

    def test_post_selection_claim_refused(self):
        p=self.values[8]
        self.reject('story_post_selection',self.build,
                    self.edit(8,replace(p,registered_at='2026-10-16T00:00:00Z')))

    def test_cross_arm_pair_refused(self):
        c=self.values[3]
        raw=c.as_dict()
        raw['pairs'][0]['control_video_id']=10
        self.reject('story_cross_arm_pair',self.build,self.edit(3,replace(c,_json=_json(raw))))

    def test_policy_rejects_unsorted_duplicate_and_unsupported_motifs(self):
        p=self.values[8]
        self.reject('invalid_story_policy',replace,p,motifs=tuple(reversed(p.motifs)))
        self.reject('invalid_story_policy',replace,p,
                    motifs=(p.motifs[0],StoryMotif('m99_duplicate',p.motifs[0].roles)))
        self.reject('invalid_story_policy',replace,p,motifs=())
        self.reject('invalid_story_motif',StoryMotif,'invalid',('hook',))
        self.reject('invalid_story_motif',StoryMotif,'invalid',('hook','cta'),
                    operation='non_contiguous')

    def test_policy_does_not_enable_live_or_mixed_sources(self):
        p=self.values[8]
        for kw in ({'alignment_mode':'guess_a_scene_timestamps'},
                   {'sample_scope':'confirmation'},
                   {'missingness':'skip_missing'},
                   {'inference_status':'confirmed'},
                   {'source_feature_family':'a'}):
            self.reject('invalid_story_policy',replace,p,**kw)

    def test_sha_and_byte_exact_replay(self):
        r=self.build()
        self.assertEqual(r.to_json(),self.build().to_json())
        self.assertEqual(self.replay(r.to_json(),r.artifact_sha256),r)
        self.assertEqual(self.replay(r.to_json().encode('utf-8'),r.artifact_sha256),r)

    def test_replay_rehashed_forgery_detected(self):
        a=self.build()
        d=a.as_dict()
        d['motifs'][0]['matched_post_prevalence_difference']['numerator']=999
        self.reject('story_replay_mismatch',self.replay,_json(d),canonical_hash(d))
        self.reject('story_replay_mismatch',self.replay,
                    json.dumps(a.as_dict(),indent=2),a.artifact_sha256)

    def test_replay_rejects_source_change_and_false_live_claim(self):
        a=self.build()
        m=self.values[4]
        raw=m.as_dict()
        raw['rows'][0]['features'][0]['observation']['value']=['intro','cta']
        alt=self.edit(4,replace(m,_json=_json(raw)))
        self.reject('story_replay_mismatch',self.replay,a.to_json(),a.artifact_sha256,alt)
        fake=a.as_dict()
        fake['approved_live']=True
        self.reject('unsupported_story_artifact',self.replay,_json(fake),canonical_hash(fake))

    def test_duplicate_keys_nan_bad_hash_and_utf8_rejected(self):
        a=self.build()
        self.reject('duplicate_json_key',self.replay,'{"x":1,"x":2}','0'*64)
        self.reject('invalid_story_artifact',self.replay,'{"x":NaN}','0'*64)
        self.reject('story_hash_mismatch',self.replay,a.to_json(),'f'*64)
        self.reject('invalid_story_artifact',self.replay,b'\xff','0'*64)

if __name__=='__main__':
    unittest.main()
