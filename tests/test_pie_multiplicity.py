"""PIE-MOD-16: synthetic BH reference examples and fail-closed frozen-family QA.

Every upstream replay below is mocked on purpose. This is NOT a live source,
not a statistical p-value producer, and not complete PIE integration.
"""
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from decimal import getcontext, ROUND_DOWN
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from intelligence.patterns_insights.contracts import PIEBoundaryError, canonical_hash
from intelligence.patterns_insights.research.marginal import CandidateMarginalDiscovery
from intelligence.patterns_insights.research.effects import CandidateDescriptiveEffects
from intelligence.patterns_insights.research.uncertainty import CandidateUncertaintyIntervals
from intelligence.patterns_insights.research.multiplicity import (
    SCHEMA, MultiplicityPolicy, SyntheticPValue, SyntheticPValueEvidence,
    CandidateMultiplicityReport, benjamini_hochberg,
    build_candidate_multiplicity_report, replay_candidate_multiplicity_report,
)

PROJECT = 'a' * 32
FAMILY = 'f' * 64
DESIGN = 'c' * 64
IDS = ('h_a', 'h_b', 'h_c', 'h_d')


def _typed(cls, obj, *fields):
    raw = json.dumps(obj, sort_keys=True, separators=(',', ':'))
    return cls(canonical_hash(obj), *fields, raw)


def synthetic_sources(ids=IDS):
    d={'project_id':PROJECT,'policy':{'design_sha256':DESIGN},
       'hypothesis_family_sha256':FAMILY, 'registered_hypothesis_count':len(ids),
       'hypotheses':[{'hypothesis':{'hypothesis_id':i}} for i in ids]}
    discovery=_typed(CandidateMarginalDiscovery,d,PROJECT,'a'*64,'b'*64,'c'*64,len(ids),8)
    e={'project_id':PROJECT, 'source_discovery_sha256':discovery.artifact_sha256,
       'policy':{'design_sha256':DESIGN},'registered_hypothesis_family_sha256':FAMILY,
       'registered_hypothesis_count':len(ids), 'effects':[{'hypothesis_id':i} for i in ids],
       'p_values':None}
    effects=_typed(CandidateDescriptiveEffects,e,PROJECT,discovery.artifact_sha256,'d'*64,len(ids))
    u={'schema_version':'pie-paired-bootstrap-uncertainty/v1','evidence_state':'unverified',
       'approved_live':False,'project_id':PROJECT,'source_effects_sha256':effects.artifact_sha256,
       'source_discovery_sha256':discovery.artifact_sha256,
       'registered_hypothesis_family_sha256':FAMILY, 'registered_hypothesis_count':len(ids),
       'policy':{'design_sha256':DESIGN},
       'all_hypotheses':[{'hypothesis_id':i} for i in ids],
       'p_values':None,'adjusted_p_values':None}
    uncertainty=_typed(CandidateUncertaintyIntervals,u,PROJECT,effects.artifact_sha256,'e'*64,len(ids))
    return (SimpleNamespace(project_id=PROJECT),None,None,None,None,None,discovery,effects,uncertainty)


def policy(**updates):
    data={'policy_version':'bh/v1', 'method_version':'bh-exact-decimal/v1',
          'design_sha256':DESIGN, 'hypothesis_family_sha256':FAMILY,
          'family_size':4, 'fdr_level':'0.05'}
    data.update(updates)
    return MultiplicityPolicy(**data)


def invented(values=('0.01','0.02','0.03','0.20'),src=None):
    s = src or synthetic_sources()
    return SyntheticPValueEvidence('synthetic-golden-v1',FAMILY,s[6].artifact_sha256,
            s[7].artifact_sha256,s[8].artifact_sha256,
            tuple(SyntheticPValue(i,v) for i,v in zip(IDS,values)))


