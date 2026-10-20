"""One-shot REAL end-to-end Taxonomy Builder acceptance on a disposable PG database.

Runs against OpenRouter ONLY when explicitly allowed by the one-shot Actions
dispatcher. Aggregate allowance <= $0.60, INCLUDING prior $0.000048472 smoke.
This test-run's human approvals are deterministic FIXTURE decisions, NOT user
approval of any production taxonomy. A single failed stage stops permanently.
"""
from __future__ import annotations
import json,os,sys,tempfile,traceback
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from intelligence import migrate
from intelligence.taxonomy_builder.catalog import refresh_model_catalog,list_models
from intelligence.taxonomy_builder.intake import get_intake_view,confirm_intake
from intelligence.taxonomy_builder.pipeline_repository import save_draft,approve_draft
from intelligence.taxonomy_builder.cost_repository import create_quote,authorize_budget
from intelligence.taxonomy_builder.run_repository import (start_run,advance_run,get_run,
    submit_fact_review,publish_reviewed_topics)
from intelligence.taxonomy_builder.provider import request_payload,reservation_cost
from intelligence.taxonomy_builder.cost_estimator import rates
from intelligence.relevance.repository import get_approved_context
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

TOTAL_LIMIT=Decimal("0.60")
PRIOR_CONFIRMED_SPEND=Decimal("0.000646664")
THIS_RUN_CAP=Decimal("0.10")
OUTFILE=Path("taxonomy_live_e2e_result.json")
PROFILE={
  "name":"Synthetic Fitness Nutrition Coach — realistic research case",
  "niche":"Health & Fitness",
  "product_type":"mobile_app",
  "goal":"Найти тематики коротких видео с максимальным потенциалом просмотров, "
         "изучить широкие связанные и смежные темы, затем ранжировать идеи "
         "для дальнейшего исследования. Не рекламировать несуществующие функции.",
  "product_functions":(
      "Мобильное приложение позволяет записывать блюда в дневник питания, "
      "оценивать калории по фотографии порции, вручную корректировать размер "
      "порции и ингредиенты, просматривать недельную историю питания. "
      "Приложение показывает рассчитанные за день ориентировочные калории "
      "и напоминания о регулярном ведении дневника. "
      "В приложении нет медицинской диагностики, назначения лекарств, "
      "измерения глюкозы или гарантированных прогнозов похудения."
  ),
  "audience_problems":(
      "Начинающим трудно вести дневник питания регулярно, "
      "они забывают записывать перекусы и напитки, "
      "ошибаются с величиной порций и оценкой калорий при еде в кафе. "
      "Люди сомневаются в оценках по фото, путают массу готового и сырого блюда, "
      "ищут простой способ понимать состав питания без сложных таблиц. "
      "Некоторые испытывают информационную перегрузку от противоречивых советов "
      "о белках, режимах питания и правильных привычках."
  ),
  "product_description":(
      "Вымышленный образовательный продукт для взрослых, интересующихся "
      "питанием и тренировками. Он помогает организовать записи о еде, "
      "но не обещает лечение, медицинскую диагностику и снижение веса. "
      "Основная задача исследования: выбрать контент-направления с высоким "
      "потенциалом охвата, включая прямые темы о ведении дневника, "
      "боли аудитории и смежные темы привычек, покупки продуктов и "
      "разбора популярных мифов. Идеи видео в таксономии иллюстративные, "
      "не следует считать их реально найденными роликами."
  ),
  "competitors":"Синтетические конкуренты: приложения для пищевых дневников и подсчёта калорий",
  "video_references":"Примеры форматов для исследования: разбор ошибки порций, дневник на неделю, питание вне дома"
}
def priced_candidates(models):
    """Ignore unsupported conditional pricing BEFORE sorting or planning calls."""
    supported=[]
    for m in models:
        cap=m.get("capabilities") or {}
        if (not m.get("selectable") or
            (cap.get("max_output_tokens") or 0)<3600 or
            (cap.get("context_length") or 0)<12000):
            continue
        try:
            prices=rates(m.get("pricing"))
        except PipelineError:
            continue
        supported.append((m,prices))
    supported.sort(key=lambda pair: (
        0 if pair[0]["model_id"]=="deepseek/deepseek-v4.1-flash" else 1,
        pair[1]["prompt"]*1000 + pair[1]["completion"]*2048 +
        pair[1]["request"]))
    return [m for m,_ in supported]

