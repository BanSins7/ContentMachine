"""Offline, no API/no model downloads, Query Validation v1 QA matrix."""
from __future__ import annotations
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.search_planner import build_hypothesis_contexts
from intelligence.search_planner.query_validation.contracts import DEFAULT_POLICY,validate_policy
from intelligence.search_planner.query_validation.embedding import (
    HashingTestEmbeddings,encode_checked,
)
from intelligence.search_planner.query_validation.selection import select_queries
from test_search_planner_foundation import snapshot,POLICY


def sample():
    cs=build_hypothesis_contexts(snapshot(),POLICY)
    candidates=[]
    coverage=[]
    for n,ctx in enumerate(cs):
        hid=ctx["hypothesis"]["id"]
        queries=[
            (ctx["hypothesis"]["name"],"direct"),
            (ctx["hypothesis"]["name"]+" быстро", "situation"),
        ]
        coverage.append({"hypothesis_id":hid,"state":"generated",
                         "candidate_count":len(queries),"reason":None})
        for ix,(text,angle) in enumerate(queries):
            candidates.append({
                "candidate_id":f"00000000-0000-4000-8000-{n*2+ix+1:012x}",
                "hypothesis_id":hid,"direction_id":ctx["direction"]["id"],
                "branch_id":ctx["branch"]["id"] if ctx["branch"] else None,
                "source_fact_ids":[f["id"] for f in ctx["approved_facts"]],
                "text":text,"query_mode":"keyword","angle":angle,
                "language":"ru","research_stance":"neutral","rationale":"Research",
            })
    return {"schema_version":"query_candidates/v1","status":"completed",
            "candidates":candidates,"hypothesis_coverage":coverage},cs


