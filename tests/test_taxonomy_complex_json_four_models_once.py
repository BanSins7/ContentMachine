"""Unpaid checks for real one-call complex schema provider compatibility suite."""
import unittest
from decimal import Decimal
from intelligence.taxonomy_builder.run_contracts import schema_for
from scripts.taxonomy_complex_json_five_models_once import (
    MODELS,FLASH,PRO,SOL,OPUS,stage_for,INPUTS,FACTS,
    NEW_BATCH_CEILING,KNOWN_USD,PREVIOUS_UNKNOWN_HOLD,
    V12_HTTP400_HOLD,KEY_GUARDED_CEILING
)

def cap():
    return {"text_output":True,"supports_structured_outputs":True,
            "supported_parameters":["structured_outputs","response_format","reasoning"],
            "max_output_tokens":16000,"context_length":262144,
            "reasoning_efforts":["low","medium","high"],
            "reasoning_mandatory":False}
def pricing(input_rate="0.0000001",output_rate="0.000001"):
    return {"prompt":input_rate,"completion":output_rate}

class FourComplexSchemaModels(unittest.TestCase):
    def test_user_named_four_not_five(self):
        self.assertEqual(len(MODELS),4)
        self.assertEqual(set(MODELS),{SOL,OPUS,PRO,FLASH})
    def test_complex_schema_not_simplified(self):
        reference=schema_for("taxonomy_generation")
        self.assertTrue(reference["strict"])
        topics=reference["schema"]["properties"]["topics"]
        self.assertEqual(topics["maxItems"],20)
        item=topics["items"]
        self.assertEqual(item["additionalProperties"],False)
        self.assertEqual(item["properties"]["positive_examples"]["minItems"],1)
        for model in MODELS:
            choice,error=stage_for(model,cap(),pricing(),Decimal("0.18"))
            with self.subTest(model=model):
                self.assertIsNotNone(choice,error)
                _,_,payload,reserve=choice
                self.assertEqual(payload["response_format"]["json_schema"],reference)
                self.assertEqual(payload["max_tokens"],2700)
                self.assertEqual(payload["messages"][0]["role"],"system")
                self.assertEqual(payload["provider"]["allow_fallbacks"],False)
                self.assertLessEqual(reserve,Decimal("0.18"))
    def test_no_money_spent_without_catalog_or_prices(self):
        missing=cap()
        missing["supported_parameters"]=["response_format"]
        selection,error=stage_for(SOL,missing,pricing(),Decimal("0.18"))
        self.assertIsNone(selection)
        self.assertEqual(error,"no_verified_json_schema_support")
        missing=cap()
        selection,error=stage_for(SOL,missing,{"completion":"0.00002"},Decimal("0.18"))
        self.assertIsNone(selection)
        self.assertIn("pricing",error)
    def test_worst_case_global_hold_below_user_one_dollar(self):
        self.assertLess(KNOWN_USD+PREVIOUS_UNKNOWN_HOLD+V12_HTTP400_HOLD
                        +NEW_BATCH_CEILING,KEY_GUARDED_CEILING)
        self.assertLess(KEY_GUARDED_CEILING,Decimal("1.00"))
        self.assertEqual(len(FACTS),4)
        self.assertIn("exactly THREE",INPUTS["request"])
    def test_budget_rejects_nonaffordable_high_priced_model(self):
        choice,error=stage_for(OPUS,cap(),
                     pricing("0.00002","0.00009"),Decimal("0.000001"))
        self.assertIsNone(choice)
        self.assertEqual(error,"no_safe_budget_or_token_configuration")
if __name__=="__main__":
    unittest.main()
