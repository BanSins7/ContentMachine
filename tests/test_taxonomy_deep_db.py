"""Real PostgreSQL two-pass Opus flow with fake provider. ZERO paid requests."""
import os,sys,json,tempfile,unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import projects
from intelligence import migrate
from intelligence.taxonomy_builder.catalog import refresh_model_catalog,list_models
from intelligence.taxonomy_builder.intake import get_intake_view,confirm_intake
from intelligence.taxonomy_builder.pipeline_repository import save_draft,approve_draft
from intelligence.taxonomy_builder.cost_repository import create_quote,authorize_budget
from intelligence.taxonomy_builder.run_repository import (
    start_run,advance_run,submit_fact_review,get_run)
from intelligence.taxonomy_builder.deep_repository import (
    get_deep,start_deep,advance_deep,first_review,final_review)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from test_taxonomy_builder_pipeline import provider_model,config,PRIMARY
from test_taxonomy_builder_run_db import PROFILE,reply
OPUS="anthropic/claude-opus-5.5"

def item(ident,name,kind,parent,relation,fact_ids):
 return {"id":ident,"name":name,"kind":kind,"parent_id":parent,"relation":relation,
         "source_fact_ids":fact_ids,
         "rationale":"Отражает утверждённый факт анкеты или независимую смежную тему",
         "definition":"Самостоятельная категория для исследования коротких роликов",
         "include":"Релевантные примеры и самостоятельные видео",
         "exclude":"Обманчивые лечебные обещания",
         "examples":["Показываем реалистичный ритуал"]}
def make_tree(facts,extra=False):
 a,b=facts[0]["id"],facts[1]["id"]
 nodes=[
    item("food","Учёт питания по фото","direction",None,"direct",[a]),
    item("problem","Ошибки отслеживания калорий","direction",None,"problem_related",[b]),
    item("howto","Как пользоваться фотоучётом","branch","food","direct",[a]),
    item("hypothesis","Проверка понятности фотоучёта","hypothesis","howto","direct",[a]),
    item("problem_idea","Как люди справляются с трудным подсчётом","hypothesis","problem","problem_related",[b])]
 if extra:
  nodes.append(item("adjacent","Ежедневные healthy habits",
                    "direction",None,"adjacent",[]))
  nodes.append(item("adjacent_idea","Какие healthy habits интересуют зрителей",
                    "hypothesis","adjacent","adjacent",[]))
 return nodes

