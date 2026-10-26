"""M13 unit coverage: deterministic batching, no skipped video/hypothesis pairs.

No PostgreSQL, provider, finance or network access. SQL enforcement is covered
separately by disposable PG17 integration tests.
"""
from __future__ import annotations

from copy import deepcopy
import unittest
from uuid import UUID

from intelligence.relevance.live_batch_manifest import (
    BATCH_VERSION, make_batch_manifest, materialize_live_batches,
    get_live_batch_coverage, summarize_pair_coverage,
)
from intelligence.relevance.v1_contracts import (
    SREContractError, fingerprint,
)

PID="a"*32
RID="11111111-1111-4111-8111-111111111111"
H1="22222222-2222-4222-8222-222222222222"
H2="33333333-3333-4333-8333-333333333333"
H3="44444444-4444-4444-8444-444444444444"
KEYS=[H1,H2,H3]


class FakeResult:
    def __init__(self,rows):
        self.rows=rows
    def fetchone(self):
        return self.rows
    def fetchall(self):
        return self.rows


class FakeDB:
    def __init__(self,n=5,state="pending_consent"):
        self.keys=KEYS
        self.sql=[]
        self.batches={}
        self.members=[]
        self.pairs=[]
        self.manifest={
            "schema_version":"sre-live-plan/v1",
            "project_id":PID,"execution_mode":"live",
            "expected_hypothesis_keys":KEYS,
            "contexts_sha256":"c"*64,
            "items":[
                {"video_id":100+i,"content_version_id":200+i,
                 "content_sha256":"d"*64,"contexts_sha256":"c"*64}
                for i in range(n)
            ],
        }
        self.sha=fingerprint(self.manifest)
        self.state=state
        self.items=[
            (10+i,100+i,200+i,"d"*64,list(KEYS),"c"*64,
             self.sha,"not_evaluated")
            for i in range(n)
        ]

    def __enter__(self):return self
    def __exit__(self,*a):return False
    def transaction(self):return self

    def execute(self,sql,params=None):
        self.sql.append((sql,params))
        if "FROM sre_live_runs" in sql:
            return FakeResult(
                (self.state,self.manifest,self.sha,len(self.items),"live")
                if params[0]==PID else None
            )
        if "FROM sre_live_items" in sql:
            return FakeResult(self.items if params[0]==PID else [])
        if "SELECT batch_id::text,ordinal,manifest" in sql:
            return FakeResult(sorted(self.batches.values(),key=lambda r:r[1]))
        if "SELECT batch_id::text,item_id,ordinal" in sql:
            return FakeResult(sorted(self.members))
        if "SELECT batch_id::text,item_id,category_key::text,state" in sql:
            return FakeResult(sorted(self.pairs))
        if "INSERT INTO sre_live_batches" in sql:
            batch_id,rid,pid,ordinal,manifest,sha,digest=params
            self.batches[batch_id]=(batch_id,ordinal,
                                    __import__("json").loads(manifest),sha,digest)
            return FakeResult(None)
        if "INSERT INTO sre_live_batch_members" in sql:
            batch_id,rid,pid,item,ordinal=params
            self.members.append((batch_id,item,ordinal))
            return FakeResult(None)
        if "INSERT INTO sre_live_expected_pairs" in sql:
            rid,pid,batch_id,item,key=params
            self.pairs.append((batch_id,item,key,"not_evaluated"))
            return FakeResult(None)
        raise AssertionError("Unexpected SQL: "+sql)


def _expected(rows):
    return [
        {"item_id":item,"category_key":key}
        for item in rows for key in KEYS
    ]


