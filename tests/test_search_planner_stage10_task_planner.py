"""SP-10: trusted held-only task preview, budget, quotas and lossless provenance.

No PostgreSQL, Collector, network, credentials or paid model are needed.
Source/DB stubs are internal trusted *test* ports; never user JSON authority.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from unittest import mock
import unittest

from intelligence.search_planner import SearchPlannerService
from intelligence.search_planner.capabilities import CapabilityMatcher
from intelligence.search_planner.task_planner import (
    TaskPlanner, build_held_task_plan, validate_plan_limits,
)
from intelligence.search_planner.task_planner.repository import (
    INPUT_SCHEMA, load_verified_planning_input, validate_planning_input,
)
from intelligence.search_planner.query_validation.linkage import build_selected_query_links
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256

from test_search_planner_service_stage01 import PID, QV, GEN, CAND, PUB, valid_bundle
from test_search_planner_foundation import snapshot as c9_fixture
from test_search_planner_stage09_capabilities import (
    NOW, FakePort, snapshot as capability_fixture,
)

CID2 = "88888888-8888-4888-8888-888888888888"
OUTBOX = "99999999-9999-4999-8999-999999999999"


def planning_input(*, second=False, no_work=False):
    validated = deepcopy(valid_bundle())
    if no_work:
        validated["queries"] = []
        validated["decisions"] = []
        validated["coverage"][0].update(
            source_count=0, selected_count=0,
            outcome="no_source_candidates", reason="No original SQG queries",
        )
    if second:
        q = deepcopy(validated["queries"][0])
        q.update(candidate_id=CID2, hypothesis_id="morning", direction_id="home",
                 source_fact_ids=["fact-second"], text="ДОМАШНЯЯ ТРЕНИРОВКА")
        validated["queries"].append(q)
        d = deepcopy(validated["decisions"][0])
        d["candidate_id"] = CID2
        validated["decisions"].append(d)
        validated["coverage"].append({
            "hypothesis_id": "morning", "source_count": 1, "selected_count": 1,
            "outcome": "selected", "reason": None,
        })
    rows = [
        (q["candidate_id"], q["hypothesis_id"], "selected", "selected",
         None, q["source_fact_ids"], q["query_mode"], q["language"], q["text"])
        for q in validated["queries"]
    ]
    links = build_selected_query_links(
        rows, project_id=PID, validation_run_id=QV,
        selected_count=len(validated["queries"]),
    )
    c9 = c9_fixture()
    return {
        "schema_version": INPUT_SCHEMA, "project_id": PID,
        "validation_run_id": QV, "generation_id": GEN,
        "taxonomy_publication_id": PUB,
        "taxonomy_version": c9["taxonomy_version"],
        "taxonomy_sha256": sha256(c9["tree"]),
        "generation_input_sha256": "c" * 64,
        "outbox_id": OUTBOX, "validated_bundle": validated,
        "shared_links": links,
    }


def price_catalog(price="0.08000000", *, quota=17):
    c = capability_fixture()
    s = c["sources"][0]
    s.update(pricing_status="verified_paid", cost_usd_per_request=price,
             quota_available_requests=quota)
    return c


def limits(**updates):
    base = validate_plan_limits()
    base.update(updates)
    return base


class Stage10TrustedPlanner(unittest.TestCase):
    def test_free_source_produces_held_task_without_any_provider_calls(self):
        src = planning_input()
        catalog = capability_fixture()
        original = deepcopy(src)
        plan = build_held_task_plan(src, catalog, limits=limits(market="BY"), now=NOW)
        self.assertEqual(plan["schema_version"], "search-planner-held-task-plan/v1")
        self.assertEqual(plan["status"], "held_preview_no_dispatch")
        self.assertEqual(plan["summary"]["task_count"], 1)
        self.assertEqual(plan["summary"]["planned_selected_count"], 1)
        self.assertEqual(plan["summary"]["estimated_upper_cost_usd"], "0")
        self.assertEqual(plan["tasks"][0]["selected_candidate_ids"], [CAND])
        self.assertEqual(plan["tasks"][0]["linked_sources"][0]["candidate_id"], CAND)
        self.assertFalse(plan["external_dispatch_authorized"])
        self.assertFalse(plan["execution_enabled"])
        self.assertEqual(plan["provider_requests_in_this_plan"], 0)
        self.assertEqual(plan["dollars_spent_in_this_plan"], "0.00000000")
        self.assertEqual(src, original)
        self.assertEqual(plan, build_held_task_plan(src, catalog,
                         limits=limits(market="BY"), now=NOW))

    def test_no_collector_port_does_not_guess_ready_source(self):
        src = planning_input()
        planner = TaskPlanner(planning_reader=lambda *a: src,
                              clock=lambda: NOW)
        plan = planner.preview(PID, QV, limits=limits(market="BY"))
        self.assertEqual(plan["tasks"], [])
        self.assertEqual(plan["summary"]["blocked_selected_count"], 1)
        self.assertEqual(plan["blocked_queries"][0]["block_reasons"],
                         ["no_compatible_source"])
        self.assertFalse(plan["external_dispatch_authorized"])

    def test_existing_qv_links_keep_all_origins_after_source_dedup(self):
        src = planning_input(second=True)
        first = build_held_task_plan(src, capability_fixture(),
                                     limits=limits(market="BY"), now=NOW)
        self.assertEqual(first["summary"]["source_selected_count"], 2)
        self.assertEqual(first["summary"]["planned_selected_count"], 2)
        self.assertEqual(first["summary"]["task_count"], 1)
        physical = first["tasks"][0]
        self.assertEqual(physical["selected_candidate_ids"], sorted([CAND,CID2]))
        self.assertEqual({r["hypothesis_id"] for r in physical["linked_sources"]},
                         {"short", "morning"})
        self.assertEqual({r["candidate_id"] for r in physical["linked_sources"]},
                         {CAND, CID2})
        self.assertEqual(physical["source_fact_ids"], ["fact-second"])
        self.assertEqual(first["summary"]["covered_hypothesis_ids"],
                         ["morning", "short"])
        self.assertEqual(first["summary"]["max_external_requests_upper_bound"], 1)
        self.assertEqual(first["summary"]["estimated_upper_cost_usd"], "0")

    def test_unknown_price_is_not_silently_zero(self):
        src = planning_input()
        c = capability_fixture()
        c["sources"][0].update(pricing_status="unknown",
                               cost_usd_per_request=None)
        plan = build_held_task_plan(src,c,limits=limits(market="BY"),now=NOW)
        self.assertEqual(plan["tasks"], [])
        self.assertIn("pricing_not_verified",
                      plan["blocked_queries"][0]["block_reasons"])
        self.assertFalse(plan["summary"]["estimated_cost_is_verified"])

    def test_paid_price_requires_explicit_planning_cost_ceiling_even_for_preview(self):
        src=planning_input()
        denied=build_held_task_plan(src,price_catalog(),limits=limits(market="BY"),now=NOW)
        self.assertEqual(denied["tasks"], [])
        self.assertIn("planning_cost_cap_exceeded",
                      denied["blocked_queries"][0]["block_reasons"])
        planned=build_held_task_plan(src,price_catalog(),
                                    limits=limits(market="BY", max_cost_usd="0.10"),now=NOW)
        self.assertEqual(planned["summary"]["estimated_upper_cost_usd"], "0.08")
        self.assertEqual(planned["tasks"][0]["estimated_upper_cost_usd"], "0.08")
        self.assertFalse(planned["external_dispatch_authorized"])
        self.assertEqual(planned["dollars_spent_in_this_plan"], "0.00000000")

    def test_source_quota_and_capability_limit_block_fail_closed(self):
        source=planning_input()
        for modified, expected in (
            (price_catalog(quota=0),"quota_exhausted"),
            (price_catalog(quota=1),"source_quota_exceeded"),
        ):
            modified["sources"][0]["capabilities"][0]["max_pages"] = 2
            modified["sources"][0]["capabilities"][0]["pagination_verified"] = True
            p=limits(market="BY",max_cost_usd="1.00",max_pages_per_task=2)
            plan=build_held_task_plan(source,modified,limits=p,now=NOW)
            self.assertIn(expected,
                          plan["blocked_queries"][0]["block_reasons"])
        c=capability_fixture()
        c["sources"][0]["capabilities"][0]["max_pages"]=1
        p=limits(market="BY",max_pages_per_task=2)
        plan=build_held_task_plan(source,c,limits=p,now=NOW)
        self.assertIn("pages_exceed_verified_capability",
                      plan["blocked_queries"][0]["block_reasons"])

    def test_unsupported_mode_wrong_language_or_market_is_blocked(self):
        src=planning_input()
        for mode, lang, market, reason in (
            ("hashtag","ru","BY","unsupported_query_mode"),
            ("keyword","de","BY","language_not_verified"),
            ("keyword","ru","NL","market_not_verified"),
            ("keyword","ru",None,"market_not_specified"),
        ):
            raw=deepcopy(src)
            raw["validated_bundle"]["queries"][0]["query_mode"]=mode
            raw["shared_links"]["links"][0]["query_mode"]=mode
            raw["validated_bundle"]["queries"][0]["language"]=lang
            raw["shared_links"]["links"][0]["language"]=lang
            raw["shared_links"]["links_sha256"]=sha256(raw["shared_links"]["links"])
            p=build_held_task_plan(raw,capability_fixture(),
                 limits=limits(market=market),now=NOW)
            self.assertIn(reason,p["blocked_queries"][0]["block_reasons"])
            self.assertEqual(p["tasks"], [])

    def test_stale_capability_catalog_never_creates_tasks(self):
        plan=build_held_task_plan(planning_input(),capability_fixture(),
              limits=limits(market="BY"),now=NOW+timedelta(days=1))
        self.assertEqual(plan["tasks"], [])
        self.assertIn("capability_snapshot_expired",
                      plan["blocked_queries"][0]["block_reasons"])

    def test_planning_quotas_do_not_discard_selected_candidates(self):
        src=planning_input(second=True)
        # Two *distinct* physical queries needed for an actual task limit.
        src["validated_bundle"]["queries"][1]["text"]="планка утром"
        src["shared_links"]["links"][1]["text"]="планка утром"
        src["shared_links"]["links_sha256"]=sha256(src["shared_links"]["links"])
        p=build_held_task_plan(src,capability_fixture(),
             limits=limits(market="BY",max_tasks=1),now=NOW)
        self.assertEqual(p["summary"]["task_count"],1)
        self.assertEqual(p["summary"]["planned_selected_count"],1)
        self.assertEqual(p["summary"]["blocked_selected_count"],1)
        self.assertIn("task_limit_reached",p["blocked_queries"][0]["block_reasons"])

    def test_true_zero_work_preserves_qv_coverage(self):
        p=build_held_task_plan(planning_input(no_work=True),capability_fixture(),
                 limits=limits(market="BY"),now=NOW)
        self.assertTrue(p["no_work"])
        self.assertEqual(p["tasks"],[])
        self.assertEqual(p["blocked_queries"],[])
        self.assertEqual(p["summary"]["source_hypothesis_count"],1)
        self.assertEqual(p["summary"]["uncovered_hypothesis_ids"],["short"])

    def test_tampered_or_foreign_qv_evidence_fails_before_task_preview(self):
        src=planning_input()
        for mutation in (
            lambda x:x.__setitem__("project_id","b"*32),
            lambda x:x["validated_bundle"]["queries"][0].__setitem__("candidate_id",CID2),
            lambda x:x["shared_links"]["links"][0].__setitem__("text","forged"),
            lambda x:x["shared_links"].__setitem__("links_sha256","0"*64),
            lambda x:x["shared_links"]["links"][0]["linked_sources"][0].__setitem__("hypothesis_id","fake"),
            lambda x:x["validated_bundle"].__setitem__("embedding_mode","test_fixture"),
            lambda x:x["validated_bundle"]["queries"].clear(),
        ):
            bad=deepcopy(src)
            mutation(bad)
            with self.subTest(mutation=str(mutation)),self.assertRaises(PipelineError):
                build_held_task_plan(bad,capability_fixture(),
                     limits=limits(market="BY"),now=NOW)

    def test_unsupported_limits_and_unknown_source_fail_before_any_request(self):
        for bad in (
            {"max_tasks":1}, limits(max_cost_usd="NaN"),
            limits(max_cost_usd="-1"), limits(max_pages_per_task=True),
            limits(source_ids=["x","x"]), limits(market="bad"),
            limits(max_tasks=999),
        ):
            with self.subTest(bad=bad),self.assertRaises(PipelineError):
                validate_plan_limits(bad)
        p=build_held_task_plan(planning_input(),capability_fixture(),
                   limits=limits(market="BY",source_ids=["missing_source"]),now=NOW)
        self.assertEqual(p["tasks"],[])
        self.assertIn("source_not_in_catalog",p["blocked_queries"][0]["block_reasons"])

    def test_service_preview_uses_server_planner_only(self):
        src=planning_input()
        port=FakePort(capability_fixture())
        planner=TaskPlanner(
             planning_reader=lambda pid,rid: deepcopy(src),
             capability_matcher=CapabilityMatcher(port,clock=lambda:NOW),
             clock=lambda:NOW)
        svc=SearchPlannerService(task_planner=planner)
        p=svc.preview_source_tasks(PID,QV,limits=limits(market="BY"))
        self.assertEqual(p["summary"]["task_count"],1)
        self.assertEqual(port.calls,[PID])
        with self.assertRaises(PipelineError):
            svc.preview_source_tasks(PID,"wrong",limits=limits(market="BY"))
        with self.assertRaises(PipelineError):
            SearchPlannerService(task_planner={})

    def test_db_reader_cross_checks_immutable_qv_and_reviewed_c9(self):
        src=planning_input()
        class Rows:
            def __init__(self, one):self.one=one
            def fetchone(self):return self.one
        class DB:
            def __init__(self):self.calls=[]
            def __enter__(self):return self
            def __exit__(self,*a):return False
            def transaction(self):return self
            def execute(self,sql,args=None):
                self.calls.append((sql,args))
                if sql.startswith("SET TRANSACTION"):return Rows(None)
                if "FROM qv_runs r" in sql:
                    return Rows((QV,GEN,src["validated_bundle"]["source_bundle_sha256"],
                       "semantic_local","completed",1,PUB,src["taxonomy_version"],
                       src["taxonomy_sha256"],src["generation_input_sha256"],
                       "completed",OUTBOX,"validated_queries_ready/v1",
                       {"schema_version":"validated_queries/v1",
                        "validation_run_id":QV,"generation_id":GEN,
                        "embedding_mode":"semantic_local","selected_count":1},
                       "committed_ack"))
                raise AssertionError(sql)
        db=DB()
        with mock.patch(
            "intelligence.search_planner.task_planner.repository.read_published_snapshot",
            return_value=c9_fixture(),
        ):
            actual=load_verified_planning_input(
               PID,QV,connect=lambda:db,
               validated_reader=lambda *_ ,**kwargs:deepcopy(src["validated_bundle"]),
               links_reader=lambda *_:deepcopy(src["shared_links"]))
        self.assertEqual(actual["taxonomy_sha256"],src["taxonomy_sha256"])
        self.assertIn("READ ONLY",db.calls[0][0])
        self.assertEqual(db.calls[1][1],(QV,PID))
        self.assertIn("sqg_generations",db.calls[1][0])
        self.assertIn("qv_outbox",db.calls[1][0])
        self.assertEqual(len(db.calls),2)


if __name__=="__main__":
    unittest.main()