@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"Disposable PostgreSQL required")
class DeepDB(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  import psycopg
  cls.dsn=os.environ["CM_TEST_PG_DSN"]
  with psycopg.connect(cls.dsn,autocommit=True) as conn:migrate.apply_migrations(conn)
 def setUp(self):
  import psycopg
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
  p=mock.patch.dict(os.environ,{"CM_PROJECTS_DIR":temp.name,
      "CM_DB_ENABLED":"1","CM_DB_DSN":self.dsn})
  p.start();self.addCleanup(p.stop)
  self.project=projects.create_project(PROFILE)
  other=projects.create_project(PROFILE)
  self.other=other
  for project in (self.project,other):
   v=get_intake_view(project["id"])
   confirm_intake(project["id"],v["profile_revision"],v["profile_hash"])
  with psycopg.connect(self.dsn) as conn:
   opus=provider_model(OPUS,temperature=True,output=16000)
   # The generic model fixture has only a 32k context. Real long-context
   # Opus models support a much larger prompt, needed for 2-pass trees.
   opus["context_length"]=262144
   refresh_model_catalog(conn,fetcher=lambda:[provider_model(PRIMARY),opus])
   entries={x["model_id"]:x for x in list_models(conn)}
  cfg=config(entries[PRIMARY]["catalog_version_id"],entries[OPUS]["catalog_version_id"])
  cfg["stages"][1]["model_id"]=OPUS
  cfg["stages"][1]["max_output_tokens"]=8000
  save_draft(self.project["id"],cfg,0)
  approved=approve_draft(self.project["id"],1)
  quote=create_quote(self.project["id"],approved["pipeline_id"])["quote"]
  consent=authorize_budget(self.project["id"],quote["quote_id"],"0.90")["consent"]
  self.rid=start_run(self.project["id"],quote["quote_id"],consent["consent_id"],
                     "deep-mode-test-idempotency-00001")["run_id"]
  self.facts=None;self.sent=[]
 def extraction(self,body):
  self.sent.append("fact_extraction")
  result=reply("fact_extraction",{},cost=0.001)
  result["model"]=body["model"]
  return result
 def sender(self,body):
  name=body["response_format"]["json_schema"]["name"]
  inputs=json.loads(body["messages"][1]["content"])
  if name.endswith("deep_first"):
   self.sent.append("first")
   obj={"nodes":make_tree(inputs["approved_facts"])}
  elif name.endswith("deep_second"):
   self.sent.append("second")
   obj={"nodes":make_tree(inputs["approved_facts"],True),"duplicates":[]}
  elif name.endswith("deep_expand"):
   self.sent.append("expansion")
   obj={"nodes":make_tree(inputs["approved_facts"],True),"duplicates":[]}
  else:raise AssertionError(name)
  return {"id":"mock-"+name,"model":body["model"],
    "choices":[{"finish_reason":"stop","message":{"content":json.dumps(obj,ensure_ascii=False)}}],
    "usage":{"cost":"0.0020","prompt_tokens":600,"completion_tokens":500,
             "completion_tokens_details":{"reasoning_tokens":100}}}
 def prepare(self):
  pid=self.project["id"]
  self.assertEqual(get_deep(pid,self.rid)["mode"],"standard")
  self.assertEqual(get_deep(pid,self.rid)["eligible"],False)
  self.assertEqual(advance_run(pid,self.rid,provider_call=self.extraction)["status"],"fact_review")
  facts=get_run(pid,self.rid)["fact_proposals"]
  submit_fact_review(pid,self.rid,[{"id":f["id"],"action":"approve"} for f in facts])
  self.facts=get_run(pid,self.rid)["approved_facts"]
  self.assertEqual(get_deep(pid,self.rid)["eligible"],True)
  self.assertEqual(start_deep(pid,self.rid)["paid_calls"],0)
  with self.assertRaisesRegex(PipelineError,"Hierarchical"):
   advance_run(pid,self.rid,provider_call=self.extraction)
 def test_complete_two_human_reviews_saturation_and_atomic_sre_tree(self):
  import psycopg
  pid=self.project["id"];self.prepare()
  self.assertEqual(self.sent,["fact_extraction"])
  self.assertEqual(advance_deep(pid,self.rid,sender=self.sender)["status"],"review1")
  first=get_deep(pid,self.rid)["nodes"]
  self.assertEqual(len(first),5)
  with self.assertRaises(PipelineError):
   first_review(pid,self.rid,[{"id":first[0]["id"],"action":"approve"}])
  choices=[{"id":n["id"],"action":"approve"} for n in first]
  self.assertEqual(first_review(pid,self.rid,choices)["status"],"pass2_pending")
  self.assertEqual(advance_deep(pid,self.rid,sender=self.sender)["status"],"expand_pending")
  q=get_deep(pid,self.rid)["quality"]
  self.assertEqual(q["new_direction_count"],1)
  self.assertEqual(q["severe_gap_count"],0)
  self.assertEqual(advance_deep(pid,self.rid,sender=self.sender)["status"],"review2")
  state=get_deep(pid,self.rid)
  self.assertTrue(state["quality"]["passed"])
  self.assertEqual(state["expansion_count"],1)
  final=[{"id":n["id"],"action":"approve"} for n in state["nodes"]]
  result=final_review(pid,self.rid,final)
  self.assertEqual(result["status"],"approved")
  self.assertEqual(result["sre_category_count"],7)
  self.assertEqual(get_run(pid,self.rid)["status"],"approved")
  # Search Planner v1 reads the same immutable, human-approved publication
  # through a fresh read-only DB transaction; no paid provider calls.
  from intelligence.search_planner import (
      load_approved_publication, build_hypothesis_contexts)
  with psycopg.connect(self.dsn) as conn:
   pub=str(conn.execute("""SELECT builder_publication_id
          FROM tb_deep_publications WHERE project_id=%s AND run_id=%s""",
          (pid,self.rid)).fetchone()[0])
  snap=load_approved_publication(pid,pub)
  contexts=build_hypothesis_contexts(snap,{
      "languages":["ru","en"],"markets":[],"allowed_modes":["keyword","hashtag"],
      "target_candidates_per_hypothesis":10,
      "max_candidates_per_hypothesis":16,"max_total_candidates":480})
  self.assertEqual(len(contexts),3)
  self.assertEqual({c["hypothesis"]["id"] for c in contexts},
                   {"hypothesis","problem_idea","adjacent_idea"})
  self.assertEqual({c["context_schema_version"] for c in contexts},
                   {"hypothesis-context/v1"})
  self.assertTrue(all(c["taxonomy_publication_id"]==pub for c in contexts))
  self.assertTrue(any(c["branch"] is None for c in contexts))
  self.assertTrue(any(c["branch"] is not None for c in contexts))
  with self.assertRaises(PipelineError):
   load_approved_publication(self.other["id"],pub)
  # Engine-A: pin a verified catalog snapshot, persist a zero-cost
  # generation + immutable contexts + deterministic pending batches.
  from intelligence.search_planner.generation_engine import (
      register_config_version, create_planned_generation, get_planned_generation)
  from test_search_planner_engine_contracts import config as generation_config
  with psycopg.connect(self.dsn) as conn:
   snaprow=conn.execute("""SELECT id,catalog_sha256
     FROM tb_model_snapshots WHERE model_id=%s
     ORDER BY id DESC LIMIT 1""",(OPUS,)).fetchone()
  gen_config=generation_config()
  gen_config["model"]["catalog_snapshot_id"]=snaprow[0]
  gen_config["model"]["model_catalog_sha256"]=snaprow[1]
  saved=register_config_version(pid,gen_config)
  with self.assertRaises(PipelineError):
   create_planned_generation(self.other["id"],pub,saved["config_id"],
                             "sqg-other-project-attempt-000001")
  planned=create_planned_generation(pid,pub,saved["config_id"],
                                     "sqg-planned-generation-000001")
  self.assertEqual(planned["status"],"awaiting_authorization")
  self.assertEqual(planned["hypothesis_count"],3)
  self.assertEqual(planned["batch_count"],3)
  self.assertEqual(planned["paid_calls"],0)
  reused=create_planned_generation(pid,pub,saved["config_id"],
                                    "sqg-planned-generation-000001")
  self.assertEqual(reused["generation_id"],planned["generation_id"])
  self.assertTrue(reused["reused"])
  summary=get_planned_generation(pid,planned["generation_id"])
  self.assertEqual(summary["batch_count"],3)
  with psycopg.connect(self.dsn) as conn:
   self.assertEqual(conn.execute("SELECT count(*) FROM sqg_contexts WHERE generation_id=%s",
      (planned["generation_id"],)).fetchone()[0],3)
   self.assertEqual(conn.execute("SELECT count(*) FROM sqg_batches WHERE generation_id=%s",
      (planned["generation_id"],)).fetchone()[0],3)
   self.assertEqual(conn.execute("SELECT count(*) FROM sqg_attempts WHERE generation_id=%s",
      (planned["generation_id"],)).fetchone()[0],0)
   self.assertEqual(conn.execute("SELECT count(*) FROM sqg_consents WHERE project_id=%s",
      (pid,)).fetchone()[0],0)
   self.assertEqual(conn.execute("SELECT count(*) FROM sqg_outbox WHERE generation_id=%s",
      (planned["generation_id"],)).fetchone()[0],0)
  # Stage B: explicit search budget, never inherited from RTB.
  from decimal import Decimal
  from concurrent.futures import ThreadPoolExecutor
  from intelligence.search_planner.generation_engine.budget_manager import (
      create_generation_quote, authorize_generation_budget,
      revoke_generation_budget, get_generation_budget)
  from intelligence.search_planner.generation_engine.repository import (
      reserve_batch, record_dispatch_intent, settle_known_failure,
      mark_uncertain_attempt, release_unsubmitted_attempt)
  quote=create_generation_quote(pid,planned["generation_id"])
  self.assertEqual(quote["batch_count"],3)
  self.assertTrue(Decimal(quote["high_usd"])>0)
  self.assertEqual(create_generation_quote(pid,planned["generation_id"])["quote_id"],
                   quote["quote_id"])
  with self.assertRaises(PipelineError):
   authorize_generation_budget(self.other["id"],planned["generation_id"],
                               quote["quote_id"],"10.00")
  with self.assertRaises(PipelineError):
   authorize_generation_budget(pid,planned["generation_id"],quote["quote_id"],"0.00000001")
  consent=authorize_generation_budget(pid,planned["generation_id"],quote["quote_id"],"10.00")
  self.assertEqual(consent["status"],"queued")
  self.assertEqual(Decimal(get_generation_budget(pid,planned["generation_id"])
                            ["consent"]["spent_usd"]),Decimal("0"))
  self.assertTrue(authorize_generation_budget(pid,planned["generation_id"],
                quote["quote_id"],"10.00")["reused"])
  with psycopg.connect(self.dsn) as conn:
   batchids=[str(r[0]) for r in conn.execute("""SELECT id FROM sqg_batches
              WHERE generation_id=%s ORDER BY batch_index""",
              (planned["generation_id"],)).fetchall()]
  def concurrently_reserve(_):
   try:
    return reserve_batch(pid,planned["generation_id"],batchids[0],"f"*64)
   except PipelineError as exc:
    return {"error":str(exc)}
  with ThreadPoolExecutor(max_workers=2) as pool:
   outcomes=list(pool.map(concurrently_reserve,[0,1]))
  self.assertEqual(sum(bool(o.get("attempt_id")) for o in outcomes),1)
  first=next(o for o in outcomes if o.get("attempt_id"))
  with self.assertRaises(PipelineError):
   reserve_batch(self.other["id"],planned["generation_id"],batchids[0],"f"*64)
  self.assertTrue(Decimal(get_generation_budget(pid,planned["generation_id"])
                         ["consent"]["reserved_usd"])>0)
  record_dispatch_intent(pid,first["attempt_id"])
  self.assertEqual(mark_uncertain_attempt(pid,first["attempt_id"])["status"],"uncertain")
  self.assertTrue(mark_uncertain_attempt(pid,first["attempt_id"])["reused"])
  with self.assertRaises(PipelineError):
   settle_known_failure(pid,first["attempt_id"],"0")
  with self.assertRaises(PipelineError):
   reserve_batch(pid,planned["generation_id"],batchids[0],"f"*64)
  self.assertEqual(revoke_generation_budget(pid,consent["consent_id"])["revoked"],True)
  self.assertTrue(get_generation_budget(pid,planned["generation_id"])["consent"]["revoked"])
  # A second explicitly authorized run: known charge, including over-reserve.
  another=create_planned_generation(pid,pub,saved["config_id"],
                                      "sqg-planned-generation-000002")
  another_quote=create_generation_quote(pid,another["generation_id"])
  auth2=authorize_generation_budget(pid,another["generation_id"],
                                   another_quote["quote_id"],"10.00")
  with psycopg.connect(self.dsn) as conn:
   b2=str(conn.execute("SELECT id FROM sqg_batches WHERE generation_id=%s ORDER BY batch_index",
       (another["generation_id"],)).fetchone()[0])
  reserved2=reserve_batch(pid,another["generation_id"],b2,"e"*64)
  self.assertEqual(record_dispatch_intent(pid,reserved2["attempt_id"])["status"],
                   "dispatch_intent")
  charged=str(Decimal(reserved2["reserved_usd"])*2)
  failure=settle_known_failure(pid,reserved2["attempt_id"],charged)
  self.assertEqual(failure["status"],"failed_known")
  self.assertTrue(settle_known_failure(pid,reserved2["attempt_id"],charged)["reused"])
  wallet=get_generation_budget(pid,another["generation_id"])["consent"]
  self.assertEqual(Decimal(wallet["reserved_usd"]),Decimal(0))
  self.assertEqual(Decimal(wallet["spent_usd"]),Decimal(charged))
  with self.assertRaises(PipelineError):
   settle_known_failure(pid,reserved2["attempt_id"],"0.00000001")
  # A third run can release only pre-dispatch reservations.
  final_plan=create_planned_generation(pid,pub,saved["config_id"],
                                        "sqg-planned-generation-000003")
  final_quote=create_generation_quote(pid,final_plan["generation_id"])
  authorize_generation_budget(pid,final_plan["generation_id"],final_quote["quote_id"],"10.00")
  with psycopg.connect(self.dsn) as conn:
   b3=str(conn.execute("SELECT id FROM sqg_batches WHERE generation_id=%s ORDER BY batch_index",
       (final_plan["generation_id"],)).fetchone()[0])
  r3=reserve_batch(pid,final_plan["generation_id"],b3,"d"*64)
  self.assertEqual(release_unsubmitted_attempt(pid,r3["attempt_id"])["status"],
                   "abandoned_before_dispatch")
  self.assertTrue(release_unsubmitted_attempt(pid,r3["attempt_id"])["reused"])
  self.assertEqual(Decimal(get_generation_budget(pid,final_plan["generation_id"])
                           ["consent"]["reserved_usd"]),Decimal(0))
  # Stage C: fake-only complete LLM processing, transactional outbox,
  # strict raw JSON validation, unknown billing and cancellation.
  from intelligence.search_planner.generation_engine.worker import (
      drain_generation,process_one_batch)
  from intelligence.search_planner.generation_engine.provider_port import FakeProviderPort
  from intelligence.search_planner.generation_engine.output_manager import (
      get_candidates_bundle)
  from intelligence.search_planner.generation_engine.recovery_manager import (
      request_cancel,pending_outbox,ack_outbox,mark_stale_dispatches)
  from uuid import UUID
  def stagec_plan(key):
   gen=create_planned_generation(pid,pub,saved["config_id"],key)
   q=create_generation_quote(pid,gen["generation_id"])
   authorize_generation_budget(pid,gen["generation_id"],q["quote_id"],"10.00")
   return gen["generation_id"]
  success_id=stagec_plan("sqg-stage-c-fake-success-001")
  fake=FakeProviderPort("success")
  finished=drain_generation(pid,success_id,transport=fake)
  self.assertEqual(len(fake.calls),3)
  self.assertEqual(finished[-1]["generation_status"],"completed")
  bundle=get_candidates_bundle(pid,success_id)
  self.assertEqual(bundle["schema_version"],"query_candidates/v1")
  self.assertEqual(bundle["status"],"completed")
  self.assertEqual(len(bundle["hypothesis_coverage"]),3)
  self.assertEqual(len(bundle["candidates"]),3)
  self.assertEqual({q["hypothesis_id"] for q in bundle["candidates"]},
                   {"hypothesis","problem_idea","adjacent_idea"})
  for query in bundle["candidates"]:
   UUID(query["candidate_id"])
   self.assertEqual(query["language"],"ru")
   self.assertEqual(query["query_mode"],"keyword")
  with psycopg.connect(self.dsn) as conn:
   self.assertEqual(conn.execute("""SELECT count(*) FROM sqg_outbox
                    WHERE generation_id=%s""",(success_id,)).fetchone()[0],1)
   self.assertEqual(conn.execute("""SELECT count(*) FROM sqg_attempts
                    WHERE generation_id=%s AND status='completed'""",
                    (success_id,)).fetchone()[0],3)
  pending=[e for e in pending_outbox(pid) if e["generation_id"]==success_id]
  self.assertEqual(len(pending),1)
  # Query Validation consumes the immutable source outbox atomically with
  # its own decisions, coverage and downstream outbox.
  from intelligence.search_planner.query_validation import (
      DEFAULT_POLICY,HashingTestEmbeddings,validate_generation_outbox,
      get_validated_bundle,pending_validated_outbox)
  from concurrent.futures import ThreadPoolExecutor
  source_event=pending[0]["outbox_id"]
  with self.assertRaises(PipelineError):
   validate_generation_outbox(pid,source_event,
       embedding_provider=HashingTestEmbeddings(),policy=DEFAULT_POLICY)
  with self.assertRaises(PipelineError):
   validate_generation_outbox(self.other["id"],source_event,
       embedding_provider=HashingTestEmbeddings(),policy=DEFAULT_POLICY,
       allow_test_embeddings=True)
  def consume(_):
   return validate_generation_outbox(pid,source_event,
       embedding_provider=HashingTestEmbeddings(),policy=DEFAULT_POLICY,
       allow_test_embeddings=True)
  with ThreadPoolExecutor(max_workers=2) as pool:
   processed=list(pool.map(consume,[0,1]))
  self.assertEqual(sum(not x["reused"] for x in processed),1)
  self.assertEqual(processed[0]["validation_run_id"],processed[1]["validation_run_id"])
  validation_id=processed[0]["validation_run_id"]
  with self.assertRaises(PipelineError):
   get_validated_bundle(pid,validation_id)
  val_bundle=get_validated_bundle(pid,validation_id,include_test=True)
  self.assertEqual(val_bundle["schema_version"],"validated_queries/v1")
  self.assertEqual(len(val_bundle["coverage"]),3)
  self.assertEqual(len(val_bundle["decisions"]),3)
  self.assertEqual(len(val_bundle["queries"]),3)
  self.assertEqual(len(pending_validated_outbox(pid)),0)
  self.assertEqual(len([x for x in pending_validated_outbox(pid,include_test=True)
       if x["validation_run_id"]==validation_id]),1)
  with psycopg.connect(self.dsn) as conn:
   self.assertIsNotNone(conn.execute("SELECT delivered_at FROM sqg_outbox WHERE id=%s",
                      (source_event,)).fetchone()[0])
   self.assertEqual(conn.execute("SELECT COUNT(*) FROM qv_runs WHERE source_outbox_id=%s",
                    (source_event,)).fetchone()[0],1)
   self.assertEqual(conn.execute("SELECT COUNT(*) FROM qv_decisions WHERE run_id=%s",
                    (validation_id,)).fetchone()[0],3)
   with self.assertRaises(Exception):
    with conn.transaction():
     conn.execute("UPDATE qv_decisions SET reason_code='quota' WHERE run_id=%s",
                  (validation_id,))
  with self.assertRaises(PipelineError):
   validate_generation_outbox(pid,source_event,
      embedding_provider=HashingTestEmbeddings(),
      policy={**DEFAULT_POLICY,"min_positive_similarity":0.95},
      allow_test_embeddings=True)
  self.assertTrue(ack_outbox(pid,pending[0]["outbox_id"])["acknowledged"])
  self.assertTrue(ack_outbox(pid,pending[0]["outbox_id"])["reused"])
  self.assertFalse([e for e in pending_outbox(pid) if e["generation_id"]==success_id])
  self.assertEqual(process_one_batch(pid,success_id,transport=fake)["status"],
                   "no_eligible_batch")
  self.assertEqual(len(fake.calls),3)
  no_viable_id=stagec_plan("sqg-stage-c-fake-no-viable-002")
  nv=drain_generation(pid,no_viable_id,transport=FakeProviderPort("no_viable"))
  self.assertEqual(nv[-1]["generation_status"],"completed")
  empty=get_candidates_bundle(pid,no_viable_id)
  self.assertEqual(empty["candidates"],[])
  self.assertEqual(len(empty["hypothesis_coverage"]),3)
  self.assertTrue(all(x["state"]=="no_viable_queries" and x["reason"]
                      for x in empty["hypothesis_coverage"]))
  source_no_viable=[x for x in pending_outbox(pid)
                   if x["generation_id"]==no_viable_id][0]["outbox_id"]
  validated_empty=validate_generation_outbox(pid,source_no_viable,
                  embedding_provider=HashingTestEmbeddings(),policy=DEFAULT_POLICY,
                  allow_test_embeddings=True)
  empty_valid=get_validated_bundle(pid,validated_empty["validation_run_id"],
                                   include_test=True)
  self.assertEqual(empty_valid["queries"],[])
  self.assertEqual(len(empty_valid["coverage"]),3)
  self.assertTrue(all(c["outcome"]=="no_source_candidates"
                      for c in empty_valid["coverage"]))
  invalid_id=stagec_plan("sqg-stage-c-fake-invalid-003")
  invalid=process_one_batch(pid,invalid_id,transport=FakeProviderPort("invalid_json"))
  self.assertEqual(invalid["status"],"failed_known")
  with self.assertRaises(PipelineError):
   get_candidates_bundle(pid,invalid_id)
  with psycopg.connect(self.dsn) as conn:
   self.assertEqual(conn.execute("""SELECT count(*) FROM sqg_candidates
      WHERE generation_id=%s""",(invalid_id,)).fetchone()[0],0)
   self.assertEqual(conn.execute("""SELECT count(*) FROM sqg_outbox
      WHERE generation_id=%s""",(invalid_id,)).fetchone()[0],0)
  unknown_id=stagec_plan("sqg-stage-c-fake-unknown-004")
  unk=process_one_batch(pid,unknown_id,transport=FakeProviderPort("uncertain"))
  self.assertEqual(unk["status"],"uncertain")
  self.assertEqual(process_one_batch(pid,unknown_id,
                   transport=FakeProviderPort("success"))["status"],
                   "no_eligible_batch")
  self.assertTrue(Decimal(get_generation_budget(pid,unknown_id)
                         ["consent"]["reserved_usd"])>0)
  self.assertEqual(mark_stale_dispatches(pid,unknown_id)["marked_uncertain"],0)
  cancelled_id=stagec_plan("sqg-stage-c-fake-cancel-005")
  self.assertEqual(request_cancel(pid,cancelled_id)["status"],"cancelled")
  self.assertTrue(request_cancel(pid,cancelled_id)["reused"])
  unused=FakeProviderPort("success")
  self.assertEqual(process_one_batch(pid,cancelled_id,transport=unused)["status"],
                   "no_eligible_batch")
  self.assertEqual(unused.calls,[])
  # Explicit cancellation during an already-dispatched request:
  # charge must settle, but cancelled/pending hypotheses MUST NOT produce outbox.
  from intelligence.search_planner.generation_engine.worker import planned_next_request
  from intelligence.search_planner.generation_engine.output_manager import settle_successful_batch
  inflight_id=stagec_plan("sqg-stage-c-cancel-inflight-006")
  plan=planned_next_request(pid,inflight_id)
  pre=reserve_batch(pid,inflight_id,plan.batch_id,plan.request_sha256)
  record_dispatch_intent(pid,pre["attempt_id"])
  self.assertEqual(request_cancel(pid,inflight_id)["status"],"uncertain")
  late_reply=FakeProviderPort("success").invoke_once(plan)
  late=settle_successful_batch(pid,pre["attempt_id"],late_reply)
  self.assertEqual(late["generation_status"],"cancelled")
  wallet=get_generation_budget(pid,inflight_id)["consent"]
  self.assertGreater(Decimal(wallet["spent_usd"]),Decimal(0))
  self.assertEqual(Decimal(wallet["reserved_usd"]),Decimal(0))
  with psycopg.connect(self.dsn) as conn:
   self.assertEqual(conn.execute("SELECT count(*) FROM sqg_outbox WHERE generation_id=%s",
                     (inflight_id,)).fetchone()[0],0)
  with self.assertRaises(PipelineError):
   get_candidates_bundle(pid,inflight_id)
  # Stage D: pinned real OpenRouter-shaped structured payload, mock HTTP
  # only. DB quote uses the serialized messages+schema envelope, not Stage A bytes.
  from intelligence.search_planner.ai_query.prompts import stage_d_fingerprints
  from intelligence.search_planner.ai_query.transport import OpenRouterQueryTransport
  from intelligence.search_planner.ai_query.live_worker import (
      preflight_live_batch, execute_one_paid_batch)
  from intelligence.search_planner.generation_engine.provider_port import (
      BatchRequest, FakeProviderPort)
  stage_d_cfg=generation_config()
  stage_d_cfg["model"]["catalog_snapshot_id"]=snaprow[0]
  stage_d_cfg["model"]["model_catalog_sha256"]=snaprow[1]
  stage_d_cfg["model"].update(stage_d_fingerprints())
  live_version=register_config_version(pid,stage_d_cfg)
  def stage_d_plan(key):
   obj=create_planned_generation(pid,pub,live_version["config_id"],key)
   qt=create_generation_quote(pid,obj["generation_id"])
   self.assertEqual(qt["batch_count"],3)
   self.assertTrue(qt["assumptions"]["not_guaranteed_cap"])
   authorize_generation_budget(pid,obj["generation_id"],qt["quote_id"],"10.00")
   return obj["generation_id"]
  live_id=stage_d_plan("sqg-stage-d-mock-openrouter-001")
  seen_paid=[]
  def mock_openrouter(payload):
   seen_paid.append(payload)
   sources=json.loads(payload["messages"][1]["content"])["hypothesis_contexts"]
   stub=BatchRequest("stub","stub",payload["model"],tuple(sources),
                     "a"*64,payload["max_tokens"])
   raw=FakeProviderPort("success").invoke_once(stub).raw_json
   return {"id":"mock_openrouter_stage_d_"+str(len(seen_paid)),
           "model":payload["model"],
           "choices":[{"finish_reason":"stop",
                       "message":{"content":json.dumps(raw,ensure_ascii=False)}}],
           "usage":{"prompt_tokens":450,"completion_tokens":300,
                    "cost":"0.00000001"}}
  mocked=OpenRouterQueryTransport(sender=mock_openrouter)
  first_preflight=preflight_live_batch(pid,live_id)
  self.assertEqual(first_preflight["provider_call_sent"],False)
  with self.assertRaises(PipelineError):
   execute_one_paid_batch(pid,live_id,transport=mocked)
  self.assertEqual(seen_paid,[])
  with self.assertRaises(PipelineError):
   preflight_live_batch(self.other["id"],live_id)
  for step in range(3):
   out=execute_one_paid_batch(pid,live_id,transport=mocked,
                              explicit_paid_execution=True)
   self.assertEqual(out["status"],"completed")
  self.assertEqual(out["generation_status"],"completed")
  self.assertEqual(len(seen_paid),3)
  self.assertTrue(all(body["response_format"]["json_schema"]["strict"]
                      and body["provider"]["require_parameters"]
                      and not body["provider"]["allow_fallbacks"]
                      for body in seen_paid))
  live_bundle=get_candidates_bundle(pid,live_id)
  self.assertEqual(len(live_bundle["candidates"]),3)
  with psycopg.connect(self.dsn) as conn:
   rows=conn.execute("""SELECT raw_result,charged_usd FROM sqg_attempts
            WHERE generation_id=%s ORDER BY started_at""",(live_id,)).fetchall()
   self.assertEqual(len(rows),3)
   self.assertTrue(all(row[0]["hypotheses"][0]["facets"][0]["sketches"] and
                       row[0]["hypotheses"][0]["facets"][0]["lexical_hints"]
                       for row in rows))
  unknown_live=stage_d_plan("sqg-stage-d-unknown-transport-002")
  broken=OpenRouterQueryTransport(sender=lambda body: {"id":"billed_unknown"})
  uncertain=execute_one_paid_batch(pid,unknown_live,transport=broken,
                                   explicit_paid_execution=True)
  self.assertEqual(uncertain["status"],"uncertain")
  self.assertGreater(Decimal(get_generation_budget(pid,unknown_live)
                    ["consent"]["reserved_usd"]),Decimal(0))
  self.assertIsNone(preflight_live_batch(pid,unknown_live))
  invalid_live=stage_d_plan("sqg-stage-d-invalid-raw-003")
  def bad_reply(body):
   ret=mock_openrouter(body)
   parsed=json.loads(ret["choices"][0]["message"]["content"])
   parsed["hypotheses"][0]["queries"][0]["text"]="fyp"
   ret["choices"][0]["message"]["content"]=json.dumps(parsed)
   return ret
  billed_bad=execute_one_paid_batch(pid,invalid_live,
                  transport=OpenRouterQueryTransport(sender=bad_reply),
                  explicit_paid_execution=True)
  self.assertEqual(billed_bad["status"],"failed_known")
  with self.assertRaises(PipelineError):
   get_candidates_bundle(pid,invalid_live)
  with psycopg.connect(self.dsn) as conn:
   self.assertEqual(conn.execute("SELECT count(*) FROM sqg_outbox WHERE generation_id=%s",
                    (invalid_live,)).fetchone()[0],0)
  self.assertEqual(len(get_deep(pid,self.rid)["nodes"]),7)
  self.assertEqual(self.sent,["fact_extraction","first","second","expansion"])
  with self.assertRaises(PipelineError):
   final_review(pid,self.rid,final)
  with self.assertRaises(PipelineError):
   advance_deep(pid,self.rid,sender=self.sender)
  with psycopg.connect(self.dsn) as conn:
   self.assertEqual(conn.execute("SELECT count(*) FROM tb_deep_reviews WHERE run_id=%s",
                                 (self.rid,)).fetchone()[0],2)
   self.assertEqual(conn.execute("SELECT count(*) FROM tb_deep_publications WHERE run_id=%s",
                                 (self.rid,)).fetchone()[0],1)
   r=conn.execute("""SELECT c.category_key,c.rules FROM sre_taxonomy_categories c
       JOIN tb_sre_taxonomy_links l ON l.sre_taxonomy_version_id=c.taxonomy_version_id
       WHERE l.run_id=%s ORDER BY c.name""",(self.rid,)).fetchall()
   self.assertEqual(len(r),7)
   from uuid import UUID
   for key,rules in r:
    self.assertIsInstance(UUID(str(key)),UUID)
    self.assertIn("hierarchy",rules)
    self.assertTrue(rules["hierarchy"]["source_node_id"])
    self.assertIn("parent_id",rules["hierarchy"])
   with self.assertRaises(Exception):
    with conn.transaction():
     conn.execute("DELETE FROM tb_deep_reviews WHERE run_id=%s",(self.rid,))
 def test_no_auto_retry_after_opaque_provider_result(self):
  pid=self.project["id"];self.prepare()
  result=advance_deep(pid,self.rid,sender=lambda body:{"choices":[]})
  self.assertEqual(result["status"],"uncertain")
  self.assertTrue(result["billing_unknown"])
  self.assertEqual(len(get_deep(pid,self.rid)["attempts"]),1)
  self.assertGreater(float(get_deep(pid,self.rid)["reserved_usd"]),0)
  with self.assertRaises(PipelineError):
   advance_deep(pid,self.rid,sender=self.sender)
 def test_saturation_never_loops_without_manual_request_and_blocks_exhausted_gaps(self):
  pid=self.project["id"];self.prepare()
  advance_deep(pid,self.rid,sender=self.sender)
  base=get_deep(pid,self.rid)["nodes"]
  first_review(pid,self.rid,[{"id":n["id"],"action":"approve"} for n in base])
  self.assertEqual(advance_deep(pid,self.rid,sender=self.sender)["status"],"expand_pending")
  self.assertEqual(self.sent.count("expansion"),0)
  def always_novel(body):
   reply=self.sender(body)
   name=body["response_format"]["json_schema"]["name"]
   if name.endswith("deep_expand"):
    k=self.sent.count("expansion")
    raw=json.loads(reply["choices"][0]["message"]["content"])
    raw["nodes"].append(item("novel_"+str(k),"Новая существенная смежная тема "+str(k),
                             "direction",None,"adjacent",[]))
    reply["choices"][0]["message"]["content"]=json.dumps(raw,ensure_ascii=False)
   return reply
  self.assertEqual(advance_deep(pid,self.rid,sender=always_novel)["status"],"expand_pending")
  self.assertEqual(self.sent.count("expansion"),1)
  result=advance_deep(pid,self.rid,sender=always_novel)
  self.assertEqual(result["status"],"blocked_quality")
  self.assertEqual(self.sent.count("expansion"),2)
  self.assertFalse(get_deep(pid,self.rid)["quality"]["passed"])
  with self.assertRaises(PipelineError):advance_deep(pid,self.rid,sender=always_novel)
  with self.assertRaises(PipelineError):final_review(pid,self.rid,[])
  import psycopg
  with psycopg.connect(self.dsn) as conn:
   self.assertEqual(conn.execute("SELECT count(*) FROM tb_deep_publications WHERE run_id=%s",
     (self.rid,)).fetchone()[0],0)

 def test_opus_cannot_restore_node_rejected_at_first_human_review(self):
  pid=self.project["id"];self.prepare()
  self.assertEqual(advance_deep(pid,self.rid,sender=self.sender)["status"],"review1")
  first=get_deep(pid,self.rid)["nodes"]
  decisions=[]
  for n in first:
   if n["id"]=="howto":decisions.append({"id":n["id"],"action":"reject"})
   elif n["id"]=="hypothesis":
    changed={**n,"parent_id":"food"}
    decisions.append({"id":n["id"],"action":"edit","node":changed})
   else:decisions.append({"id":n["id"],"action":"approve"})
  self.assertEqual(first_review(pid,self.rid,decisions)["status"],"pass2_pending")
  # The model ignores our rejection and returns its original proposal.
  got=advance_deep(pid,self.rid,sender=self.sender)
  self.assertEqual(got["status"],"failed")
  self.assertIn("reintroduced",got["error"])
  self.assertEqual(get_deep(pid,self.rid)["status"],"failed")
  with self.assertRaises(PipelineError):
   final_review(pid,self.rid,[])

 def test_project_isolation_and_no_advance_before_review(self):
  pid=self.project["id"];self.prepare()
  with self.assertRaises(PipelineError):get_deep(self.other["id"],self.rid)
  with self.assertRaises(PipelineError):first_review(pid,self.rid,[])
  self.assertEqual(get_deep(pid,self.rid)["status"],"pass1_pending")
  self.assertEqual(self.sent,["fact_extraction"])

if __name__=="__main__":unittest.main()
