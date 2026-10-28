"""PIE-MOD-35: independently calculated synthetic mathematical golden vectors.

All examples are INVENTED, reproducible and not source-attested, real p-values,
independent holdout evidence, or human release approval. No fixtures that
claim true correlations or creator-level independence.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
import json
import random
import unittest

from intelligence.patterns_insights.contracts import PIEBoundaryError,canonical_hash
from intelligence.patterns_insights.research.uncertainty import (
    _numeric_stats,_binary_stats,_draw_indices,_bounds,
)
from intelligence.patterns_insights.research.multiplicity import (
    SyntheticPValue,benjamini_hochberg,
)
from intelligence.patterns_insights.release_readiness import (
    GateReceipt,ReleaseGateError,assess_standalone_readiness,
    MACHINE_GATES,EXTERNAL_GATES,
)

HEAD='a'*64


def gold_pairs():
    """Hand oracle: (2−1,6−3,8−4,9−7) = (1,3,4,2); mean=5/2 ms."""
    high=(2,6,8,9)
    low=(1,3,4,7)
    creators=tuple(f'creator_{i}' for i in range(4))
    return tuple(zip(high,low,creators))


def gold_holdout():
    """Fixed prechosen non-overlapping creator/time/arm labels."""
    return (
      {'id':'d0','creator':'c0','day':1,'arm':'discovery'},
      {'id':'d1','creator':'c1','day':2,'arm':'discovery'},
      {'id':'c0','creator':'c2','day':8,'arm':'confirmation'},
      {'id':'c1','creator':'c3','day':9,'arm':'confirmation'},
    )


def audit_holdout(rows):
    """No post-selection and no duplicate creator across discovery/holdout."""
    ids=[r['id'] for r in rows]
    creator=[r['creator'] for r in rows]
    discovery=[r for r in rows if r['arm']=='discovery']
    holdout=[r for r in rows if r['arm']=='confirmation']
    return (len(set(ids))==len(ids) and len(set(creator))==len(creator)
            and bool(discovery) and bool(holdout)
            and max(x['day'] for x in discovery)<min(x['day'] for x in holdout)
            and all(x['day']>5 for x in holdout)
            and all(x['day']<5 for x in discovery))


def audit_drift(discovery:tuple[int,...],holdout:tuple[int,...])->Fraction:
    """Exact unweighted difference in hit rates; diagnostic only."""
    if not discovery or not holdout:
        raise ValueError("Nonempty frozen arms required")
    return Fraction(sum(holdout),len(holdout))-Fraction(sum(discovery),len(discovery))


class MathematicalGold(unittest.TestCase):
    def test_four_pair_difference_oracle_exact_fraction(self):
        pairs=gold_pairs()
        expected=Fraction(sum(h-l for h,l,_ in pairs),len(pairs))
        self.assertEqual(expected,Fraction(5,2))
        self.assertEqual(str(expected),'5/2')

    def test_fixed_unit_is_milliseconds_not_percent(self):
        value=Fraction(5,2)
        self.assertEqual(str(value)+' ms','5/2 ms')
        self.assertNotEqual(value,Fraction(250,100))

    def test_numeric_median_difference_is_not_mean_pair_difference(self):
        pairs=gold_pairs()
        median,tv=_numeric_stats([h for h,_,_ in pairs],
                                 [l for _,l,_ in pairs],[3,7])
        self.assertEqual(median,Decimal('3.5'))
        self.assertEqual(tv,Fraction(1,2))
        self.assertNotEqual(median,Decimal('2.5'))

    def test_independent_histogram_total_variation_hand_oracle(self):
        # High bins [1,1,2], low [1,3,0] with cutpoints 3 and 7.
        p=(Fraction(1,4),Fraction(1,4),Fraction(2,4))
        q=(Fraction(1,4),Fraction(3,4),Fraction(0,4))
        tv=sum((abs(a-b) for a,b in zip(p,q)),Fraction())/2
        self.assertEqual(tv,Fraction(1,2))
        self.assertEqual(_numeric_stats([2,6,8,9],[1,3,4,7],[3,7])[1],tv)

    def test_zero_baseline_ratio_remains_undefined(self):
        delta,ratio=_binary_stats([True,True,False,True],
                                   [False,False,False,False])
        self.assertEqual(delta,Fraction(3,4))
        self.assertIsNone(ratio)

    def test_exact_binary_risk_ratio_oracle(self):
        delta,ratio=_binary_stats([True,True,False,True],
                                   [True,False,False,False])
        self.assertEqual(delta,Fraction(1,2))
        self.assertEqual(ratio,Fraction(3,1))

    def test_bootstrap_stream_replay_with_fixed_seed(self):
        a=list(_draw_indices(20261028,4,40))
        b=list(_draw_indices(20261028,4,40))
        self.assertEqual(a,b)
        self.assertEqual(len(a),40)
        self.assertTrue(all(len(x)==4 for x in a))
        self.assertTrue(all(0<=i<4 for x in a for i in x))

    def test_bootstrap_seed_changes_frozen_sample(self):
        a=list(_draw_indices(20261028,4,40))
        b=list(_draw_indices(20261029,4,40))
        self.assertNotEqual(a,b)

    def test_percentile_nearest_rank_manual_bounds(self):
        # n=40 => indices [0,38], not an interpolated Gaussian CI.
        values=[Fraction(x,1) for x in range(40)]
        interval=_bounds(values,4,'fraction')
        self.assertEqual(interval['lower']['numerator'],0)
        self.assertEqual(interval['upper']['numerator'],38)

    def test_bh_four_synthetic_hypotheses_hand_calculated(self):
        p=('0.01','0.02','0.03','0.20')
        ids=('h_a','h_b','h_c','h_d')
        got=benjamini_hochberg(tuple(SyntheticPValue(i,v) for i,v in zip(ids,p)),'0.05')
        self.assertEqual([Decimal(x['adjusted_q_value']) for x in got],
                         [Decimal('0.04'),Decimal('0.04'),
                          Decimal('0.04'),Decimal('0.20')])
        self.assertEqual([x['passes_synthetic_bh_threshold'] for x in got],
                         [True,True,True,False])

    def test_bh_invariant_under_input_order_and_exact_tie_break(self):
        seq=tuple(SyntheticPValue(i,v) for i,v in [
            ('h_c','0.03'),('h_a','0.01'),('h_d','0.20'),('h_b','0.02')])
        baseline=benjamini_hochberg(seq,'0.05')
        inverse=benjamini_hochberg(tuple(reversed(seq)),'0.05')
        a={x['hypothesis_id']:x['adjusted_q_value'] for x in baseline}
        b={x['hypothesis_id']:x['adjusted_q_value'] for x in inverse}
        self.assertEqual(a,b)

    def test_bh_all_missing_values_cannot_be_fabricated(self):
        with self.assertRaises(PIEBoundaryError):
            benjamini_hochberg(tuple(SyntheticPValue('h_a','0.05') for _ in range(2)),'0.05')

    def test_bh_invalid_p_value_is_rejected(self):
        with self.assertRaises(PIEBoundaryError):
            SyntheticPValue('h_a','NaN')

    def test_golden_holdout_has_disjoint_creators(self):
        self.assertTrue(audit_holdout(gold_holdout()))

    def test_creator_leakage_rejected(self):
        rows=list(gold_holdout())
        rows[2]={**rows[2],'creator':'c0'}
        self.assertFalse(audit_holdout(rows))

    def test_future_to_past_temporal_leakage_rejected(self):
        rows=list(gold_holdout())
        rows[2]={**rows[2],'day':1}
        self.assertFalse(audit_holdout(rows))

    def test_duplicate_media_id_rejected_across_arms(self):
        rows=list(gold_holdout())
        rows[2]={**rows[2],'id':'d1'}
        self.assertFalse(audit_holdout(rows))

    def test_drift_shift_is_fraction_not_significance(self):
        delta=audit_drift((0,0,1,1),(1,1,1,0))
        self.assertEqual(delta,Fraction(1,4))
        self.assertNotEqual(delta,0)

    def test_identical_synthetic_holdout_rates_zero_drift(self):
        self.assertEqual(audit_drift((1,0,1,0),(0,1,0,1)),Fraction())

    def test_10000_rows_deterministic_and_bounded_by_pages(self):
        seed=20261028
        rnd=random.Random(seed)
        rows=[(i,rnd.randint(0,1)) for i in range(12000)]
        self.assertEqual(len(rows),12000)
        self.assertEqual(len({i for i,_ in rows}),12000)
        chunks=tuple(rows[x:x+80] for x in range(0,len(rows),80))
        self.assertEqual(len(chunks),150)
        self.assertTrue(all(len(chunk)<=80 for chunk in chunks))
        self.assertEqual(sum(len(chunk) for chunk in chunks),12000)
        check=random.Random(seed)
        self.assertEqual([x[1] for x in rows[:8]],[check.randint(0,1) for _ in range(8)])

    def test_10000_rows_hash_replay_identity(self):
        rows=[(x,x%5) for x in range(10000)]
        digest=canonical_hash({'sample_scope':'synthetic_only','rows':rows})
        self.assertEqual(digest,canonical_hash({'rows':list(rows),'sample_scope':'synthetic_only'}))
        rows[100]=(100,99)
        self.assertNotEqual(digest,canonical_hash({'sample_scope':'synthetic_only','rows':rows}))


class ReleaseGateTests(unittest.TestCase):
    def test_no_evidence_never_release_ready(self):
        x=assess_standalone_readiness(HEAD,())
        self.assertEqual(x.status,'blocked_unverified')
        self.assertEqual(x.gates_passed,0)
        self.assertEqual(x.gates_total,len(MACHINE_GATES|EXTERNAL_GATES))
        self.assertFalse(x.may_merge)
        self.assertFalse(x.may_publish)

    def test_one_machine_pass_does_not_imply_external_validity(self):
        r=GateReceipt('python_full_suite','passed','local_test_runner','b'*64)
        x=assess_standalone_readiness(HEAD,(r,))
        self.assertEqual(x.gates_passed,1)
        self.assertIn('uae_current_rights',x.missing_gates)
        self.assertFalse(x.may_merge)

    def test_external_attestation_cannot_be_self_declared_machine_result(self):
        with self.assertRaises(ReleaseGateError):
            GateReceipt('uae_current_rights','passed','local_test_runner','b'*64)

    def test_machine_gate_cannot_pass_from_human_claim(self):
        with self.assertRaises(ReleaseGateError):
            GateReceipt('pg17_two_worker_fencing','passed',
                        'independent_qualified_attestation','b'*64)

    def test_pass_requires_evidence_digest(self):
        with self.assertRaises(ReleaseGateError):
            GateReceipt('pg17_fresh','passed','local_test_runner',None)

    def test_duplicate_evidence_gates_are_rejected(self):
        r=GateReceipt('compileall','passed','local_test_runner','b'*64)
        with self.assertRaises(ReleaseGateError):
            assess_standalone_readiness(HEAD,(r,r))

    def test_failing_gate_overrides_any_partial_positive(self):
        receipts=(GateReceipt('compileall','passed','local_test_runner','a'*64),
                  GateReceipt('pg17_fresh','failed','local_test_runner','b'*64))
        x=assess_standalone_readiness(HEAD,receipts)
        self.assertEqual(x.status,'failed_validation')
        self.assertIn('pg17_fresh',x.failed_gates)
        self.assertFalse(x.may_merge)

    def test_all_machine_gates_pass_still_missing_independent_source(self):
        machine=tuple(GateReceipt(x,'passed','local_test_runner','b'*64)
                      for x in sorted(MACHINE_GATES))
        report=assess_standalone_readiness(HEAD,machine)
        self.assertEqual(report.gates_passed,len(MACHINE_GATES))
        self.assertIn('source_license_release',report.missing_gates)
        self.assertEqual(report.status,'blocked_unverified')

    def test_all_fields_present_still_no_automatic_merge_or_publication(self):
        receipts=tuple(GateReceipt(name,'passed',
                      'local_test_runner' if name in MACHINE_GATES
                      else 'independent_qualified_attestation','b'*64)
                      for name in sorted(MACHINE_GATES|EXTERNAL_GATES))
        report=assess_standalone_readiness(HEAD,receipts)
        self.assertEqual(report.status,'candidate_verified_pending_explicit_merge_authorization')
        self.assertFalse(report.may_merge)
        self.assertFalse(report.may_publish)
        self.assertFalse(report.can_run_paid_model)

    def test_release_gate_hash_is_deterministic_and_branch_bound(self):
        a=assess_standalone_readiness(HEAD,())
        b=assess_standalone_readiness(HEAD,())
        c=assess_standalone_readiness('c'*64,())
        self.assertEqual(a.artifact_sha256,b.artifact_sha256)
        self.assertNotEqual(a.artifact_sha256,c.artifact_sha256)
        self.assertEqual(a.artifact_sha256,sha256(a.to_json().encode()).hexdigest())

    def test_no_hidden_live_approval_flags(self):
        report=assess_standalone_readiness(HEAD,())
        x=report.as_dict()
        self.assertFalse(x['human_release_approved'])
        self.assertFalse(x['independently_verified_scientific_claim'])
        self.assertFalse(x['may_publish'])

    def test_no_actual_billing_or_login_in_mathematical_suite(self):
        root=Path(__file__).resolve().parents[1]
        src=(root/'intelligence/patterns_insights/release_readiness.py').read_text('utf-8')
        self.assertNotIn('import requests',src)
        self.assertNotIn('publish_insight(',src)
        self.assertNotIn('openrouter.ai',src)


if __name__=='__main__':
    unittest.main()