class QueryValidationTests(unittest.TestCase):
    def setUp(self):
        self.embed=HashingTestEmbeddings()

    def test_selection_deterministic_complete_and_non_mutating(self):
        bundle,cs=sample()
        source=copy.deepcopy(bundle)
        one=select_queries(bundle,cs,DEFAULT_POLICY,self.embed)
        two=select_queries({**bundle,"candidates":list(reversed(bundle["candidates"]))},
                           cs,DEFAULT_POLICY,self.embed)
        self.assertEqual(one,two)
        self.assertEqual(bundle,source)
        self.assertEqual(len(one["decisions"]),len(bundle["candidates"]))
        self.assertEqual(len(one["coverage"]),len(cs))
        self.assertGreater(one["selected_count"],0)
        self.assertEqual(one["embedding_model_key"],"qa-hashing-fixture-v1")
        for decision in one["decisions"]:
            self.assertIn(decision["decision"],("selected","rejected"))
            self.assertTrue(-1<=decision["positive_similarity"]<=1)
            self.assertTrue(-1<=decision["negative_similarity"]<=1)

    def test_exact_and_semantic_duplicates_have_provenance(self):
        bundle,cs=sample()
        original=bundle["candidates"][0]
        dupe=copy.deepcopy(original)
        dupe["candidate_id"]="00000000-0000-4000-8000-000000000aaa"
        dupe["text"]="  "+original["text"].upper()+" "
        bundle["candidates"].append(dupe)
        for x in bundle["hypothesis_coverage"]:
            if x["hypothesis_id"]==dupe["hypothesis_id"]:
                x["candidate_count"]+=1
        result=select_queries(bundle,cs,DEFAULT_POLICY,self.embed)
        duplicates=[x for x in result["decisions"] if x["reason_code"]=="exact_duplicate"]
        self.assertEqual(len(duplicates),1)
        self.assertIsNotNone(duplicates[0]["duplicate_of"])
        self.assertEqual(len(result["decisions"]),len(bundle["candidates"]))

    def test_mmr_quota_and_angle_diversity(self):
        bundle,cs=sample()
        p={**DEFAULT_POLICY,"max_selected_per_hypothesis":1}
        result=select_queries(bundle,cs,p,self.embed)
        self.assertLessEqual(result["selected_count"],len(cs))
        self.assertEqual(sum(x["selected_count"] for x in result["coverage"]),
                         result["selected_count"])
        self.assertTrue(any(x["reason_code"] in ("quota","semantic_duplicate")
                            for x in result["decisions"]))

    def test_explicit_exclude_and_low_relevance_rejected(self):
        bundle,cs=sample()
        one=bundle["candidates"][0]
        ctx=next(x for x in cs if x["hypothesis"]["id"]==one["hypothesis_id"])
        one["text"]=ctx["hypothesis"]["exclude"]
        low=bundle["candidates"][1]
        low["text"]="zzzzzzbbbbqqqq unreferenced gibberish"
        policy={**DEFAULT_POLICY,"min_positive_similarity":.75}
        result=select_queries(bundle,cs,policy,self.embed)
        reasons={x["candidate_id"]:x["reason_code"] for x in result["decisions"]}
        self.assertEqual(reasons[one["candidate_id"]],"explicit_exclusion")
        self.assertEqual(reasons[low["candidate_id"]],"low_relevance")
        self.assertEqual(
            next(x for x in result["coverage"] if x["hypothesis_id"]==one["hypothesis_id"])["outcome"],
            "all_rejected",
        )

    def test_no_viable_coverage_not_silently_discarded(self):
        bundle,cs=sample()
        bundle["candidates"]=[]
        bundle["hypothesis_coverage"]=[{
            "hypothesis_id":c["hypothesis"]["id"],
            "state":"no_viable_queries","candidate_count":0,
            "reason":"No valid queries from LLM",
        } for c in cs]
        result=select_queries(bundle,cs,DEFAULT_POLICY,self.embed)
        self.assertEqual(result["selected_count"],0)
        self.assertEqual(len(result["coverage"]),len(cs))
        self.assertTrue(all(x["outcome"]=="no_source_candidates" for x in result["coverage"]))

    def test_fail_closed_on_lineage_and_scope_confusion(self):
        bundle,cs=sample()
        for field,value in (
            ("hypothesis_id","unknown"),("direction_id","wrong"),
            ("source_fact_ids",["not-approved"]),("language","zh"),
            ("query_mode","invalid")
        ):
            with self.subTest(field=field):
                broken=copy.deepcopy(bundle)
                broken["candidates"][0][field]=value
                with self.assertRaises(PipelineError):
                    select_queries(broken,cs,DEFAULT_POLICY,self.embed)
        broken=copy.deepcopy(bundle)
        broken["hypothesis_coverage"][0]["candidate_count"]=7
        with self.assertRaises(PipelineError):
            select_queries(broken,cs,DEFAULT_POLICY,self.embed)

    def test_provider_zero_nan_count_and_dimensions_rejected(self):
        class Bad:
            mode="semantic_local"
            model_key="fake-bad"
            def __init__(self,v): self.v=v
            def encode(self,t): return [self.v for _ in t]
        for v in ([0.]*64,[float("nan")]*64,[1.]+[0.]*4096):
            with self.subTest(v=v[:2]),self.assertRaises(PipelineError):
                encode_checked(Bad(v),["test"])
        with self.assertRaises(PipelineError):
            encode_checked(Bad([1.]*64),[""]*2)

    def test_finite_config_and_no_float_bool(self):
        for field,value in (
            ("min_positive_similarity",float("nan")),
            ("mmr_lambda",1.5),
            ("semantic_duplicate_similarity",.2),
            ("max_selected_per_hypothesis",True),
            ("cross_hypothesis_dedup",1),
        ):
            with self.subTest(field=field):
                p=copy.deepcopy(DEFAULT_POLICY);p[field]=value
                with self.assertRaises(PipelineError):
                    validate_policy(p)


if __name__=="__main__":
    unittest.main()
