"""Offline regression for Google Gemini Argon 400 INVALID_ARGUMENT diagnosis.

The provider transport schema is intentionally small; server-side topic
validation remains the same and human approval remains mandatory.
No API key / network / paid model call in these tests.
"""
import unittest

from intelligence.taxonomy_builder.provider import request_payload
from intelligence.taxonomy_builder.run_contracts import (
    schema_for,validate_topics,reviewable_topic_batch,validate_facts)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

ARGON="google/gemini-4-argon"
OTHER="anthropic/claude-opus-5.5"
FACT_A="00000000-0000-4000-8000-000000000011"
FACT_B="00000000-0000-4000-8000-000000000022"
FACTS=[
    {"id":FACT_A,"kind":"function","text":"Мобильный дневник субъективных ощущений",
     "review_status":"approved"},
    {"id":FACT_B,"kind":"problem","text":"Люди забывают отмечать симптомы",
     "review_status":"approved"}
]
def stage(role,model):
    return {"role":role,"model_id":model,"max_output_tokens":10000,
            "reasoning":{"mode":"effort","effort":"high"},"temperature":None,
            "prompt_version":{"fact_extraction":"facts-v2",
                              "taxonomy_generation":"taxonomy-v2",
                              "taxonomy_critic":"critic-v2"}[role]}
def snapshot():
    return {"capabilities":{"supported_parameters":["structured_outputs","reasoning"],
                           "context_length":1048576},"pricing":{"prompt":"0.000002","completion":"0.000010"}}
def inputs():
    return {"approved_facts":FACTS,"niche":"health_fitness",
            "research_goal":"maximize_views",
            "request":"Find grounded research themes, not invented medical effects."}
def topic(name,relation,ref):
    return {"name":name,"relation":relation,"origin":"inferred","fact_ids":[ref],
            "definition":"Описание направления исследования", 
            "include":"Образовательные объяснения", 
            "exclude":"Обещания чудесного лечения",
            "positive_examples":["Формат образовательного видео"],
            "negative_examples":["Волшебное средство от всего"]}

class GoogleArgonTransport(unittest.TestCase):
    def test_google_generation_simplifies_only_transport_constraints(self):
        payload=request_payload(stage("taxonomy_generation",ARGON),snapshot(),inputs())
        wrapped=payload["response_format"]
        self.assertEqual(wrapped["type"],"json_schema")
        self.assertEqual(wrapped["json_schema"]["strict"],False)
        self.assertEqual(wrapped["json_schema"]["name"],"tb_taxonomy_generation")
        schema=wrapped["json_schema"]["schema"]
        self.assertEqual(schema["properties"]["topics"]["type"],"array")
        item=schema["properties"]["topics"]["items"]
        self.assertEqual(set(item["required"]),set((
            "name","relation","origin","fact_ids","definition",
            "include","exclude","positive_examples","negative_examples")))
        def scan(node):
            if isinstance(node,dict):
                self.assertFalse({"minItems","maxItems","additionalProperties","enum"} & set(node))
                for value in node.values():scan(value)
            elif isinstance(node,list):
                for value in node:scan(value)
        scan(schema)
        self.assertEqual(payload["reasoning"],{"effort":"high"})
        self.assertEqual(payload["max_tokens"],10000)
        self.assertEqual(payload["provider"],{
            "require_parameters":True,"allow_fallbacks":False})

    def test_other_models_and_google_other_stages_keep_existing_strict_schema(self):
        for model,role in ((OTHER,"taxonomy_generation"),
                           (ARGON,"fact_extraction"),(ARGON,"taxonomy_critic")):
            with self.subTest(model=model,role=role):
                ins=inputs() if role=="taxonomy_generation" else (
                    {"questionnaire":{"product_functions":"Это тест", 
                                      "audience_problems":"Тестовые боли"}} if role=="fact_extraction"
                    else {"approved_facts":FACTS,"topics":[],"review_only":True})
                payload=request_payload(stage(role,model),snapshot(),ins)
                wrapper=payload["response_format"]["json_schema"]
                self.assertTrue(wrapper["strict"])
                self.assertIn("additionalProperties",wrapper["schema"])
        self.assertEqual(schema_for("taxonomy_generation")["schema"]["properties"]["topics"]["maxItems"],20)
        with self.assertRaises(PipelineError):
            schema_for("fact_extraction",google_argon_compat=True)

    def test_server_still_rejects_fabricated_grounding(self):
        valid={"topics":[topic("Дневник","direct",FACT_A),
                         topic("Забытые записи","problem_related",FACT_B)]}
        self.assertEqual(len(validate_topics(valid,FACTS)),2)
        false={"topics":[topic("Вылечит за час","direct",FACT_B),
                         topic("Дневник","direct",FACT_A)]}
        with self.assertRaisesRegex(PipelineError,"function"):
            validate_topics(false,FACTS)
        fake={"topics":[topic("Ложное лечение","direct",
                               "00000000-0000-4000-8000-000000000099"),
                         topic("Дневник","direct",FACT_A),
                         topic("Забытые записи","problem_related",FACT_B)]}
        batch=reviewable_topic_batch(fake,FACTS)
        self.assertEqual(len(batch["topics"]),2)
        self.assertEqual(len(batch["excluded_topics"]),1)
        with self.assertRaises(PipelineError):
            validate_topics(fake,FACTS)

if __name__=="__main__":
    unittest.main()
