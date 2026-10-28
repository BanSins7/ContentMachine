"""SP-13 durable HTTP one-shot guard, not a financial authorization ledger.

Replay/simulated process crash must never execute another paid SQG batch.
PostgreSQL17 isolation, WAL and migration upgrade checks remain SP-16.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

from intelligence.search_planner.http_paid import one_paid_batch_with_idempotency
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from test_search_planner_service_stage01 import PID,GEN

ACTION_ID="cccccccc-cccc-4ccc-8ccc-cccccccccccc"
KEY="sp13-paid-request-0001"


class Result:
    def __init__(self,row=None):self.row=row
    def fetchone(self):return self.row


class FakePaidDB:
    def __init__(self):
        self.ledger={}
        self.logs=[]
        self.transactions=0
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def transaction(self):
        self.transactions+=1
        return self
    def execute(self,sql,args=None):
        self.logs.append((sql,args))
        if "SELECT id FROM project_registry" in sql:
            return Result((PID,))
        if "SELECT id,generation_id,request_sha256,state,result" in sql:
            key=args[1]
            state=self.ledger.get(key)
            if state is None:return Result(None)
            return Result((ACTION_ID,state["gen"],state["sha"],
                           state["status"],deepcopy(state["result"])))
        if "INSERT INTO sp_http_paid_actions" in sql:
            pid,gen,key,digest=args
            if key in self.ledger:
                raise AssertionError("Duplicate ledger record inserted")
            self.ledger[key]={"gen":gen,"sha":digest,
                              "status":"in_progress","result":None}
            return Result((ACTION_ID,))
        if "UPDATE sp_http_paid_actions" in sql:
            if "SET state='uncertain'" in sql:
                self.ledger[KEY]["status"]="uncertain"
                return Result()
            if "SET state='completed'" in sql:
                import json
                result,action_id=args
                self.ledger[KEY]["status"]="completed"
                self.ledger[KEY]["result"]=json.loads(result)
                return Result((ACTION_ID,))
        raise AssertionError("Unknown SQL "+sql[:150])


class PaidHTTPDurability(unittest.TestCase):
    def test_first_execution_reserved_before_worker_then_replays_with_zero_calls(self):
        db=FakePaidDB()
        seen=[]
        def worker(pid,gid):
            self.assertEqual(db.ledger[KEY]["status"],"in_progress")
            seen.append((pid,gid))
            return {"status":"single_batch_completed","paid_calls_reported":1}
        first=one_paid_batch_with_idempotency(
            PID,GEN,KEY,executor=worker,connect=lambda:db)
        self.assertEqual(first["status"],"completed")
        self.assertFalse(first["reused"])
        self.assertEqual(first["worker_calls_in_this_request"],1)
        again=one_paid_batch_with_idempotency(
            PID,GEN,KEY,executor=worker,connect=lambda:db)
        self.assertEqual(again["status"],"completed")
        self.assertTrue(again["reused"])
        self.assertEqual(again["worker_calls_in_this_request"],0)
        self.assertEqual(again["result"],first["result"])
        self.assertEqual(seen,[(PID,GEN)])
        self.assertEqual(sum("INSERT INTO sp_http_paid_actions" in sql
                             for sql,args in db.logs),1)

    def test_unknown_worker_result_requires_financial_reconciliation_no_retry(self):
        db=FakePaidDB()
        calls=[]
        def uncertain(*args):
            calls.append(args)
            raise TimeoutError("Intentionally lost paid OpenRouter response")
        first=one_paid_batch_with_idempotency(
            PID,GEN,KEY,executor=uncertain,connect=lambda:db)
        self.assertEqual(first["status"],"needs_attention")
        self.assertEqual(first["worker_calls_in_this_request"],1)
        self.assertEqual(db.ledger[KEY]["status"],"uncertain")
        second=one_paid_batch_with_idempotency(
            PID,GEN,KEY,executor=uncertain,connect=lambda:db)
        self.assertEqual(second["status"],"needs_attention")
        self.assertEqual(second["worker_calls_in_this_request"],0)
        self.assertEqual(len(calls),1)

    def test_crash_after_paid_reservation_without_worker_is_not_resubmitted(self):
        db=FakePaidDB()
        from intelligence.taxonomy_builder.pipeline_contracts import sha256
        digest=sha256({
            "schema_version":"search-planner-http-paid-dispatch/v1",
            "project_id":PID,"generation_id":GEN,"action":"execute_one",
        })
        db.ledger[KEY]={"gen":GEN,"sha":digest,
                        "status":"in_progress","result":None}
        calls=[]
        output=one_paid_batch_with_idempotency(
            PID,GEN,KEY,executor=lambda *a:calls.append(a),connect=lambda:db)
        self.assertEqual(output["status"],"needs_attention")
        self.assertEqual(output["worker_calls_in_this_request"],0)
        self.assertEqual(calls,[])

    def test_cross_generation_reusing_paid_key_never_executes(self):
        db=FakePaidDB()
        one_paid_batch_with_idempotency(
            PID,GEN,KEY,executor=lambda *_:{"status":"ok"},connect=lambda:db)
        with self.assertRaisesRegex(PipelineError,"another request"):
            one_paid_batch_with_idempotency(
                PID,"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",KEY,
                executor=lambda *_:{"status":"wrong"},connect=lambda:db)

    def test_invalid_id_or_noncallable_worker_no_mutating_sql(self):
        db=FakePaidDB()
        for pid,gen,key,worker in (
            (PID,"bad",KEY,lambda *_:None),
            (PID,GEN,"too-short",lambda *_:None),
            (PID,GEN,KEY,None),
        ):
            with self.subTest(values=(pid,gen,key)),self.assertRaises(PipelineError):
                one_paid_batch_with_idempotency(
                    pid,gen,key,executor=worker,connect=lambda:db)
        self.assertEqual(db.logs,[])

    def test_migration_has_project_unique_key_and_immutable_once_resolved(self):
        path=(Path(__file__).resolve().parents[1] / "intelligence" /
              "migrations" / "056_sp_http_paid_idempotency.sql")
        data=path.read_text(encoding="utf-8")
        for expr in (
            "CREATE TABLE sp_http_paid_actions",
            "UNIQUE(project_id,idempotency_key)",
            "REFERENCES sqg_generations(id,project_id)",
            "'in_progress','completed','uncertain'",
            "OLD.state <> 'in_progress'",
            "CREATE TRIGGER sp_http_paid_actions_no_delete",
        ):
            with self.subTest(expr=expr):
                self.assertIn(expr,data)


if __name__=="__main__":
    unittest.main()
