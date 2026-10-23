"""Zero-cost deterministic quote tests (no OpenRouter)."""
import copy, sys, unittest
from decimal import Decimal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from intelligence.search_planner.generation_engine.cost_estimator import money,usd,approved_budget,estimate_generation
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

def model():
 return {"model_id":"test/model","snapshot_id":23,"catalog_sha256":"a"*64,
  "verified_at":"2026-10-23T09:00:00Z","context_length":100000,
  "max_output_tokens":8000,"rates":{"prompt":"0.0000002",
  "completion":"0.000002","internal_reasoning":"0","request":"0.00001"}}

def batches():
 return [{"id":"11111111-1111-4111-8111-111111111111",
   "input_sha256":"b"*64,"estimated_input_tokens":3500,
   "output_token_cap":8000},
  {"id":"22222222-2222-4222-8222-222222222222",
   "input_sha256":"c"*64,"estimated_input_tokens":2200,
   "output_token_cap":6000}]

class SearchQueryCostTests(unittest.TestCase):
 def test_deterministic_quote(self):
  a=estimate_generation(batches(),model(),generation_id="gen_1",
        input_sha256="d"*64,config_sha256="e"*64)
  b=estimate_generation(batches(),model(),generation_id="gen_1",
        input_sha256="d"*64,config_sha256="e"*64)
  self.assertEqual(a,b)
  self.assertEqual(len(a["pricing_snapshot"]["batch_reservations"]),2)
  self.assertGreater(Decimal(a["high_usd"]),Decimal(a["expected_usd"]))
  self.assertEqual(sum((Decimal(x["reserve_high_usd"]) for x in
       a["pricing_snapshot"]["batch_reservations"]),Decimal(0)),
       Decimal(a["high_usd"]))
  self.assertEqual(approved_budget(a["high_usd"],a["high_usd"]),
       Decimal(a["high_usd"]))
 def test_invalid_usd(self):
  for value in ("NaN","Infinity","-1","",1.2,True,None):
   with self.subTest(value=value),self.assertRaises(PipelineError):
    money(value,"test")
  for value in ("0","-1","10000.01","0.000000001"):
   with self.subTest(value=value),self.assertRaises(PipelineError):
    approved_budget(value,"0.00000001")
  with self.assertRaises(PipelineError):
   approved_budget("0.01","0.02")
 def test_context_limit_and_dupe(self):
  b=batches();b[0]["estimated_input_tokens"]=100000
  with self.assertRaises(PipelineError):
   estimate_generation(b,model(),generation_id="x",
       input_sha256="a"*64,config_sha256="b"*64)
  b=batches();b.append(copy.deepcopy(b[0]))
  with self.assertRaisesRegex(PipelineError,"Duplicate"):
   estimate_generation(b,model(),generation_id="x",
       input_sha256="a"*64,config_sha256="b"*64)
 def test_free_model_quantum(self):
  m=model();m["rates"]={key:"0" for key in m["rates"]}
  q=estimate_generation(batches(),m,generation_id="x",
       input_sha256="a"*64,config_sha256="b"*64)
  self.assertEqual(q["high_usd"],"0.00000002")
 def test_ceil(self):
  self.assertEqual(usd(Decimal("0.000000001")),"0.00000001")

if __name__=="__main__":
 unittest.main()
