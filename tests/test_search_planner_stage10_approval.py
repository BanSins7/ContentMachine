"""SP-10: immutable held snapshot owner commit and resource/routing invariants.

Unit tests model SQL transaction state with in-memory rows. PostgreSQL 17
concurrency, migration/trigger enforcement and cross-worker crash tests
remain independent SP-16 gates; no paid calls are made.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

from intelligence.search_planner import SearchPlannerService
from intelligence.search_planner.task_planner import TaskPlanner, build_held_task_plan
from intelligence.search_planner.task_planner.commit import persist_approved_held_plan
from intelligence.search_planner.capabilities import CapabilityMatcher
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from test_search_planner_stage10_task_planner import (
    planning_input, limits, capability_fixture, PID, QV, GEN, PUB, CAND,
    OUTBOX, NOW, FakePort,
)
from test_search_planner_stage10_task_planner import price_catalog
from test_search_planner_stage09_capabilities import snapshot as catalog_fixture


PLAN_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class DBResult:
    def __init__(self, item=None):
        self.item=item
    def fetchone(self):
        return self.item


class FakePlanDB:
    def __init__(self, plan):
        self.plan=plan
        self.saved=None
        self.approvals={}
        self.queries=[]
        self.transactions=0
        self.rolled_back=0
    def __enter__(self):
        return self
    def __exit__(self, exc_type, *_):
        if exc_type is not None:self.rolled_back+=1
        return False
    def transaction(self):
        self.transactions+=1
        return self
    def execute(self, statement, args=None):
        self.queries.append((statement,args))
        if "pg_advisory_xact_lock" in statement:
            return DBResult(None)
        if "JOIN sp_held_task_plans p" in statement:
            key=args[1]
            return DBResult(self.approvals.get(key))
        if "FROM qv_runs r" in statement:
            return DBResult((
                GEN,"semantic_local","completed",
                self.plan["qv_config_sha256"],
                self.plan["qv_source_bundle_sha256"],
                PUB,self.plan["taxonomy_sha256"],
                self.plan["generation_input_sha256"],
                "completed",OUTBOX,
            ))
        if "INSERT INTO sp_held_task_plans" in statement:
            if self.saved is None:
                self.saved=(PLAN_ID,deepcopy(self.plan))
                return DBResult((PLAN_ID,))
            return DBResult(None)
        if "SELECT id,snapshot FROM sp_held_task_plans" in statement:
            return DBResult(self.saved)
        if "SELECT id FROM sp_held_plan_approvals" in statement:
            return DBResult((PLAN_ID,) if self.approvals else None)
        if "INSERT INTO sp_held_plan_approvals" in statement:
            self.approvals[args[2]]=(PLAN_ID,QV,self.plan["plan_hash"])
            return DBResult(None)
        raise AssertionError("Unexpected SQL in fake DB: "+statement)


class SP10ApprovalAndRouting(unittest.TestCase):
    def setUp(self):
        self.source=planning_input()
        self.catalog=capability_fixture()
        self.plan=build_held_task_plan(self.source,self.catalog,
                  limits=limits(market="BY"),now=NOW)

    def test_default_owner_gate_blocks_commit_before_any_sql(self):
        called=[]
        with self.assertRaisesRegex(PipelineError,"Verified local owner"):
            persist_approved_held_plan(
                PID,QV,self.plan,"stage10-approval-key-0001",
                connect=lambda:called.append(1),now=NOW)
        self.assertEqual(called,[])

    def test_exact_owner_approval_is_atomic_immutable_and_idempotent(self):
        db=FakePlanDB(self.plan)
        checks=[]
        def gate(action,pid,oid):
            checks.append((action,pid,oid))
            return action=="search_planner.plan.commit_held" and pid==PID and oid==QV
        first=persist_approved_held_plan(
            PID,QV,self.plan,"stage10-approval-key-0001",
            owner_action_guard=gate,connect=lambda:db,now=NOW)
        second=persist_approved_held_plan(
            PID,QV,self.plan,"stage10-approval-key-0001",
            owner_action_guard=gate,connect=lambda:db,now=NOW)
        self.assertEqual(first["plan_id"],PLAN_ID)
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["status"],"held_approved_not_dispatched")
        self.assertFalse(first["external_dispatch_authorized"])
        self.assertEqual(db.transactions,2)
        self.assertEqual(len(checks),2)
        self.assertEqual(sum("INSERT INTO sp_held_plan_approvals" in x[0]
                             for x in db.queries),1)
        self.assertFalse(any("collector" in sql.lower() for sql,_ in db.queries))

    def test_same_idempotency_key_changed_limits_refuses_conflict(self):
        db=FakePlanDB(self.plan)
        guard=lambda *_:True
        persist_approved_held_plan(
           PID,QV,self.plan,"stage10-approval-key-0001",
           owner_action_guard=guard,connect=lambda:db,now=NOW)
        altered=build_held_task_plan(self.source,self.catalog,
           limits=limits(market="BY",max_tasks=1),now=NOW)
        with self.assertRaisesRegex(PipelineError,"idempotency key conflicts"):
            persist_approved_held_plan(
               PID,QV,altered,"stage10-approval-key-0001",
               owner_action_guard=guard,connect=lambda:db,now=NOW)

    def test_corrupt_plan_sha_and_execution_claim_block_pre_transaction(self):
        for mutation in (
            lambda p:p.__setitem__("plan_hash","0"*64),
            lambda p:p.__setitem__("execution_enabled",True),
            lambda p:p.__setitem__("external_dispatch_authorized",True),
            lambda p:p.__setitem__("dollars_spent_in_this_plan","10.00"),
        ):
            altered=deepcopy(self.plan)
            mutation(altered)
            calls=[]
            with self.subTest(mutation=str(mutation)),self.assertRaises(PipelineError):
                persist_approved_held_plan(
                    PID,QV,altered,"stage10-approval-key-0001",
                    owner_action_guard=lambda *_:True,
                    connect=lambda:calls.append(1),now=NOW)
            self.assertEqual(calls,[])

    def test_expired_pinned_catalog_cannot_be_committed(self):
        calls=[]
        with self.assertRaisesRegex(PipelineError,"current source snapshot"):
            persist_approved_held_plan(
                PID,QV,self.plan,"stage10-approval-key-0001",
                owner_action_guard=lambda *_:True,
                connect=lambda:calls.append(1),now=NOW.replace(year=2030))
        self.assertEqual(calls,[])

    def test_service_commit_regenerates_plan_and_does_not_accept_raw_payload(self):
        count=[]
        def writer(pid,vid,plan,key,**kw):
            count.append((pid,vid,plan["plan_hash"],key,kw["owner_action_guard"]))
            return {"plan_id":PLAN_ID,"status":"held_approved_not_dispatched"}
        tp=TaskPlanner(
            planning_reader=lambda *_:deepcopy(self.source),
            capability_matcher=CapabilityMatcher(
                FakePort(self.catalog),clock=lambda:NOW),
            clock=lambda:NOW,
            owner_action_guard=lambda *_:True,
            commit_writer=writer,
        )
        result=SearchPlannerService(task_planner=tp).commit_held_source_plan(
           PID,QV,"stage10-approval-key-0001",limits=limits(market="BY"))
        self.assertEqual(result["status"],"held_approved_not_dispatched")
        self.assertEqual(len(count),1)
        self.assertEqual(count[0][0:2],(PID,QV))
        self.assertEqual(count[0][2],self.plan["plan_hash"])

    def test_explicit_all_sources_only_creates_multiple_tasks_by_policy(self):
        c=deepcopy(self.catalog)
        extra=deepcopy(c["sources"][0])
        extra["source_id"]="another_verified_test_source"
        c["sources"].append(extra)
        single=build_held_task_plan(
             self.source,c,limits=limits(market="BY"),now=NOW)
        many=build_held_task_plan(
             self.source,c,limits=limits(market="BY",
                                        source_routing="all_compatible"),now=NOW)
        self.assertEqual(single["summary"]["task_count"],1)
        self.assertEqual(many["summary"]["task_count"],2)
        self.assertEqual(many["summary"]["planned_selected_count"],1)
        self.assertEqual(
             {t["source_id"] for t in many["tasks"]},
             {"another_verified_test_source","verified_test_source"})
        self.assertEqual(
             {r["candidate_id"] for t in many["tasks"] for r in t["linked_sources"]},
             {CAND})
        self.assertFalse(many["external_dispatch_authorized"])

    def test_candidate_cap_prevents_overestimated_result_count(self):
        p=build_held_task_plan(self.source,self.catalog,
          limits=limits(market="BY",max_total_candidates=1),now=NOW)
        self.assertEqual(p["tasks"],[])
        self.assertIn("candidate_result_cap_exceeded",
                      p["blocked_queries"][0]["block_reasons"])

    def test_approval_migration_has_immutable_source_and_unique_idempotency(self):
        path=(Path(__file__).resolve().parents[1] / "intelligence" /
              "migrations" / "054_sp_held_task_plans.sql")
        sql=path.read_text(encoding="utf-8")
        for marker in (
            "CREATE TABLE sp_held_task_plans", "CREATE TABLE sp_held_plan_approvals",
            "UNIQUE(project_id,validation_run_id,plan_hash)",
            "UNIQUE(project_id,idempotency_key)", "UNIQUE(plan_id)",
            "qv_runs(id,project_id)",
            "sqg_reject_immutable_write()",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker,sql)
        self.assertNotIn("DROP TABLE",sql.upper())


if __name__=="__main__":
    unittest.main()