class LiveBatchM13(unittest.TestCase):
    def test_5_by_3_deterministic_sealed_batches_and_exact_pair_rows(self):
        db=FakeDB(5)
        first=materialize_live_batches(db,PID,RID,batch_size=2)
        self.assertEqual(first["schema_version"],BATCH_VERSION)
        self.assertEqual(first["batches"],3)
        self.assertEqual(first["items"],5)
        self.assertEqual(first["expected_pairs"],15)
        self.assertEqual(first["evaluated_pairs"],0)
        self.assertFalse(first["execution_allowed"])
        self.assertFalse(first["replayed"])
        self.assertEqual(len(db.batches),3)
        self.assertEqual(len(db.members),5)
        self.assertEqual(len(db.pairs),15)
        self.assertEqual(len({(p[1],p[2]) for p in db.pairs}),15)
        for b in db.batches.values():
            self.assertEqual(b[3],fingerprint(b[2]))
            self.assertEqual(b[2]["schema_version"],BATCH_VERSION)
            self.assertTrue(all(x["state"]=="not_evaluated"
                                for x in b[2]["expected_pairs"]))
            UUID(b[0])
        second=materialize_live_batches(db,PID,RID,batch_size=2)
        self.assertTrue(second["replayed"])
        self.assertEqual(second["batch_ids"],first["batch_ids"])
        self.assertEqual(len(db.members),5)
        self.assertEqual(len(db.pairs),15)
        coverage=get_live_batch_coverage(db,PID,RID)
        self.assertEqual(coverage["coverage"]["total_pairs"],15)
        self.assertEqual(coverage["coverage"]["not_evaluated_pairs"],15)
        self.assertEqual(coverage["coverage"]["evaluated_pairs"],0)
        self.assertFalse(coverage["coverage"]["complete"])

    def test_all_pairs_100_by_20_exact_2000_no_skipped_ids(self):
        db=FakeDB(100)
        db.keys=[
            f"00000000-0000-4000-8000-{i:012x}" for i in range(20)
        ]
        db.manifest["expected_hypothesis_keys"]=db.keys
        db.items=[(r[0],r[1],r[2],r[3],list(db.keys),r[5],None,r[7])
                  for r in db.items]
        db.sha=fingerprint(db.manifest)
        db.items=[(a,b,c,d,keys,ctx,db.sha,state)
                  for a,b,c,d,keys,ctx,_,state in db.items]
        result=materialize_live_batches(db,PID,RID,batch_size=25)
        self.assertEqual(result["batches"],4)
        self.assertEqual(result["expected_pairs"],2000)
        self.assertEqual(len(db.pairs),2000)
        self.assertEqual(len(set((p[1],p[2]) for p in db.pairs)),2000)

    def test_existing_manifest_tamper_or_missing_pair_fails_closed(self):
        db=FakeDB(4)
        materialize_live_batches(db,PID,RID,batch_size=2)
        db.pairs.pop()
        with self.assertRaises(SREContractError) as ctx:
            materialize_live_batches(db,PID,RID,batch_size=2)
        self.assertEqual(ctx.exception.code,"incomplete_coverage")
        with self.assertRaises(SREContractError):
            get_live_batch_coverage(db,PID,RID)
        db=FakeDB(4)
        materialize_live_batches(db,PID,RID,batch_size=2)
        db.members.pop()
        with self.assertRaises(SREContractError):
            materialize_live_batches(db,PID,RID,batch_size=2)
        db=FakeDB(4)
        materialize_live_batches(db,PID,RID,batch_size=2)
        with self.assertRaises(SREContractError):
            materialize_live_batches(db,PID,RID,batch_size=3)

    def test_consent_and_tenant_boundaries(self):
        for state in ("running","queued","completed","cancelled"):
            db=FakeDB(2,state)
            with self.assertRaises(SREContractError):
                materialize_live_batches(db,PID,RID,batch_size=2)
            self.assertFalse(db.batches)
        db=FakeDB(2)
        with self.assertRaises(SREContractError):
            materialize_live_batches(db,"b"*32,RID)
        with self.assertRaises(SREContractError):
            materialize_live_batches(db,PID,"bad")
        for size in (True,0,-1,26,"2",1.2):
            with self.subTest(size=size),self.assertRaises(SREContractError):
                materialize_live_batches(FakeDB(2),PID,RID,batch_size=size)

    def test_no_false_completion_when_2_of_3_or_0_of_3_labels(self):
        expected=_expected([10])
        for observed in ([],[
            {"item_id":10,"category_key":H1,"state":"match"},
            {"item_id":10,"category_key":H2,"state":"no_match"},
        ]):
            with self.assertRaises(SREContractError) as denied:
                summarize_pair_coverage(expected,observed)
            self.assertEqual(denied.exception.code,"incomplete_coverage")
        observed=[
            {"item_id":10,"category_key":H1,"state":"match"},
            {"item_id":10,"category_key":H2,"state":"no_match"},
            {"item_id":10,"category_key":H3,"state":"not_evaluated"},
        ]
        summary=summarize_pair_coverage(expected,observed)
        self.assertEqual(summary["total_pairs"],3)
        self.assertEqual(summary["evaluated_pairs"],2)
        self.assertEqual(summary["not_evaluated_pairs"],1)
        self.assertTrue(summary["partial"])
        self.assertFalse(summary["complete"])
        observed[2]["state"]="blocked_rights"
        summary=summarize_pair_coverage(expected,observed)
        self.assertEqual(summary["blocked_pairs"],1)
        self.assertFalse(summary["complete"])
        observed[2]["state"]="uncertain"
        self.assertTrue(summarize_pair_coverage(expected,observed)["complete"])
        observed.append(observed[2].copy())
        with self.assertRaises(SREContractError):
            summarize_pair_coverage(expected,observed)

    def test_extra_unapproved_hypothesis_never_counted_as_completion(self):
        expected=_expected([10])
        observed=[{"item_id":10,"category_key":k,"state":"no_match"}
                  for k in KEYS]
        observed[0]["category_key"]="55555555-5555-4555-8555-555555555555"
        with self.assertRaises(SREContractError):
            summarize_pair_coverage(expected,observed)
        with self.assertRaises(SREContractError):
            make_batch_manifest(PID,RID,"d"*64,0,[],KEYS)


if __name__=="__main__":
    unittest.main()
