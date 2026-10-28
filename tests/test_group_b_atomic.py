"""GB-MOD-22 pure atomic writer deterministic key/pins and no-silent-approval QA.

All work here is in-memory; no PostgreSQL/paid provider or network needed.
"""
from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager
from copy import deepcopy
import unittest

from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.validation import validate_bundle
from intelligence.group_b.persistence import (
    AtomicBError,persist_group_b_bundle,read_group_b_result,
)
from intelligence.group_b.persistence.repository import (
    _analysis_key,_fingerprints,_pins,_status_map,VERSION,
)
from intelligence.group_b.blocks.b8_topic import validate_b8_topic,B8TopicProposal
from test_group_b_b8 import source,context,output,response,Receipt,taxonomy


class Host:
    def verify_unified_block(self,block,manifest,candidate,packet,request):
        return block=="B8" and request=="host-request" and packet.verify_digest()
    def verify_unified_completion(self,manifest,reports,candidates):
        return False
    def verify_bundle_persistence(self,manifest,assessment,candidates,key):
        return True
    def verify_topic_persistence(self,analysis_id,manifest,registry,proposal):
        return True


class FakeConn:
    def __init__(self):
        self.commands=0
    @contextmanager
    def transaction(self):
        yield
    def execute(self,*args,**kwargs):
        self.commands+=1
        raise AssertionError("SQL must not start on failed proof")


class TestGroupBAtomicPure(unittest.TestCase):
    def setUp(self):
        self.m=source()
        self.packet=context(self.m)
        self.registry=taxonomy()
        self.c=validate_b8_topic(
            manifest=self.m,packet=self.packet,registry=self.registry,
            response=response(output(self.m)),receipt_verifier=Receipt(),
            delivered_image_refs=tuple(o.ref_sha256 for o in self.m.observations
                                       if o.kind=="sampled_frame"),
        )
        self.candidates={"B8":self.c}
        self.packets={"B8":self.packet}
        self.proofs={"B8":"host-request"}
        self.host=Host()
        self.assessment=validate_bundle(
            manifest=self.m,candidates=self.candidates,packets=self.packets,
            request_proofs=self.proofs,trusted_attestor=self.host,
        )

    def test_atomic_key_is_versioned_deterministic_and_source_pinned(self):
        fingerprints=_fingerprints(self.candidates,self.assessment,None,self.registry)
        self.assertEqual(_status_map(self.assessment)["B8"],"complete")
        self.assertEqual(_status_map(self.assessment)["B1"],"partial")
        k=_analysis_key(self.m,fingerprints,None,self.registry)
        self.assertEqual(k,_analysis_key(self.m,fingerprints,None,self.registry))
        self.assertTrue(k.startswith(VERSION+":"))
        self.assertLessEqual(len(k),250)
        changed=replace(self.m,source_id="some-other-source")
        changed=replace(changed,manifest_sha256=canonical_hash(changed.payload()))
        self.assertNotEqual(k,_analysis_key(changed,fingerprints,None,self.registry))
        p=_pins(self.m,self.assessment,fingerprints,None,self.registry)
        self.assertEqual(p["group_b_b8_topic_proposal_sha256"],self.c.proposal_sha256)
        self.assertEqual(p["group_b_project_id"],self.m.project_id)

    def test_quarantined_is_not_persistable_under_any_admission(self):
        report=self.assessment
        wrong=replace(report,blocks=tuple(replace(v,state="quarantined")
            if v.block=="B8" else v for v in report.blocks))
        with self.assertRaises(AtomicBError):
            _status_map(wrong)

    def test_forged_accepted_report_rejected_before_db_access(self):
        reports=list(self.assessment.blocks)
        reports[0]=replace(reports[0],state=reports[-1].state,
                           reason_codes=(),proposal_sha256="f"*64)
        forged=replace(self.assessment,blocks=tuple(reports),all_accepted=True,
                       can_complete=True)
        db=FakeConn()
        # Transaction context is available, but forged acceptance must fail
        # before SQL dispatch or any fake side effects.
        with self.assertRaises(AtomicBError):
            persist_group_b_bundle(
                db,manifest=self.m,assessment=forged,
                candidates=self.candidates,packets=self.packets,
                request_proofs=self.proofs,registry=self.registry,
                admission_verifier=self.host,
            )
        self.assertEqual(db.commands,0)

    def test_forged_provider_result_requires_fresh_unified_attestor(self):
        bad=deepcopy(self.c.payload)
        bad["topic_id"]=999999
        tampered=B8TopicProposal(bad,canonical_hash(bad),True)
        candidates={"B8":tampered}
        with self.assertRaises(AtomicBError):
            persist_group_b_bundle(
                FakeConn(),manifest=self.m,assessment=self.assessment,
                candidates=candidates,packets=self.packets,
                request_proofs=self.proofs,registry=self.registry,
                admission_verifier=self.host,
            )

    def test_missing_host_must_never_start_sql(self):
        class NoHost: pass
        with self.assertRaises(AtomicBError):
            persist_group_b_bundle(
                FakeConn(),manifest=self.m,assessment=self.assessment,
                candidates=self.candidates,packets=self.packets,
                request_proofs=self.proofs,registry=self.registry,
                admission_verifier=NoHost(),
            )

    def test_invalid_or_cross_project_read_args_deny(self):
        with self.assertRaises(AtomicBError):
            read_group_b_result(FakeConn(),project_id="",run_id="fake")
        with self.assertRaises(AtomicBError):
            read_group_b_result(FakeConn(),project_id="test",run_id="")


if __name__=="__main__":
    unittest.main()
