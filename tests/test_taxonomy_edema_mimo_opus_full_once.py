"""No paid calls: verify exact model assignment, source fixture and budget."""
import unittest
from decimal import Decimal
from scripts.taxonomy_edema_mimo_flash_opus_full_once import (
    CASES, MIMO, OPUS, PROFILE, PROJECT_CAP, BATCH_CAP,
    PRIOR_CONFIRMED, worst_inputs, pick_config
)

def fake(mid,rate_in,rate_out):
    return {"model_id":mid,"selectable":True,"catalog_version_id":1,
            "verified_at":"2026-10-20T00:00:00+00:00",
            "pricing":{"prompt":rate_in,"completion":rate_out},
            "capabilities":{"text_output":True,"supports_structured_outputs":True,
                "max_output_tokens":64000,"context_length":1048576,
                "reasoning_efforts":["low","medium","high"],
                "supported_parameters":["max_tokens","reasoning","structured_outputs"]}}

class MiMoOpusFullOnce(unittest.TestCase):
    def test_one_exact_user_combo(self):
        self.assertEqual(len(CASES),1)
        self.assertEqual(tuple(CASES[0][1:]),(MIMO,OPUS,OPUS))
    def test_synthetic_detailed_edema_fixture(self):
        self.assertGreater(sum(len(x) for x in PROFILE.values()),5000)
        self.assertIn("не ставит диагноз",PROFILE["product_functions"])
        self.assertIn("не назначает мочегонные",PROFILE["product_functions"])
        self.assertIn("одностороннего отёка",PROFILE["audience_problems"])
    def test_global_budget_caps_under_one_dollar(self):
        self.assertLess(PROJECT_CAP,Decimal("1"))
        self.assertLess(PRIOR_CONFIRMED+BATCH_CAP,PROJECT_CAP)
        self.assertGreater(BATCH_CAP,Decimal("0.1"))
    def test_realistic_downstream_preflight(self):
        m={MIMO:fake(MIMO,"0.00000012","0.0000003"),
           OPUS:fake(OPUS,"0.0000015","0.000009")}
        choice,error=pick_config(CASES[0],m,Decimal("0.56"))
        self.assertIsNotNone(choice,error)
        stages,_,reserve=choice
        self.assertEqual([x["model_id"] for x in stages],[MIMO,OPUS,OPUS])
        self.assertEqual([x["prompt_version"] for x in stages],
                         ["facts-v2","taxonomy-v2","critic-v2"])
        self.assertEqual(stages[1]["reasoning"],{"mode":"effort","effort":"high"})
        self.assertLessEqual(sum(reserve)*Decimal("1.05"),Decimal("0.56"))
        self.assertEqual(len(worst_inputs()["taxonomy_critic"]["topics"]),9)
    def test_fails_closed_if_reservation_unaffordable(self):
        m={MIMO:fake(MIMO,"0.00000012","0.0000003"),
           OPUS:fake(OPUS,"0.0001","0.0001")}
        choice,error=pick_config(CASES[0],m,Decimal("0.56"))
        self.assertIsNone(choice)
        self.assertEqual(error,"conservative_preflight_exceeds_remaining_key_budget")

if __name__=="__main__":
    unittest.main()
