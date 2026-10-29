"""PIE-MOD-10: matching tests use invented UAE fixtures, NEVER live approval."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
import unittest

from intelligence.patterns_insights import (
    PIEBoundaryError, PIEDatasetPointer, canonical_hash, validate_dataset_manifest,
    build_candidate_dataset, ResearchSampleWeight,
)
from intelligence.patterns_insights.intake.provenance import SourceEligibilityGate
from intelligence.patterns_insights.research.strata import build_candidate_strata
from intelligence.patterns_insights.research.outcomes import build_candidate_outcome_plan
from intelligence.patterns_insights.research.controls import (
    SCHEMA, MatchingPolicy, CandidateMatchedControls, build_candidate_controls,
    replay_candidate_controls, _maximum_matching,
)
from test_pie_research_dataset import SOURCE, READ, NOW, Authority, design
from test_pie_strata import policy as strata_policy
from test_pie_outcomes import OutcomePolicyTests, Approval


def match_policy(**changes):
    data={
        'policy_version':'matching/v1', 'creator_rule':'stratum_only_no_creator_control',
        'require_observed_niche':False, 'max_publication_gap_seconds':3600,
        'max_observation_gap_seconds':3600, 'minimum_pairs':1,
        'require_all_high_matched':True,
    }
    data.update(changes)
    return MatchingPolicy(**data)


class ControlAssemblyTests(unittest.TestCase):
    def setUp(self):
        # Reuse positive but clearly synthetic stage09 fixture, no live connector.
        OutcomePolicyTests.setUp(self)
        self.outcome=build_candidate_outcome_plan(
            self.snapshot,self.strata,self.policy,Approval(),now=NOW)
        self.match=match_policy()

    def rejected(self,code,fn,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as caught:
            fn(*args,**kwargs)
        self.assertEqual(caught.exception.code,code)

    def assemble(self,*,match=None,snapshot=None,strata=None,outcome=None):
        return build_candidate_controls(snapshot or self.snapshot,strata or self.strata,
                                       outcome or self.outcome,match or self.match)

    def expanded(self, mutation, *, low_min=1, high_min=1):
        """Rebuild a wholly NEW integrity-valid synthetic UAE source version."""
        from dataclasses import replace as dc_replace
        fixture=deepcopy(SOURCE)
        rows=fixture['manifest']['observations']
        rows[1]['content_kind']='video'
        mutation(rows)
        rows.sort(key=lambda row:row['video_id'])
        for row in rows:
            row['features_sha256']=canonical_hash({'a':row['features_a'],'b':row['features_b']})
        fixture['manifest']['selection']['included_count']=len(rows)
        fixture['manifest']['selection']['scanned_count']=len(rows)+len(fixture['manifest']['selection']['exclusions'])
        fixture['pointer']['uae_dataset_sha256']=canonical_hash({
            'schema_version':'pie-row-dataset/v1','observations':rows})
        fixture['pointer']['uae_manifest_sha256']=canonical_hash(fixture['manifest'])
        pointer=PIEDatasetPointer(**fixture['pointer'])
        dataset=validate_dataset_manifest(pointer,fixture['manifest'])
        read=dc_replace(READ,dataset=dataset)
        weights=tuple(ResearchSampleWeight(r['video_id'],'1.000') for r in rows)
        snapshot=build_candidate_dataset(read,SourceEligibilityGate(Authority(),now=lambda:NOW),design(weights=weights))
        strata=build_candidate_strata(snapshot,strata_policy())
        self.assertEqual(strata.stratum_count,1)
        key=strata.as_dict()['strata'][0]['key_sha256']
        outcome_policy=dc_replace(self.policy,stratum_sha256=key,min_low_count=low_min,min_high_count=high_min)
        outcome=build_candidate_outcome_plan(snapshot,strata,outcome_policy,Approval(),now=NOW)
        return snapshot,strata,outcome

    def test_basic_one_to_one_and_full_source_population_preserved(self):
        artifact=self.assemble()
        self.assertIsInstance(artifact,CandidateMatchedControls)
        self.assertEqual(artifact.pair_count,1)
        self.assertEqual(artifact.high_video_ids,(101,))
        self.assertEqual(artifact.low_video_ids,(102,))
        payload=artifact.as_dict()
        self.assertEqual(payload['schema_version'],SCHEMA)
        self.assertEqual(payload['evidence_state'],'unverified')
        self.assertIs(payload['approved_live'],False)
        self.assertEqual(payload['population']['scanned_total'],3)
        self.assertEqual(payload['population']['included_total'],2)
        self.assertEqual(payload['population']['high_total'],1)
        self.assertEqual(payload['population']['low_total'],1)
        self.assertEqual(payload['population']['original_excluded_total'],1)
        self.assertEqual(payload['original_exclusions'],[{'video_id':103,'reason_code':'policy_unavailable'}])
        self.assertEqual(payload['unmatched_cases'],[])
        self.assertEqual(payload['unmatched_controls'],[])
        self.assertFalse(artifact.verified_live)
        self.assertFalse(artifact.ready_for_analysis)
        with self.assertRaises(FrozenInstanceError):
            artifact.pair_count=2

    def test_pair_exact_row_lineage_and_weights(self):
        pair=self.assemble().as_dict()['pairs'][0]
        included={m['video_id']:m for m in self.snapshot.as_dict()['selection']['included']}
        self.assertEqual(pair['case_row_sha256'],included[101]['row_sha256'])
        self.assertEqual(pair['control_row_sha256'],included[102]['row_sha256'])
        self.assertEqual(pair['case_snapshot_id'],included[101]['uae_snapshot_id'])
        self.assertEqual(pair['control_snapshot_id'],included[102]['uae_snapshot_id'])
        self.assertEqual(pair['case_weight'],'1.000')
        self.assertEqual(pair['control_weight'],'0.50')
        self.assertEqual(pair['publication_gap_us'],0)
        self.assertEqual(pair['observation_gap_us'],0)
        self.assertNotIn('outcome_raw',pair)
        self.assertNotIn('breakout_score',pair)

    def test_deterministic_repeated_identity_and_replay(self):
        first=self.assemble()
        self.assertEqual(first,self.assemble())
        self.assertEqual(first.artifact_sha256,canonical_hash(first.as_dict()))
        self.assertEqual(replay_candidate_controls(first.to_json(),first.artifact_sha256,
                           self.snapshot,self.strata,self.outcome),first)
        self.assertNotEqual(self.assemble(match=match_policy(policy_version='matching/v2')).artifact_sha256,
                            first.artifact_sha256)
        self.assertNotEqual(self.assemble(match=match_policy(max_publication_gap_seconds=0)).artifact_sha256,
                            first.artifact_sha256)

    def test_different_creators_require_explicit_cohort_policy(self):
        self.rejected('insufficient_controls',self.assemble,match=match_policy(creator_rule='same_creator'))
        self.assertEqual(self.assemble().pair_count,1)

    def test_niche_missing_isolation_cannot_satisfy_strict_topic_control(self):
        self.rejected('missing_niche',self.assemble,match=match_policy(require_observed_niche=True))

    def test_time_gaps_and_bad_matching_configuration_denied(self):
        def mutate(rows):rows[1]['published_at']='2026-10-25T02:00:00+00:00'
        # The earlier stratum policy keeps both in day0 when age < 24h.
        # Choosing a 2 hour time shift instead would be a different age bucket
        # for the present 23-hour fixture; use 30 minutes within the same bucket.
        def safe_mutate(rows):rows[1]['published_at']='2026-10-25T08:30:00+00:00'
        candidate,strata,outcome=self.expanded(safe_mutate)
        self.rejected('insufficient_controls',self.assemble,snapshot=candidate,strata=strata,
                      outcome=outcome,match=match_policy(max_publication_gap_seconds=60))
        self.assertEqual(self.assemble(snapshot=candidate,strata=strata,
                         outcome=outcome,match=match_policy(max_publication_gap_seconds=1800)).pair_count,1)
        for kw in (
            {'policy_version':''}, {'creator_rule':'derived'},
            {'require_observed_niche':None}, {'max_publication_gap_seconds':-1},
            {'max_observation_gap_seconds':1.2}, {'minimum_pairs':0},
            {'minimum_pairs':True}, {'require_all_high_matched':1},
        ):
            with self.subTest(kw=kw):
                self.rejected('invalid_matching_policy',match_policy,**kw)
        self.rejected('invalid_matching_policy',build_candidate_controls,
                      self.snapshot,self.strata,self.outcome,object())

    def test_maximum_cardinality_augmenting_path_recovers_more_pairs(self):
        # H1->L1,L2; H2->L1 only. A single-pass greedy would match only H1.
        def mutate(rows):
            high,low=rows
            high['published_at']='2026-10-25T08:05:00+00:00'
            low['published_at']='2026-10-25T08:00:00+00:00'
            second_high=deepcopy(high);second_low=deepcopy(low)
            second_high.update(video_id=104,uae_snapshot_id=1104,metric_snapshot_id=2104,
                               calculated_metric_id=3104,published_at='2026-10-25T07:55:00+00:00',breakout_score='220.10')
            second_low.update(video_id=105,uae_snapshot_id=1105,metric_snapshot_id=2105,
                              calculated_metric_id=3105,published_at='2026-10-25T08:10:00+00:00',breakout_score='-5.00')
            rows.extend([second_high,second_low])
        snapshot,strata,outcome=self.expanded(mutate,low_min=2,high_min=2)
        artifact=self.assemble(snapshot=snapshot,strata=strata,outcome=outcome,
                               match=match_policy(max_publication_gap_seconds=300,minimum_pairs=2))
        self.assertEqual(artifact.pair_count,2)
        self.assertEqual([(p['case_video_id'],p['control_video_id']) for p in artifact.as_dict()['pairs']],
                         [(101,105),(104,102)])
        self.assertEqual(artifact.as_dict()['population']['scanned_total'],5)
        self.assertEqual(replay_candidate_controls(artifact.to_json(),artifact.artifact_sha256,
                         snapshot,strata,outcome),artifact)
        self.rejected('insufficient_controls',self.assemble,snapshot=snapshot,strata=strata,
                      outcome=outcome,match=match_policy(max_publication_gap_seconds=240,minimum_pairs=2))

    def test_unmatched_case_retained_explicitly_if_allowed(self):
        def mutate(rows):
            copy=deepcopy(rows[0]);copy.update(video_id=104,uae_snapshot_id=1104,
                metric_snapshot_id=2104,calculated_metric_id=3104,breakout_score='240.00')
            rows.append(copy)
        snapshot,strata,outcome=self.expanded(mutate)
        self.rejected('insufficient_controls',self.assemble,snapshot=snapshot,strata=strata,
                      outcome=outcome)
        artifact=self.assemble(snapshot=snapshot,strata=strata,outcome=outcome,
                              match=match_policy(require_all_high_matched=False))
        self.assertEqual(artifact.pair_count,1)
        p=artifact.as_dict()
        self.assertEqual(p['population']['high_total'],2)
        self.assertEqual(p['population']['low_total'],1)
        self.assertEqual(p['unmatched_cases'],[{'video_id':104,'reason_code':'control_capacity_exhausted'}])
        self.assertEqual(p['population']['included_total'],3)
        self.assertEqual(p['population']['original_excluded_total'],1)
        self.assertEqual(p['population']['scanned_total'],4)
        self.rejected('insufficient_controls',self.assemble,snapshot=snapshot,strata=strata,
                      outcome=outcome,match=match_policy(require_all_high_matched=False,minimum_pairs=2))

    def test_unmatched_control_retained_in_report(self):
        def mutate(rows):
            copy=deepcopy(rows[1]);copy.update(video_id=105,uae_snapshot_id=1105,
                metric_snapshot_id=2105,calculated_metric_id=3105,breakout_score='-7.00')
            rows.append(copy)
        snapshot,strata,outcome=self.expanded(mutate,low_min=2)
        artifact=self.assemble(snapshot=snapshot,strata=strata,outcome=outcome)
        self.assertEqual(artifact.pair_count,1)
        self.assertEqual(artifact.as_dict()['unmatched_controls'],[
            {'video_id':105,'reason_code':'not_selected_for_pair'}])

    def test_source_stratum_and_approval_hashes_pinned(self):
        result=self.assemble()
        p=result.as_dict()
        self.assertEqual(p['source_snapshot_sha256'],self.snapshot.snapshot_sha256)
        self.assertEqual(p['source_strata_sha256'],self.strata.artifact_sha256)
        self.assertEqual(p['source_outcome_sha256'],self.outcome.artifact_sha256)
        self.assertEqual(p['matching_policy_sha256'],self.match.policy_sha256)
        self.assertEqual(result.project_id,self.snapshot.project_id)
        self.assertEqual(p['stratum_sha256'],self.policy.stratum_sha256)
        self.rejected('outcome_hash_mismatch',self.assemble,
                      outcome=replace(self.outcome,artifact_sha256='f'*64))
        self.rejected('invalid_matching_input',build_candidate_controls,
                      self.snapshot,self.strata,object(),self.match)

    def test_changed_source_outcome_identity_changes_match_artifact(self):
        first=self.assemble()
        def mutate(rows):rows[0]['breakout_score']='251.00'
        snapshot,strata,outcome=self.expanded(mutate)
        second=self.assemble(snapshot=snapshot,strata=strata,outcome=outcome)
        self.assertNotEqual(first.artifact_sha256,second.artifact_sha256)
        self.assertEqual(second.high_video_ids,first.high_video_ids)
        self.assertEqual(second.low_video_ids,first.low_video_ids)

    def test_mutable_copy_never_changes_frozen_pair_proof(self):
        artifact=self.assemble()
        forged=artifact.as_dict()
        forged['pairs'][0]['control_row_sha256']='f'*64
        self.assertEqual(artifact.as_dict()['pairs'][0]['control_row_sha256'],
                         self.snapshot.as_dict()['selection']['included'][1]['row_sha256'])
        self.assertEqual(replay_candidate_controls(artifact.to_json(),artifact.artifact_sha256,
                         self.snapshot,self.strata,self.outcome),artifact)

    def test_replay_refuses_resealed_forged_memberships_weights_and_counts(self):
        a=self.assemble()
        changes=[
            lambda p:p['pairs'][0].update({'control_video_id':999}),
            lambda p:p['pairs'][0].update({'case_weight':'100000000'}),
            lambda p:p['pairs'][0].update({'publication_gap_us':999}),
            lambda p:p.update({'pair_count':88}),
            lambda p:p['population'].update({'high_total':88}),
            lambda p:p['original_exclusions'].clear(),
            lambda p:p.update({'source_outcome_sha256':'0'*64}),
            lambda p:p['unmatched_cases'].append({'video_id':999,'reason_code':'suppressed'}),
        ]
        for idx,change in enumerate(changes):
            obj=a.as_dict();change(obj)
            with self.subTest(idx=idx):
                self.rejected('matched_replay_mismatch',replay_candidate_controls,
                              json.dumps(obj),canonical_hash(obj),self.snapshot,self.strata,self.outcome)
        self.rejected('matched_hash_mismatch',replay_candidate_controls,a.to_json(),
                      '0'*64,self.snapshot,self.strata,self.outcome)

    def test_replay_rejects_duplicate_keys_nan_and_fake_live_state(self):
        a=self.assemble()
        self.rejected('duplicate_json_key',replay_candidate_controls,
                      '{"x":1,"x":2}','0'*64,self.snapshot,self.strata,self.outcome)
        self.rejected('invalid_matched_controls',replay_candidate_controls,
                      '{"x":NaN}','0'*64,self.snapshot,self.strata,self.outcome)
        data=a.as_dict();data['approved_live']=True
        self.rejected('unsupported_matched_controls',replay_candidate_controls,
                      json.dumps(data),canonical_hash(data),self.snapshot,self.strata,self.outcome)
        data=a.as_dict();data['matching_policy'].pop('minimum_pairs')
        self.rejected('invalid_matching_policy',replay_candidate_controls,
                      json.dumps(data),canonical_hash(data),self.snapshot,self.strata,self.outcome)
        self.rejected('invalid_matched_controls',replay_candidate_controls,
                      object(),'0'*64,self.snapshot,self.strata,self.outcome)

    def test_small_matching_graph_maximum_cardinality_independent_oracle(self):
        # Hand-calculated matching: greedy A->1 would strand B, augmenting path recovers 2.
        adjacency={101:[201,202],102:[201]}
        self.assertEqual(_maximum_matching(adjacency,[101,102]),{101:202,102:201})
        self.assertEqual(_maximum_matching({101:[],102:[201]},[101,102]),{102:201})
        self.assertEqual(_maximum_matching({101:[201],102:[201]},[101,102]),{101:201})

    def test_no_statistics_or_auto_release_or_fabricated_creator_ids(self):
        a=self.assemble()
        for api in ('calculate_p_values','estimate_uplift','publish_insight',
                    'mark_verified','authorize','run_matching_in_production'):
            self.assertFalse(hasattr(a,api))
        self.assertFalse(a.verified_live)
        self.assertFalse(a.ready_for_analysis)


if __name__=='__main__':
    unittest.main()
