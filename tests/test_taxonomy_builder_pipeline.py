"""Block 3: provider-supported model capabilities and strict stage contracts."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from intelligence.taxonomy_builder.pipeline_contracts import (
    PipelineError,validate_pipeline,normalize_capability,serialize_openrouter_stage,sha256)
from intelligence.taxonomy_builder.catalog import fetch_openrouter_models

PRIMARY="openai/gpt-6.1-sol"
SECOND="anthropic/claude-sonnet-5.5"

def provider_model(model_id=PRIMARY, *, temperature=False, reasoning=True,
                   efforts=None, structured=True, output=8000):
    params=["max_tokens"]
    if structured: params+=["structured_outputs","response_format"]
    if temperature: params.append("temperature")
    if reasoning: params.append("reasoning")
    row={"id":model_id,"supported_parameters":params,"context_length":32000,
         "architecture":{"output_modalities":["text"]},
         "top_provider":{"max_completion_tokens":output},
         "pricing":{"prompt":"0.000002","completion":"0.000010"}}
    if reasoning:
        row["reasoning"]={"mandatory":False,
                          "supported_efforts":efforts if efforts is not None else
                          ["low","medium","high"]}
    return row

def config(a_id=1,b_id=2):
    return {"schema_version":1,"stages":[
      {"role":"fact_extraction","model_id":PRIMARY,"catalog_version_id":a_id,
       "reasoning":{"mode":"effort","effort":"medium"},"temperature":None,
       "max_output_tokens":3000,"prompt_version":"facts-v1"},
      {"role":"taxonomy_generation","model_id":SECOND,"catalog_version_id":b_id,
       "reasoning":{"mode":"default"},"temperature":0.5,
       "max_output_tokens":4000,"prompt_version":"taxonomy-v1"}]}

def entries():
    return {1:{"model_id":PRIMARY,"capabilities":normalize_capability(provider_model()),
               "pricing":{"prompt":"0.000002"},"verified_at":"2026-10-20T00:00:00Z",
               "catalog_sha256":"a"*64},
            2:{"model_id":SECOND,"capabilities":normalize_capability(
                   provider_model(SECOND,temperature=True)),
               "pricing":{"prompt":"0.000001"},"verified_at":"2026-10-20T00:00:00Z",
               "catalog_sha256":"b"*64}}

class StageValidation(unittest.TestCase):
    def test_model_switch_reasoning_temperature_per_stage(self):
        result,snapshots=validate_pipeline(config(),entries())
        self.assertEqual(result["stages"][0]["reasoning"]["effort"],"medium")
        self.assertEqual(result["stages"][1]["temperature"],0.5)
        self.assertEqual(snapshots["fact_extraction"]["model_id"],PRIMARY)
    def test_temperature_rejected_on_unsupported_model(self):
        stages=config()
        stages["stages"][0]["temperature"]=0.4
        with self.assertRaisesRegex(PipelineError,"temperature"):
            validate_pipeline(stages,entries())
    def test_combined_effort_and_temperature_not_inferred(self):
        stages=config()
        stages["stages"][1]["reasoning"]={"mode":"effort","effort":"high"}
        with self.assertRaisesRegex(PipelineError,"not verified"):
            validate_pipeline(stages,entries())
        # A *provider verified* combination is allowed if the catalog states so.
        eligible=entries()
        eligible[2]["capabilities"]["temperature_compatible_efforts"]=["high"]
        result,_=validate_pipeline(stages,eligible)
        self.assertEqual(result["stages"][1]["reasoning"]["effort"],"high")
    def test_unsupported_effort(self):
        stages=config()
        stages["stages"][0]["reasoning"]={"mode":"effort","effort":"max"}
        with self.assertRaisesRegex(PipelineError,"unverified"):
            validate_pipeline(stages,entries())
    def test_budget_not_inferred(self):
        stages=config()
        stages["stages"][0]["reasoning"]={"mode":"budget","budget_tokens":1000}
        with self.assertRaisesRegex(PipelineError,"not verified"):
            validate_pipeline(stages,entries())
    def test_no_fake_structured_support(self):
        eligible=entries()
        eligible[1]["capabilities"]["supports_structured_outputs"]=False
        with self.assertRaisesRegex(PipelineError,"JSON"):
            validate_pipeline(config(),eligible)
    def test_cannot_remove_gate_or_disable_required_stage(self):
        stages=config()
        stages["stages"][0]["enabled"]=False
        with self.assertRaises(PipelineError):
            validate_pipeline(stages,entries())
        with self.assertRaises(PipelineError):
            validate_pipeline({"schema_version":1,"stages":[stages["stages"][1]]},entries())
        stages=config()
        stages["stages"]=list(reversed(stages["stages"]))
        with self.assertRaisesRegex(PipelineError,"order"):
            validate_pipeline(stages,entries())
    def test_optional_critic_disabled(self):
        stages=config()
        stages["stages"].append({"role":"taxonomy_critic","enabled":False})
        result,_=validate_pipeline(stages,entries())
        self.assertEqual(result["stages"][-1]["enabled"],False)
        stages["stages"][-1]["temperature"]=0
        with self.assertRaises(PipelineError):
            validate_pipeline(stages,entries())
    def test_catalog_id_pinning(self):
        stages=config(a_id=2,b_id=2)
        with self.assertRaises(PipelineError):
            validate_pipeline(stages,entries())
    def test_reasoning_schema_negative_and_prompt_injection_blocked(self):
        stages=config()
        stages["stages"][1]["temperature"]=float("nan")
        with self.assertRaises(PipelineError):
            validate_pipeline(stages,entries())
        stages=config()
        stages["stages"][0]["prompt_version"]="not-approved"
        with self.assertRaises(PipelineError):
            validate_pipeline(stages,entries())
    def test_provider_fields_and_serialization(self):
        norm,snapshots=validate_pipeline(config(),entries())
        first=serialize_openrouter_stage(norm["stages"][0],snapshots["fact_extraction"])
        second=serialize_openrouter_stage(norm["stages"][1],snapshots["taxonomy_generation"])
        self.assertEqual(first["reasoning"],{"effort":"medium"})
        self.assertNotIn("temperature",first)
        self.assertEqual(second["temperature"],0.5)
        self.assertNotIn("reasoning",second)
        self.assertEqual(second["response_format"]["type"],"json_schema")
    def test_false_reasoning_metadata_not_invented(self):
        row=provider_model(reasoning=False)
        cap=normalize_capability(row)
        self.assertEqual(cap["reasoning_efforts"],[])
        self.assertFalse(cap["reasoning_mandatory"])
        self.assertIsNone(cap["reasoning_budget_limits"])
    def test_immutable_checksum_order(self):
        self.assertEqual(sha256({"a":1,"b":2}),sha256({"b":2,"a":1}))