class MultiplicityTests(unittest.TestCase):
    def setUp(self):
        self.sources=synthetic_sources()
        self.policy=policy()

    def fail_code(self,code,fn,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as err:fn(*args,**kwargs)
        self.assertEqual(err.exception.code,code)

    def build(self,*,sources=None,selected=None,fixture=None):
        sources = sources or self.sources
        with patch('intelligence.patterns_insights.research.multiplicity.replay_candidate_uncertainty_intervals',
                   return_value=sources[8]):
            return build_candidate_multiplicity_report(*sources,selected or self.policy,fixture)

    def replay(self, report, sources=None):
        sources = sources or self.sources
        with patch('intelligence.patterns_insights.research.multiplicity.replay_candidate_uncertainty_intervals',
                   return_value=sources[8]):
            return replay_candidate_multiplicity_report(report.to_json(),report.artifact_sha256,*sources)

    def test_default_blocks_real_inference_not_invents_p_values(self):
        result=self.build()
        self.assertIsInstance(result,CandidateMultiplicityReport)
        self.assertEqual(result.family_size,4)
        self.assertFalse(result.verified_live)
        self.assertFalse(result.ready_for_analysis)
        p=result.as_dict()
        self.assertEqual(p['schema_version'],SCHEMA)
        self.assertEqual(p['correction_status'],'blocked_no_inferential_p_values')
        self.assertEqual(p['tested_hypothesis_count'],0)
        self.assertEqual(p['registered_hypothesis_count'],4)
        self.assertEqual([x['hypothesis_id'] for x in p['hypotheses']],list(IDS))
        self.assertTrue(all(x['p_value'] is None and x['adjusted_q_value'] is None for x in p['hypotheses']))
        self.assertIsNone(p['confirmed_discoveries'])
        self.assertFalse(p['can_publish_significance'])
        self.assertIsNone(p['source_p_values'])
        with self.assertRaises(FrozenInstanceError):result.family_size=5

    def test_independent_bh_golden_four_hypotheses(self):
        # Independent hand calculation sorted p=[.01,.02,.03,.20].
        # Raw scaled m*p/rank = [.04,.04,.04,.20]; reverse minima unchanged.
        f=invented()
        result=self.build(fixture=f)
        p=result.as_dict()
        self.assertEqual(p['correction_status'],'synthetic_fixture_arithmetic_not_inference')
        self.assertEqual(p['evidence_kind'],'synthetic_test_fixture_only')
        self.assertEqual([x['adjusted_q_value'] for x in p['hypotheses']],['0.04','0.04','0.04','0.20'])
        self.assertEqual([x['passes_synthetic_bh_threshold'] for x in p['hypotheses']],[True,True,True,False])
        self.assertEqual([x['bh_rank'] for x in p['hypotheses']],[1,2,3,4])
        self.assertEqual(p['tested_hypothesis_count'],4)
        self.assertFalse(p['can_publish_significance'])
        self.assertEqual(self.replay(result),result)

    def test_pinned_synthetic_golden_artifact_and_blocked_state(self):
        expected=json.loads((Path(__file__).parent/'fixtures'/'pie'/'pie_bh_golden_v1.json').read_text())
        self.assertEqual(expected['fixture_kind'],'synthetic_non_authoritative_upstream_replay_stubbed')
        synthetic=self.build(fixture=invented())
        blocked=self.build()
        self.assertEqual(synthetic.artifact_sha256,expected['expected_synthetic_artifact_sha256'])
        self.assertEqual(blocked.artifact_sha256,expected['expected_blocked_artifact_sha256'])
        self.assertEqual([x['adjusted_q_value'] for x in synthetic.as_dict()['hypotheses']],expected['expected_adjusted_q'])
        self.assertEqual([x['passes_synthetic_bh_threshold'] for x in synthetic.as_dict()['hypotheses']],expected['expected_synthetic_bh_threshold'])

    def test_bh_monotone_reverse_minima_and_input_order_preserved(self):
        values=(SyntheticPValue('h_a','0.01'),SyntheticPValue('h_b','0.03'),
                SyntheticPValue('h_c','0.02'),SyntheticPValue('h_d','0.80'))
        output=benjamini_hochberg(values,'0.05')
        self.assertEqual([r['hypothesis_id'] for r in output],list(IDS))
        self.assertEqual([r['adjusted_q_value'] for r in output],['0.04','0.04','0.04','0.80'])
        self.assertEqual([r['bh_rank'] for r in output],[1,3,2,4])

    def test_identical_p_ties_sorted_by_hypothesis_id(self):
        values=(SyntheticPValue('h_b','0.02'),SyntheticPValue('h_a','0.02'),
                SyntheticPValue('h_d','0.02'),SyntheticPValue('h_c','0.02'))
        rows=benjamini_hochberg(values,'0.05')
        self.assertEqual([r['bh_rank'] for r in rows],[2,1,4,3])
        self.assertEqual([r['adjusted_q_value'] for r in rows],['0.02']*4)

    def test_equal_zero_one_extremes_and_zero_not_division(self):
        v=(SyntheticPValue('h_a','0'),SyntheticPValue('h_b','0'),
           SyntheticPValue('h_c','1'),SyntheticPValue('h_d','1'))
        rows=benjamini_hochberg(v,'0.01')
        self.assertEqual([r['adjusted_q_value'] for r in rows],['0','0','1','1'])

    def test_bh_precision_independent_of_global_context(self):
        base=benjamini_hochberg(tuple(SyntheticPValue(i,v) for i,v in
              zip(IDS,('0.000000000001','0.000000000002','0.123456789012','0.999999999999'))),'0.05')
        ctx=getcontext().copy()
        try:
            getcontext().prec=6
            getcontext().rounding=ROUND_DOWN
            self.assertEqual(base,benjamini_hochberg(tuple(SyntheticPValue(i,v) for i,v in
              zip(IDS,('0.000000000001','0.000000000002','0.123456789012','0.999999999999'))),'0.05'))
        finally:
            getcontext().prec=ctx.prec
            getcontext().rounding=ctx.rounding

    def test_no_posthoc_filtering_partial_vectors(self):
        src=self.sources;f=invented(src=src)
        self.fail_code('p_value_family_mismatch',self.build,fixture=replace(f,values=f.values[:-1]))
        self.fail_code('p_value_family_mismatch',self.build,fixture=replace(f,values=f.values[1:]))
        self.fail_code('p_value_family_mismatch',self.build,fixture=replace(f,values=(
            SyntheticPValue('h_a','0.01'),SyntheticPValue('h_b','0.02'),
            SyntheticPValue('h_c','0.03'),SyntheticPValue('h_z','0.20'))))

    def test_cross_project_lineage_mismatch_refused(self):
        f=invented(src=self.sources)
        self.fail_code('p_value_family_mismatch',self.build,fixture=replace(f,source_effects_sha256='0'*64))
        self.fail_code('p_value_family_mismatch',self.build,fixture=replace(f,source_uncertainty_sha256='0'*64))
        self.fail_code('p_value_family_mismatch',self.build,fixture=replace(f,source_discovery_sha256='0'*64))
        self.fail_code('p_value_family_mismatch',self.build,fixture=replace(f,hypothesis_family_sha256='0'*64))

    def test_production_p_value_assertion_cannot_be_ingested(self):
        f=invented()
        self.fail_code('invalid_p_evidence',replace,f,provenance='production')
        self.fail_code('p_value_unavailable',self.build,fixture={'values':[{'p_value':'0.01'}]})

    def test_invalid_probability_numeric_text_and_alpha(self):
        for x in ('NaN','Infinity','-1','2','01','0.1234567890123','1.1','0e-3','',None,0.1,True):
            with self.subTest(value=x):
                self.fail_code('invalid_probability',SyntheticPValue,'h_a',x)
        for x in ('0','0.0','NaN','1.5',0.05,True):
            with self.subTest(alpha=x):
                self.fail_code('invalid_probability',policy,fdr_level=x)

    def test_missing_policy_versions_family_size_and_strategy_rejected(self):
        for data in ({'policy_version':''},{'method_version':'x y'},
             {'design_sha256':'bad'}, {'hypothesis_family_sha256':'a'},
             {'family_size':0}, {'family_size':65}, {'family_size':True},
             {'correction':'uncorrected'}, {'family_scope':'selected_hits_only'},
             {'missing_p_values':'discard_non_significant'}):
            with self.subTest(data=data):
                self.fail_code('invalid_multiplicity_policy',policy,**data)
        self.fail_code('invalid_multiplicity_policy',self.build,selected='bad')

    def test_snapshot_full_family_count_and_policy_hash_enforced(self):
        self.fail_code('multiplicity_policy_mismatch',self.build,selected=policy(family_size=3))
        self.fail_code('multiplicity_policy_mismatch',self.build,selected=policy(design_sha256='9'*64))
        self.fail_code('multiplicity_policy_mismatch',self.build,selected=policy(hypothesis_family_sha256='9'*64))

    def test_partial_source_family_and_order_poisoning_refused(self):
        for obj,attr in (('drop_source','hypotheses'),('drop_interval','all_hypotheses'),('reverse','effects')):
            sources=list(synthetic_sources())
            ix={'drop_source':6,'drop_interval':8,'reverse':7}[obj]
            original=sources[ix].as_dict()
            if obj=='drop_source':original['hypotheses'].pop()
            elif obj=='drop_interval':original['all_hypotheses'].pop()
            else:original['effects'].reverse()
            current=sources[ix]
            raw=json.dumps(original,sort_keys=True,separators=(',',':'))
            sources[ix]=replace(current,artifact_sha256=sha256(raw.encode()).hexdigest(),_json=raw)
            with self.subTest(type=obj):
                self.fail_code('multiplicity_family_mismatch' if obj=='drop_interval' else 'multiplicity_lineage_mismatch',
                               self.build,sources=tuple(sources))

    def test_lineage_replay_mismatch_blocks_before_correction(self):
        with patch('intelligence.patterns_insights.research.multiplicity.replay_candidate_uncertainty_intervals',
                   return_value=SimpleNamespace(artifact_sha256='not-same')):
            self.fail_code('multiplicity_lineage_mismatch',build_candidate_multiplicity_report,*self.sources,self.policy)

    def test_repeatable_identity_policy_and_fixture_mutations(self):
        a=self.build(fixture=invented());b=self.build(fixture=invented())
        self.assertEqual(a.to_json(),b.to_json())
        self.assertEqual(a.artifact_sha256,canonical_hash(a.as_dict()))
        self.assertEqual(a,self.replay(a))
        self.assertNotEqual(a.artifact_sha256,self.build(selected=policy(method_version='bh-exact-decimal/v2'),fixture=invented()).artifact_sha256)
        self.assertNotEqual(a.artifact_sha256,self.build(fixture=invented(('0.01','0.02','0.031','0.20'))).artifact_sha256)
        self.assertNotEqual(a.artifact_sha256,self.build().artifact_sha256)

    def test_replay_refuses_resealed_result_suppression(self):
        good=self.build(fixture=invented())
        tests=[lambda p:p['hypotheses'].pop(),
               lambda p:p['hypotheses'][0].update({'adjusted_q_value':'0'}),
               lambda p:p.update({'tested_hypothesis_count':1}),
               lambda p:p.update({'confirmed_discoveries':['h_a']}),
               lambda p:p.update({'correction_status':'production_bh_complete'}),
               lambda p:p.update({'source_effects_sha256':'0'*64})]
        for tamper in tests:
            obj=good.as_dict();tamper(obj)
            raw=json.dumps(obj,sort_keys=True,separators=(',',':'))
            with patch('intelligence.patterns_insights.research.multiplicity.replay_candidate_uncertainty_intervals',
                       return_value=self.sources[8]):
                with self.subTest(change=tamper), self.assertRaises(PIEBoundaryError):
                    replay_candidate_multiplicity_report(raw,canonical_hash(obj),*self.sources)

    def test_replay_duplicate_keys_nonfinite_and_invalid_bytes(self):
        with patch('intelligence.patterns_insights.research.multiplicity.replay_candidate_uncertainty_intervals',
                   return_value=self.sources[8]):
            self.fail_code('duplicate_json_key',replay_candidate_multiplicity_report,'{"a":1,"a":2}','0'*64,*self.sources)
            self.fail_code('invalid_multiplicity_artifact',replay_candidate_multiplicity_report,'{"a":NaN}','0'*64,*self.sources)
            self.fail_code('invalid_multiplicity_artifact',replay_candidate_multiplicity_report,b'\xff','0'*64,*self.sources)
            good=self.build()
            self.fail_code('multiplicity_hash_mismatch',replay_candidate_multiplicity_report,good.to_json(),'1'*64,*self.sources)

    def test_no_builtin_earlier_stage_p_value_producer(self):
        report=self.build().as_dict()
        self.assertTrue(all(row['status']=='not_tested_no_p_value_producer' for row in report['hypotheses']))
        self.assertNotIn('p_values',report['policy'])
        self.assertNotIn('publish',dir(self.build()))
        self.assertFalse(self.build().ready_for_analysis)


if __name__ == '__main__':
    unittest.main()
