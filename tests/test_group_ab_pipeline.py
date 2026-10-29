"""Offline GA->B contract tests: independent QDP v2 hashes and durable pin ACK."""
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from intelligence.group_a.contracts_v1 import canonical_sha256
from intelligence.group_a.group_b_exact_transport import GroupBBindDenied
from intelligence.group_ab.bridge import GroupABTransport, GroupABPipeline, require_scope

PID="a"*32
GA_RUN="00000000-0000-4000-8000-000000000011"
QDP_RUN="00000000-0000-4000-8000-000000000012"
B_RUN="00000000-0000-4000-8000-000000000013"
A_HASH="a"*64
B_HASH="b"*64
SNAP_HASH="c"*64
MANIFEST="d"*64

def event():
    return {
        "schema_version":"group_a.result_ready/v1",
        "ga_run_id":GA_RUN,"ga_result_id":101,
        "result_sha256":SNAP_HASH,
        "project_id":PID,"qdp_run_id":QDP_RUN,"video_id":55,
        "source_id":"source-1","source_policy_version_id":3,
        "manifest_sha256":MANIFEST,"content_kind":"video",
        "video_a_analysis_id":99,"carousel_a_analysis_id":None,
    }

def pin():
    return {
        "pinned":True,"b_run_id":B_RUN,"project_id":PID,
        "qdp_run_id":QDP_RUN,"video_id":55,"source_id":"source-1",
        "result_sha256":SNAP_HASH,"manifest_sha256":MANIFEST,
        "source_policy_version_id":3,"video_a_analysis_id":99,
        "carousel_a_analysis_id":None,
    }

class _Rows:
    def __init__(self, rows):
        self.rows=rows
    def fetchall(self):
        return self.rows

class _Conn:
    def __init__(self,rows=None):
        self.rows=rows if rows is not None else []
        self.sql=""
        self.args=None
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def transaction(self): return nullcontext()
    def execute(self,sql,args=None):
        self.sql=sql
        self.args=args
        return _Rows(self.rows)

class GroupABPipelineTests(TestCase):
    def test_selector_requires_independent_current_a_and_b_preparations(self):
        conn=_Conn([(B_RUN,)])
        transport=GroupABTransport(lambda:conn)
        selected=transport.select_b_run(event())
        self.assertEqual(selected,B_RUN)
        self.assertIn("pa.input_sha256",conn.sql)
        self.assertIn("pb.input_sha256",conn.sql)
        self.assertIn("pa.analysis_group='A'",conn.sql)
        self.assertIn("pb.analysis_group='B'",conn.sql)
        self.assertNotIn("r.qdp_input_sha256=i.preparation_sha256",conn.sql)
        self.assertEqual(conn.args,(3,3,3,GA_RUN,101,SNAP_HASH,PID,55,"source-1",QDP_RUN))

    def test_selector_rejects_missing_or_ambiguous_b(self):
        for rows in ([],[(B_RUN,),("00000000-0000-4000-8000-000000000014",)]):
            with self.subTest(rows=rows):
                with self.assertRaises(GroupBBindDenied):
                    GroupABTransport(lambda:_Conn(rows)).select_b_run(event())

    def test_qdp_currentness_needs_both_groups_not_matching_hashes(self):
        pipeline=GroupABPipeline(lambda:_Conn(),lambda:_Conn())
        scope={
            "project_id":PID,"qdp_run_id":QDP_RUN,"video_id":55,
            "source_id":"source-1","source_policy_version_id":3,
            "qdp_input_sha256":A_HASH,
        }
        def current(project,run,video,group,*,connect):
            return {
                "state":"ready","input_sha256":A_HASH if group=="A" else B_HASH,
                "inputs":{
                    "group":group,"result_schema":"qdp-preparation/v2",
                    "enrichment_source_id":"source-1","source_policy_version_id":3,
                    "media_asset_id":501,"media_sha256":"e"*64,
                    "media_kind":"video",
                },
            }
        with patch("intelligence.group_ab.bridge.read_current_preparation",
                   side_effect=current) as read:
            self.assertTrue(pipeline.verify_current(scope))
            self.assertEqual(read.call_count,2)
            denied=dict(scope,source_policy_version_id=4)
            self.assertFalse(pipeline.verify_current(denied))

    def test_bridge_never_fabricates_pin_on_replay(self):
        transport=GroupABTransport(lambda:_Conn())
        with patch.object(transport,"select_b_run",return_value=B_RUN),patch.object(
            transport,"read_b_committed_pin",return_value=pin()) as lookup,patch.object(
            transport,"bind") as bind:
            result=transport(event(),idempotency_key=GA_RUN+":101:"+SNAP_HASH)
        self.assertEqual(result["result_sha256"],SNAP_HASH)
        self.assertEqual(result["consumer_receipt_sha256"],canonical_sha256({
            "schema_version":"ga24-b-committed-exact-pin/v1",
            **{k:v for k,v in pin().items() if k!="pinned"},
            "commit_state":"exact_a_bound",
        }))
        bind.assert_not_called()
        self.assertEqual(lookup.call_count,2)

    def test_receipt_requires_verified_readback_after_commit(self):
        transport=GroupABTransport(lambda:_Conn())
        claim=SimpleNamespace(payload=event(),run_id=GA_RUN,ga_result_id=101,
                              result_sha256=SNAP_HASH)
        envelope={
            "scope":{
                "project_id":PID,"qdp_run_id":QDP_RUN,"video_id":55,
                "source_id":"source-1","source_policy_version_id":3,
                "manifest_sha256":MANIFEST,"qdp_input_sha256":A_HASH,
            },
            "claim":claim,"receipt":None,
        }
        with patch.object(transport,"select_b_run",return_value=B_RUN),patch.object(
            transport,"read_b_committed_pin",return_value=pin()):
            proof=transport(event(),idempotency_key=GA_RUN+":101:"+SNAP_HASH)
            envelope["receipt"]=proof
            self.assertTrue(transport.verify_receipt(envelope))
            self.assertFalse(transport.verify_receipt({
                **envelope,"receipt":dict(proof,consumer_receipt_sha256="0"*64),
            }))

    def test_scoped_dispatch_and_no_untrusted_project(self):
        pipe=GroupABPipeline(lambda:_Conn(),lambda:_Conn())
        with patch("intelligence.group_ab.bridge.deliver_once",
                   return_value="delivered") as call:
            self.assertEqual(pipe.deliver_once(
                worker_id="ga-b-worker",project_id=PID),"delivered")
            self.assertEqual(call.call_args.kwargs["project_id"],PID)
        with self.assertRaises(GroupBBindDenied):
            require_scope("other-project")
