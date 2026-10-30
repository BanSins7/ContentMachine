"""Block 6 strict evidence, human review and provider budget unit tests."""
import json,os,sys,unittest
from unittest import mock
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.run_contracts import (
    validate_facts,reviewable_fact_batch,review_facts,validate_topics,reviewable_topic_batch,validate_critic,review_topics,
    prompt_for,schema_for)
from intelligence.taxonomy_builder.provider import (
    invoke,request_payload,reservation_cost,ProviderUncertain,safe_openrouter_http_error,resolve_key)

PROFILE={"product_functions":"Приложение считает калории по фото",
         "audience_problems":"Сложно считать калории"}
FACTS={"facts":[
    {"kind":"function","text":"Считать калории","source_field":"product_functions",
     "source_quote":"считает калории по фото"},
    {"kind":"problem","text":"Контроль калорий","source_field":"audience_problems",
     "source_quote":"Сложно считать калории"}]}
TOPICS={"topics":[
    {"name":"Питание по фото","relation":"direct","origin":"explicit",
     "fact_ids":[],"definition":"Фотодневники питания","include":"Учёт","exclude":"Сомнительное",
     "positive_examples":["Как считать калории по фото"],
     "negative_examples":["Футбольные матчи"]},
    {"name":"Трудности подсчёта калорий","relation":"problem_related","origin":"inferred",
     "fact_ids":[],"definition":"Сложности новичков","include":"Ошибки учёта",
     "exclude":"Сверхрецепты","positive_examples":["Три ошибки"],
     "negative_examples":["Дрифт"]}]}
def snapshot():
    return {"capabilities":{"supported_parameters":["structured_outputs","reasoning"],
        "context_length":32000},"pricing":{"prompt":"0.000001","completion":"0.000006"}}
def stage(role="fact_extraction"):
    return {"role":role,"model_id":"x/model","max_output_tokens":700,
            "reasoning":{"mode":"effort","effort":"low"},"temperature":None,
            "prompt_version":"facts-v1"}

