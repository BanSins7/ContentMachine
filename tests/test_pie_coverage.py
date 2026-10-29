"""PIE-MOD-12: synthetic fixtures only; no live UAE rights or validated bias claims."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
import unittest

from intelligence.patterns_insights import PIEBoundaryError, canonical_hash
from intelligence.patterns_insights.research.coverage import (
    SCHEMA, CoveragePolicy, CandidateCoverageAudit,
    build_candidate_coverage_audit, replay_candidate_coverage_audit,
)
from intelligence.patterns_insights.research.feature_matrix import (
    FeatureMatrixPolicy, FeatureColumn, build_candidate_feature_matrix,
)
from intelligence.patterns_insights.research.controls import build_candidate_controls
import test_pie_feature_matrix as fm
import test_pie_controls as ct


def coverage_policy(**changes):
    values = {
        'policy_version':'coverage/v1', 'design_sha256':'c'*64,
        'min_matched_rows':2, 'min_rows_per_role':1,
        'min_present_per_role_per_feature':1,
        'max_missing_fraction':'0.500000', 'min_cell_disclosure_n':1,
        'require_shared_creators':False, 'require_all_high_matched':True,
    }
    values.update(changes)
    return CoveragePolicy(**values)


class CoverageAuditTests(unittest.TestCase):
    def setUp(self):
        fm.FeatureMatrixTests.setUp(self)
        self.matrix_obj=fm.FeatureMatrixTests.matrix(self)
        self.coverage_policy=coverage_policy()

    def audit(self, policy=None, matrix=None, snapshot=None, strata=None, outcome=None, controls=None):
        return build_candidate_coverage_audit(
            snapshot if snapshot is not None else self.snapshot,
            strata if strata is not None else self.strata,
            outcome if outcome is not None else self.outcome,
            controls if controls is not None else self.controls,
            matrix if matrix is not None else self.matrix_obj,
            policy if policy is not None else self.coverage_policy,
        )

    def rejected(self,code,fn,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as context:
            fn(*args,**kwargs)
        self.assertEqual(context.exception.code,code)

    def feature(self,name):
        return next(x for x in self.audit().as_dict()['feature_coverage'] if x['name']==name)

    def test_exact_scanned_included_excluded_matched_population(self):
        audit=self.audit();data=audit.as_dict()
        self.assertEqual(data['schema_version'],SCHEMA)
        self.assertIsInstance(audit,CandidateCoverageAudit)
        self.assertEqual((data['population']['scanned_n'],data['population']['included_n'],
                          data['population']['excluded_n'],data['population']['matched_n']), (3,2,1,2))
        self.assertEqual((data['population']['high_n'],data['population']['low_n']), (1,1))
        self.assertEqual(data['population']['pairs_n'],1)
        self.assertEqual(data['population']['unmatched_n'],0)
        self.assertEqual(data['population']['neutral_n'],0)
        self.assertEqual(data['population']['other_strata_n'],0)
        self.assertEqual(data['population']['excluded_by_reason'],{'policy_unavailable':1})
        self.assertEqual(data['population']['eligible_fraction_of_scanned'],'0.666667')
        self.assertEqual(data['population']['matched_fraction_of_included'],'1.000000')
        self.assertEqual(data['population']['unmatched_by_reason'],{})
        self.assertFalse(data['selection_bias_proven_absent'])
        with self.assertRaises(FrozenInstanceError):audit.matched_n=500

    def test_source_feature_coverage_uses_unmatched_eligible_denominator(self):
        feature=self.feature('cta_has_cta')
        self.assertEqual(feature['source_eligible'],{'eligible_n':2,'present_n':1,'null_n':1,'absent_n':0})
        self.assertEqual(feature['matched_roles']['high']['present_n'],1)
        self.assertEqual(feature['matched_roles']['high']['false_n'],1)
        self.assertEqual(feature['matched_roles']['low']['null_n'],1)
        self.assertEqual(feature['matched_roles']['low']['present_n'],0)
        self.assertEqual(feature['matched_roles']['low']['missing_fraction'],'1.000000')
        self.assertEqual(self.audit().as_dict()['offline_coverage_status'],'blocked')
        self.assertIn('excess_missingness:b.cta_has_cta:low',self.audit().as_dict()['blockers'])
        self.assertIn('insufficient_feature_coverage:b.cta_has_cta:low',self.audit().as_dict()['blockers'])

    def test_not_run_and_partial_cannot_be_inferred_from_absence(self):
        data=self.audit().as_dict()
        self.assertFalse(data['not_run_and_partial_states_available'])
        self.assertIn('unknown_missingness_mechanism',data['warnings'])
        for column in data['feature_coverage']:
            self.assertIsNone(column['not_run_n'])
            self.assertIsNone(column['partial_n'])
            self.assertEqual(column['missing_cause'],'not_observable_in_uae_to_pie_v1')
            self.assertEqual(column['not_run_or_partial_inference'],'forbidden')
            self.assertFalse(column['matched_vs_source_missingness_comparable'])

    def test_a_complete_observed_subset_passes_offline_thresholds_only(self):
        cols=tuple(c for c in self.selected.features if c.name!='cta_has_cta')
        policy=fm.policy(features=cols)
        feature=fm.FeatureMatrixTests.matrix(self,policy_=policy)
        audit=self.audit(matrix=feature)
        self.assertEqual(audit.offline_coverage_status,'pass')
        self.assertEqual(audit.as_dict()['blockers'],[])
        self.assertEqual(len(audit.as_dict()['feature_coverage']),5)
        self.assertFalse(audit.ready_for_analysis)
        self.assertFalse(audit.verified_live)
        self.assertIn('selection_bias_not_established',audit.as_dict()['warnings'])

    def test_high_and_low_roles_balance_only_by_pairing(self):
        b=self.audit().as_dict()['balance']
        self.assertEqual(b['role_n_difference'],0)
        self.assertEqual(b['creator_roles']['high'],{'n':1,'unique_creators':1,'maximum_posts_per_creator':1})
        self.assertEqual(b['creator_roles']['low'],{'n':1,'unique_creators':1,'maximum_posts_per_creator':1})
        self.assertEqual(b['shared_creator_n'],0)
        self.assertFalse(b['creator_adjustment_claimed'])
        self.assertFalse(b['statistical_balance_proven'])
        self.assertIn('uncontrolled_dimensions',self.audit().as_dict()['warnings'])

    def test_require_shared_creator_requires_true_overlap(self):
        audit=self.audit(policy=coverage_policy(require_shared_creators=True))
        self.assertIn('no_shared_creators',audit.as_dict()['blockers'])
        self.assertEqual(audit.offline_coverage_status,'blocked')

    def test_missing_threshold_0_vs_1_preserves_unobserved(self):
        strict=self.audit(policy=coverage_policy(max_missing_fraction='0'))
        permissive=self.audit(policy=coverage_policy(max_missing_fraction='1.000000'))
        self.assertIn('excess_missingness:b.cta_has_cta:low',strict.as_dict()['blockers'])
        self.assertNotIn('excess_missingness:b.cta_has_cta:low',permissive.as_dict()['blockers'])
        self.assertIn('insufficient_feature_coverage:b.cta_has_cta:low',permissive.as_dict()['blockers'])
        self.assertEqual(strict.as_dict()['feature_coverage'][3]['matched_roles']['low']['null_n'],1)

    def test_minimum_group_and_matched_size_fail_closed(self):
        audit=self.audit(policy=coverage_policy(min_matched_rows=4,min_rows_per_role=2))
        self.assertIn('insufficient_matched_n',audit.as_dict()['blockers'])
        self.assertIn('insufficient_high_n',audit.as_dict()['blockers'])
        self.assertIn('insufficient_low_n',audit.as_dict()['blockers'])

    def test_suppressed_subgroup_cells_explicit_none_not_zero(self):
        audit=self.audit(policy=coverage_policy(min_cell_disclosure_n=2))
        rows=audit.as_dict()['feature_coverage'][0]['matched_roles']
        self.assertTrue(rows['high']['suppressed'])
        self.assertTrue(rows['low']['suppressed'])
        self.assertIsNone(rows['high']['present_n'])
        self.assertIsNone(rows['high']['missing_fraction'])
        self.assertEqual(rows['high']['n'],1)
        self.assertNotEqual(audit.artifact_sha256,self.audit().artifact_sha256)

    def test_false_and_empty_list_are_present_not_missing(self):
        a=self.audit().as_dict()['feature_coverage']
        by={x['name']:x for x in a}
        self.assertEqual(by['cta_has_cta']['matched_roles']['high']['false_n'],1)
        self.assertEqual(by['hook_mechanisms']['matched_roles']['low']['empty_sequence_n'],1)
        self.assertEqual(by['story_roles']['matched_roles']['low']['null_n'],0)
        self.assertEqual(by['duration_sec']['matched_roles']['low']['zero_n'],0)

    def test_zero_numeric_is_observed_not_implicitly_null(self):
        def change(rows):
            for row in rows: row['features_a']['duration_sec']=0
        snapshot,strata,outcome=ct.ControlAssemblyTests.expanded(self,change)
        controls=build_candidate_controls(snapshot,strata,outcome,ct.match_policy())
        matrix=build_candidate_feature_matrix(snapshot,strata,outcome,controls,self.selected)
        audit=self.audit(snapshot=snapshot,strata=strata,outcome=outcome,controls=controls,matrix=matrix)
        col=next(c for c in audit.as_dict()['feature_coverage'] if c['name']=='duration_sec')
        self.assertEqual(col['matched_roles']['high']['zero_n'],1)
        self.assertEqual(col['matched_roles']['low']['zero_n'],1)
        self.assertEqual(col['matched_roles']['high']['present_n'],1)
        self.assertEqual(col['matched_roles']['low']['present_n'],1)

    def test_absent_vs_null_with_integrity_correct_changed_source(self):
        def change(rows):rows[1]['features_b'].pop('cta_has_cta')
        snapshot,strata,outcome=ct.ControlAssemblyTests.expanded(self,change)
        controls=build_candidate_controls(snapshot,strata,outcome,ct.match_policy())
        cols=tuple(replace(col,allow_absent=True) if col.name=='cta_has_cta' else col for col in self.selected.features)
        matrix=build_candidate_feature_matrix(snapshot,strata,outcome,controls,fm.policy(features=cols))
        audit=self.audit(snapshot=snapshot,strata=strata,outcome=outcome,controls=controls,matrix=matrix)
        col=next(c for c in audit.as_dict()['feature_coverage'] if c['name']=='cta_has_cta')
        self.assertEqual(col['source_eligible'],{'eligible_n':2,'present_n':1,'null_n':0,'absent_n':1})
        self.assertEqual(col['matched_roles']['low']['absent_n'],1)
        self.assertEqual(col['matched_roles']['low']['null_n'],0)

    def test_unmatched_high_retained_and_threshold_blocks(self):
        def change(rows):
            extra=deepcopy(rows[0]);extra.update(video_id=104,uae_snapshot_id=1104,
                metric_snapshot_id=2104,calculated_metric_id=3104,breakout_score='240.00')
            rows.append(extra)
        snapshot,strata,outcome=ct.ControlAssemblyTests.expanded(self,change)
        controls=build_candidate_controls(snapshot,strata,outcome,ct.match_policy(require_all_high_matched=False))
        matrix=build_candidate_feature_matrix(snapshot,strata,outcome,controls,self.selected)
        audit=self.audit(snapshot=snapshot,strata=strata,outcome=outcome,controls=controls,matrix=matrix)
        p=audit.as_dict()['population']
        self.assertEqual((p['scanned_n'],p['included_n'],p['matched_n'],p['unmatched_n']), (4,3,2,1))
        self.assertEqual(p['matched_fraction_of_included'],'0.666667')
        self.assertIn('unmatched_high_cases',audit.as_dict()['blockers'])
        self.assertEqual(audit.as_dict()['offline_coverage_status'],'blocked')
        self.assertEqual(len(audit.as_dict()['population']['excluded_by_reason']),1)

    def test_replay_determinism_and_frozen_identity(self):
        first=self.audit();second=self.audit()
        self.assertEqual(first,second)
        self.assertEqual(first.artifact_sha256,canonical_hash(first.as_dict()))
        self.assertEqual(replay_candidate_coverage_audit(first.to_json(),first.artifact_sha256,
                          self.snapshot,self.strata,self.outcome,self.controls,self.matrix_obj),first)
        self.assertEqual(replay_candidate_coverage_audit(first.to_json().encode(),first.artifact_sha256,
                          self.snapshot,self.strata,self.outcome,self.controls,self.matrix_obj),first)
        obj=first.as_dict();obj['feature_coverage'].clear()
        self.assertNotEqual(obj,first.as_dict())
        self.assertEqual(first.artifact_sha256,canonical_hash(first.as_dict()))

    def test_version_and_design_change_alters_fingerprint(self):
        a=self.audit()
        b=self.audit(policy=coverage_policy(policy_version='coverage/v2'))
        c=self.audit(policy=coverage_policy(min_cell_disclosure_n=2))
        self.assertNotEqual(a.artifact_sha256,b.artifact_sha256)
        self.assertNotEqual(a.artifact_sha256,c.artifact_sha256)
        self.rejected('coverage_design_mismatch',self.audit,coverage_policy(design_sha256='d'*64))

    def test_resealed_hash_attack_cannot_replace_counts_or_source(self):
        base=self.audit()
        alterations=[
            lambda obj:obj['population'].update({'scanned_n':99}),
            lambda obj:obj['balance'].update({'statistical_balance_proven':True}),
            lambda obj:obj['feature_coverage'][0]['matched_roles']['high'].update({'null_n':12}),
            lambda obj:obj['feature_coverage'][0].update({'not_run_n':0}),
            lambda obj:obj['feature_coverage'].pop(),
            lambda obj:obj.update({'offline_coverage_status':'pass'}),
            lambda obj:obj['warnings'].clear(),
        ]
        for mutate in alterations:
            sample=base.as_dict();mutate(sample)
            with self.subTest(mutate=mutate):
                self.rejected('coverage_replay_mismatch',replay_candidate_coverage_audit,
                              json.dumps(sample),canonical_hash(sample),
                              self.snapshot,self.strata,self.outcome,self.controls,self.matrix_obj)

    def test_json_attack_and_unauthorized_state_rejected(self):
        base=self.audit()
        self.rejected('coverage_hash_mismatch',replay_candidate_coverage_audit,
                      base.to_json(),'0'*64,self.snapshot,self.strata,self.outcome,self.controls,self.matrix_obj)
        self.rejected('duplicate_json_key',replay_candidate_coverage_audit,
                      '{"a":1,"a":2}','0'*64,self.snapshot,self.strata,self.outcome,self.controls,self.matrix_obj)
        self.rejected('invalid_coverage_audit',replay_candidate_coverage_audit,
                      '{"a":NaN}','0'*64,self.snapshot,self.strata,self.outcome,self.controls,self.matrix_obj)
        v=base.as_dict();v['approved_live']=True
        self.rejected('unsupported_coverage_audit',replay_candidate_coverage_audit,
                      json.dumps(v),canonical_hash(v),self.snapshot,self.strata,self.outcome,self.controls,self.matrix_obj)

    def test_invalid_policy_rejected(self):
        examples=[
            {'policy_version':''},{'design_sha256':'fake'},
            {'min_matched_rows':0},{'min_rows_per_role':True},
            {'min_present_per_role_per_feature':0},{'min_cell_disclosure_n':-1},
            {'max_missing_fraction':'0.1234567'}, {'max_missing_fraction':'NaN'},
            {'max_missing_fraction':'1.5'},{'max_missing_fraction':0.5},
            {'require_shared_creators':None}, {'require_all_high_matched':1},
            {'min_matched_rows':1,'min_rows_per_role':1},
        ]
        for change in examples:
            with self.subTest(change=change):
                self.rejected('invalid_coverage_policy',coverage_policy,**change)
        self.rejected('invalid_coverage_policy',self.audit,object())

    def test_reject_untrusted_matrix_or_wrong_input(self):
        self.rejected('invalid_coverage_input',build_candidate_coverage_audit,
                      self.snapshot,self.strata,self.outcome,self.controls,object(),self.coverage_policy)
        self.rejected('invalid_coverage_input',build_candidate_coverage_audit,
                      self.snapshot,self.strata,self.outcome,self.controls,None,self.coverage_policy)
        self.rejected('invalid_coverage_input',build_candidate_coverage_audit,
                      object(),self.strata,self.outcome,self.controls,self.matrix_obj,self.coverage_policy)

    def test_no_production_or_inferential_claims(self):
        audit=self.audit()
        self.assertEqual(audit.as_dict()['evidence_state'],'unverified')
        self.assertIs(audit.as_dict()['approved_live'],False)
        self.assertFalse(audit.verified_live)
        self.assertFalse(audit.ready_for_analysis)
        self.assertFalse(audit.as_dict()['balance']['statistical_balance_proven'])
        for name in ('publish','calculate_p_value','run_inference','mark_authorized','impute'):
            self.assertFalse(hasattr(audit,name))


if __name__=='__main__':
    unittest.main()
