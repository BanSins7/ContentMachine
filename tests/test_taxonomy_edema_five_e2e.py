"""Free confidence tests for 5-model paid E2E preflight and synthetic case."""
import unittest
from decimal import Decimal

from scripts.taxonomy_edema_five_e2e import (
    PROFILE,CASES,ARGON,MIMO,OPUS,PRIOR_CONFIRMED,PROJECT_CAP,
    BATCH_CAP,worst_inputs,pick_config,assert_sre_equal,
)

def row(mid,prompt,completion):
    return {
      "model_id":mid,"selectable":True,"catalog_version_id":1,
      "verified_at":"2026-10-20T00:00:00+00:00",
      "pricing":{"prompt":prompt,"completion":completion},
      "capabilities":{
        "max_output_tokens":64000,"context_length":262144,
        "reasoning_efforts":["low","medium","high"] if mid!=MIMO else [],
        "supported_parameters":["max_tokens","reasoning","response_format"],
        "text_output":True,"supports_structured_outputs":True,
      }
    }

class FiveEdemaCases(unittest.TestCase):
    def test_exact_owner_matrix(self):
        self.assertEqual(CASES,(
          ("01_argon_all",ARGON,ARGON,ARGON),
          ("02_mimo_all",MIMO,MIMO,MIMO),
          ("03_opus_all",OPUS,OPUS,OPUS),
          ("04_mimo_argon",MIMO,ARGON,ARGON),
          ("05_mimo_opus",MIMO,OPUS,OPUS),
        ))

    def test_no_treatment_claims_as_features(self):
        features=PROFILE["product_functions"]
        self.assertIn("не ставит диагноз",features)
        self.assertIn("не назначает мочегонные",features)
        self.assertIn("врачу",features)
        self.assertGreater(sum(len(v) for v in PROFILE.values()),3500)

    def test_budget_below_key_maximum(self):
        self.assertLess(PROJECT_CAP,Decimal("1"))
        self.assertLess(PRIOR_CONFIRMED+BATCH_CAP,PROJECT_CAP)

    def test_proxy_represents_downstream_dynamic_size(self):
        inputs=worst_inputs()
        self.assertEqual(len(inputs["taxonomy_generation"]["approved_facts"]),14)
        self.assertEqual(len(inputs["taxonomy_critic"]["topics"]),9)
        self.assertEqual(set(inputs),{"fact_extraction","taxonomy_generation","taxonomy_critic"})

    def test_every_combo_can_select_when_budget_sufficient(self):
        models={
            ARGON:row(ARGON,"0.000002","0.000010"),
            OPUS:row(OPUS,"0.000004","0.000020"),
            MIMO:row(MIMO,"0.0000001","0.0000003"),
        }
        for case in CASES:
            with self.subTest(case=case[0]):
                chosen,error=pick_config(case,models,Decimal("0.80"))
                self.assertIsNotNone(chosen,error)
                stages,snaps,reserves=chosen
                self.assertEqual(len(stages),3)
                self.assertEqual([x["model_id"] for x in stages],list(case[1:]))
                self.assertLessEqual(sum(reserves)*Decimal("1.05"),Decimal("0.80"))

    def test_compact_fact_prompt_and_low_cost_extraction(self):
        from intelligence.taxonomy_builder.run_contracts import prompt_for
        from intelligence.taxonomy_builder.pipeline_contracts import ALLOWED_PROMPT_VERSIONS
        self.assertIn("facts-v2",ALLOWED_PROMPT_VERSIONS["fact_extraction"])
        messages=prompt_for("fact_extraction",{
          "questionnaire":PROFILE,"niche":"health_fitness",
          "research_goal":"maximize_views","attachments":"excluded"
        },"facts-v2")
        self.assertIn("8 to 10",messages[0]["content"])
        self.assertIn("EXACT contiguous substring",messages[0]["content"])
        models={
            ARGON:row(ARGON,"0.000002","0.000010"),
            OPUS:row(OPUS,"0.000004","0.000020"),
            MIMO:row(MIMO,"0.0000001","0.0000003"),
        }
        for case in CASES:
            selected,error=pick_config(case,models,Decimal("0.80"))
            self.assertIsNotNone(selected,error)
            stages=selected[0]
            self.assertEqual(stages[0]["prompt_version"],"facts-v2")
            if stages[0]["model_id"] in (ARGON,OPUS):
                self.assertEqual(stages[0]["reasoning"],{"mode":"effort","effort":"low"})
            self.assertEqual(stages[1]["reasoning"],(
                {"mode":"effort","effort":"high"} if stages[1]["model_id"] in (ARGON,OPUS)
                else {"mode":"default"}))

    def test_sre_deep_equality_detects_tampering(self):
        topic={
          "id":"c7b076f3-0cf5-4a09-a10d-8d44baa66261",
          "name":"Дневник утренней припухлости","relation":"direct",
          "definition":"Ведение заметок","origin":"inferred",
          "fact_ids":["de57f36c-4e2d-4964-a6a3-4dd1377ec04c"],
          "include":"Примеры дневников","exclude":"Волшебные диуретики",
          "positive_examples":["Дневник самочувствия"],
          "negative_examples":["Чудо-таблетка"],
        }
        sre={
          "categories":[{
            "category_key":topic["id"],"name":topic["name"],
            "relation_type":topic["relation"],"definition":topic["definition"],
            "origin":topic["origin"],"related_fact_ids":topic["fact_ids"],
            "rules":{"include":[topic["include"]],"exclude":[topic["exclude"]],
                     "positive_examples":topic["positive_examples"],
                     "negative_examples":topic["negative_examples"]},
          }]
        }
        assert_sre_equal([topic],sre)
        sre["categories"][0]["rules"]["negative_examples"]=["Не тот контрпример"]
        with self.assertRaisesRegex(RuntimeError,"SRE"):
            assert_sre_equal([topic],sre)

if __name__=="__main__":
    unittest.main()
