"""PIE-MOD-20: synthetic typed-interaction regression, with upstream replay MOCKED.

No real UAE row eligibility, independent p-values, confidence bounds or live sources.
"""
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from intelligence.patterns_insights.contracts import PIEBoundaryError, canonical_hash
from intelligence.patterns_insights.research.interactions import (
    InteractionPredicate, InteractionHypothesis, InteractionPolicy,
    build_candidate_interaction_report, replay_candidate_interaction_report)
from test_pie_adjustment import _make_source, _json

YES={1,3,4}
NAMES=('cta','format','has_hook','has_subtitles','story_sequence','tags')
REPRESENTATIONS=('boolean','category','boolean','boolean','ordered_sequence','multi_label')


def fixtures():
    source=list(_make_source())   # 16 invented posts; 8 discovery, 8 untouched confirmation.
    old=source[4]
    payload=old.as_dict()
    payload['columns']=[{'family':'b','name':n,'representation':t}
                        for n,t in zip(NAMES,REPRESENTATIONS)]
    for row in payload['rows']:
        i=row['video_id']
        has_hook=row['features'][0]['observation']['value']
        on=i in YES
        states=[
            {'state':'present','value':on},
            {'state':'present','value':'tutorial' if on else 'interview'},
            {'state':'present','value':has_hook},
            {'state':'present','value':on},
            {'state':'present','value':['hook','problem','cta'] if on else ['intro','cta']},
            {'state':'present','value':['hook'] if on else ['other']},
        ]
        row['features']=[{'family':'b','name':n,'representation':t,'observation':s}
                          for n,t,s in zip(NAMES,REPRESENTATIONS,states)]
    source[4]=replace(old,column_count=len(NAMES),_json=_json(payload))
    predicates={
        'cta':InteractionPredicate('b','cta','boolean_equals',True),
        'format':InteractionPredicate('b','format','category_equals','tutorial'),
        'hook':InteractionPredicate('b','has_hook','boolean_equals',True),
        'subtitles':InteractionPredicate('b','has_subtitles','boolean_equals',True),
        'story':InteractionPredicate('b','story_sequence','sequence_equals',('hook','problem','cta')),
        'tags':InteractionPredicate('b','tags','multilabel_contains','hook'),
    }
    hyps=(
        InteractionHypothesis('h01_hook_subtitles',predicates['hook'],predicates['subtitles']),
        InteractionHypothesis('h02_format_hook',predicates['format'],predicates['hook']),
        InteractionHypothesis('h03_hook_story',predicates['hook'],predicates['story']),
        InteractionHypothesis('h04_story_cta',predicates['cta'],predicates['story']),
        InteractionHypothesis('h05_hook_tags',predicates['hook'],predicates['tags']),
    )
    policy=InteractionPolicy('pairwise-policy-v1','fraction-paired-v1',
        source[8].design_sha256,source[0].snapshot_sha256,source[3].artifact_sha256,
        source[4].artifact_sha256,source[5].artifact_sha256,
        '2026-10-09T00:00:00Z','unverified-preregistration-claim-v1',
        4,1,hyps)
    return (*source[:8],policy)


