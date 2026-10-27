"""PIE-MOD-18 synthetic unit tests; upstream stages 07-11/17 replay are MOCKED.

The stage18 algorithm executes for real on handcrafted, SHA-bound toy evidence.
No PostgreSQL, live UAE, inferential tests or authenticated approval is implied.
"""
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from intelligence.patterns_insights.contracts import canonical_hash, PIEBoundaryError
from intelligence.patterns_insights.research.dataset_builder import (
    SNAPSHOT_SCHEMA, CandidateResearchSnapshot)
from intelligence.patterns_insights.research.strata import CandidateStrata
from intelligence.patterns_insights.research.outcomes import CandidateOutcomePlan
from intelligence.patterns_insights.research.controls import CandidateMatchedControls
from intelligence.patterns_insights.research.feature_matrix import CandidateFeatureMatrix
from intelligence.patterns_insights.research.holdout import (
    HoldoutPolicy, HoldoutMediaLineage, build_candidate_holdout_split)
from intelligence.patterns_insights.research.adjustment import (
    AdjustmentPolicy, AdjustmentCovariate, build_candidate_adjustment_report,
    replay_candidate_adjustment_report)

PROJECT = 'a' * 32
DESIGN = 'b' * 64
EARLY = '2026-10-10T12:00:00Z'
LATE = '2026-10-21T12:00:00Z'