def write(out):
    OUTFILE.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")

def main():
    outcome={"test_kind":"real OpenRouter disposable Postgres full taxonomy run",
       "budget_total_usd":str(TOTAL_LIMIT),"prior_real_spend_usd":str(PRIOR_CONFIRMED_SPEND),
       "this_run_authorized_usd":str(THIS_RUN_CAP),"stage_results":[],
       "human_review_mode":"synthetic fixture decisions, not a user-approved real project",
       "run_success":False}
    if os.environ.get("RUN_LIVE_TAXONOMY_E2E")!="YES_THIS_ONE_RUN":
        raise SystemExit("LIVE opt-in absent — no charged requests")
    token=os.environ.get("OPENROUTER_API_KEY")
    if not token:raise SystemExit("OPENROUTERKEY unavailable — no charged requests")
    if not os.environ.get("CM_DB_DSN") or not os.environ.get("CM_TEST_PG_DSN"):
        raise SystemExit("Disposable PostgreSQL not configured — no charged requests")
    if PRIOR_CONFIRMED_SPEND+THIS_RUN_CAP > TOTAL_LIMIT:
        raise SystemExit("Insufficient aggregate user permission — no charged requests")
    import psycopg,projects
    with tempfile.TemporaryDirectory() as td:
        os.environ["CM_PROJECTS_DIR"]=td
        os.environ["CM_DB_ENABLED"]="1"
        with psycopg.connect(os.environ["CM_DB_DSN"],autocommit=True) as c:
            migrate.apply_migrations(c)
        p=projects.create_project(PROFILE)
        pid=p["id"]
        profile=get_intake_view(pid)
        if not profile["can_confirm"]:
            raise SystemExit("Synthetic intake missing fields — no charged requests")
        confirm_intake(pid,profile["profile_revision"],profile["profile_hash"])
        with psycopg.connect(os.environ["CM_DB_DSN"]) as conn:
            refresh_model_catalog(conn)
            valid=[m for m in list_models(conn)
                 if m["selectable"] and m["capabilities"].get("max_output_tokens")
                 and m["capabilities"].get("context_length")]
        # Conditional pricing is excluded before comparing candidates.
        ranked=priced_candidates(valid)
        if not ranked:
            raise SystemExit("No model has verifiable fixed pricing and enough context; zero paid calls")
        # Previous live run demonstrated that the cheapest reasoning-heavy model
        # is reliable for extraction but truncated taxonomy JSON at 3600 tokens.
        # Prefer independently selected stronger models for generation.
        priorities=[
          "anthropic/claude-sonnet-5.5","openai/gpt-6.1-sol",
          "google/gemini-4-argon","google/gemini-3.8-flash",
          "anthropic/claude-haiku-5.5","qwen/qwen3.8-27b",
          "deepseek/deepseek-v4.1-flash"]
        # Realistic outputs: 10–14K+ generation tokens and explicit reasoning
        # when the verified provider advertises the effort. This is an output
        # allowance, not a request to fill it artificially.
        capable=[m for m in ranked if m["capabilities"]["max_output_tokens"]>=12000
                 and m["capabilities"]["context_length"]>=32000]
        capable.sort(key=lambda m:(
                   0 if "structured_outputs" in m["capabilities"].get("supported_parameters",[])
                       else 1,
                   priorities.index(m["model_id"]) if m["model_id"] in priorities else len(priorities),
                   0 if ("high" in m["capabilities"].get("reasoning_efforts",[]) or
                         "medium" in m["capabilities"].get("reasoning_efforts",[])) else 1,
                   rates(m["pricing"])["completion"]))
        # Pair a validated low-cost extractor/critic with a capable generator.
        chosen=None
        def verified_reasoning(row,prefer_high=False):
            allowed=row["capabilities"].get("reasoning_efforts",[])
            effort=("high" if prefer_high and "high" in allowed else
                    "medium" if "medium" in allowed else
                    "low" if "low" in allowed else None)
            return {"mode":"effort","effort":effort} if effort else {"mode":"default"}
        for generator in capable:
            for cheap in ranked:
                if cheap["capabilities"]["max_output_tokens"]<6000:
                    continue
                specs=[("fact_extraction",6000,"facts-v1",cheap),
                       ("taxonomy_generation",
                        min(20000,generator["capabilities"]["max_output_tokens"]),
                        "taxonomy-v1",generator),
                       ("taxonomy_critic",6000,"critic-v1",cheap)]
                stages=[{"role":role,"enabled":True,
                    "model_id":row["model_id"],
                    "catalog_version_id":row["catalog_version_id"],
                    "reasoning":verified_reasoning(row,prefer_high=role=="taxonomy_generation"),
                    "temperature":None,
                    "max_output_tokens":n,"prompt_version":prompt}
                    for role,n,prompt,row in specs]
                try:
                    snapshots={role:{"capabilities":row["capabilities"],"pricing":row["pricing"]}
                      for role,_,_,row in specs}
                    proxy={"questionnaire":PROFILE,"approved_facts":[
                      {"id":"00000000-0000-0000-0000-000000000001",
                       "kind":"function","text":"Sample confirmed fact"}],
                      "topics":[{"name":"test category","relation":"direct"}]}
                    reserves=[reservation_cost(request_payload(stage,snapshots[stage["role"]],proxy),
                                               snapshots[stage["role"]])
                              for stage in stages]
                    if sum(reserves) <= THIS_RUN_CAP:
                        chosen=(specs,stages,reserves);break
                except PipelineError:
                    continue
            if chosen is not None:break
        if chosen is None:
            raise SystemExit("No verified three-stage model configuration fits $0.10; zero charged calls")
        specs,stages,reserves=chosen
        outcome["models_by_stage"]={role:row["model_id"] for role,_,_,row in specs}
        outcome["stage_token_limits"]={st["role"]:st["max_output_tokens"] for st in stages}
        outcome["stage_reasoning"]={st["role"]:st["reasoning"] for st in stages}
        outcome["model_id"]=outcome["models_by_stage"]["taxonomy_generation"]
        outcome["preflight_stage_reservations_usd"]=[str(x) for x in reserves]
        outcome["preflight_total_reservation_usd"]=str(sum(reserves))
        write(outcome)
        config={"schema_version":1,"stages":stages}
        saved=save_draft(pid,config,0)
        approved=approve_draft(pid,saved["draft_revision"])
        quote=create_quote(pid,approved["pipeline_id"])["quote"]
        if Decimal(quote["high_usd"]) > THIS_RUN_CAP:
            raise SystemExit("Quote high scenario exceeds $0.10, abort before any charged requests")
        consent=authorize_budget(pid,quote["quote_id"],str(THIS_RUN_CAP))["consent"]
        started=start_run(pid,quote["quote_id"],consent["consent_id"],
                           "live-e2e-fixture-one-shot")
        run_id=started["run_id"]
        outcome["started_status"]=started["status"]
        outcome["quote_high_usd"]=quote["high_usd"]
        def one_paid_stage(expected,role):
            if expected not in ("extraction_pending","generation_pending","critic_pending"):
                raise ValueError("Unexpected expected stage state")
            before=get_run(pid,run_id)
            if before["status"]!=expected:
                raise RuntimeError("Missing human gate or wrong stage before charge")
            actual=advance_run(pid,run_id,api_key=token)
            after=get_run(pid,run_id)
            outcome["stage_results"].append({
                "role":role,"state":after["status"],
                "provider_charged_usd":actual.get("charged_usd"),
                "attempts":len(after["stages"]),
                "spent_cumulative_usd":after["spent_usd"],
                "reserved_usd":after["reserved_usd"],
                "provider_response_id":actual.get("provider_response_id"),
                "usage":after["stages"][-1].get("usage") if after["stages"] else None,
                "validation_error":after["stages"][-1].get("error") if after["stages"] else None})
            write(outcome)
            if actual["status"] not in ("fact_review","critic_pending","topic_review"):
                raise RuntimeError("Live stage failed/uncertain. Stop; DO NOT retry")
            if Decimal(after["spent_usd"])+PRIOR_CONFIRMED_SPEND>TOTAL_LIMIT:
                raise RuntimeError("Aggregate charged amount exceeds user authorization; STOP")
            return after
        extracted=one_paid_stage("extraction_pending","fact_extraction")
        facts=extracted["fact_proposals"]
        outcome["fact_count"]=len(facts)
        write(outcome)
        if len(facts)<4:
            raise RuntimeError("Realistic source questionnaire yielded fewer than four verifiable facts")
        # This is a synthetic test owner's predetermined fixture review.
        # No production content is approved on the user's behalf.
        choices=[{"id":f["id"],"action":"approve"} for f in facts]
        submit_fact_review(pid,run_id,choices)
        generated=one_paid_stage("generation_pending","taxonomy_generation")
        if generated["status"]!="critic_pending":
            raise RuntimeError("Critic gate not reached; stop without additional charge")
        generated_topics=generated["topic_proposals"]
        outcome["proposed_topic_count"]=len(generated_topics)
        outcome["proposed_topic_relations"]=sorted({x["relation"] for x in generated_topics})
        write(outcome)
        if len(generated_topics)<8:
            raise RuntimeError("Realistic taxonomy must propose at least eight distinct directions")
        if not {"direct","problem_related","adjacent"}.issubset(
            {x["relation"] for x in generated_topics}):
            raise RuntimeError("Taxonomy must cover direct, problem_related and adjacent directions")
        critiqued=one_paid_stage("critic_pending","taxonomy_critic")
        if critiqued["status"]!="topic_review":
            raise RuntimeError("Topic human review gate not reached")
        topic_choices=[{"id":t["id"],"action":"approve"}
                       for t in critiqued["topic_proposals"]]
        publication=publish_reviewed_topics(pid,run_id,topic_choices)
        with psycopg.connect(os.environ["CM_DB_DSN"]) as conn:
            sre=get_approved_context(conn,pid)
            if sre["taxonomy_version_id"]!=publication["sre_taxonomy_version_id"]:
                raise RuntimeError("SRE canonical version mismatch")
            if len(sre["categories"])!=len(publication["topics"]):
                raise RuntimeError("SRE does not contain exact human-approved categories")
        finish=get_run(pid,run_id)
        if finish["status"]!="approved" or Decimal(finish["reserved_usd"])!=0:
            raise RuntimeError("Unexpected final run state or reserved money")
        outcome["final_spent_usd"]=finish["spent_usd"]
        outcome["total_spent_including_prior_usd"]=str(Decimal(finish["spent_usd"])+PRIOR_CONFIRMED_SPEND)
        outcome["taxonomy_categories_count"]=len(sre["categories"])
        outcome["sre_taxonomy_version_id"]=sre["taxonomy_version_id"]
        outcome["run_success"]=True
        write(outcome)
        print("Full real 3-stage Taxonomy Builder E2E PASSED:",
              "models",outcome["models_by_stage"],"cost USD",finish["spent_usd"],
              "SRE categories",len(sre["categories"]))
if __name__=="__main__":
    try:main()
    except Exception as err:
        print("LIVE E2E STOPPED (no automatic retry):",type(err).__name__,str(err)[:300])
        sys.exit(1)
