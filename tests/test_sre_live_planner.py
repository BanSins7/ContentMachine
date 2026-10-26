"""M12 offline contract QA: no DB/provider required.

The in-memory DB simulates transactional planner commands. Real PostgreSQL17
tests exercise unique constraints, foreign keys and rollback independently.
"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch
from uuid import UUID

from intelligence.relevance.live_planner import (
    PLAN_VERSION, create_live_run, get_live_plan,
)
from intelligence.relevance.v1_contracts import SREContractError, fingerprint
from intelligence.relevance.presets import strong_only
from test_sre_taxonomy_adapter import FakeConnection, db_fixtures, PID, PUB
from intelligence.relevance.taxonomy_adapter import load_approved_taxonomy


SHA="d"*64


def s2():
    return load_approved_taxonomy(PID,PUB,connect=lambda:FakeConnection(db_fixtures()))


def content(cid,video,rev=1):
    origin={"discovery_id":video+200,"search_query_id":None,"source_request_id":None}
    return (cid,video,rev,SHA,origin,None)


def verified(row):
    cid,video,rev,sha,origin,obs=row
    return {
        "content":{
            "project_id":PID,"video_id":video,"content_version_id":cid,
            "source_id":"offline_fixture_source", "source_policy_version_id":17,
            "data":{"title":"Real D1 title","description":None,"hashtags":[],
                    "transcript":None,"scenes":None},
            "provenance":copy.deepcopy(origin),
        },
        "provenance":{"discovery_id":origin["discovery_id"],
                      "metadata_observation_id":obs,"content_sha256":sha},
    }


class Result:
    def __init__(self,value): self.value=value
    def fetchone(self): return self.value
    def fetchall(self): return self.value


class FakeDB:
    def __init__(self,rows=None,config=None):
        self.rows=rows or [content(10,100),content(11,101)]
        self.config=config if config is not None else strong_only()
        self.statements=[]
        self.runs={}
        self.items=[]
        self.has_workspace=True

    def transaction(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):return False

    def execute(self,sql,params=None):
        self.statements.append((sql,params))
        if "SELECT 1 FROM sre_workspaces" in sql:
            return Result((1,) if self.has_workspace else None)
        if "SELECT config FROM sre_pipeline_configs" in sql:
            return Result((copy.deepcopy(self.config),) if params[1]==12 else None)
        if "FROM sre_content_versions c" in sql:
            selected=set(params[1])
            return Result([row for row in self.rows if row[0] in selected])
        if "SELECT run_id::text,request_sha256" in sql:
            old=self.runs.get((params[0],params[1]))
            return Result((old["run_id"],old["digest"],old["digest"],old["state"])
                          if old else None)
        if "INSERT INTO sre_live_runs" in sql:
            self.runs[(params[1],params[10])]={
                "run_id":params[0],"digest":params[7],
                "manifest":json.loads(params[8]),"state":"pending_consent",
            }
            return Result(None)
        if "INSERT INTO sre_live_items" in sql:
            self.items.append(params)
            return Result(None)
        if "SELECT state,publication_id::text" in sql:
            old=next((v for (pid,key),v in self.runs.items()
                      if pid==params[0] and v["run_id"]==params[1]),None)
            if old is None:return Result(None)
            m=old["manifest"]
            return Result((old["state"],m["publication_id"],
                           m["taxonomy_version_id"],m["pipeline_config_id"],
                           old["digest"],old["digest"],len(m["items"]),"live"))
        if "SELECT video_id,content_version_id,state" in sql:
            rows=[(p[2],p[3],"not_evaluated") for p in self.items
                  if p[0]==params[1] and p[1]==params[0]]
            return Result(sorted(rows))
        raise AssertionError("unexpected DB statement: "+sql)


class LivePlannerUnit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.authorized=s2()

    def call(self,db,ids,key="request-key",project=PID,publication=PUB,config_id=12,
             gates=None):
        def gate(conn,pid,cid,**kw):
            if gates:
                return gates(conn,pid,cid,**kw)
            return verified(next(r for r in db.rows if r[0]==cid))
        with patch("intelligence.relevance.taxonomy_adapter.read_authorized_taxonomy",
                   return_value=self.authorized) as c9,patch(
            "intelligence.relevance.live_content_gate.read_live_content_gate",
            side_effect=gate) as check:
            result=create_live_run(db,project,publication,ids,config_id,key)
        return result,c9,check

    def test_one_write_transaction_unapproved_no_batches_or_provider(self):
        db=FakeDB()
        result,c9,gates=self.call(db,[11,10])
        self.assertEqual(result["status"],"pending_consent")
        self.assertFalse(result["replayed"])
        self.assertEqual(result["items"],2)
        self.assertEqual(result["hypotheses"],3)
        self.assertEqual(result["execution_mode"],"live")
        self.assertEqual(result["request_sha256"],result["manifest_sha256"])
        self.assertEqual(len(db.runs),1)
        self.assertEqual(len(db.items),2)
        self.assertEqual([p[2] for p in db.items],[100,101])
        self.assertEqual(len(db.items),2)\n        self.assertTrue(all(len(p)==14 for p in db.items))
        sql="\n".join(q for q,_ in db.statements)
        self.assertIn("FOR UPDATE",sql)
        self.assertNotIn("sre_offline_runs",sql)
        self.assertNotIn("sre_live_batches",sql)
        self.assertNotIn("sre_classification_results",sql)
        self.assertNotIn("OPENROUTER",sql.upper())
        self.assertEqual(gates.call_count,2)
        for c in gates.call_args_list:
            self.assertTrue(c.kwargs["external_llm"])
            self.assertEqual(c.kwargs["phase"],"planning")
        c9.assert_called_once()
        self.assertEqual(
            db.statements[0][1],(PID,),
        )
        p=get_live_plan(db,PID,result["run_id"])
        self.assertEqual(p["status"],"pending_consent")
        self.assertEqual(len(p["items"]),2)
        self.assertEqual({x["state"] for x in p["items"]},{"not_evaluated"})
        with self.assertRaises(SREContractError):
            get_live_plan(db,"b"*32,result["run_id"])

    def test_same_key_replay_and_input_order_repeat_canonical(self):
        db=FakeDB()
        first,_,_=self.call(db,[10,11])
        second,_,_=self.call(db,[11,10,11,10])
        self.assertEqual(first["run_id"],second["run_id"])
        self.assertEqual(first["request_sha256"],second["request_sha256"])
        self.assertTrue(second["replayed"])
        self.assertEqual(len(db.runs),1)
        self.assertEqual(len(db.items),2)
        # New key creates a distinct plan, still no paid execution.
        third,_,_=self.call(db,[10,11],key="another-key")
        self.assertNotEqual(first["run_id"],third["run_id"])
        self.assertEqual(first["request_sha256"],third["request_sha256"])
        self.assertEqual(len(db.runs),2)
        self.assertEqual(len(db.items),4)

    def test_changed_data_and_new_superseded_version_conflict(self):
        db=FakeDB()
        original,_,_=self.call(db,[10,11])
        with self.assertRaises(SREContractError) as x:
            self.call(db,[10])
        self.assertEqual(x.exception.code,"idempotency_conflict")
        # Same two selected final videos but with an extra older revision,
        # still a different requested immutable source selection.
        db.rows.append(content(12,100,0+1))
        with self.assertRaises(SREContractError) as x:
            self.call(db,[10,11,12])
        self.assertEqual(x.exception.code,"idempotency_conflict")
        self.assertEqual(len(db.runs),1)
        self.assertEqual(len(db.items),2)

    def test_newest_selected_revision_and_no_hidden_video_duplication(self):
        db=FakeDB([content(10,100,1),content(12,100,2),content(11,101,1)])
        result,_,gates=self.call(db,[12,10,11])
        self.assertEqual(result["items"],2)
        m=list(db.runs.values())[0]["manifest"]
        self.assertEqual(m["schema_version"],PLAN_VERSION)
        self.assertEqual([x["content_version_id"] for x in m["items"]],[12,11])
        self.assertEqual(m["selected_content_versions"],[10,11,12])
        self.assertEqual(m["superseded_in_selection"],[{
            "video_id":100,"content_version_id":10,
            "kept_content_version_id":12,"reason":"superseded_in_selection",
        }])
        self.assertEqual(gates.call_count,2)
        self.assertEqual(len(db.items),2)

    def test_invalid_identity_missing_rights_and_origin_never_mutate(self):
        for ids, key, config, project, publication in (
            ([], "x",12,PID,PUB),([True],"x",12,PID,PUB),
            ([0],"x",12,PID,PUB),([2**63],"x",12,PID,PUB),
            ([10],"bad key",12,PID,PUB),
            ([10],"x",0,PID,PUB),([10],"x",12,"X"*32,PUB),
            ([10],"x",12,PID,"bad"),
            ([99],"x",12,PID,PUB),
        ):
            db=FakeDB()
            with self.subTest(ids=ids,key=key),self.assertRaises(SREContractError):
                self.call(db,ids,key,project,publication,config)
            self.assertFalse(db.runs)
            self.assertFalse(db.items)

        db=FakeDB();db.has_workspace=False
        with self.assertRaises(SREContractError):
            self.call(db,[10])
        self.assertFalse(db.runs)
        db=FakeDB()
        def denied(*a,**k):
            raise SREContractError("rights_denied","source","revoked")
        with self.assertRaises(SREContractError):
            self.call(db,[10],gates=denied)
        self.assertFalse(db.runs)
        db=FakeDB()
        def forged(*a,**k):
            fake=verified(db.rows[0]);fake["content"]["provenance"]["discovery_id"]=42
            return fake
        with self.assertRaises(SREContractError):
            self.call(db,[10],gates=forged)
        self.assertFalse(db.runs)

    def test_pipeline_invalid_and_missing_project_config_fail_closed(self):
        for conf in ({}, {"stages":[]},
                     {"stages":[{"id":"x","type":"embedding_retrieval",
                                 "model_ref":"embed","run_when":"always"}]}):
            db=FakeDB(config=conf)
            with self.assertRaises(SREContractError):
                self.call(db,[10])
            self.assertFalse(db.runs)
        db=FakeDB()
        with self.assertRaises(SREContractError) as x:
            self.call(db,[10],config_id=99)
        self.assertEqual(x.exception.code,"rights_denied")
        self.assertEqual(len(db.runs),0)


if __name__=="__main__":
    unittest.main()
