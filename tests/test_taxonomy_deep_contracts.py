"""Free unit tests for hierarchical Builder, coverage, human gates and safety."""
import unittest
from uuid import uuid4
from intelligence.taxonomy_builder.deep_contracts import (
    validate_tree,review_nodes,quality_gate,schema_for_deep,prompt_for_deep)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.provider import request_payload,reservation_cost

A=str(uuid4());B=str(uuid4());C=str(uuid4())
FACTS=[
 {"id":A,"kind":"function","text":"Персонализированный массаж лица"},
 {"id":B,"kind":"problem","text":"Утренняя припухлость лица"},
 {"id":C,"kind":"audience","text":"Взрослые, желающие удобный уход"}
]
def n(ident,name,kind="direction",parent=None,relation="direct",fact_id=A):
 return {"id":ident,"name":name,"kind":kind,"parent_id":parent,
         "relation":relation,"source_fact_ids":[fact_id] if fact_id else [],
         "rationale":"Отражает подтверждённую потребность аудитории",
         "definition":"Короткие видео с понятной целью и правилами",
         "include":"Уход за лицом и обычные ритуалы",
         "exclude":"Медицинские обещания и лечение",
         "examples":["Утренняя бережная рутина"]}
TREE=[
 n("massage","Массаж лица"),
 n("problem","Утренняя припухлость","direction",None,"problem_related",B),
 n("morning","Короткие утренние массажи","branch","massage"),
 n("howto","Как делать бережный массаж","hypothesis","morning"),
 n("prob_hyp","Как зрители решают проблему припухлости","hypothesis","problem","problem_related",B)
]
class DeepContracts(unittest.TestCase):
 def test_valid_tree_and_coverage_matrix(self):
  nodes=validate_tree(TREE,FACTS)
  self.assertEqual(len(nodes),5)
  q=quality_gate(nodes,FACTS)
  self.assertEqual(q["covered_facts"],2)
  self.assertEqual(q["severe_gap_count"],0)
  self.assertEqual(len(q["gaps"]),1)
  self.assertTrue(q["passed"],q)
  self.assertEqual(q["saturation"],"provisional")
 def test_new_direction_triggers_additional_saturation_check(self):
  more=TREE+[n("beauty","Эстетичный вечерний уход",
                 relation="adjacent",fact_id=None),
              n("beauty_hyp","Как рассказать о вечерней рутине",
                "hypothesis","beauty","adjacent",None)]
  self.assertEqual(quality_gate(more,FACTS,previous=TREE)["new_direction_count"],1)
  self.assertFalse(quality_gate(more,FACTS,previous=TREE)["passed"])
  self.assertTrue(quality_gate(more,FACTS,previous=more)["passed"])
 def test_missing_product_function_is_severe(self):
  only=[n("problem","Припухлость","direction",None,"problem_related",B),
        n("adjacent","Beauty lifestyle","direction",None,"adjacent",None),
        n("idea","Истории зрителей","hypothesis","problem","problem_related",B)]
  q=quality_gate(only,FACTS)
  self.assertGreaterEqual(q["severe_gap_count"],1)
  self.assertFalse(q["passed"])
 def test_model_semantic_duplicate_assertion_blocks_gate(self):
  q=quality_gate(TREE,FACTS,model_duplicates=[
       {"a":"massage","b":"morning","reason":"Практически одинаковая исследовательская тема"}])
  self.assertEqual(q["duplicate_count"],1)
  self.assertFalse(q["passed"])
 def test_fact_hallucination_and_cycles_blocked(self):
  bad=[dict(TREE[0],source_fact_ids=[str(uuid4())])]+TREE[1:]
  with self.assertRaises(PipelineError):validate_tree(bad,FACTS)
  bad=[dict(TREE[2],parent_id="howto") if x["id"]=="morning" else x for x in TREE]
  with self.assertRaises(PipelineError):validate_tree(bad,FACTS)
 def test_two_manual_review_gates_and_merge(self):
  all_approve=[{"id":x["id"],"action":"approve"} for x in TREE]
  self.assertEqual(len(review_nodes(TREE,all_approve,FACTS)),5)
  with self.assertRaisesRegex(PipelineError,"Review every proposed node"):
   review_nodes(TREE,all_approve[:-1],FACTS)
  changed=[{"id":x["id"],"action":"approve"} for x in TREE]
  changed[0]={"id":"massage","action":"edit",
              "node":dict(TREE[0],name="Массаж и утренние ритуалы")}
  self.assertEqual(review_nodes(TREE,changed,FACTS)[0]["name"],
                   "Массаж и утренние ритуалы")
 def test_rejecting_parent_requires_explicit_child_review(self):
  bad=[{"id":x["id"],"action":"approve"} for x in TREE]
  bad[2]={"id":"morning","action":"reject"}
  with self.assertRaisesRegex(PipelineError,"reparent"):
   review_nodes(TREE,bad,FACTS)
 def test_add_and_merge_supported(self):
  sample=TREE+[n("routine","Варианты массажа",relation="direct")]
  actions=[{"id":x["id"],"action":"approve"} for x in sample]
  actions[-1]={"id":"routine","action":"merge","target_id":"massage"}
  result=review_nodes(sample,actions,FACTS)
  self.assertEqual(len(result),5)
  add=n("human_idea","Идеи для спокойного вечера","direction",None,"adjacent",None)
  result=review_nodes(TREE,[{"id":x["id"],"action":"approve"} for x in TREE]+[
       {"action":"add","node":add}],FACTS)
  self.assertIn("human_idea",{x["id"] for x in result})
 def test_opus_deep_schema_and_single_request_cost(self):
  snapshot={"capabilities":{"supported_parameters":["structured_outputs"],
       "context_length":131072,"max_output_tokens":16000},
       "pricing":{"prompt":"0.000001","completion":"0.000005"}}
  stage={"role":"deep_first","model_id":"anthropic/claude-opus-5.5",
       "max_output_tokens":8000,"reasoning":{"mode":"effort","effort":"high"},
       "temperature":None}
  payload=request_payload(stage,snapshot,{"approved_facts":FACTS})
  self.assertEqual(payload["model"],stage["model_id"])
  self.assertEqual(payload["response_format"]["json_schema"]["schema"]["required"],["nodes"])
  self.assertTrue(payload["provider"]["require_parameters"])
  self.assertFalse(payload["provider"]["allow_fallbacks"])
  self.assertGreater(reservation_cost(payload,snapshot),0)
  for role in ("deep_first","deep_second","deep_expand"):
   self.assertTrue(schema_for_deep(role)["strict"])
   self.assertEqual(prompt_for_deep(role,{"approved_facts":FACTS})[0]["role"],"system")

if __name__=="__main__":unittest.main()
