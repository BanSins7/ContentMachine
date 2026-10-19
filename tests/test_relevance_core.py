"""Offline SRE regression coverage; no credentials, API calls, or database needed."""
import sys
import unittest
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from intelligence.relevance.contracts import (
    ContractError,validate_profile,validate_taxonomy,validate_pipeline)
from intelligence.relevance.pipeline import run_pipeline
from intelligence.relevance.presets import cascade,strong_only
from intelligence.relevance.experiments import evaluate

def sample():
    return [
      {"category_key":str(uuid4()),"name":"Ошибки при похудении",
       "relation_type":"problem",
       "definition":"Ошибки и заблуждения, мешающие снижению веса",
       "rules":{"include":["Разбор ошибки"],"exclude":["Личная история без разбора"],
                "positive_examples":["Неучтённые калории напитков"],
                "negative_examples":["Дневник тренировок"]},
       "related_fact_ids":[]},
      {"category_key":str(uuid4()),"name":"Здоровые рецепты",
       "relation_type":"adjacent",
       "definition":"Приготовление полезных блюд",
       "rules":{"include":["Пошаговый рецепт"],"exclude":["Обзор автомобиля"],
                "positive_examples":["Завтрак из овсянки"],
                "negative_examples":["Как накачать бицепс"]},
       "related_fact_ids":[]}
    ]

class ContractTests(unittest.TestCase):
    def test_profile_has_fixed_niche_and_goal(self):
        good={"niche_code":"health_fitness","product_type":"app",
              "functions_text":"Подсчёт калорий","problems_text":"Не получается похудеть"}
        self.assertEqual(validate_profile(good)["research_goal"],"maximize_views")
        with self.assertRaises(ContractError):
            validate_profile({**good,"niche_code":"cars"})
        with self.assertRaises(ContractError):
            validate_profile({**good,"research_goal":"sales"})
    def test_taxonomy_requires_explicit_criteria(self):
        rows=sample()
        self.assertEqual(len(validate_taxonomy(rows)),2)
        with self.assertRaises(ContractError):
            validate_taxonomy(rows+rows)
        with self.assertRaises(ContractError):
            validate_taxonomy([{**rows[0],"rules":{"include":[]}}])
    def test_pipeline_validation(self):
        self.assertEqual(len(validate_pipeline(cascade())["stages"]),3)
        self.assertEqual(len(validate_pipeline(strong_only())["stages"]),1)
        with self.assertRaises(ContractError):
            validate_pipeline({"stages":[{"id":"1","type":"embedding_retrieval","model_ref":"emb"}]})
        with self.assertRaises(ContractError):
            validate_pipeline({"stages":[{"id":"1","type":"llm_classifier","model_ref":"llm",
                                          "run_when":"on_uncertain"}]})
        with self.assertRaises(ContractError):
            validate_pipeline({"stages":[{"id":"1","type":"embedding_retrieval","model_ref":"emb",
                                          "settings":{"allow_candidate_drop":True}},
                                         {"id":"2","type":"llm_classifier","model_ref":"llm"}]})
    def test_strong_only(self):
        cats=sample()
        def strong(stage,video,cs):
            return {x["category_key"]:{"decision":"match" if x==cs[0] else "no_match",
                                        "evidence":["test"]} for x in cs}
        value=run_pipeline(strong_only(),{},cats,{"strong_llm":strong})
        self.assertEqual(set(value["labels"]),{c["category_key"] for c in cats})
        self.assertEqual(len(value["stage_trace"]),1)
    def test_cascade_escalation_and_no_silent_negatives(self):
        cats=sample()
        called=[]
        def embed(stage,video,cs):
            called.append("embed")
            return {c["category_key"]:0.8 for c in cs}
        def fast(stage,video,cs):
            called.append("fast")
            return {c["category_key"]:{"decision":"uncertain" if i==0 else "no_match"}
                    for i,c in enumerate(cs)}
        def strong(stage,video,cs):
            called.append("strong")
            self.assertEqual(len(cs),1)
            return {cs[0]["category_key"]:{"decision":"match","evidence":["example"]}}
        out=run_pipeline(cascade(),{},cats,{"embedding_model":embed,"fast_llm":fast,"strong_llm":strong})
        self.assertEqual(called,["embed","fast","strong"])
        self.assertEqual(out["labels"][cats[0]["category_key"]]["decision"],"match")
        self.assertEqual(out["labels"][cats[1]["category_key"]]["decision"],"no_match")
    def test_calibrated_filter_is_not_evaluated_not_no_match(self):
        cats=sample()
        cfg=cascade()
        cfg["stages"][0]["settings"]={"calibrated":True,"allow_candidate_drop":True,"top_k":1}
        def embedding(stage,video,cs):
            return {cats[0]["category_key"]:0.9,cats[1]["category_key"]:0.3}
        def fast(stage,video,cs):
            return {cs[0]["category_key"]:{"decision":"match"}}
        out=run_pipeline(cfg,{},cats,{"embedding_model":embedding,"fast_llm":fast,"strong_llm":fast})
        self.assertEqual(out["labels"][cats[1]["category_key"]]["decision"],"not_evaluated")
    def test_invalid_model_output_rejected(self):
        cats=sample()
        with self.assertRaises(ContractError):
            run_pipeline(strong_only(),{},cats,{"strong_llm":lambda s,v,c: {"unknown":{"decision":"match"}}})
    def test_benchmark_abstentions_are_counted(self):
        a=("video1","category1")
        b=("video2","category1")
        stats=evaluate({a:"match",b:"not_evaluated"},{a:"match",b:"match"})
        self.assertEqual(stats["recall"],0.5)
        self.assertEqual(stats["coverage"],0.5)
        self.assertEqual(stats["fn"],1)
