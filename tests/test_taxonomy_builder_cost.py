"""Cost estimation: pricing is OpenRouter USD PER TOKEN and results are indicative."""
import sys,unittest
from pathlib import Path
from decimal import Decimal
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from intelligence.taxonomy_builder.cost_estimator import estimate,rates,check_budget,amount
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

def fixture(profile=None,extra=None):
    profile=profile or {"functions_text":"Подсчёт калорий по фото","problems_text":"Не могу похудеть"}
    config={"schema_version":1,"stages":[
       {"role":"fact_extraction","enabled":True,"model_id":"m1",
        "catalog_version_id":1,"reasoning":{"mode":"effort","effort":"high"},
        "temperature":None,"max_output_tokens":2000,"prompt_version":"facts-v1"},
       {"role":"taxonomy_generation","enabled":True,"model_id":"m2",
        "catalog_version_id":2,"reasoning":{"mode":"default"},
        "temperature":0.5,"max_output_tokens":4000,"prompt_version":"taxonomy-v1"},
       {"role":"taxonomy_critic","enabled":False}
    ]}
    snaps={
       "fact_extraction":{"model_id":"m1","pricing":{
           "prompt":"0.000002","completion":"0.000010","request":"0"},
           "verified_at":"2026-10-20T00:00:00Z",
           "capabilities":{"text_output":True,"supports_structured_outputs":True,
                           "context_length":100000}},
       "taxonomy_generation":{"model_id":"m2","pricing":{
           "prompt":"0.000001","completion":"0.000005","request":"0.0001"},
           "verified_at":"2026-10-20T00:00:00Z",
           "capabilities":{"text_output":True,"supports_structured_outputs":True,
                           "context_length":100000}},
    }
    if extra: snaps["fact_extraction"]["pricing"].update(extra)
    return profile,config,snaps

class CostEstimatorTests(unittest.TestCase):
    def test_whole_cycle_with_real_per_token_rates(self):
        p,c,s=fixture()
        x=estimate(p,[],c,s)
        self.assertEqual(len(x["stages"]),2)
        self.assertEqual(x["currency"],"USD")
        self.assertEqual(x["stages"][0]["usd_per_million_input"],"2.000000")
        self.assertEqual(x["stages"][0]["usd_per_million_output"],"10.000000")
        self.assertTrue(Decimal(x["low_usd"])<=Decimal(x["expected_usd"])<=Decimal(x["high_usd"]))
        self.assertTrue(x["assumptions"]["human_review_can_change_generation_input"])
        self.assertFalse(x["assumptions"]["documents_included"])
    def test_critic_adds_cost(self):
        p,c,s=fixture()
        basic=estimate(p,[],c,s)
        c["stages"][2]={"role":"taxonomy_critic","enabled":True,
            "model_id":"m3","catalog_version_id":3,
            "reasoning":{"mode":"default"},"temperature":None,
            "max_output_tokens":1800,"prompt_version":"critic-v1"}
        s["taxonomy_critic"]={"model_id":"m3","pricing":{"prompt":"0.000001",
             "completion":"0.000005"},"capabilities":{"text_output":True,
             "supports_structured_outputs":True,"context_length":100000}}
        with_critic=estimate(p,[],c,s)
        self.assertEqual(len(with_critic["stages"]),3)
        self.assertGreater(Decimal(with_critic["high_usd"]),Decimal(basic["high_usd"]))
    def test_extra_reasoning_fee_counted_conservatively(self):
        p,c,s=fixture()
        basic=estimate(p,[],c,s)
        s["fact_extraction"]["pricing"]["internal_reasoning"]="0.00001"
        extra=estimate(p,[],c,s)
        self.assertGreater(Decimal(extra["expected_usd"]),Decimal(basic["expected_usd"]))
    def test_documents_declared_as_excluded(self):
        p,c,s=fixture()
        value=estimate(p,[{"name":"study.pdf","size":550000}],c,s)
        self.assertEqual(value["assumptions"]["excluded_documents_count"],1)
        self.assertEqual(value["assumptions"]["excluded_documents_bytes"],550000)
    def test_unknown_and_conditional_prices_fail_closed(self):
        p,c,s=fixture()
        for pricing in (
          {"prompt":"not a price","completion":"0.01"},
          {"prompt":"0.1","completion":"NaN"},
          {"prompt":"0.1","completion":"-1"},
          {"prompt":"0.1"},
          {"prompt":"0.1","completion":"0.1","overrides":[{"conditions":{"min_prompt_tokens":1000}}]},
          {"prompt":"0.1","completion":"0.1","discount":0.2},
        ):
            s["fact_extraction"]["pricing"]=pricing
            with self.assertRaises(PipelineError):
                estimate(p,[],c,s)
    def test_budget_requires_conservative_scenario_and_limits(self):
        self.assertEqual(check_budget("0.05","0.03"),Decimal("0.05"))
        for cap in ("0","NaN","-1","0.001","10000.000001","0.00000001"):
            with self.assertRaises(PipelineError):check_budget(cap,"0.01")
    def test_quote_not_guaranteed_ceiling(self):
        p,c,s=fixture()
        result=estimate(p,[],c,s)
        self.assertIn("NOT a guaranteed maximum",result["assumptions"]["uncertainty"])
