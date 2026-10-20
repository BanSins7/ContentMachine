"""Five one-shot REAL Taxonomy Builder E2Es against disposable PostgreSQL.

SYNTHETIC adult wellness/edema diary product, no diagnostic/treatment claims.
Same enlarged questionnaire and taxonomy instructions in all five configurations.
Three REAL OpenRouter stages per test (Extract, Generate, Critic); fixture human
review decisions between stages, Builder publication + SRE read-back.
No retry of any billed/uncertain stage. Unknown billing HALTS ALL further tests.
This is NOT a live user approval or a medical recommendation.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from intelligence import migrate
from intelligence.taxonomy_builder.catalog import refresh_model_catalog,list_models
from intelligence.taxonomy_builder.intake import get_intake_view,confirm_intake
from intelligence.taxonomy_builder.pipeline_repository import save_draft,approve_draft
from intelligence.taxonomy_builder.cost_repository import create_quote,authorize_budget
from intelligence.taxonomy_builder.run_repository import (
    start_run,advance_run,get_run,submit_fact_review,publish_reviewed_topics)
from intelligence.taxonomy_builder.provider import request_payload,reservation_cost
from intelligence.taxonomy_builder.cost_estimator import rates
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.relevance.repository import get_approved_context

# The key itself is capped by the owner at $1; retain extra software headroom.
# Historic verified: previous E2E runs $0.045898899 + 12-model matrix $0.045069680708.
PRIOR_CONFIRMED=Decimal("0.090968579708")
PROJECT_CAP=Decimal("0.95")
BATCH_CAP=Decimal("0.80")
OUT=Path("taxonomy_edema_five_e2e_results.json")
ARGON="google/gemini-4-argon"
MIMO="xiaomi/mimo-v2.6-flash"
OPUS="anthropic/claude-opus-5.5"
CASES=(
    ("01_argon_all",ARGON,ARGON,ARGON),
    ("02_mimo_all",MIMO,MIMO,MIMO),
    ("03_opus_all",OPUS,OPUS,OPUS),
    ("04_mimo_argon",MIMO,ARGON,ARGON),
    ("05_mimo_opus",MIMO,OPUS,OPUS),
)
# Entire profile is deliberately fictional; its claims are test fixture only.
PROFILE={
  "name":"Синтетическое приложение — Дневник отёчности и повседневных привычек",
  "niche":"Health & Fitness",
  "product_type":"mobile_app",
  "goal":(
    "Исследовать короткие видео для взрослой аудитории о субъективной отёчности, "
    "ощущении тяжести, бытовых привычках и достоверной медицинской грамотности. "
    "Сформировать полезные различимые поисковые направления с потенциалом интереса "
    "зрителей без обещаний лечить заболевания, без опасных советов и без рекламы "
    "несуществующих возможностей продукта. Расширить исследование на смежные темы, "
    "не подменяя медицинскую консультацию рекомендациями блогеров."
  ),
  "product_functions":(
    "Вымышленное мобильное приложение позволяет взрослому человеку вести дневник "
    "субъективного ощущения отёчности лица, кистей, стоп и голеней по утрам и вечерам. "
    "Пользователь самостоятельно отмечает участок тела, время суток, приблизительную "
    "выраженность ощущения по шкале от одного до пяти и произвольный комментарий. "
    "В приложении можно фиксировать, когда ощущалась тяжесть после длительного сидения "
    "или стояния, и отмечать длительность малоподвижного периода со слов пользователя. "
    "Приложение предоставляет отдельные поля для учёта примерной продолжительности "
    "сна, времени отхода ко сну и субъективного качества пробуждения. "
    "Пользователь может внести приблизительную продолжительность пешей прогулки "
    "или другой лёгкой повседневной активности, без расчёта медицинских показателей. "
    "В нём есть необязательная запись о времени приёма пищи и личной оценке "
    "солёности блюда; приложение не знает точное содержание натрия в пище. "
    "Человек может вручную отмечать количество выпитых напитков, но приложение "
    "не назначает питьевой режим и не рассчитывает лечебную дозу воды. "
    "Оно позволяет добавлять произвольные теги: поездка, долгий перелёт, "
    "работа за компьютером, поздний ужин, жара, изменение режима дня. "
    "На экране истории отображаются последовательные пользовательские записи "
    "и простые графики частоты субъективных ощущений за семь и тридцать дней. "
    "Внутри можно создавать напоминание заполнить дневник утром или вечером; "
    "уведомления можно отключить без потери записей. "
    "Для личного использования доступны экспорт текстового дневника и "
    "его ручная передача врачу на приёме. "
    "Приложение не ставит диагноз, не измеряет объективный объём отёка, "
    "не распознаёт болезни по фотографии, не анализирует лабораторные результаты, "
    "не назначает мочегонные, массаж, компрессию, БАДы или диету. "
    "Оно не обещает убрать отёки, вылечить причину задержки жидкости "
    "или заменить медицинскую помощь."
  ),
  "audience_problems":(
    "Некоторые взрослые замечают по утрам субъективную припухлость лица, "
    "но не могут вспомнить, когда это впервые случилось и как часто повторялось. "
    "Другие ощущают тяжесть в ногах после работы за столом, но не имеют "
    "понятного дневника для фиксации времени и сопутствующих обстоятельств. "
    "Люди путают временное субъективное ощущение тяжести с клиническим отёком "
    "и пытаются делать самостоятельные выводы по советам блогеров. "
    "Некоторым неловко рассказывать врачу о нерегулярных симптомах: "
    "они забывают даты, время суток и контекст появления ощущений. "
    "Есть аудитория, которая связывает ощущения только с солёной едой, "
    "хотя такая связь не установлена для конкретного человека. "
    "Зрители часто спрашивают о влиянии сна, длительного сидения, перелётов, "
    "жары, поездок и повседневной подвижности, но смешивают наблюдение "
    "за своими привычками с доказательством причинного механизма. "
    "У некоторых возникают противоречивые ожидания от питьевого режима, "
    "самостоятельных ограничений соли, массажных техник и домашних средств. "
    "Людям сложно отличить осторожный образовательный разбор от рекламы "
    "чудо-средства, обещающего быстро убрать отёчность. "
    "Часть аудитории тревожится из-за нового или одностороннего отёка, "
    "боли, одышки и других тревожных признаков: этим вопросам нужна "
    "своевременная профессиональная медицинская оценка, а не контент-лайфхак. "
    "Люди хотят не диагноз от приложения, а более точный способ записывать "
    "свои наблюдения, описывать время и обстоятельства, а затем обсуждать "
    "их с медицинским специалистом. "
    "Им трудно поддерживать регулярное ведение заметок, не превращая "
    "это в навязчивый мониторинг тела или обещание идеального самочувствия."
  ),
  "product_description":(
    "Это полностью вымышленное приложение для образовательного исследования "
    "контента в тематике самонаблюдения за субъективной отёчностью. "
    "Оно предназначено для взрослых пользователей, которые желают вести "
    "структурированные записи о самочувствии и повседневных привычках. "
    "Его ценность — не лечение отёков, а хронология личных записей, "
    "напоминания, простые визуализации и возможность показать дневник врачу. "
    "Контент должен различать факты о функциях приложения, трудности аудитории "
    "и соседние образовательные темы, не приписывая продукту чужих функций. "
    "Подходящие исследовательские направления: примеры полезных дневников, "
    "различия между субъективной припухлостью и требующими оценки симптомами, "
    "популярные заблуждения, бытовые наблюдения, безопасность советов "
    "о соли и воде, организация вопросов перед приёмом у специалиста, "
    "разбор маркетинговых обещаний и критическое восприятие вирусных роликов. "
    "Любые примеры возможных TikTok или Reels — исключительно иллюстрации "
    "форматов, а не реально найденные или проверенные видео. "
    "Нельзя обещать уменьшить отёчность за одну ночь, предлагать "
    "лекарства, мочегонные, ограничение жидкости, исключающие диеты, "
    "манипуляции с телом или обход медицинской помощи. "
    "При внезапных, болезненных, односторонних отёках или сочетании "
    "с одышкой образовательный контент должен направлять за медицинской "
    "оценкой, а не к самостоятельному лечению."
  ),
  "competitors":"Вымышленные дневники самочувствия и трекеры повседневных привычек; реальных конкурентов не исследовали.",
  "video_references":"Гипотетические ролики: объяснение ведения дневника; ошибки вирусных советов; организация вопросов врачу; как записать контекст ощущения после перелёта."
}

def save(report):
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")

def limited_stage(role,model,limit):
    efforts=model["capabilities"].get("reasoning_efforts",[])
    effort="high" if "high" in efforts and model["model_id"] in (ARGON,OPUS) else None
    return {
        "role":role,"enabled":True,"model_id":model["model_id"],
        "catalog_version_id":model["catalog_version_id"],
        "reasoning":({"mode":"effort","effort":effort} if effort else {"mode":"default"}),
        "temperature":None,"max_output_tokens":limit,
        "prompt_version":{"fact_extraction":"facts-v1",
                          "taxonomy_generation":"taxonomy-v2",
                          "taxonomy_critic":"critic-v2"}[role]
    }

def snapshot(m):
    return {key:m[key] for key in ("model_id","catalog_version_id",
      "capabilities","pricing","verified_at")}

def worst_inputs():
    """Realistic upper-bound of subsequent JSON-heavy stages, not a 1-fact proxy."""
    facts=[]
    for i in range(22):
        kind="function" if i%2==0 else "problem"
        facts.append({
          "id":f"00000000-0000-4000-8000-{i+1:012d}","kind":kind,
          "text":("Пользователь может вести дневник субъективных ощущений и "
                  "уточнять подробности по времени суток, привычкам и "
                  "контексту личных записей." if kind=="function" else
                  "Люди испытывают сложности при фиксации повторяющихся "
                  "ощущений, причинных гипотез и обстоятельств повседневной жизни."),
          "source_field":"product_functions" if kind=="function" else "audience_problems",
          "source_quote":"Это только техническая оценка объёма данных, не источник.",
          "origin":"source_verified","review_status":"approved"
        })
    topics=[{
      "name":"Образовательное направление "+str(i),
      "relation":("direct","problem_related","adjacent")[i%3],
      "definition":"Наблюдение за признаками и обстоятельствами, не медицинская диагностика.",
      "include":"Видео с осторожным разбором наблюдения и ведения личного дневника.",
      "exclude":"Видео с обещанием лечить или убирать отёки без медицинской оценки.",
      "positive_examples":["Иллюстрация записи в дневник ощущений по утрам.",
                           "Как грамотно сформулировать вопрос врачу."],
      "negative_examples":["Уберите отёки за час с помощью чудо-таблетки."],
      "fact_ids":[facts[i]["id"]],"origin":"inferred"
    } for i in range(12)]
    return {
      "fact_extraction":{"questionnaire":PROFILE,"niche":"health_fitness",
                         "research_goal":"maximize_views","attachments":"excluded"},
      "taxonomy_generation":{"approved_facts":facts,"niche":"health_fitness",
         "research_goal":"maximize_views",
         "request":"Generate 8-12 distinct grounded Health & Fitness video-research "
                   "directions across direct, problem_related and adjacent. "
                   "Never assert medical diagnosis or guaranteed edema removal."},
      "taxonomy_critic":{"approved_facts":facts,"topics":topics,"review_only":True},
    }

def pick_config(case,models,available):
    """Choose ample generation tokens while respecting actual 3-stage reserves."""
    _,extract_id,gen_id,critic_id=case
    chosen={mid:models.get(mid) for mid in (extract_id,gen_id,critic_id)}
    if any(not m or not m["selectable"] for m in chosen.values()):
        return None,"model_catalog_unavailable"
    for m in chosen.values():
        try: rates(m["pricing"])
        except PipelineError: return None,"unsupported_pricing"
    fake=worst_inputs()
    # Prioritise Generation payload; high reasoning output budget is TOTAL tokens.
    # No model substitution; lowering token ceiling is the only fallback.
    for out in (11000,9500,8500,7500,6500):
        for ext in (5200,4200,3400):
            for critique in (3400,2800,2200):
                lims=(ext,out,critique)
                roles=("fact_extraction","taxonomy_generation","taxonomy_critic")
                stages=[limited_stage(role,chosen[mid],lim)
                        for role,mid,lim in zip(roles,(extract_id,gen_id,critic_id),lims)]
                snaps={role:snapshot(chosen[mid])
                       for role,mid in zip(roles,(extract_id,gen_id,critic_id))}
                try:
                    reserved=[reservation_cost(request_payload(st,snaps[st["role"]],
                                 fake[st["role"]]),snaps[st["role"]]) for st in stages]
                except PipelineError:
                    continue
                # Allow headroom for dynamic fact/topic count and imperfect token bounds.
                if sum(reserved)*Decimal("1.08") <= available:
                    return (stages,snaps,reserved),None
    return None,"conservative_preflight_exceeds_remaining_key_budget"

def assert_sre_equal(source,sre):
    cats=sre["categories"]
    if len(cats)!=len(source):
        raise RuntimeError("SRE category count mismatch")
    bykey={str(c["category_key"]):c for c in cats}
    if len(bykey)!=len(cats):
        raise RuntimeError("Duplicate SRE category keys")
    for t in source:
        c=bykey.get(str(t["id"]))
        if c is None:
            raise RuntimeError("SRE missing Builder category")
        for src,dst in (("name","name"),("relation","relation_type"),
                        ("definition","definition"),("origin","origin")):
            if str(t[src])!=str(c[dst]):
                raise RuntimeError("Builder/SRE "+src+" mismatch")
        if set(map(str,t["fact_ids"]))!=set(map(str,c["related_fact_ids"])):
            raise RuntimeError("SRE fact links mismatch")
        rules=c["rules"]
        if rules["include"]!=[t["include"]] or rules["exclude"]!=[t["exclude"]]:
            raise RuntimeError("SRE include/exclude mismatch")
        if rules["positive_examples"]!=t["positive_examples"] or rules["negative_examples"]!=t["negative_examples"]:
            raise RuntimeError("SRE illustrative examples mismatch")

def main():
    if os.environ.get("RUN_FIVE_EDEMA_E2E_ONCE")!="YES_ONE_EDEMA_MATRIX_20261020":
        raise SystemExit("No exact owner opt-in; no paid calls")
    key=os.environ.get("OPENROUTER_API_KEY")
    if not key:raise SystemExit("No server-side OpenRouter key; no paid calls")
    if not os.environ.get("CM_DB_DSN") or not os.environ.get("CM_TEST_PG_DSN"):
        raise SystemExit("No disposable PostgreSQL; no paid calls")
    if PROJECT_CAP>=Decimal("1") or PRIOR_CONFIRMED+BATCH_CAP>=PROJECT_CAP:
        raise SystemExit("Improper aggregate budget safety guard; no paid calls")
    report={
      "kind":"five REAL full Taxonomy Builder stages + fixture reviews + SRE",
      "fixture":"fictional edema wellness diary, not advice/medical efficacy",
      "profile_characters":sum(len(v) for v in PROFILE.values()),
      "prior_verified_usd":str(PRIOR_CONFIRMED),
      "software_total_ceiling_usd":str(PROJECT_CAP),
      "new_batch_ceiling_usd":str(BATCH_CAP),
      "new_known_spent_usd":"0","tests":[],
      "finished":False,"unknown_billing_stop":False
    }
    save(report)
    import psycopg,projects
    with tempfile.TemporaryDirectory() as td:
        os.environ["CM_PROJECTS_DIR"]=td
        os.environ["CM_DB_ENABLED"]="1"
        with psycopg.connect(os.environ["CM_DB_DSN"],autocommit=True) as conn:
            migrate.apply_migrations(conn)
        # Free catalog refresh once, snapshots are pinned per pipeline version.
        with psycopg.connect(os.environ["CM_DB_DSN"]) as conn:
            refresh_model_catalog(conn)
            models={m["model_id"]:m for m in list_models(conn)}
        spent=Decimal(0)
        for index,case in enumerate(CASES):
            name,ex,gen,critic=case
            record={
              "name":name,"models_by_stage":{
                "fact_extraction":ex,"taxonomy_generation":gen,"taxonomy_critic":critic},
              "stages":[],"status":"preflight","run_success":False,
              "human_review":"synthetic fixture automatic approval of source-verified drafts only"
            }
            report["tests"].append(record)
            save(report)
            available=min(BATCH_CAP-spent,PROJECT_CAP-PRIOR_CONFIRMED-spent)
            selected,error=pick_config(case,models,available)
            if not selected:
                record.update(status="skipped_before_charge",reason=error)
                save(report);continue
            stages,snapshots,reserves=selected
            record["limits"]={s["role"]:s["max_output_tokens"] for s in stages}
            record["reasoning"]={s["role"]:s["reasoning"] for s in stages}
            record["worst_case_reservations_usd"]=[str(x) for x in reserves]
            # Reserve a realistic per-case amount while not pretending all cases
            # need to reserve their worst-case caps simultaneously.
            run_allowance=available
            try:
                p=projects.create_project({**PROFILE,"name":PROFILE["name"]+" "+name})
                pid=p["id"]
                profile=get_intake_view(pid)
                if not profile["can_confirm"]:
                    raise RuntimeError("Invalid synthetic onboarding")
                confirm_intake(pid,profile["profile_revision"],profile["profile_hash"])
                config={"schema_version":1,"stages":stages}
                draft=save_draft(pid,config,0)
                approved=approve_draft(pid,draft["draft_revision"])
                quote=create_quote(pid,approved["pipeline_id"])["quote"]
                record["quote_high_usd"]=quote["high_usd"]
                if Decimal(quote["high_usd"])>run_allowance:
                    raise RuntimeError("High quote exceeds available limit; no paid calls")
                consent=authorize_budget(pid,quote["quote_id"],str(run_allowance))["consent"]
                started=start_run(pid,quote["quote_id"],consent["consent_id"],
                                  "edema-real-fixture-"+name)
                rid=started["run_id"]
                record["run_started"]=True
                save(report)
                def call(role,expected,after_expected):
                    nonlocal spent
                    before=get_run(pid,rid)
                    if before["status"]!=expected:
                        raise RuntimeError("Wrong state before real "+role)
                    outcome=advance_run(pid,rid,api_key=key)
                    after=get_run(pid,rid)
                    charged=Decimal(str(outcome.get("charged_usd","0")))
                    spent+=charged
                    record["stages"].append({
                        "role":role,"model":record["models_by_stage"][role],
                        "status":after["status"],"provider_charge_usd":str(charged),
                        "response_id":outcome.get("provider_response_id"),
                        "usage":after["stages"][-1].get("usage") if after["stages"] else None,
                        "validation_error":after["stages"][-1].get("error") if after["stages"] else None
                    })
                    report["new_known_spent_usd"]=str(spent)
                    save(report)
                    if outcome["status"]=="uncertain":
                        report["unknown_billing_stop"]=True
                        raise RuntimeError("UNKNOWN_BILLING_STOP: no retries, halt all five")
                    if spent>BATCH_CAP or PRIOR_CONFIRMED+spent>PROJECT_CAP:
                        report["unknown_billing_stop"]=True
                        raise RuntimeError("GLOBAL_BUDGET_STOP: reported provider charge exceeds ceiling")
                    if outcome["status"]!=after_expected:
                        raise RuntimeError(role+" returned "+str(outcome["status"])+
                                           ": "+str(outcome.get("message",""))[:160])
                    return after
                a=call("fact_extraction","extraction_pending","fact_review")
                facts=a["fact_proposals"]
                record["valid_facts"]=len(facts)
                record["excluded_facts"]=len(a.get("excluded_fact_proposals") or [])
                if len(facts)<4:raise RuntimeError("Too few grounded fact proposals")
                submit_fact_review(pid,rid,[{"id":f["id"],"action":"approve"} for f in facts])
                a=call("taxonomy_generation","generation_pending","critic_pending")
                topics=a["topic_proposals"]
                record["valid_topics"]=len(topics)
                record["excluded_topics"]=len(a.get("excluded_topic_proposals") or [])
                record["topic_relations"]=sorted({t["relation"] for t in topics})
                # Minimum quality is deliberately modest: plumbing first; report
                # model shortcomings transparently, never invent topic content.
                if len(topics)<2:
                    raise RuntimeError("Fewer than two grounded topics")
                a=call("taxonomy_critic","critic_pending","topic_review")
                proposal=a["topic_proposals"]
                publication=publish_reviewed_topics(
                  pid,rid,[{"id":t["id"],"action":"approve"} for t in proposal])
                with psycopg.connect(os.environ["CM_DB_DSN"]) as conn:
                    sre=get_approved_context(conn,pid)
                    if str(sre["taxonomy_version_id"])!=str(publication["sre_taxonomy_version_id"]):
                        raise RuntimeError("SRE publication ID mismatch")
                    assert_sre_equal(publication["topics"],sre)
                final=get_run(pid,rid)
                if final["status"]!="approved" or Decimal(final["reserved_usd"])!=0:
                    raise RuntimeError("Final state not approved or reservation unreleased")
                record["run_success"]=True
                record["status"]="success"
                record["final_run_usd"]=final["spent_usd"]
                record["sre_categories"]=len(sre["categories"])
                record["sre_taxonomy_version_id"]=str(sre["taxonomy_version_id"])
                # Synthetic sample is safe to show owner in full, for comparisons.
                record["categories"]=[{
                  "name":t["name"],"relation":t["relation"],"definition":t["definition"],
                  "include":t["include"],"exclude":t["exclude"],
                  "positive_examples":t["positive_examples"],
                  "negative_examples":t["negative_examples"],
                } for t in publication["topics"]]
                save(report)
            except Exception as exc:
                record["status"]="stopped"
                record["reason"]=str(exc)[:350]
                save(report)
                if report["unknown_billing_stop"]:
                    break
                # Known failed output is billed once; continue to the next
                # unique scenario only if ledger guards still allow it.
        report["finished"]=True
        report["known_total_usd"]=str(PRIOR_CONFIRMED+spent)
        save(report)
        print("EDMA-5 E2E cases:",[(x["name"],x["status"]) for x in report["tests"]])
        print("Known new OpenRouter USD:",spent)
        if report["unknown_billing_stop"]:
            raise SystemExit("Stopped on uncertain billing or cost overage; no retry")

if __name__=="__main__":
    main()
