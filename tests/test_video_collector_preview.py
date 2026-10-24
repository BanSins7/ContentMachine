"""No-cost preview tests: there is no provider/network execution."""
import unittest
from intelligence.collector.planner import build_preview,CollectorPlanError

PROJECT="a"*32
def tree():
 return [
  {"id":"face","name":"Уход за лицом","kind":"direction","parent_id":None},
  {"id":"selfcare","name":"Self-care","kind":"direction","parent_id":None},
  {"id":"massage","name":"Ритуалы массажа","kind":"branch","parent_id":"face"},
  {"id":"wake","name":"Утренний массаж лица","kind":"hypothesis",
   "parent_id":"massage","source_fact_ids":["fact-1"]},
  {"id":"stress","name":"Расслабление вечером","kind":"hypothesis",
   "parent_id":"selfcare","source_fact_ids":["fact-2"]}
 ]

class VideoCollectorPreviewTests(unittest.TestCase):
 def test_preview_no_network_and_actor_fields(self):
  p=build_preview(PROJECT,1,tree(),publication_verified=True,
                  max_queries=4,results_per_query=7)
  self.assertFalse(p["execution_enabled"])
  self.assertEqual(p["status"],"preview_only")
  self.assertEqual(p["actor_input"]["searchSection"],"/video")
  self.assertEqual(p["actor_input"]["resultsPerPage"],7)
  self.assertEqual(p["requested_result_upper_bound"],28)
  self.assertEqual(len(p["query_provenance"]),4)
  self.assertEqual(p["query_provenance"][0]["node_id"],"wake")
  self.assertEqual(p["query_provenance"][0]["source_fact_ids"],["fact-1"])
  self.assertNotIn("videoSearchSorting",p["actor_input"])
 def test_idempotent_preview_and_reordered_names(self):
  a=build_preview(PROJECT,1,tree(),publication_verified=True)
  b=build_preview(PROJECT,1,tree(),publication_verified=True)
  self.assertEqual(a["plan_id"],b["plan_id"])
  n=tree();n[3]["name"]="Ночной массаж"
  self.assertNotEqual(a["plan_id"],build_preview(
      PROJECT,1,n,publication_verified=True)["plan_id"])
 def test_no_publication_no_plan(self):
  with self.assertRaises(CollectorPlanError):
   build_preview(PROJECT,1,tree())
 def test_reject_unbounded_or_malformed(self):
  for keyword,argument in [
   ("max_queries",1000),("results_per_query",0),("results_per_query",True)
  ]:
   with self.subTest(keyword=keyword),self.assertRaises(CollectorPlanError):
    build_preview(PROJECT,1,tree(),publication_verified=True,**{keyword:argument})
  for mutant in [
    [dict(x) for x in tree()[:-1]],
    [dict(x) for x in tree()]
  ]:
   mutant[-1]["parent_id"]="missing"
   with self.assertRaises(CollectorPlanError):
    build_preview(PROJECT,1,mutant,publication_verified=True)
 def test_no_hypothesis_no_preview(self):
  short=tree()[:3]
  with self.assertRaisesRegex(CollectorPlanError,"hypotheses"):
   build_preview(PROJECT,1,short,publication_verified=True)

if __name__=="__main__":unittest.main()
