"""M30 exact C9 x video coverage and source/receipt-backed one-pair gates.

No live requests, OpenRouter keys, remote semantic validation or model POST.
Full SQL transitions require separate disposable PostgreSQL17 acceptance.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from copy import deepcopy

from intelligence.relevance.live_verified_coverage import (
    VERSION,_coverage,commit_verified_live_sre_pair,
    inspect_live_sre_coverage,finalize_live_sre_coverage,
)
from intelligence.relevance.v1_contracts import SREContractError

PROJECT="a"*32
RUN="11111111-1111-4111-8111-111111111111"
RECEIPT="22222222-2222-4222-8222-222222222222"
KEYS=[
    "00000000-0000-4000-8000-000000000011",
    "00000000-0000-4000-8000-000000000012",
    "00000000-0000-4000-8000-000000000013",
]
SHA="f"*64
def fixture():
    items=[(10,1,11,SHA,"running",KEYS)]
    pairs=[(10,key,"not_evaluated") for key in KEYS]
    return items,pairs,[]


def verified(key):
    return (10,key,"match",RECEIPT,SHA,SHA,
            "validated_untrusted_output",True)


class FakeIdleDB:
    autocommit=False
    def transaction(self):raise AssertionError("must not open PG transaction")


class M30CoverageContracts(unittest.TestCase):
    def test_zero_receipts_never_means_no_match_or_completed(self):
        items,pairs,results=fixture()
        r=_coverage(items,pairs,results,0)
        self.assertEqual(r["status"],"not_evaluated")
        self.assertEqual(r["expected_pair_count"],3)
        self.assertEqual(r["verified_pair_count"],0)
        self.assertEqual(r["unverified_pair_count"],3)
        self.assertFalse(r["all_pair_evidence_present"])
        self.assertFalse(r["run_completed"])
        self.assertFalse(r["dispatch_allowed"])

    def test_one_of_three_verified_is_partial_candidate_never_completed(self):
        items,pairs,results=fixture()
        pairs[0]=(10,KEYS[0],"match")
        results=[verified(KEYS[0])]
        r=_coverage(items,pairs,results,0)
        self.assertEqual(r["status"],"partial_candidate_not_completed")
        self.assertEqual(r["verified_pair_count"],1)
        self.assertEqual(r["unverified_pair_count"],2)
        self.assertFalse(r["all_pair_evidence_present"])
        self.assertFalse(r["run_completed"])
        self.assertFalse(r["automatic_retry_allowed"])

    def test_actual_complete_requires_all_three_original_proofs(self):
        items,pairs,results=fixture()
        pairs=[(10,k,"match") for k in KEYS]
        results=[verified(k) for k in KEYS]
        r=_coverage(items,pairs,results,0)
        self.assertEqual(r["status"],"complete_candidate_requires_terminal_checks")
        self.assertEqual(r["verified_pair_count"],3)
        self.assertTrue(r["all_pair_evidence_present"])
        self.assertFalse(r["run_completed"])
        self.assertFalse(r["dispatch_allowed"])

    def test_unknown_paid_or_rights_budget_block_honestly(self):
        items,pairs,results=fixture()
        p=deepcopy(pairs)
        p[0]=(10,KEYS[0],"match")
        v=[verified(KEYS[0])]
        self.assertEqual(_coverage(items,p,v,1)["status"],
                         "blocked_unknown_provider_charge")
        p[1]=(10,KEYS[1],"blocked_rights")
        outcome=_coverage(items,p,v,0)
        self.assertEqual(outcome["blocked_pair_count"],1)
        self.assertEqual(outcome["status"],"blocked_rights_or_budget")

    def test_wrong_proof_or_category_matrix_is_not_semantic_no_match(self):
        items,pairs,results=fixture()
        p=deepcopy(pairs)
        p[0]=(10,KEYS[0],"no_match")
        for attempted in (
            [verified(KEYS[0])],
            [(*verified(KEYS[0])[:4],"0"*64,*verified(KEYS[0])[5:])],
            [(*verified(KEYS[0])[:6],"invalid_untrusted_output",True)],
            [(*verified(KEYS[0])[:7],False)],
        ):
            with self.subTest(proof=attempted),self.assertRaises(SREContractError):
                _coverage(items,p,attempted,0)
        with self.assertRaises(SREContractError):
            _coverage(items,pairs[1:],results,0)
        with self.assertRaises(SREContractError):
            _coverage(items,pairs+[pairs[0]],results,0)
        with self.assertRaises(SREContractError):
            _coverage(items,pairs,[verified(KEYS[0])],0)
        with self.assertRaises(SREContractError):
            _coverage(items,pairs,results,-1)
        with self.assertRaises(SREContractError):
            _coverage(items,pairs,results,True)

    def test_cross_video_and_duplicate_categories_rejected(self):
        items,pairs,results=fixture()
        with self.assertRaises(SREContractError):
            _coverage([items[0],items[0]],pairs,results,0)
        with self.assertRaises(SREContractError):
            _coverage([(10,1,11,SHA,"running",KEYS+KEYS[:1])],pairs,results,0)
        with self.assertRaises(SREContractError):
            _coverage(items,pairs+[(999,KEYS[0],"not_evaluated")],results,0)

    def test_owner_permission_and_idle_connection_fail_before_mutation(self):
        def deny(*args):return False
        with self.assertRaises(SREContractError) as denied:
            inspect_live_sre_coverage(
                FakeIdleDB(),PROJECT,RUN,actor_id="guest",
                actor_authorizer=deny)
        self.assertEqual(denied.exception.code,"actor_not_authorized")
        with self.assertRaises(SREContractError):
            finalize_live_sre_coverage(
                FakeIdleDB(),PROJECT,RUN,
                scope="run",target_state="completed",actor_id="guest",
                actor_authorizer=deny)
        with self.assertRaises(SREContractError):
            commit_verified_live_sre_pair(
                FakeIdleDB(),PROJECT,RUN,10,RECEIPT,{},KEYS[0],{},
                operator_expected_sha256=SHA,actor_id="guest",
                actor_authorizer=deny)

    def test_import_does_not_need_paid_model_or_provider_packages(self):
        completed=subprocess.run([sys.executable,"-c",
            "import sys;import intelligence.relevance.live_verified_coverage;"
            "assert 'httpx' not in sys.modules;"
            "assert 'requests' not in sys.modules;"
            "assert 'openai' not in sys.modules;"],
            capture_output=True,text=True,timeout=15)
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertEqual(VERSION,"sre-live-verified-coverage/v1")


if __name__=="__main__":
    unittest.main()
