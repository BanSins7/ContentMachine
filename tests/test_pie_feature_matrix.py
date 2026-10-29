"""PIE-MOD-11 contract against synthetic prior-stage fixtures (no real UAE)."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
import unittest

from intelligence.patterns_insights import PIEBoundaryError, canonical_hash
from intelligence.patterns_insights.research.feature_matrix import (
    SCHEMA, FeatureColumn, FeatureMatrixPolicy, CandidateFeatureMatrix,
    build_candidate_feature_matrix, replay_candidate_feature_matrix,
)
from test_pie_controls import ControlAssemblyTests, match_policy


def policy(**changes):
    base = dict(policy_version='feature-map/v1', study_design_sha256='c'*64,
        features=(
            FeatureColumn('a', 'duration_sec', 'numeric_int', False, False, 'seconds'),
            FeatureColumn('a', 'measured', 'boolean', False, False),
            FeatureColumn('a', 'speech_wpm', 'numeric_int', False, False, 'words_per_minute'),
            FeatureColumn('b', 'cta_has_cta', 'boolean', False, True),
            FeatureColumn('b', 'hook_mechanisms', 'multi_label', False, False),
            FeatureColumn('b', 'story_roles', 'ordered_sequence', False, False),
        ))
    base.update(changes)
    return FeatureMatrixPolicy(**base)


class FeatureMatrixTests(unittest.TestCase):
    def setUp(self):
        ControlAssemblyTests.setUp(self)
        self.controls = ControlAssemblyTests.assemble(self)
        self.selected = policy()

    def matrix(self, *, policy_=None, snapshot=None, strata=None, outcome=None, controls=None):
        return build_candidate_feature_matrix(snapshot or self.snapshot, strata or self.strata,
                  outcome or self.outcome, controls or self.controls, policy_ or self.selected)

    def rejected(self, code, fn, *args, **kwargs):
        with self.assertRaises(PIEBoundaryError) as err:
            fn(*args, **kwargs)
        self.assertEqual(err.exception.code, code)

    def test_candidate_schema_lineage_and_exact_replay(self):
        a = self.matrix()
        self.assertIsInstance(a, CandidateFeatureMatrix)
        self.assertEqual(a, self.matrix())
        self.assertEqual(a.artifact_sha256, canonical_hash(a.as_dict()))
        self.assertEqual(a.to_json(), self.matrix().to_json())
        self.assertEqual(replay_candidate_feature_matrix(a.to_json().encode(), a.artifact_sha256,
                         self.snapshot, self.strata, self.outcome, self.controls), a)
        self.assertFalse(a.verified_live)
        self.assertFalse(a.ready_for_analysis)
        self.assertEqual(a.as_dict()['schema_version'], SCHEMA)
        self.assertEqual(a.as_dict()['evidence_state'], 'unverified')
        self.assertIs(a.as_dict()['approved_live'], False)
        self.assertEqual(a.snapshot_sha256, self.snapshot.snapshot_sha256)
        self.assertEqual(a.controls_sha256, self.controls.artifact_sha256)
        self.assertEqual(a.row_count, 2)
        self.assertEqual(a.column_count, 6)
        self.assertEqual(a.as_dict()['source_dictionary_sha256'],self.snapshot.feature_dictionary_sha256)
        with self.assertRaises(FrozenInstanceError):
            a.row_count=3

    def test_multilabel_B1_set_and_exact_original_order(self):
        a = self.matrix().as_dict()
        high = a['rows'][0]
        cells = {x['name']: x for x in high['features']}
        self.assertEqual(high['role'], 'high')
        self.assertEqual(high['video_id'],101)
        self.assertEqual(cells['hook_mechanisms']['observation'],
                         {'state':'present','value':['question'], 'observed_order':['question','question']})
        self.assertEqual(cells['story_roles']['observation'],
                         {'state':'present','value':['hook','proof','proof']})
        self.assertEqual(a['row_count'], 2)  # repeated B1/B3 labels never inflate N
        low = {c['name']:c for c in a['rows'][1]['features']}
        self.assertEqual(low['hook_mechanisms']['observation'],
                         {'state':'present','value':[], 'observed_order':[]})
        self.assertEqual(low['story_roles']['observation']['value'], ['hook','closing'])

    def test_bool_false_and_explicit_null_are_different(self):
        a = self.matrix().as_dict()
        high = next(x for x in a['rows'] if x['role']=='high')
        low = next(x for x in a['rows'] if x['role']=='low')
        self.assertEqual(next(c['observation'] for c in high['features'] if c['name']=='cta_has_cta'),
                         {'state':'present','value':False})
        self.assertEqual(next(c['observation'] for c in low['features'] if c['name']=='cta_has_cta'),
                         {'state':'null','value':None})
        self.rejected('null_feature', self.matrix,
                      policy_=policy(features=tuple(replace(c,allow_null=False) if c.name=='cta_has_cta' else c
                                                    for c in policy().features)))

    def test_score_raw_unbounded_only_target_never_predictor(self):
        p = self.matrix().as_dict()
        targets = {r['video_id']: r['target']['value'] for r in p['rows']}
        self.assertEqual(targets,{101:'250.50',102:'-3.25'})
        for row in p['rows']:
            target = row['target']
            self.assertEqual(target['kind'], 'raw_c7_decimal_label')
            self.assertEqual(target['calculation_config_id'],17)
            self.assertEqual(target['outcome_policy_sha256'],self.outcome.policy_sha256)
            self.assertEqual(target['calculated_metric_sha256'],
                             next(x for x in self.snapshot.as_dict()['selection']['included']
                                  if x['video_id']==row['video_id'])['calculated_metric_sha256'])
            self.assertNotIn('breakout_score', [col['name'] for col in row['features']])
        self.rejected('unsupported_feature', self.matrix,
            policy_=policy(features=(FeatureColumn('a','breakout_score','numeric_int',False,False,'count'),)))
        self.rejected('unsupported_feature', self.matrix,
            policy_=policy(features=(FeatureColumn('b','made_up_by_model','category',True,True),)))

    def test_A_B_cell_provenance_versions_and_source_hashes(self):
        a = self.matrix().as_dict()
        actual = self.snapshot.as_dict()['selection']['included']
        by_id={x['video_id']:x['row'] for x in actual}
        for sample in a['rows']:
            orig=by_id[sample['video_id']]
            self.assertEqual(sample['row_sha256'],next(x['row_sha256'] for x in actual
                                                           if x['video_id']==sample['video_id']))
            for cell in sample['features']:
                prefix = 'group_'+cell['family']
                self.assertEqual(cell['analysis_sha256'],orig[prefix+'_sha256'])
                self.assertEqual(cell['analysis_version'],orig[prefix+'_version'])
                self.assertEqual(cell['analysis_id'],orig[prefix+'_analysis_id'])
                self.assertEqual(cell['features_sha256'],orig['features_sha256'])

    def test_exact_population_and_exclusions_never_silently_dropped(self):
        p = self.matrix().as_dict()['population']
        self.assertEqual((p['scanned_count'],p['included_count'],p['matched_count'],
                          p['unmatched_count'],p['neutral_count'],p['other_strata_count']),
                         (3,2,2,0,0,0))
        self.assertEqual(p['original_excluded'],[{'video_id':103,'reason_code':'policy_unavailable'}])
        self.assertEqual(p['unmatched'],[])

    def test_predeclared_kind_must_match_observed_dictionary(self):
        for column in (
            FeatureColumn('a','measured','numeric_int',False,False,'count'),
            FeatureColumn('b','story_roles','boolean',False,False),
            FeatureColumn('a','duration_sec','category',False,False),
            FeatureColumn('b','cta_has_cta','category',False,True),
        ):
            with self.subTest(column=column):
                self.rejected('feature_type_mismatch',self.matrix, policy_=policy(features=(column,)))

    def test_absent_vs_null_vs_zero_explicit(self):
        def change(rows):
            rows[0]['features_a']['duration_sec']=0
            rows[1]['features_a']['duration_sec']=0
            rows[1]['features_b'].pop('cta_has_cta')
        snap,strata,outcome=ControlAssemblyTests.expanded(self,change)
        controls=build_controls_for(snap,strata,outcome)
        actual=self.matrix(snapshot=snap,strata=strata,outcome=outcome,controls=controls)
        high,low=actual.as_dict()['rows']
        self.assertEqual(next(c for c in high['features'] if c['name']=='duration_sec')['observation']['value'],0)
        self.assertEqual(next(c for c in low['features'] if c['name']=='cta_has_cta')['observation'],
                         {'state':'absent','value':None})
        strict=policy(features=tuple(replace(c,allow_absent=False) if c.name=='cta_has_cta' else c for c in policy().features))
        self.rejected('missing_feature',self.matrix,policy_=strict,snapshot=snap,strata=strata,outcome=outcome,controls=controls)

    def test_unsupported_numeric_float_bad_boolean_and_mixed_types(self):
        self.rejected('invalid_feature_policy',FeatureColumn,'a','duration_sec','numeric_int',False,False)
        self.rejected('invalid_feature_policy',FeatureColumn,'b','hook_mechanisms','multi_label',1,False)
        self.rejected('invalid_feature_policy',FeatureColumn,'b','hook_mechanisms','made_up',False,False)
        self.rejected('invalid_feature_policy',FeatureColumn,'b','hook_mechanisms','multi_label',False,False,'seconds')
        self.rejected('invalid_feature_policy',FeatureColumn,'b','not-a-safe-key','boolean',False,False)
        self.rejected('invalid_feature_policy',FeatureColumn,'c','c7','category',False,False)
        self.rejected('invalid_feature_policy',FeatureColumn,'a','duration_sec','numeric_int',False,False,'bad unit')

    def test_policy_requires_explicit_unique_sorted_features(self):
        original=policy()
        self.rejected('invalid_feature_policy',FeatureMatrixPolicy,'', 'c'*64, original.features)
        self.rejected('invalid_feature_policy',FeatureMatrixPolicy,'map/v1','X'*64,original.features)
        self.rejected('invalid_feature_policy',FeatureMatrixPolicy,'map/v1','c'*64,[])
        self.rejected('invalid_feature_policy',FeatureMatrixPolicy,'map/v1','c'*64,())
        self.rejected('duplicate_feature',FeatureMatrixPolicy,'map/v1','c'*64,original.features[::-1])
        self.rejected('duplicate_feature',FeatureMatrixPolicy,'map/v1','c'*64,original.features+(original.features[-1],))
        self.rejected('invalid_feature_policy',self.matrix,policy_=object())
        self.rejected('feature_design_mismatch',self.matrix,policy_=policy(study_design_sha256='d'*64))

    def test_changed_policy_identity_requires_new_snapshot(self):
        a=self.matrix()
        alt=policy(policy_version='feature-map/v2')
        self.assertNotEqual(self.matrix(policy_=alt).artifact_sha256,a.artifact_sha256)
        alt=policy(features=tuple(c for c in policy().features if c.name!='measured'))
        self.assertNotEqual(self.matrix(policy_=alt).artifact_sha256,a.artifact_sha256)
        changed=policy(features=(FeatureColumn('a','duration_sec','numeric_int',False,False,'milliseconds'),))
        self.assertNotEqual(self.matrix(policy_=changed).artifact_sha256,a.artifact_sha256)

    def test_input_from_other_project_or_wrong_version_fails(self):
        self.rejected('invalid_feature_input',build_candidate_feature_matrix,
                      object(),self.strata,self.outcome,self.controls,self.selected)
        self.rejected_any(self.matrix,snapshot=replace(self.snapshot,project_id='b'*32))
        self.rejected_any(self.matrix,controls=replace(self.controls,source_outcome_sha256='0'*64))
        self.rejected_any(self.matrix,outcome=replace(self.outcome,project_id='b'*32))

    def test_replay_rejects_resealed_tampering_even_with_new_checksum(self):
        obj=self.matrix()
        modifications=(
            lambda x:x['rows'][0]['features'][0]['observation'].update({'value':999}),
            lambda x:x['rows'][0]['target'].update({'value':'0'}),
            lambda x:x['rows'][0]['features'][3].update({'analysis_sha256':'f'*64}),
            lambda x:x['rows'].pop(),
            lambda x:x['population']['original_excluded'].clear(),
            lambda x:x['rows'][0].update({'role':'low'}),
            lambda x:x.update({'approved_live':True}),
        )
        for i,modify in enumerate(modifications):
            payload=obj.as_dict();modify(payload)
            with self.subTest(i=i):
                self.rejected_any(replay_candidate_feature_matrix,json.dumps(payload),canonical_hash(payload),
                                  self.snapshot,self.strata,self.outcome,self.controls)
        self.rejected('feature_hash_mismatch',replay_candidate_feature_matrix,
                      obj.to_json(),'0'*64,self.snapshot,self.strata,self.outcome,self.controls)
        self.rejected('duplicate_json_key',replay_candidate_feature_matrix,
                      '{"x":1,"x":2}','0'*64,self.snapshot,self.strata,self.outcome,self.controls)
        self.rejected('invalid_feature_matrix',replay_candidate_feature_matrix,
                      '{"x":NaN}','0'*64,self.snapshot,self.strata,self.outcome,self.controls)

    def test_cannot_claim_production_authorization(self):
        result=self.matrix()
        for name in ('publish','compute_p_values','authorize','mark_verified','run_statistics'):
            self.assertFalse(hasattr(result,name))
        self.assertFalse(result.ready_for_analysis)
        self.assertFalse(result.verified_live)
        self.assertIsNone(getattr(result,'source_rights_authority',None))

    def test_no_mutable_alias_to_caller(self):
        result=self.matrix()
        v=result.as_dict()
        v['rows'][0]['features'][0]['observation']['value']=9999
        v['rows'][0]['target']['value']='9999'
        self.assertNotEqual(v,result.as_dict())
        self.assertTrue(result.artifact_sha256==canonical_hash(result.as_dict()))

    def rejected_any(self, fn, *args, **kwargs):
        with self.assertRaises(PIEBoundaryError):fn(*args,**kwargs)


def build_controls_for(snapshot,strata,outcome):
    from intelligence.patterns_insights.research.controls import build_candidate_controls
    return build_candidate_controls(snapshot,strata,outcome,match_policy())


if __name__=='__main__':
    unittest.main()