class InteractionSyntheticTests(unittest.TestCase):
    def setUp(self):
        self.values=fixtures()

    def alter(self,ix,item):
        a=list(self.values);a[ix]=item;return tuple(a)

    def build(self,values=None):
        v=self.values if values is None else values
        with patch('intelligence.patterns_insights.research.interactions.replay_candidate_controls',
                   return_value=v[3]), patch(
             'intelligence.patterns_insights.research.interactions.replay_candidate_feature_matrix',
                   return_value=v[4]), patch(
             'intelligence.patterns_insights.research.interactions.replay_candidate_holdout_split',
                   return_value=v[5]):
            return build_candidate_interaction_report(*v)

    def replay(self,raw,sha,values=None):
        v=self.values if values is None else values
        with patch('intelligence.patterns_insights.research.interactions.replay_candidate_controls',
                   return_value=v[3]), patch(
             'intelligence.patterns_insights.research.interactions.replay_candidate_feature_matrix',
                   return_value=v[4]), patch(
             'intelligence.patterns_insights.research.interactions.replay_candidate_holdout_split',
                   return_value=v[5]):
            return replay_candidate_interaction_report(raw,sha,*v[:8])

    def reject(self,code,fn,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as caught:
            fn(*args,**kwargs)
        self.assertEqual(caught.exception.code,code)

    def test_hand_calculated_paired_joint_prevalence_and_excess(self):
        out=self.build()
        d=out.as_dict()
        self.assertEqual(d['report_status'],'candidate_descriptive_interactions_only')
        self.assertEqual(d['registered_hypothesis_count'],5)
        self.assertEqual(d['registered_hypothesis_family_sha256'],self.values[8].family_sha256)
        self.assertEqual(d['discovery_pair_count'],4)
        self.assertEqual(len(d['reserved_pairs']),4)
        h=d['hypotheses'][0]
        self.assertEqual(h['role_counts']['high'],{'n':4,'left_hits':3,'right_hits':2,'joint_hits':2})
        self.assertEqual(h['role_counts']['low'],{'n':4,'left_hits':1,'right_hits':1,'joint_hits':1})
        self.assertEqual(h['joint_prevalence_difference_high_minus_low']['numerator'],1)
        self.assertEqual(h['joint_prevalence_difference_high_minus_low']['denominator'],4)
        self.assertEqual((h['joint_prevalence_ratio_high_over_matched_low']['numerator'],
                          h['joint_prevalence_ratio_high_over_matched_low']['denominator']),(2,1))
        self.assertEqual((h['excess_cooccurrence_high']['numerator'],
                          h['excess_cooccurrence_high']['denominator']),(1,8))
        self.assertEqual((h['excess_cooccurrence_low']['numerator'],
                          h['excess_cooccurrence_low']['denominator']),(3,16))
        self.assertEqual((h['excess_cooccurrence_difference']['numerator'],
                          h['excess_cooccurrence_difference']['denominator']),(-1,16))

    def test_typed_category_multilabel_ordered_sequence_and_story_cta(self):
        d=self.build().as_dict()
        self.assertEqual([x['hypothesis_id'] for x in d['hypotheses']],
                         [h.hypothesis_id for h in self.values[8].hypotheses])
        self.assertTrue(all(x['role_counts']['high']['joint_hits']==2 for x in d['hypotheses']))
        self.assertTrue(all(x['role_counts']['low']['joint_hits']==1 for x in d['hypotheses']))
        self.assertTrue(all(x['status']=='descriptive_candidate_only' for x in d['hypotheses']))

    def test_sequence_order_and_repetition_not_flattened(self):
        predicate=self.values[8].hypotheses[2].right
        from intelligence.patterns_insights.research.interactions import _hit
        self.assertTrue(_hit(['hook','problem','cta'],predicate))
        self.assertFalse(_hit(['cta','problem','hook'],predicate))
        self.assertFalse(_hit(['hook','cta'],predicate))
        self.assertFalse(_hit(['hook','problem','cta','cta'],predicate))

    def test_multilabel_duplicate_never_counts_twice(self):
        from intelligence.patterns_insights.research.interactions import _hit
        p=self.values[8].hypotheses[4].right
        self.assertTrue(_hit(['hook','hook'],p))
        self.assertFalse(_hit([],p))
        self.assertEqual(self.build().as_dict()['hypotheses'][4]['role_counts']['high']['joint_hits'],2)

    def test_no_real_inference_or_publishable_corrected_ci(self):
        report=self.build()
        r=report.as_dict()
        self.assertFalse(report.verified_live)
        self.assertFalse(report.ready_for_analysis)
        self.assertFalse(r['can_publish_significance'])
        self.assertFalse(r['heldout_confirmatory_analysis'])
        self.assertIsNone(r['confirmed_discoveries'])
        self.assertIsNone(r['simultaneous_confidence_interval'])
        self.assertEqual(r['tested_inferential_hypothesis_count'],0)
        self.assertEqual(r['multiplicity_status'],'blocked_no_valid_inferential_p_values')
        self.assertTrue(all(item['p_value'] is None and item['adjusted_q_value'] is None
                       and item['multiplicity_adjusted_confidence_interval'] is None
                       for item in r['hypotheses']))

    def test_missing_cell_blocks_family_not_selective_removal(self):
        matrix=self.values[4];data=matrix.as_dict()
        row=next(row for row in data['rows'] if row['video_id']==3)
        row['features'][3]['observation']={'state':'null','value':None}
        altered=self.alter(4,replace(matrix,_json=_json(data)))
        r=self.build(altered).as_dict()
        self.assertEqual(r['registered_hypothesis_count'],5)
        self.assertEqual(len(r['hypotheses']),5)
        self.assertEqual(r['hypotheses'][0]['status'],'blocked_incomplete_or_sparse_joint_support')
        self.assertEqual(r['report_status'],'blocked_incomplete_registered_family')
        self.assertIsNone(r['hypotheses'][0]['joint_prevalence_difference_high_minus_low'])
        self.assertTrue(r['hypotheses'][1]['status']=='descriptive_candidate_only')

    def test_insufficient_joint_hits_blocks_only_without_fake_ratio(self):
        matrix=self.values[4];data=matrix.as_dict()
        data['rows'][3]['features'][3]['observation']['value']=False  # source video 4 (low)
        result=self.build(self.alter(4,replace(matrix,_json=_json(data)))).as_dict()
        self.assertEqual(result['hypotheses'][0]['status'],'blocked_incomplete_or_sparse_joint_support')
        self.assertIsNone(result['hypotheses'][0]['joint_prevalence_ratio_high_over_matched_low'])

    def test_post_selection_registration_refused(self):
        p=replace(self.values[8],registered_at='2026-10-16T00:00:00Z')
        self.reject('interaction_post_selection',self.build,self.alter(8,p))

    def test_duplicate_reverse_same_feature_and_unsorted_ids_refused(self):
        p=self.values[8]
        first=p.hypotheses[0]
        self.reject('invalid_interaction_hypothesis',InteractionHypothesis,'bad',first.right,first.left)
        self.reject('invalid_interaction_hypothesis',InteractionHypothesis,'bad',first.left,first.left)
        self.reject('invalid_interaction_policy',replace,p,hypotheses=tuple(reversed(p.hypotheses)))
        self.reject('duplicate_interaction_hypothesis',replace,p,hypotheses=(
            first,InteractionHypothesis('h99_duplicate',first.left,first.right)))
        self.reject('invalid_interaction_policy',replace,p,hypotheses=())

    def test_untyped_predicates_and_unsupported_operations_fail(self):
        self.reject('invalid_interaction_predicate',InteractionPredicate,'b','foo','boolean_equals',1)
        self.reject('invalid_interaction_predicate',InteractionPredicate,'c','breakout_score','boolean_equals',True)
        self.reject('invalid_interaction_predicate',InteractionPredicate,'b','story','sequence_equals',())
        self.reject('invalid_interaction_predicate',InteractionPredicate,'b','story','sequence_equals',['hook'])
        self.reject('invalid_interaction_predicate',InteractionPredicate,'b','format','category_equals',None)

    def test_missing_unknown_type_in_matrix_fails_closed(self):
        p=self.values[8]
        bad=InteractionPredicate('b','unknown_bool','boolean_equals',True)
        hi=InteractionHypothesis('h91_unknown',p.hypotheses[0].left,bad)
        self.reject('interaction_feature_unavailable',self.build,self.alter(8,replace(p,hypotheses=(hi,))))

    def test_candidate_holdout_cross_pair_blocks(self):
        c=self.values[3];v=c.as_dict();v['pairs'][0]['control_video_id']=10
        self.reject('interaction_cross_arm_pair',self.build,
                    self.alter(3,replace(c,_json=_json(v))))

    def test_match_pair_coverage_minimum_blocks(self):
        p=self.values[8]
        self.reject('interaction_insufficient_pairs',self.build,
                    self.alter(8,replace(p,minimum_discovery_pairs=5)))

    def test_replay_is_byte_deterministic(self):
        a=self.build()
        self.assertEqual(a.artifact_sha256,self.build().artifact_sha256)
        self.assertEqual(self.replay(a.to_json(),a.artifact_sha256),a)
        self.assertEqual(self.replay(a.to_json().encode(),a.artifact_sha256),a)

    def test_replay_rehashed_tamper_and_alternate_source(self):
        a=self.build()
        fake=a.as_dict()
        fake['hypotheses'][0]['joint_prevalence_difference_high_minus_low']['numerator']=999
        self.reject('interaction_replay_mismatch',self.replay,_json(fake),canonical_hash(fake))
        m=self.values[4];d=m.as_dict();d['rows'][0]['features'][3]['observation']['value']=False
        altered=self.alter(4,replace(m,_json=_json(d)))
        self.reject('interaction_replay_mismatch',self.replay,a.to_json(),a.artifact_sha256,altered)

    def test_noncanonical_and_malformed_replay(self):
        a=self.build()
        self.reject('interaction_replay_mismatch',self.replay,json.dumps(a.as_dict(),indent=2),a.artifact_sha256)
        self.reject('duplicate_json_key',self.replay,'{"x":1,"x":2}','0'*64)
        self.reject('invalid_interaction_artifact',self.replay,'{"x":NaN}','0'*64)
        self.reject('interaction_hash_mismatch',self.replay,a.to_json(),'f'*64)

    def test_fake_live_claim_and_significance_policy_refused(self):
        a=self.build()
        fake=a.as_dict();fake['approved_live']=True
        self.reject('unsupported_interaction_artifact',self.replay,_json(fake),canonical_hash(fake))
        policy=self.values[8]
        for opts in ({'cohort_scope':'confirmation'}, {'missingness':'skip_sparse'},
                     {'multiplicity_mode':'uncorrected_significant'},
                     {'confidence_mode':'pretend_95pct_ci'},
                     {'family_scope':'selected_after_inspection'}):
            self.reject('invalid_interaction_policy',replace,policy,**opts)

    def test_scope_and_source_hash_mismatch_refused(self):
        p=self.values[8]
        for attr in ('design_sha256','snapshot_sha256','matrix_sha256',
                     'controls_sha256','holdout_sha256'):
            self.reject('interaction_lineage_mismatch',self.build,
                        self.alter(8,replace(p,**{attr:'f'*64})))

if __name__=='__main__':
    unittest.main()
