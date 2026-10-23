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