def _json(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _make_source():
    # 16 unrelated creator/media identities, balanced case/control, 8 early/8 late.
    rows = []
    for i in range(1, 17):
        day = EARLY if i <= 8 else LATE
        rows.append({'video_id':i, 'row_sha256':f'{i:064x}', 'uae_snapshot_id':100+i,
                     'row':{'creator_key':f'author_{i}', 'platform':'tiktok',
                            'published_at':day,
                            'observed_at': ('2026-10-11T12:00:00Z' if i <= 8
                                            else '2026-10-22T12:00:00Z')}})
    payload={'schema_version':SNAPSHOT_SCHEMA,'evidence_state':'unverified',
             'design':{'design_sha256':DESIGN},
             'source':{'pointer':{'project_id':PROJECT,'as_of':'2026-10-27T00:00:00Z'}},
             'selection':{'included':rows,'excluded':[]}}
    snapshot=CandidateResearchSnapshot(canonical_hash(payload),PROJECT,'c'*64,'d'*64,
        tuple(range(1,17)),(),16,'e'*64,_json(payload))
    hpolicy=HoldoutPolicy('holdout-policy-v1','split-v1',DESIGN,snapshot.snapshot_sha256,
        '2026-10-09T00:00:00Z','2026-10-15T00:00:00Z','2026-10-18T00:00:00Z',
        8,8,8,8,2,73)
    lineage=tuple(HoldoutMediaLineage(i,f'{i:064x}',f'family_{i}',f'revision_{i}')
                  for i in range(1,17))
    with patch('intelligence.patterns_insights.research.holdout.replay_candidate_snapshot',
               return_value=snapshot):
        split=build_candidate_holdout_split(snapshot,hpolicy,lineage)
    strata=CandidateStrata('f'*64,snapshot.snapshot_sha256,'e'*64,1,snapshot.included_video_ids,(),
                           _json({'project_id':PROJECT}))
    outcome=CandidateOutcomePlan('f'*64,snapshot.snapshot_sha256,strata.artifact_sha256,'e'*64,
        PROJECT,tuple(range(2,17,2)),tuple(range(1,17,2)),(),_json({'project_id':PROJECT}))
    pairs=[{'case_video_id':i,'control_video_id':i+1} for i in range(1,17,2)]
    controls_payload={'source_snapshot_sha256':snapshot.snapshot_sha256,'pairs':pairs}
    controls=CandidateMatchedControls('1'*64,snapshot.snapshot_sha256,
        strata.artifact_sha256,outcome.artifact_sha256,'2'*64,PROJECT,8,
        tuple(range(1,17,2)),tuple(range(2,17,2)),_json(controls_payload))
    # Early 8: alpha: 3 cases (1,3,5), 1 control (2); beta: 1 case (7),
    # 3 controls (4,6,8). alpha RD=1; beta RD=-1/3; adjusted RD=1/3.
    # Marginal RD = 3/4 - 1/4 = 1/2.
    hits={1:True,2:False,3:True,4:True,5:True,6:False,7:False,8:False}
    matrix_rows=[{'video_id':i,'role':'high' if i%2 else 'low',
                  'row_sha256':f'{i:064x}',
                  'features':[{'observation':{'state':'present','value':hits.get(i,False)}}]}
                 for i in range(1,17)]
    matrix_payload={'source_controls_sha256':controls.artifact_sha256,
                    'columns':[{'family':'b','name':'has_hook','representation':'boolean'}],
                    'rows':matrix_rows}
    matrix=CandidateFeatureMatrix('3'*64,snapshot.snapshot_sha256,strata.artifact_sha256,
        outcome.artifact_sha256,controls.artifact_sha256,'4'*64,PROJECT,16,1,
        _json(matrix_payload))
    cov=tuple(AdjustmentCovariate(i, f'{i:064x}',
        ('alpha' if i in (1,2,3,5) else 'beta'), 'topic_one',
        '2026-10-09T00:00:00Z') for i in range(1,17))
    policy=AdjustmentPolicy('adjust-v1','pool-fraction-v1',DESIGN,snapshot.snapshot_sha256,
       controls.artifact_sha256,matrix.artifact_sha256,split.artifact_sha256,
       '2026-10-09T00:00:00Z','discovery','b','has_hook',('niche',),
       (3600,86400,604800),(0,6,12,18,24),1,2,4,16)
    return snapshot,strata,outcome,controls,matrix,split,hpolicy,lineage,policy,cov


class AdjustmentSyntheticTests(unittest.TestCase):
    def setUp(self):
        self.sources=_make_source()

    def build(self, sources=None):
        source=self.sources if sources is None else sources
        with patch('intelligence.patterns_insights.research.adjustment.replay_candidate_controls',
                   return_value=source[3]), patch(
             'intelligence.patterns_insights.research.adjustment.replay_candidate_feature_matrix',
                   return_value=source[4]), patch(
             'intelligence.patterns_insights.research.adjustment.replay_candidate_holdout_split',
                   return_value=source[5]):
            return build_candidate_adjustment_report(*source)

    def replay(self, raw, sha, sources=None):
        source=self.sources if sources is None else sources
        with patch('intelligence.patterns_insights.research.adjustment.replay_candidate_controls',
                   return_value=source[3]), patch(
             'intelligence.patterns_insights.research.adjustment.replay_candidate_feature_matrix',
                   return_value=source[4]), patch(
             'intelligence.patterns_insights.research.adjustment.replay_candidate_holdout_split',
                   return_value=source[5]):
            return replay_candidate_adjustment_report(raw,sha,*source)

    def replace_source(self, ix, new):
        data=list(self.sources)
        data[ix]=new
        return tuple(data)

    def reject(self, code, fn, *args, **kwargs):
        with self.assertRaises(PIEBoundaryError) as ctx:
            fn(*args,**kwargs)
        self.assertEqual(ctx.exception.code,code)

    def test_independent_hand_arithmetic_adjusted_one_third(self):
        report=self.build()
        d=report.as_dict()
        self.assertEqual(d['report_status'],'candidate_descriptive_association_only')
        self.assertEqual(d['adjusted_risk_difference']['numerator'],1)
        self.assertEqual(d['adjusted_risk_difference']['denominator'],3)
        self.assertEqual(d['unadjusted_risk_difference']['numerator'],1)
        self.assertEqual(d['unadjusted_risk_difference']['denominator'],2)
        self.assertEqual([(r['dimensions']['niche'],r['high_n'],r['low_n']) for r in d['cells']],
                         [('alpha',3,1),('beta',1,3)])
        self.assertEqual(d['sample']['discovery_pairs'],4)
        self.assertEqual(len(d['sample']['excluded_pairs']),4)
        self.assertFalse(report.verified_live)
        self.assertFalse(report.ready_for_analysis)
        self.assertIsNone(d['p_values'])
        self.assertIsNone(d['confirmed_effect'])
        self.assertFalse(d['causal_claim'])

    def test_replay_deterministic_byte_and_sha(self):
        a=self.build()
        self.assertEqual(a,self.build())
        self.assertEqual(self.replay(a.to_json(),a.artifact_sha256),a)
        self.assertEqual(self.replay(a.to_json().encode('utf-8'),a.artifact_sha256),a)

    def test_rehashed_fake_arithmetic_refused(self):
        report=self.build()
        fake=report.as_dict()
        fake['adjusted_risk_difference']['numerator']=999
        self.reject('adjustment_replay_mismatch',self.replay,
                    _json(fake),canonical_hash(fake))

    def test_pretty_print_fails_byte_canonicality(self):
        a=self.build()
        self.reject('adjustment_replay_mismatch',self.replay,
                    json.dumps(a.as_dict(),indent=2),a.artifact_sha256)

    def test_inadequate_cell_overlap_blocks_not_partial_estimates(self):
        cov=tuple(replace(x,niche=('alpha' if x.video_id%2 else 'beta'))
                  for x in self.sources[-1])
        a=self.build(self.replace_source(9,cov))
        d=a.as_dict()
        self.assertEqual(d['report_status'],'blocked_insufficient_joint_support')
        self.assertIsNone(d['adjusted_risk_difference'])
        self.assertIsNone(d['unadjusted_risk_difference'])
        self.assertEqual(d['sample']['supported_cells'],0)
        self.assertEqual(d['sample']['discovery_pairs'],4)

    def test_minimum_cells_and_creators_and_counts_block(self):
        policy=self.sources[8]
        for kwargs in ({'minimum_cells':3},{'minimum_distinct_creators':9},
                       {'minimum_per_role_per_cell':2}):
            x=self.build(self.replace_source(8,replace(policy,**kwargs)))
            self.assertEqual(x.report_status,'blocked_insufficient_joint_support')
            self.assertIsNone(x.as_dict()['adjusted_risk_difference'])

    def test_no_hypothesis_convenience_drop_when_cell_sparse(self):
        cov=list(self.sources[-1])
        cov[1]=replace(cov[1],niche='rare')
        x=self.build(self.replace_source(9,tuple(cov)))
        self.assertEqual(x.report_status,'blocked_insufficient_joint_support')
        self.assertEqual(x.as_dict()['sample']['cells'],3)

    def test_new_covariate_sha_changes_report_hash(self):
        report=self.build()
        cov=tuple(replace(x,topic='another_topic') for x in self.sources[-1])
        modified=self.build(self.replace_source(9,cov))
        self.assertNotEqual(report.artifact_sha256,modified.artifact_sha256)
        self.assertEqual(report.as_dict()['adjusted_risk_difference'],
                         modified.as_dict()['adjusted_risk_difference'])
        self.reject('adjustment_replay_mismatch',self.replay,
                    report.to_json(),report.artifact_sha256,self.replace_source(9,cov))

    def test_changed_source_row_sha_cannot_pass(self):
        cov=list(self.sources[-1])
        cov[0]=replace(cov[0],row_sha256='f'*64)
        self.reject('adjustment_covariate_mismatch',self.build,
                    self.replace_source(9,tuple(cov)))

    def test_external_covariate_after_publication_is_rejected(self):
        cov=list(self.sources[-1])
        cov[0]=replace(cov[0],asserted_at='2026-10-13T00:00:00Z')
        self.reject('adjustment_covariate_mismatch',self.build,
                    self.replace_source(9,tuple(cov)))

    def test_missing_extra_or_reordered_covariates_refused(self):
        cov=self.sources[-1]
        for bad in (cov[:-1],cov + cov[:1],tuple(reversed(cov))):
            self.reject('adjustment_covariate_population_mismatch',self.build,
                        self.replace_source(9,bad))

    def test_source_and_design_pin_mismatch(self):
        p=self.sources[8]
        self.reject('adjustment_lineage_mismatch',self.build,
                    self.replace_source(8,replace(p,design_sha256='f'*64)))
        self.reject('adjustment_lineage_mismatch',self.build,
                    self.replace_source(8,replace(p,holdout_sha256='f'*64)))

    def test_registration_after_discovery_cutoff_refused(self):
        p=self.sources[8]
        self.reject('adjustment_post_selection',self.build,
                    self.replace_source(8,replace(p,registered_at='2026-10-16T00:00:00Z')))

    def test_cross_holdout_pair_is_refused(self):
        controls=self.sources[3]
        data=controls.as_dict()
        data['pairs'][0]['control_video_id']=10
        altered=replace(controls,_json=_json(data))
        self.reject('adjustment_cross_arm_pair',self.build,
                    self.replace_source(3,altered))

    def test_matrix_null_not_misread_as_false(self):
        matrix=self.sources[4]
        data=matrix.as_dict()
        data['rows'][0]['features'][0]['observation']={'state':'null','value':None}
        self.reject('adjustment_incomplete_feature',self.build,
                    self.replace_source(4,replace(matrix,_json=_json(data))))

    def test_negative_subsecond_publication_age_rejected_without_float_rounding(self):
        snapshot=self.sources[0]
        data=snapshot.as_dict()
        data['selection']['included'][0]['row']['observed_at']='2026-10-10T11:59:59.900000Z'
        altered=replace(snapshot,_canonical_json=_json(data))
        self.reject('adjustment_invalid_time',self.build,
                    self.replace_source(0,altered))

    def test_feature_must_be_original_binary_a_or_b(self):
        policy=self.sources[8]
        self.reject('adjustment_feature_unavailable',self.build,
                    self.replace_source(8,replace(policy,feature_name='ghost')))
        self.reject('invalid_adjustment_policy',replace,policy,feature_family='c')

    def test_policy_rejects_posthoc_partial_or_causal_modes(self):
        p=self.sources[8]
        for kw in ({'arm':'confirmation'}, {'method':'regression_significant'},
                   {'insufficient_support':'drop_sparse_cells'},
                   {'label':'causal_effect'}):
            self.reject('invalid_adjustment_policy',replace,p,**kw)

    def test_invalid_dimensions_and_age_time_buckets(self):
        p=self.sources[8]
        for kw in ({'adjust_dimensions':('topic','niche')},
                   {'adjust_dimensions':('niche','niche')},
                   {'adjust_dimensions':('foo',)},
                   {'age_cutoffs_seconds':(0,)},
                   {'age_cutoffs_seconds':(60,40)},
                   {'utc_hour_cutoffs':(0,12,23)},
                   {'minimum_cells':True}):
            self.reject('invalid_adjustment_policy',replace,p,**kw)

    def test_noncanonical_live_claim_duplicate_nonfinite_and_hash(self):
        a=self.build()
        self.reject('duplicate_json_key',self.replay,'{"x":1,"x":2}','0'*64)
        self.reject('invalid_adjustment_artifact',self.replay,'{"x":NaN}','0'*64)
        self.reject('adjustment_hash_mismatch',self.replay,a.to_json(),'f'*64)
        fake=a.as_dict()
        fake['approved_live']=True
        self.reject('unsupported_adjustment_artifact',self.replay,
                    _json(fake),canonical_hash(fake))

    def test_unverified_provenance_cannot_be_faked(self):
        self.reject('invalid_adjustment_covariate',replace,self.sources[-1][0],
                    provenance='official_verified_live')
        self.reject('invalid_adjustment_covariate',replace,self.sources[-1][0],
                    topic='unknown topic')
        self.reject('invalid_adjustment_covariate',replace,self.sources[-1][0],
                    video_id=True)

if __name__=='__main__':
    unittest.main()
