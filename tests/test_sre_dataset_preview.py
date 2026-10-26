"""M09 pure/fake-DB QA: exact C9 x D1 Cartesian coverage, no provider spend."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from intelligence.relevance.dataset_preview import (
    MAX_HYPOTHESES, MAX_PAIRS, MAX_SELECTED_VERSIONS, PREVIEW_VERSION,
    read_dataset_preview, load_dataset_preview,
)
from intelligence.relevance.v1_contracts import (
    SREContractError, fingerprint,
)
from intelligence.relevance.taxonomy_adapter import load_approved_taxonomy
from test_sre_taxonomy_adapter import FakeConnection, db_fixtures, PID, PUB


class Result:
    def __init__(self, rows):
        self.rows=rows

    def fetchall(self):
        return self.rows


class FakeDB:
    def __init__(self, rows):
        self.rows=rows
        self.sql=[]

    def __enter__(self):
        return self

    def __exit__(self,*args):
        return False

    def transaction(self):
        return self

    def execute(self,sql,params=None):
        self.sql.append((sql,params))
        if sql.startswith("SET TRANSACTION"):
            return Result([])
        if not sql.lstrip().startswith("SELECT"):
            raise AssertionError("M09 tried to mutate PostgreSQL")
        return Result(self.rows)


def snapshot():
    return load_approved_taxonomy(
        PID, PUB, connect=lambda: FakeConnection(db_fixtures())
    )


def gate(video,cid,*,title="D1 approved title", description="Observed",
         completeness="metadata_only"):
    data={"title":title,"description":description,"hashtags":[],
          "transcript":None,"scenes":None}
    return {"content":{
        "project_id":PID, "video_id":video, "content_version_id":cid,
        "completeness":completeness, "data":data,
    },"content_sha256":"a"*64}


def row(cid,video,rev=1,sha="a"*64):
    return (cid,video,rev,sha)


class PreviewUnit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s2=snapshot()

    def call(self,rows,selected,behavior=None):
        db=FakeDB(rows)
        gates=behavior or (lambda conn,pid,cid,**kw:
            gate(next(v for c,v,*rest in rows if c==cid),cid))
        with patch("intelligence.relevance.taxonomy_adapter.read_authorized_taxonomy",
                   return_value=self.s2) as taxonomy,patch(
            "intelligence.relevance.live_content_gate.read_live_content_gate",
            side_effect=gates) as checker:
            preview=load_dataset_preview(PID,PUB,selected,connect=lambda:db)
        return preview,db,taxonomy,checker

    def test_full_context_coverage_and_readonly_one_transaction(self):
        preview,db,taxonomy,gate_mock=self.call(
            [row(11,101),row(12,102)], [12,11])
        self.assertEqual(preview["schema_version"],PREVIEW_VERSION)
        self.assertEqual(preview["project_id"],PID)
        self.assertEqual(preview["publication_id"],PUB)
        self.assertEqual(preview["counts"]["selected_videos"],2)
        self.assertEqual(preview["counts"]["hypotheses"],3)
        self.assertEqual(preview["counts"]["total_pairs"],6)
        self.assertEqual(preview["counts"]["eligible_pairs"],6)
        self.assertEqual(preview["counts"]["blocked_pairs"],0)
        self.assertEqual({p["category_key"] for p in preview["pairs"]},
                         set(preview["hypothesis_keys"]))
        self.assertTrue(all(v["status"]=="eligible" for v in preview["videos"]))
        self.assertTrue(all("transcript_unavailable" in v["warnings"]
                            for v in preview["videos"]))
        self.assertEqual(
            preview["preview_sha256"],
            fingerprint({k:v for k,v in preview.items() if k!="preview_sha256"}),
        )
        self.assertFalse(preview["execution_allowed"])
        self.assertEqual(len(db.sql),2)
        self.assertEqual(db.sql[0][0],
                         "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self.assertIn("sre_content_versions",db.sql[1][0])
        self.assertIn("sre_workspaces",db.sql[1][0])
        self.assertEqual(db.sql[1][1],(PID,[11,12]))
        taxonomy.assert_called_once_with(db,PID,PUB)
        self.assertEqual(gate_mock.call_count,2)
        for call in gate_mock.call_args_list:
            self.assertEqual(call.kwargs["phase"],"planning")
            self.assertEqual(call.kwargs["pins"]["content_sha256"],"a"*64)

    def test_duplicate_input_ids_and_multiple_video_versions_are_explicit(self):
        preview,db,_,gate_mock=self.call(
            [row(2,10,1),row(3,10,2),row(4,11,1)], [2,4,3,3,2])
        self.assertEqual(preview["counts"]["requested_references"],5)
        self.assertEqual(preview["counts"]["unique_versions"],3)
        self.assertEqual(preview["counts"]["duplicate_references"],2)
        self.assertEqual(preview["counts"]["deduplicated_video_versions"],1)
        self.assertEqual(preview["counts"]["selected_videos"],2)
        self.assertEqual(preview["counts"]["total_pairs"],6)
        self.assertEqual(preview["deduplicated_video_versions"],[
            {"video_id":10,"content_version_id":2,
             "kept_content_version_id":3,"reason":"superseded_in_selection"},
        ])
        self.assertEqual([v["content_version_id"] for v in preview["videos"]],[3,4])
        self.assertEqual(gate_mock.call_count,2)
        reversed_result,_,_,_=self.call(
            [row(2,10,1),row(3,10,2),row(4,11,1)], [4,2,3,2,3])
        self.assertEqual(reversed_result["videos"],preview["videos"])
        self.assertEqual(reversed_result["pairs"],preview["pairs"])

    def test_blocked_rights_and_missing_metadata_keep_every_pair(self):
        def conditional(conn,pid,cid,**kw):
            if cid==5:
                raise SREContractError("rights_expired","content","Expired")
            if cid==6:
                raise SREContractError("rights_denied","content","Revoked")
            if cid==7:
                raise SREContractError("snapshot_mismatch","lineage","Tampered")
            if cid==8:
                return gate(108,8,title=None,description=None)
            return gate(109,9)
        preview,_,_,_=self.call(
            [row(5,105),row(6,106),row(7,107),row(8,108),row(9,109)],
            [5,6,7,8,9],behavior=conditional)
        self.assertEqual(preview["counts"]["total_pairs"],15)
        self.assertEqual(preview["counts"]["eligible_pairs"],3)
        self.assertEqual(preview["counts"]["blocked_pairs"],12)
        reasons={v["video_id"]:v["reason"] for v in preview["videos"]}
        self.assertEqual(reasons,{
            105:"content_expired",106:"rights_unavailable",
            107:"invalid_d1_provenance",108:"no_classifiable_content",109:None,
        })
        self.assertEqual([p["status"] for p in preview["pairs"]].count("blocked"),12)
        self.assertTrue(all(p["reason"] is not None for p in preview["pairs"]
                            if p["status"]=="blocked"))
        self.assertTrue(all(p["reason"] is None for p in preview["pairs"]
                            if p["status"]=="eligible"))
        for v in preview["videos"][:3]:
            self.assertIsNone(v["content_sha256"])
            self.assertEqual(v["warnings"],["content_unavailable"])

    def test_cross_project_or_not_attached_content_never_leaks(self):
        for selected,returned in (
            ([1,2],[row(1,101)]),
            ([1],[]),
            ([1],[row(2,101)]),
            ([1],[row(1,101),row(1,101)]),
        ):
            with self.subTest(selected=selected,returned=returned):
                with self.assertRaises(SREContractError) as err:
                    self.call(returned,selected)
                self.assertEqual(err.exception.code,"rights_denied")

    def test_bad_selection_and_noncanonical_publication_rejected(self):
        wrong=([True],["3"],[0],[-2],[float("nan")],
               [2**63],[],list(range(1,1002)),"12")
        for item in wrong:
            with self.subTest(kind=str(item)[:30]),self.assertRaises(SREContractError):
                self.call([],item)
        with self.assertRaises(SREContractError):
            load_dataset_preview(PID,"bad",[5],connect=lambda:FakeDB([]))

    def test_large_100_by_20_has_exact_2000_pairs_no_hidden_skips(self):
        source=copy.deepcopy(self.s2)
        fake_bundle={
            "project_id":PID,"publication_id":PUB,
            "taxonomy_sha256":"e"*64,
            "sre_taxonomy_version_id":12,
            "bundle_sha256":"b"*64,
            "hypothesis_keys":[f"h{i:02d}" for i in range(20)],
            "contexts":[{"category_key":f"h{i:02d}"} for i in range(20)],
        }
        rows=[row(i,10000+i) for i in range(1,101)]
        db=FakeDB(rows)
        with patch("intelligence.relevance.taxonomy_adapter.read_authorized_taxonomy",
                   return_value=source),patch(
                   "intelligence.relevance.dataset_preview.build_c9_bundle",
                   return_value=fake_bundle),patch(
                   "intelligence.relevance.live_content_gate.read_live_content_gate",
                   side_effect=lambda conn,pid,cid,**kw:gate(10000+cid,cid)):
            result=read_dataset_preview(db,PID,PUB,list(range(1,101)))
        self.assertEqual(result["counts"]["selected_videos"],100)
        self.assertEqual(result["counts"]["hypotheses"],20)
        self.assertEqual(result["counts"]["total_pairs"],2000)
        self.assertEqual(len(result["pairs"]),2000)
        self.assertEqual(result["counts"]["eligible_pairs"],2000)
        self.assertEqual(result["counts"]["blocked_pairs"],0)
        self.assertEqual(len(set((p["video_id"],p["category_key"])
                                 for p in result["pairs"])),2000)
        self.assertLessEqual(len(result["pairs"]),MAX_PAIRS)
        self.assertEqual(MAX_HYPOTHESES,48)
        self.assertEqual(MAX_SELECTED_VERSIONS,1000)

    def test_import_does_not_require_collector_provider_or_postgres(self):
        import subprocess,sys
        code=(
            "import sys;import intelligence.relevance.dataset_preview;"
            "assert 'psycopg' not in sys.modules;"
            "assert 'intelligence.collector' not in sys.modules;"
            "assert 'openrouter' not in sys.modules;"
        )
        p=subprocess.run([sys.executable,"-c",code],capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr)


if __name__=="__main__":
    unittest.main()
