"""PIE-MOD-09: pure, synthetic-only outcome policy and design review tests."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
import unittest

from intelligence.patterns_insights import (
    PIEBoundaryError, PIEDatasetPointer, canonical_hash, validate_dataset_manifest,
    build_candidate_dataset, replay_candidate_snapshot,
)
from intelligence.patterns_insights.intake.provenance import SourceEligibilityGate
from intelligence.patterns_insights.research.strata import build_candidate_strata
from intelligence.patterns_insights.research.outcomes import (
    SCHEMA, OutcomeComparisonPolicy, DesignApprovalDecision,
    CandidateOutcomePlan, build_candidate_outcome_plan, replay_candidate_outcome_plan,
)
from test_pie_research_dataset import SOURCE, READ, NOW, Authority, design
from test_pie_strata import policy as strata_policy


class Approval:
    """In-memory fake review authority; never a real approval issuer."""
    def __init__(self, *, approved=True, revoked=False):
        self.count = 0
        self.approved = approved
        self.revoked = revoked
        self.change_at_second_read = False
        self.deny_on_second_read = False
        self.stale = False
        self.cross_project = False
        self.pre_registered_late = False
        self.expires = False

    def check_design(self, project_id, policy_sha256, snapshot_sha256, strata_sha256):
        self.count += 1
        checked = NOW - timedelta(hours=2) if self.stale else NOW
        return DesignApprovalDecision(
            project_id=('b'*32 if self.cross_project else project_id),
            policy_sha256=policy_sha256, design_sha256='c'*64,
            snapshot_sha256=snapshot_sha256, strata_sha256=strata_sha256,
            approval_id=('fake-v2' if self.change_at_second_read and self.count==2 else 'fake-review-001'),
            reviewer_id='synthetic-reviewer',
            preregistered_at=('2026-10-26T07:00:00+00:00' if self.pre_registered_late
                              else '2026-10-25T20:00:00+00:00'),
            reviewed_at='2026-10-26T07:01:00+00:00' if self.pre_registered_late else '2026-10-25T21:00:00+00:00',
            checked_at=checked.isoformat(),
            expires_at=(NOW - timedelta(seconds=1) if self.expires else NOW + timedelta(hours=1)).isoformat(),
            approved=self.approved and not (self.deny_on_second_read and self.count==2),
            active=True, reviewer_authorized=True, not_revoked=not self.revoked,
        )


class OutcomePolicyTests(unittest.TestCase):
    def setUp(self):
        fixture = deepcopy(SOURCE)
        fixture['manifest']['observations'][1]['content_kind'] = 'video'
        fixture['pointer']['uae_dataset_sha256'] = canonical_hash({
            'schema_version':'pie-row-dataset/v1',
            'observations':fixture['manifest']['observations'],
        })
        fixture['pointer']['uae_manifest_sha256'] = canonical_hash(fixture['manifest'])
        pointer = PIEDatasetPointer(**fixture['pointer'])
        read = replace(READ,dataset=validate_dataset_manifest(pointer,fixture['manifest']))
        self.snapshot=build_candidate_dataset(read,SourceEligibilityGate(Authority(), now=lambda:NOW),design())
        self.strata=build_candidate_strata(self.snapshot,strata_policy())
        self.assertEqual(self.strata.stratum_count,1)
        self.stratum=self.strata.as_dict()['strata'][0]['key_sha256']
        self.policy=OutcomeComparisonPolicy(
            policy_version='outcome-policy/v1',design_sha256='c'*64,
            outcome='breakout_score',outcome_definition_version='group-c-raw/v1',
            lower_bound='0.00',upper_bound='100.00',
            lower_group='at_or_below',upper_group='at_or_above',baseline_group='low',
            stratum_sha256=self.stratum,
            observation_start='2026-10-26T06:00:00+00:00',
            observation_end='2026-10-26T08:00:00+00:00',
            min_low_count=1,min_high_count=1,
        )

    def rejected(self,code,fn,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as caught:
            fn(*args,**kwargs)
        self.assertEqual(caught.exception.code,code)

    def build(self, *, policy=None, approval=None, snapshot=None, strata=None):
        return build_candidate_outcome_plan(snapshot or self.snapshot, strata or self.strata,
                                            policy or self.policy,approval or Approval(),now=NOW)

    def test_explicit_baseline_high_neutral_and_unbounded_c7(self):
        auth=Approval()
        result=self.build(approval=auth)
        self.assertEqual(auth.count,2)
        self.assertIsInstance(result,CandidateOutcomePlan)
        self.assertEqual(result.low_video_ids,(102,))
        self.assertEqual(result.high_video_ids,(101,))
        self.assertEqual(result.neutral_video_ids,())
        self.assertEqual(result.as_dict()['comparison']['low'][0]['outcome_raw'],'-3.25')
        self.assertEqual(result.as_dict()['comparison']['high'][0]['outcome_raw'],'250.50')
        self.assertEqual(result.as_dict()['comparison']['endpoint_rule'],
                         'low<=lower_bound;high>=upper_bound')
        self.assertEqual(result.as_dict()['comparison']['included_total'],2)
        self.assertEqual(result.as_dict()['comparison']['selected_total'],2)
        self.assertEqual(result.as_dict()['comparison']['original_exclusions'],
                         [{'video_id':103,'reason_code':'policy_unavailable'}])
        self.assertFalse(result.verified_live)
        self.assertFalse(result.ready_for_analysis)
        self.assertFalse(result.as_dict()['approved_live'])
        self.assertEqual(result.as_dict()['schema_version'],SCHEMA)
        with self.assertRaises(FrozenInstanceError):
            result.policy_sha256='0'*64

    def test_deterministic_approval_replay_no_external_calls(self):
        approval=Approval()
        first=self.build(approval=approval)
        second=self.build(approval=Approval())
        self.assertEqual(first,second)
        self.assertEqual(first.artifact_sha256,canonical_hash(first.as_dict()))
        self.assertEqual(replay_candidate_outcome_plan(first.to_json(),first.artifact_sha256,
                                                      self.snapshot,self.strata),first)
        self.assertEqual(approval.count,2)
        self.assertEqual(first.snapshot_sha256,self.snapshot.snapshot_sha256)
        self.assertEqual(first.strata_sha256,self.strata.artifact_sha256)

    def test_missing_or_fake_approval_never_allows_planning(self):
        self.rejected('capability_unavailable',build_candidate_outcome_plan,
                      self.snapshot,self.strata,self.policy,None,now=NOW)
        self.rejected('capability_unavailable',build_candidate_outcome_plan,
                      self.snapshot,self.strata,self.policy,object(),now=NOW)
        class Untyped:
            def check_design(self,*a):return {'approved':True}
        self.rejected('approval_unavailable',build_candidate_outcome_plan,
                      self.snapshot,self.strata,self.policy,Untyped(),now=NOW)

    def test_denied_revoked_or_authority_exception_blocks(self):
        self.rejected('approval_denied',self.build,approval=Approval(approved=False))
        self.rejected('approval_denied',self.build,approval=Approval(revoked=True))
        class Offline:
            def check_design(self,*a):raise RuntimeError('secret credential')
        self.rejected('approval_unavailable',self.build,approval=Offline())

    def test_mid_run_revocation_and_review_swap_block(self):
        authority=Approval();authority.deny_on_second_read=True
        self.rejected('approval_denied',self.build,approval=authority)
        self.assertEqual(authority.count,2)
        authority=Approval();authority.change_at_second_read=True
        self.rejected('approval_changed',self.build,approval=authority)

    def test_stale_expired_wrong_scope_and_late_preregistration(self):
        for flag,code in (('stale','approval_stale'),('expires','approval_stale'),
                          ('cross_project','approval_scope_mismatch'),
                          ('pre_registered_late','preregistration_invalid')):
            a=Approval();setattr(a,flag,True)
            with self.subTest(flag=flag):self.rejected(code,self.build,approval=a)

    def test_only_raw_C7_has_real_endpoint_in_current_wire(self):
        for outcome in ('creator_ratio','creator_adjusted_views'):
            p=replace(self.policy,outcome=outcome)
            with self.subTest(outcome=outcome):self.rejected('outcome_unavailable',self.build,policy=p)
        self.rejected('unsupported_outcome',replace,self.policy,outcome='views_per_hypothesis')

    def test_no_inferred_thresholds_or_implicit_baseline(self):
        for change,code in (
            ({'lower_bound':'100','upper_bound':'0'},'invalid_outcome_threshold'),
            ({'lower_bound':'100','upper_bound':'100'},'invalid_outcome_threshold'),
            ({'lower_bound':'NaN'},'invalid_outcome_threshold'),
            ({'lower_bound':'01'},'invalid_outcome_threshold'),
            ({'upper_bound':100.0},'invalid_outcome_threshold'),
            ({'baseline_group':'high'},'invalid_outcome_policy'),
            ({'lower_group':'below'},'invalid_outcome_policy'),
            ({'min_low_count':0},'invalid_outcome_policy'),
            ({'min_high_count':True},'invalid_outcome_policy'),
            ({'policy_version':''},'invalid_outcome_policy'),
            ({'design_sha256':'unverified'},'invalid_outcome_policy'),
        ):
            with self.subTest(change=change):self.rejected(code,replace,self.policy,**change)

    def test_explicit_window_and_outcome_version_change_identity(self):
        a=self.build()
        self.assertNotEqual(a.artifact_sha256,self.build(policy=replace(self.policy,
              policy_version='outcome-policy/v2')).artifact_sha256)
        self.assertNotEqual(a.artifact_sha256,self.build(policy=replace(self.policy,
              outcome_definition_version='group-c-raw/v2')).artifact_sha256)
        self.assertNotEqual(a.artifact_sha256,self.build(policy=replace(self.policy,
              lower_bound='-1.0')).artifact_sha256)
        self.rejected('invalid_study_window',replace,self.policy,
                      observation_start='2026-10-27T00:00:00+00:00',
                      observation_end='2026-10-26T08:00:00+00:00')
        self.rejected('incomparable_window',self.build,policy=replace(self.policy,
                      observation_start='2026-10-26T07:01:00+00:00'))
        self.rejected('invalid_study_window',self.build,policy=replace(self.policy,
                      observation_end='2026-10-26T10:00:00+00:00'))

    def test_missing_high_low_min_n_and_no_cherrypicking(self):
        self.rejected('insufficient_comparison',self.build,policy=replace(self.policy,upper_bound='300'))
        self.rejected('insufficient_comparison',self.build,policy=replace(self.policy,lower_bound='-100'))
        self.rejected('insufficient_comparison',self.build,policy=replace(self.policy,min_low_count=2))
        self.rejected('unknown_stratum',self.build,policy=replace(self.policy,stratum_sha256='f'*64))
        # Reusing the original two-kind snapshot must not let this study pool them.
        other_snap=build_candidate_dataset(READ,SourceEligibilityGate(Authority(),now=lambda:NOW),design())
        other_strata=build_candidate_strata(other_snap,strata_policy())
        self.rejected('unknown_stratum',self.build,snapshot=other_snap,strata=other_strata)

    def test_intermediate_score_kept_as_neutral_without_dropping_n(self):
        # A wider high threshold alone cannot invent neutral observations. The
        # original two samples have no intermediate scores, so fail min counts.
        self.rejected('insufficient_comparison',self.build,policy=replace(self.policy,
                    lower_bound='-10.0',upper_bound='300.0'))

    def test_neutral_samples_retained_in_full_population(self):
        source=deepcopy(SOURCE)
        rows=source['manifest']['observations']
        rows[1]['content_kind']='video'
        middle=deepcopy(rows[1])
        middle['video_id']=104
        middle['uae_snapshot_id']=1104
        middle['metric_snapshot_id']=2104
        middle['calculated_metric_id']=3104
        middle['breakout_score']='50.00'
        rows.append(middle)
        source['manifest']['selection']['included_count']=3
        source['manifest']['selection']['scanned_count']=4
        source['pointer']['uae_dataset_sha256']=canonical_hash({
            'schema_version':'pie-row-dataset/v1','observations':rows})
        source['pointer']['uae_manifest_sha256']=canonical_hash(source['manifest'])
        pointer=PIEDatasetPointer(**source['pointer'])
        data=validate_dataset_manifest(pointer,source['manifest'])
        read=replace(READ,dataset=data)
        from intelligence.patterns_insights import ResearchSampleWeight
        spec=design(weights=(ResearchSampleWeight(101,'1.000'),
                             ResearchSampleWeight(102,'0.50'),
                             ResearchSampleWeight(104,'1.000')))
        candidate=build_candidate_dataset(read,SourceEligibilityGate(Authority(),now=lambda:NOW),spec)
        strata=build_candidate_strata(candidate,strata_policy())
        self.assertEqual(strata.stratum_count,1)
        configured=replace(self.policy,stratum_sha256=strata.as_dict()['strata'][0]['key_sha256'])
        plan=self.build(snapshot=candidate,strata=strata,policy=configured)
        self.assertEqual(plan.high_video_ids,(101,))
        self.assertEqual(plan.low_video_ids,(102,))
        self.assertEqual(plan.neutral_video_ids,(104,))
        self.assertEqual(plan.as_dict()['comparison']['selected_total'],3)
        self.assertEqual(plan.as_dict()['comparison']['included_total'],3)
        self.assertEqual(len(plan.as_dict()['comparison']['original_exclusions']),1)
        self.assertEqual(plan,replay_candidate_outcome_plan(plan.to_json(),plan.artifact_sha256,candidate,strata))

    def test_offline_replay_rejects_resealed_tampering(self):
        result=self.build()
        mutations=(
            lambda p:p['comparison']['high'][0].update({'outcome_raw':'0'}),
            lambda p:p['comparison']['low'].clear(),
            lambda p:p['comparison'].update({'high_count':999}),
            lambda p:p['comparison']['original_exclusions'].clear(),
            lambda p:p['comparison'].update({'other_strata_video_ids':[101]}),
            lambda p:p['approval_claim'].update({'reviewer_authorized':False}),
        )
        for i,mutation in enumerate(mutations):
            data=result.as_dict();mutation(data)
            with self.subTest(i=i):
                self.rejected_any(replay_candidate_outcome_plan,json.dumps(data),
                                  canonical_hash(data),self.snapshot,self.strata)
        self.rejected('outcome_hash_mismatch',replay_candidate_outcome_plan,
                      result.to_json(),'f'*64,self.snapshot,self.strata)
        self.rejected('duplicate_json_key',replay_candidate_outcome_plan,
                      '{"a":1,"a":2}','f'*64,self.snapshot,self.strata)
        self.rejected('invalid_outcome_plan',replay_candidate_outcome_plan,
                      '{"a":NaN}','f'*64,self.snapshot,self.strata)

    def rejected_any(self,fn,*args):
        with self.assertRaises(PIEBoundaryError):fn(*args)

    def test_cannot_upgrade_offline_plan_to_live_or_publish(self):
        plan=self.build()
        for item in ('run_statistical_comparison','calculate_p_values',
                     'publish_insights','authorize', 'mark_verified'):
            self.assertFalse(hasattr(plan,item))
        p=plan.as_dict();p['approved_live']=True
        self.rejected('unsupported_outcome_plan',replay_candidate_outcome_plan,
                      json.dumps(p),canonical_hash(p),self.snapshot,self.strata)


if __name__=='__main__':
    unittest.main()
