"""SRE structural regression suite: evidence, routing, provenance and honest metrics."""
import sys
import unittest
from pathlib import Path
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from intelligence.relevance.contracts import (
    ContractError,InvalidModelOutput,validate_profile,validate_taxonomy,
    validate_pipeline,validate_labels)
from intelligence.relevance.pipeline import run_pipeline,StageExecutionError
from intelligence.relevance.presets import cascade,strong_only
from intelligence.relevance.experiments import evaluate
from intelligence.relevance.builder import validate_topic_proposals

def sample():
    return [
      {"category_key":str(uuid4()),"name":"Ошибки при похудении",
       "relation_type":"problem_related","origin":"inferred","independent":True,
       "definition":"Ошибки и заблуждения, мешающие снижению веса",
       "rules":{"include":["Разбор ошибки"],"exclude":["Личная история без разбора"],
                "positive_examples":["Неучтённые калории напитков"],
                "negative_examples":["Дневник тренировок"]},
       "related_fact_ids":[]},
      {"category_key":str(uuid4()),"name":"Здоровые рецепты",
       "relation_type":"adjacent","origin":"inferred","independent":True,
       "definition":"Приготовление полезных блюд",
       "rules":{"include":["Пошаговый рецепт"],"exclude":["Обзор автомобиля"],
                "positive_examples":["Завтрак из овсянки"],
                "negative_examples":["Как накачать бицепс"]},
       "related_fact_ids":[]}
    ]

VIDEO={"title":"Калории в напитках мешают худеть",
       "description":"Разбор неучтённых калорий",
       "transcript":[{"text":"Не забывайте учитывать калории напитков","start_ms":0,"end_ms":3200}],
       "scenes":[{"text":"Человек показывает стакан сока","start_ms":0,"end_ms":3200}]}

def label(decision, quote="Калории в напитках",source="title"):
    return {"decision":decision,"reason":"Source-backed decision",
            "evidence":[{"source":source,"quote":quote}] if decision=="match" else []}

