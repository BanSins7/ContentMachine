"""Flat RTB -> SP is an explicit verified source, not fake Deep C9."""
import copy
import unittest

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.search_planner.integration.flat_rtb_pipeline import (
    SOURCE_FLAT, verify_flat_snapshot, build_flat_contexts, read_flat_publication,
)
from intelligence.search_planner.generation_engine.batch_manager import make_batches
from intelligence.search_planner.ai_query.prompts import prompt_messages

PID = "f"*32
PUB = "a"*8+"-aaaa-aaaa-aaaa-"+"a"*12
RUN = "b"*8+"-bbbb-bbbb-bbbb-"+"b"*12
FACT = "c"*8+"-cccc-cccc-cccc-"+"c"*12
POLICY = {"languages":["ru","en"],"markets":["BY","PL"],
  "allowed_modes":["keyword","hashtag"],
  "target_candidates_per_hypothesis":4,
  "max_candidates_per_hypothesis":8,"max_total_candidates":240}

def snapshot(n=13):
    topics=[{
       "id":f"{i:08x}-0000-4000-8000-000000000001",
       "name":f"Утверждённая тема {i}",
       "definition":f"Точное определение темы {i}",
       "include":"Разрешённые короткие ролики",
       "exclude":"Медицинские обещания и недоказанные заявления",
       "positive_examples":["Подтверждённый пример"],
       "negative_examples":["Неподходящий пример"],
       "relation":"direct","origin":"explicit",
       "fact_ids":[FACT],"review_status":"approved"
    } for i in range(1,n+1)]
    digest=sha256(topics)
    return {"project_id":PID,"taxonomy_publication_id":PUB,"rtb_run_id":RUN,
      "taxonomy_version":3,"topics":topics,
      "profile_snapshot":{"product_type":"mobile_app","product_functions":"Face yoga",
        "audience_problems":"Утренняя отёчность"},
      "approved_facts":[{"id":FACT,"kind":"function","text":"Упражнения для лица",
        "source_quote":"facial exercises","origin":"source_verified"}],
      "taxonomy_sha256":digest,"sre_snapshot_sha256":digest,
      "run_status":"approved","sre_status":"approved",
      "approved_by":"local_owner","publication_source":SOURCE_FLAT}

class FlatRTBSearchPlannerTests(unittest.TestCase):
    def test_thirteen_approved_topics_survive_without_fake_deep_hierarchy(self):
        source=snapshot()
        published,contexts=build_flat_contexts(source,POLICY)
        self.assertEqual(published["publication_source"],SOURCE_FLAT)
        self.assertEqual(len(contexts),13)
        self.assertEqual({x["hypothesis"]["name"] for x in contexts},
                         {x["name"] for x in source["topics"]})
        for context in contexts:
            self.assertEqual(context["context_schema_version"],"hypothesis-context/flat-v1")
            self.assertEqual(context["direction"]["source_kind"],
                             "not_a_deep_research_direction")
            self.assertIsNone(context["branch"])
            self.assertEqual(context["flat_rtb_provenance"]["topic"],
                             context["hypothesis"])
            self.assertEqual(context["approved_facts"][0]["source_quote"],
                             "facial exercises")
            self.assertEqual(context["search_policy"],POLICY)
        # Contexts are actual usable inputs to the existing SP batch engine.
        config={"execution":{"target_hypotheses_per_batch":4,
                             "hard_max_hypotheses_per_batch":5,
                             "max_input_bytes_per_batch":200000},
                "model":{"max_output_tokens":25000,"model_id":"test-model"}}
        batches=make_batches(contexts,config)
        self.assertEqual(sum(len(b["hypothesis_ids"]) for b in batches),13)
        self.assertEqual(set(x for b in batches for x in b["hypothesis_ids"]),
                         set(t["id"] for t in source["topics"]))
        messages=prompt_messages(contexts[:2])
        self.assertIn("hypothesis_contexts",messages[1]["content"])
        self.assertEqual(len(source["topics"]),13)
        self.assertEqual(source["taxonomy_sha256"],sha256(source["topics"]))

    def test_no_approval_or_checksum_shortcuts(self):
        base=snapshot()
        for mutate in [
          lambda s:s.update(run_status="topic_review"),
          lambda s:s.update(sre_status="draft"),
          lambda s:s.update(approved_by="another_person"),
          lambda s:s.update(sre_snapshot_sha256="0"*64),
          lambda s:s.update(taxonomy_sha256="0"*64),
          lambda s:s["topics"][0].update(review_status="pending"),
          lambda s:s["topics"][0].update(fact_ids=["not-approved"]),
          lambda s:s["topics"][0].update(name=s["topics"][1]["name"]),
        ]:
            source=copy.deepcopy(base)
            mutate(source)
            with self.subTest(source=source["taxonomy_sha256"][:6],
                              mutation=str(mutate)):
                with self.assertRaises(PipelineError):
                    build_flat_contexts(source,POLICY)

    def test_project_scoped_and_source_does_not_mutate(self):
        source=snapshot(2)
        copy_before=copy.deepcopy(source)
        self.assertEqual(verify_flat_snapshot(source,PID,PUB),source)
        self.assertEqual(source,copy_before)
        with self.assertRaises(PipelineError):
            verify_flat_snapshot(source,"e"*32,PUB)
        with self.assertRaises(PipelineError):
            verify_flat_snapshot(source,PID,RUN)

    def test_authoritative_db_reader_rejects_unpublished_and_bad_checksum(self):
        source=snapshot(2)
        class FakeConn:
            def __init__(self,row):self.row=row;self.sql=""
            def execute(self,query,args):
                self.sql=query
                self.args=args
                return self
            def fetchone(self):return self.row
        conn=FakeConn(None)
        with self.assertRaises(PipelineError):
            read_flat_publication(conn,PID,PUB)
        self.assertIn("r.status='approved'",conn.sql)
        self.assertIn("v.status='approved'",conn.sql)
        self.assertIn("tb_sre_taxonomy_links",conn.sql)
        row=[RUN,3,source["topics"],source["profile_snapshot"],
             source["approved_facts"],source["taxonomy_sha256"],
             "approved",source["sre_snapshot_sha256"],"local_owner"]
        self.assertEqual(len(read_flat_publication(FakeConn(row),PID,PUB)["topics"]),2)
        row[7]="0"*64
        with self.assertRaises(PipelineError):
            read_flat_publication(FakeConn(row),PID,PUB)

    def test_additive_migration_keeps_both_deep_sql_guards(self):
        from pathlib import Path
        path=Path(__file__).resolve().parents[1]/"intelligence/migrations"/"077_sp_flat_rtb_source.sql"
        sql=path.read_text(encoding="utf-8")
        for critical in ["ALTER TABLE sqg_generations","ALTER TABLE sqg_quotes",
          "ALTER TABLE sqg_consents","flat_rtb_source",
          "unapproved or mismatched flat RTB publication",
          "only project-scoped approved deep publications",
          "unpublished or unapproved V2 source",
          "mismatched flat RTB consent/quote",
          "sqg_generation_flat_mode_update_guard"]:
            with self.subTest(critical=critical):
                self.assertIn(critical,sql)

if __name__=="__main__":
    unittest.main()
