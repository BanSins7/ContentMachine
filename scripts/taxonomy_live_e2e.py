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
PRIOR_CONFIRMED_SPEND=Decimal("0.000048472")
THIS_RUN_CAP=Decimal("0.10")
OUTFILE=Path("taxonomy_live_e2e_result.json")
PROFILE={
  "name":"Synthetic Fitness Nutrition Coach E2E",
  "niche":"Health & Fitness",
  "product_type":"mobile_app",
  "product_functions":"Приложение считает калории по фото еды и сохраняет дневник питания",
  "audience_problems":"Людям сложно вручную считать калории и контролировать размер порций",
  "product_description":"Тестовый продукт без реальных пользователей или личных данных"
}
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
        if not valid:raise SystemExit("No verified eligible models — no charged requests")
        # Explicitly select the already smoke-tested model where available,
        # otherwise the least-priced compatible candidate. Never invented IDs.
        ranked=sorted(valid,key=lambda m:(
            0 if m["model_id"]=="deepseek/deepseek-v4.1-flash" else 1,
            rates(m["pricing"])["prompt"]*1000+rates(m["pricing"])["completion"]*2048))
        chosen=None
        for model in ranked:
            stages=[{"role":role,"enabled":True,"model_id":model["model_id"],
                "catalog_version_id":model["catalog_version_id"],
                "reasoning":{"mode":"default"},"temperature":None,
                "max_output_tokens":n,"prompt_version":prompt}
                for role,n,prompt in [
                    ("fact_extraction",1800,"facts-v1"),
                    ("taxonomy_generation",3600,"taxonomy-v1"),
                    ("taxonomy_critic",1400,"critic-v1")]]
            snapshot={"capabilities":model["capabilities"],"pricing":model["pricing"]}
            try:
                reserves=[reservation_cost(request_payload(stage,snapshot,{
                   "questionnaire":PROFILE,"approved_facts":[
                     {"id":"00000000-0000-0000-0000-000000000001",
                      "kind":"function","text":"Sample confirmed fact"}],
                   "topics":[{"name":"test category","relation":"direct"}]
                   }),snapshot) for stage in stages]
                if sum(reserves) <= THIS_RUN_CAP:
                    chosen=(model,stages,reserves);break
            except PipelineError:continue
        if chosen is None:raise SystemExit("No three-stage model combination fits $0.10 run reservation — zero charged requests")
        model,stages,reserves=chosen
        outcome["model_id"]=model["model_id"]
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
                "provider_response_id":actual.get("provider_response_id")})
            write(outcome)
            if actual["status"] not in ("fact_review","critic_pending","topic_review"):
                raise RuntimeError("Live stage failed/uncertain. Stop; DO NOT retry")
            if Decimal(after["spent_usd"])+PRIOR_CONFIRMED_SPEND>TOTAL_LIMIT:
                raise RuntimeError("Aggregate charged amount exceeds user authorization; STOP")
            return after
        extracted=one_paid_stage("extraction_pending","fact_extraction")
        facts=extracted["fact_proposals"]
        if not facts:raise RuntimeError("No source-verified facts")
        # This is a synthetic test owner's predetermined fixture review.
        # No production content is approved on the user's behalf.
        choices=[{"id":f["id"],"action":"approve"} for f in facts]
        submit_fact_review(pid,run_id,choices)
        generated=one_paid_stage("generation_pending","taxonomy_generation")
        if generated["status"]!="critic_pending":
            raise RuntimeError("Critic gate not reached; stop without additional charge")
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
              "model",model["model_id"],"cost USD",finish["spent_usd"],
              "SRE categories",len(sre["categories"]))
if __name__=="__main__":
    try:main()
    except Exception as err:
        print("LIVE E2E STOPPED (no automatic retry):",type(err).__name__,str(err)[:300])
        sys.exit(1)