class ContractTests(unittest.TestCase):
    def test_profile_fixed_niche_and_goal(self):
        p={"niche_code":"health_fitness","product_type":"app",
           "functions_text":"Подсчёт калорий","problems_text":"Вес не снижается"}
        self.assertEqual(validate_profile(p)["research_goal"],"maximize_views")
        for field,value in (("niche_code","cars"),("research_goal","sell")):
            with self.assertRaises(ContractError):
                validate_profile({**p,field:value})
    def test_taxonomy_has_origin_approved_links_or_independence(self):
        rows=sample()
        self.assertEqual(len(validate_taxonomy(rows)),2)
        for changed in (
            {**rows[0],"relation_type":"problem"},
            {**rows[0],"origin":"magic"},
            {**rows[0],"independent":False},
            {**rows[0],"related_fact_ids":[str(uuid4())]},
        ):
            with self.assertRaises(ContractError): validate_taxonomy([changed])
        with self.assertRaises(ContractError):
            validate_taxonomy(rows+rows)
    def test_example_kind_distinction_and_ai_cannot_claim_verified_video(self):
        row=sample()[0]
        row["examples"]={"positive":[{"text":"Example","example_kind":"verified_video","video_id":22}],
                         "negative":[{"text":"Bad","example_kind":"illustrative"}]}
        self.assertEqual(validate_taxonomy([row])[0]["examples"]["positive"][0]["video_id"],22)
        with self.assertRaises(ContractError): validate_topic_proposals([row],set())
        row["examples"]["positive"][0].pop("video_id")
        with self.assertRaises(ContractError): validate_taxonomy([row])
    def test_pipeline_compatibility_and_calibration(self):
        self.assertEqual(len(validate_pipeline(cascade())["stages"]),3)
        self.assertEqual(len(validate_pipeline(strong_only())["stages"]),1)
        with self.assertRaises(ContractError):
            validate_pipeline({"stages":[{"id":"e","type":"embedding_retrieval","model_ref":"e"}]})
        with self.assertRaises(ContractError):
            validate_pipeline({"stages":[{"id":"s","type":"llm_classifier",
                                         "model_ref":"s","run_when":"on_uncertain"}]})
        cfg=cascade()
        cfg["stages"][0]["settings"]["allow_candidate_drop"]=True
        with self.assertRaises(ContractError): validate_pipeline(cfg)
        cfg["stages"][0]["settings"]["calibration"]={
            "dataset_id":"human-gold-v1","taxonomy_version":"v1",
            "model_ref":"embedding_model","observed_recall":0.99}
        self.assertEqual(len(validate_pipeline(cfg)["stages"]),3)
    def test_unverified_or_empty_evidence_cannot_produce_match(self):
        cid=sample()[0]["category_key"]
        for invalid in (
            {"decision":"match","reason":"x","evidence":[]},
            {"decision":"match","reason":"x","evidence":[{"source":"title","quote":"Fabricated"}]},
            {"decision":"match","reason":"x","evidence":[{"source":"scene","quote":"Человек"}]},
            {"decision":"match","reason":"x","evidence":[{"source":"transcript","quote":"Missing"}]},
        ):
            with self.assertRaises(InvalidModelOutput):
                validate_labels({cid:invalid},[cid],VIDEO)
        valid={"decision":"match","reason":"Transcript explains the issue",
               "evidence":[{"source":"transcript","index":0,
                           "quote":"учитывать калории напитков","start_ms":0,"end_ms":3200}]}
        self.assertEqual(validate_labels({cid:valid},[cid],VIDEO)[cid]["decision"],"match")
        invalid={**valid,"evidence":[{"source":"transcript","index":0,
                       "quote":"учитывать калории напитков","start_ms":9999,"end_ms":10500}]}
        with self.assertRaises(InvalidModelOutput):
            validate_labels({cid:invalid},[cid],VIDEO)
    def test_missing_model_output_is_error_not_uncertain(self):
        cid=sample()[0]["category_key"]
        with self.assertRaises(InvalidModelOutput):
            validate_labels({},[cid],VIDEO)
    def test_strong_only_matches(self):
        cats=sample()
        def strong(stage,video,cs):
            return {c["category_key"]:label("match" if i==0 else "no_match")
                    for i,c in enumerate(cs)}
        out=run_pipeline(strong_only(),VIDEO,cats,{"strong_llm":strong})
        self.assertEqual(out["labels"][cats[0]["category_key"]]["decision"],"match")
    def test_cascade_embedding_ranks_and_strong_escalates(self):
        cats=sample()
        called=[]
        def embed(stage,video,cs):
            called.append("embedding")
            return {cats[0]["category_key"]:0.2,cats[1]["category_key"]:0.99}
        def fast(stage,video,cs):
            called.append("fast")
            self.assertEqual(cs[0]["category_key"],cats[1]["category_key"])
            return {c["category_key"]:label("uncertain" if c["category_key"]==cats[0]["category_key"]
                                              else "no_match") for c in cs}
        def strong(stage,video,cs):
            called.append("strong")
            self.assertEqual([c["category_key"] for c in cs],[cats[0]["category_key"]])
            return {cs[0]["category_key"]:label("match")}
        out=run_pipeline(cascade(),VIDEO,cats,
                         {"embedding_model":embed,"fast_llm":fast,"strong_llm":strong})
        self.assertEqual(called,["embedding","fast","strong"])
        self.assertEqual(out["labels"][cats[1]["category_key"]]["decision"],"no_match")
    def test_candidate_drop_is_not_a_negative(self):
        cats=sample()
        cfg=cascade()
        cfg["stages"][0]["settings"]={
            "allow_candidate_drop":True,"top_k":1,
            "calibration":{"dataset_id":"approved-gold-set","taxonomy_version":"1",
                           "model_ref":"embedding_model","observed_recall":0.95}}
        def emb(s,v,cs):
            return {cats[0]["category_key"]:0.95,cats[1]["category_key"]:0.1}
        def llm(s,v,cs):
            return {c["category_key"]:label("match") for c in cs}
        out=run_pipeline(cfg,VIDEO,cats,{"embedding_model":emb,
                                           "fast_llm":llm,"strong_llm":llm})
        self.assertEqual(out["labels"][cats[1]["category_key"]]["decision"],"not_evaluated")
        self.assertEqual(out["stage_trace"][0]["candidates"],[cats[0]["category_key"]])
    def test_provider_and_schema_errors_are_distinct(self):
        cats=sample()
        with self.assertRaises(StageExecutionError) as c:
            run_pipeline(strong_only(),VIDEO,cats,{"strong_llm":lambda *args: {}})
        self.assertEqual(c.exception.kind,"invalid_output")
        def crash(*args):
            raise RuntimeError("Private provider failure")
        with self.assertRaises(StageExecutionError) as c:
            run_pipeline(strong_only(),VIDEO,cats,{"strong_llm":crash})
        self.assertEqual(c.exception.kind,"provider_error")
    def test_benchmark_abstentions_are_counted(self):
        a=("v1","T1"); b=("v2","T1"); c=("v3","T1")
        scores=evaluate({a:"match",b:"not_evaluated",c:"no_match"},
                        {a:"match",b:"match",c:"no_match"})
        self.assertEqual(scores["recall"],0.5)
        self.assertEqual(scores["coverage"],2/3)
        self.assertEqual(scores["decided_count"],2)