class BuilderRunContracts(unittest.TestCase):
    def test_local_settings_key_takes_priority_over_expired_environment_key(self):
        # No real keys or requests. Settings must control the next paid call.
        with (mock.patch.dict(os.environ, {"OPENROUTER_API_KEY":"sk-or-expired-env",
                                           "OPENROUTERKEY":"sk-or-old-alt"}),
              mock.patch("worker.openrouter_transcribe.get_key",
                         return_value="sk-or-updated-local") as read_local):
            self.assertEqual(resolve_key(),"sk-or-updated-local")
            read_local.assert_called_once()

    def test_headless_key_falls_back_to_environment(self):
        with (mock.patch.dict(os.environ, {"OPENROUTER_API_KEY":"sk-or-headless",
                                           "OPENROUTERKEY":"sk-or-alt"}),
              mock.patch("worker.openrouter_transcribe.get_key", return_value="")):
            self.assertEqual(resolve_key(),"sk-or-headless")
        with (mock.patch.dict(os.environ, {"OPENROUTER_API_KEY":"",
                                           "OPENROUTERKEY":"sk-or-alt"}),
              mock.patch("worker.openrouter_transcribe.get_key", return_value="")):
            self.assertEqual(resolve_key(),"sk-or-alt")


    def test_quote_normalization_restores_only_true_contiguous_source_span(self):
        profile={"product_functions":"Приложение считает калории по фото  еды."}
        data={"facts":[{"kind":"function","text":"Учитывает калории",
          "source_field":"product_functions","source_quote":"считает калории по ФОТО еды."}]}
        got=validate_facts(data,profile)
        self.assertEqual(got[0]["source_quote"],"считает калории по фото  еды.")
        self.assertIn(got[0]["source_quote"],profile["product_functions"])
        data["facts"][0]["source_quote"]="назначает лечение диабета"
        with self.assertRaises(PipelineError):
            validate_facts(data,profile)

    def test_partial_bad_quotes_are_quarantined_not_approved(self):
        response=json.loads(json.dumps(FACTS))
        response["facts"].append({"kind":"function","text":"Назначает лечение",
          "source_field":"product_functions","source_quote":"назначает лечение диабета"})
        verified=reviewable_fact_batch(response,PROFILE)
        self.assertEqual(len(verified["facts"]),2)
        self.assertEqual(len(verified["excluded_facts"]),1)
        self.assertEqual(verified["excluded_facts"][0]["review_status"],"not_source_verified")
        self.assertNotIn("id",verified["excluded_facts"][0])
        approved=review_facts(verified["facts"],[
            {"id":f["id"],"action":"approve"} for f in verified["facts"]])
        self.assertEqual(len(approved),2)
        with self.assertRaises(PipelineError):
            reviewable_fact_batch({"facts":[response["facts"][-1]]},PROFILE)

    def test_grounded_facts_and_human_review(self):
        facts=validate_facts(FACTS,PROFILE)
        self.assertEqual(len(facts),2)
        decisions=[{"id":facts[0]["id"],"action":"approve"},
                   {"id":facts[1]["id"],"action":"edit","text":"Подсчёт порций"},
                   {"action":"add","kind":"audience","text":"Спортсмены"}]
        accepted=review_facts(facts,decisions)
        self.assertEqual(len(accepted),3)
        self.assertEqual(accepted[-1]["origin"],"human_added")
    def test_hallucinated_source_never_approved(self):
        bad=json.loads(json.dumps(FACTS))
        bad["facts"][0]["source_quote"]="распознаёт анализы крови"
        with self.assertRaises(PipelineError):validate_facts(bad,PROFILE)
        bad["facts"][0]["source_field"]="unknown_field"
        with self.assertRaises(PipelineError):validate_facts(bad,PROFILE)
    def test_fact_review_requires_every_decision(self):
        f=validate_facts(FACTS,PROFILE)
        with self.assertRaises(PipelineError):review_facts(f,[{"id":f[0]["id"],"action":"approve"}])
        with self.assertRaises(PipelineError):review_facts(f,[
            {"id":f[0]["id"],"action":"approve"},
            {"id":f[1]["id"],"action":"reject"},
            {"id":f[0]["id"],"action":"approve"}])
    def test_harmless_provider_metadata_dropped_but_required_topic_fields_still_validated(self):
        f=validate_facts(FACTS,PROFILE)
        approved=review_facts(f,[{"id":x["id"],"action":"approve"} for x in f])
        rows=json.loads(json.dumps(TOPICS["topics"]))
        rows[0]["fact_ids"]=[approved[0]["id"]]
        rows[1]["fact_ids"]=[approved[1]["id"]]
        rows[0]["provider_explanation"]="This is NOT persisted or approved."
        for shape in (
            {"topics":rows,"reasoning_summary":"extraneous"},
            {"categories":rows},
            {"taxonomy":{"topics":rows}},
        ):
            value=validate_topics(shape,approved)
            self.assertEqual(len(value),2)
            self.assertNotIn("provider_explanation",value[0])
        bad=json.loads(json.dumps(rows))
        del bad[0]["fact_ids"]
        with self.assertRaises(PipelineError):
            validate_topics({"categories":bad},approved)
        with self.assertRaises(PipelineError):
            validate_topics({"categories":rows,"topics":rows},approved)

    def test_provider_schema_requires_nonempty_examples(self):
        schema=schema_for("taxonomy_generation")["schema"]
        item=schema["properties"]["topics"]["items"]
        for key in ("positive_examples","negative_examples"):
            self.assertEqual(item["properties"][key]["minItems"],1)
            self.assertEqual(item["properties"][key]["maxItems"],8)

    def test_real_model_example_formats_are_reviewable_without_invention(self):
        f=validate_facts(FACTS,PROFILE)
        approved=review_facts(f,[{"id":x["id"],"action":"approve"} for x in f])
        examples=json.loads(json.dumps(TOPICS["topics"]))
        examples[0]["fact_ids"]=[approved[0]["id"]]
        examples[1]["fact_ids"]=[approved[1]["id"]]
        examples[0]["positive_examples"]="Фото-дневник питания"
        examples[1]["negative_examples"]=[{"text":"Автообзор"}]
        invalid=dict(examples[1]);invalid["name"]="Неверная категория";invalid["positive_examples"]=[]
        result=reviewable_topic_batch({"topics":examples+[invalid]},approved)
        self.assertEqual(len(result["topics"]),2)
        self.assertEqual(len(result["excluded_topics"]),1)
        self.assertEqual(result["topics"][0]["positive_examples"],["Фото-дневник питания"])
        self.assertIn("extracted supplied text"," ".join(result["topics"][1]["normalization_notes"]))
        self.assertNotIn("id",result["excluded_topics"][0])
        with self.assertRaises(PipelineError):validate_topics({"topics":examples},approved)
        with self.assertRaises(PipelineError):
            reviewable_topic_batch({"topics":[invalid]},approved)

    def test_taxonomy_links_are_grounded(self):
        f=validate_facts(FACTS,PROFILE)
        approved=review_facts(f,[{"id":x["id"],"action":"approve"} for x in f])
        proposal=json.loads(json.dumps(TOPICS))
        proposal["topics"][0]["fact_ids"]=[approved[0]["id"]]
        proposal["topics"][1]["fact_ids"]=[approved[1]["id"]]
        generated=validate_topics(proposal,approved)
        self.assertEqual(len(generated),2)
        self.assertTrue(all(x["example_kind"]=="illustrative" for x in generated))
        decisions=[{"id":x["id"],"action":"approve"} for x in generated]
        reviewed=review_topics(generated,decisions,approved)
        self.assertEqual(len(reviewed),2)
        self.assertEqual(reviewed[0]["review_status"],"approved")
        proposal["topics"][0]["fact_ids"]=["unknown"]
        with self.assertRaises(PipelineError):validate_topics(proposal,approved)
    def test_human_can_add_independent_adjacent_category(self):
        f=validate_facts(FACTS,PROFILE)
        approved=review_facts(f,[{"id":x["id"],"action":"approve"} for x in f])
        proposal=json.loads(json.dumps(TOPICS))
        proposal["topics"][0]["fact_ids"]=[approved[0]["id"]]
        proposal["topics"][1]["fact_ids"]=[approved[1]["id"]]
        generated=validate_topics(proposal,approved)
        decisions=[{"id":t["id"],"action":"approve"} for t in generated]
        decisions.append({"action":"add","name":"Привычки здорового питания",
          "definition":"Общие полезные привычки",
          "include":"Общие советы","exclude":"Лекарства",
          "positive_examples":["Как питаться разнообразно"],
          "negative_examples":["Обзор автомобиля"]})
        published=review_topics(generated,decisions,approved)
        self.assertEqual(len(published),3)
        self.assertEqual(published[-1]["origin"],"user_added")
        self.assertEqual(published[-1]["relation"],"adjacent")
        self.assertTrue(published[-1]["independent"])
        self.assertEqual(published[-1]["fact_ids"],[])
    def test_human_cannot_add_false_direct_function(self):
        f=validate_facts(FACTS,PROFILE)
        approved=review_facts(f,[{"id":x["id"],"action":"approve"} for x in f])
        proposal=json.loads(json.dumps(TOPICS))
        proposal["topics"][0]["fact_ids"]=[approved[0]["id"]]
        proposal["topics"][1]["fact_ids"]=[approved[1]["id"]]
        generated=validate_topics(proposal,approved)
        decisions=[{"id":t["id"],"action":"approve"} for t in generated]
        decisions.append({"action":"add","name":"Вымышленная функция",
          "definition":"Функция из воздуха",
          "include":"Функция","exclude":"Неважное",
          "positive_examples":["Пример"],"negative_examples":["Другой"],
          "relation":"direct"})
        with self.assertRaises(PipelineError):
            review_topics(generated,decisions,approved)

    def test_critic_cannot_publish_or_reference_new_topics(self):
        f=validate_facts(FACTS,PROFILE)
        approved=review_facts(f,[{"id":x["id"],"action":"approve"} for x in f])
        proposal=json.loads(json.dumps(TOPICS))
        proposal["topics"][0]["fact_ids"]=[approved[0]["id"]]
        proposal["topics"][1]["fact_ids"]=[approved[1]["id"]]
        generated=validate_topics(proposal,approved)
        self.assertEqual(len(validate_critic({"findings":[{"topic_name":generated[0]["name"],
            "issue":"Overlap","suggestion":"Merge","severity":"medium"}]},generated)),1)
        with self.assertRaises(PipelineError):validate_critic({"findings":[{
            "topic_name":"Invented","issue":"Overlaps","suggestion":"Fix","severity":"high"}]},generated)
    def test_prompt_v2_versions_preserve_historical_v1_and_exact_allowed_fact_ids(self):
        fact_id="00000000-0000-0000-0000-000000000001"
        facts=[{"id":fact_id,"kind":"function","text":"Tracks portions"}]
        older=prompt_for("taxonomy_generation",{"approved_facts":facts},version="taxonomy-v1")
        newer=prompt_for("taxonomy_generation",{"approved_facts":facts},version="taxonomy-v2")
        self.assertNotIn("approved_fact_ids_by_kind",older[1]["content"])
        structured=json.loads(newer[1]["content"])
        self.assertEqual(structured["approved_fact_ids_by_kind"]["function"],[fact_id])
        self.assertIn("EXACT full UUID",newer[0]["content"])
        self.assertNotEqual(older[0]["content"],newer[0]["content"])
        with self.assertRaises(PipelineError):
            prompt_for("taxonomy_generation",{"approved_facts":facts},version="taxonomy-v9")
        st=stage("taxonomy_generation")
        st["prompt_version"]="taxonomy-v2"
        out=request_payload(st,snapshot(),{"approved_facts":facts})
        self.assertIn(fact_id,out["messages"][1]["content"])

    def test_preflight_reservation_and_provider_parameter_enforcement(self):
        snap=snapshot();st=stage()
        payload=request_payload(st,snap,{"questionnaire":PROFILE})
        self.assertTrue(payload["provider"]["require_parameters"])
        self.assertFalse(payload["provider"]["allow_fallbacks"])
        self.assertEqual(payload["reasoning"]["effort"],"low")
        self.assertNotIn("temperature",payload)
        self.assertGreater(reservation_cost(payload,snap),0)
    def test_real_adapter_mocked_usage_accounting(self):
        snap=snapshot();st=stage()
        output=json.dumps(FACTS,ensure_ascii=False)
        result=invoke(st,snap,{"questionnaire":PROFILE},key="test",
          sender=lambda body:{"id":"fake","model":"x/model","choices":[
             {"finish_reason":"stop","message":{"content":output}}],
             "usage":{"cost":0.0005,"prompt_tokens":320,
                      "completion_tokens":420,"completion_tokens_details":{"reasoning_tokens":80}}})
        self.assertEqual(result["usage"]["reasoning_tokens"],80)
        self.assertEqual(str(result["charged_usd"]),"0.0005")
    def test_real_http_invalid_argument_error_is_identifiable_but_redacted(self):
        from urllib.error import HTTPError
        from io import BytesIO
        body=json.dumps({"error":{"code":400,"message":"Provider returned error",
            "metadata":{"provider_name":"Google","provider_error_code":"400",
                        "raw":json.dumps({"error":{"status":"INVALID_ARGUMENT",
                                          "message":"Request contains an invalid argument."}}),
                        "unsafe":"sk-or-private"}},"user_id":"private"}).encode()
        ex=HTTPError("https://openrouter.ai/api/v1/chat/completions",400,
                     "Bad Request",{},BytesIO(body))
        got=safe_openrouter_http_error(ex,"sk-or-private")
        self.assertIn("HTTP 400",got)
        self.assertIn("Google",got)
        self.assertIn("INVALID_ARGUMENT",got)
        self.assertIn("Request contains an invalid argument",got)
        self.assertIn("Billing not confirmed",got)
        self.assertNotIn("private",got)
        self.assertNotIn("user_id",got)

    def test_missing_usage_blocks_retries(self):
        with self.assertRaises(ProviderUncertain):
            invoke(stage(),snapshot(),{"questionnaire":PROFILE},key="test",
                   sender=lambda body:{"choices":[{"message":{"content":"{}"}}]})
