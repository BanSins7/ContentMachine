"""SP-14 real project-scoped PG read-only UI overview and verified model catalog.

SQL doubles are not proof of PostgreSQL concurrency or a fresh/upgrade
migration. No browser credentials, provider calls or paid work in tests.
"""
from __future__ import annotations
from datetime import datetime, timezone
from unittest.mock import patch
import unittest

from intelligence.search_planner.ui_overview import (
    read_sp_ui_overview,read_sp_pinned_model_catalog,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

PID="a"*32
GEN="55555555-5555-4555-8555-555555555555"
PUB="22222222-2222-4222-8222-222222222222"
CFG="88888888-8888-4888-8888-888888888888"
OUTBOX="99999999-9999-4999-8999-999999999999"
QV="66666666-6666-4666-8666-666666666666"
INBOX="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
T=datetime(2026,10,26,12,30,tzinfo=timezone.utc)

class Rows:
    def __init__(self,rows):self.rows=rows
    def fetchall(self):return self.rows
    def fetchone(self):return self.rows[0] if self.rows else None

class Database:
    def __init__(self):
        self.calls=[]
        self.transactions=0
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def transaction(self):
        self.transactions+=1
        return self
    def execute(self,query,args=None):
        self.calls.append((query,args))
        if query.startswith("SET TRANSACTION"):return Rows([])
        if "FROM project_registry" in query:return Rows([(1,)])
        if "FROM sqg_config_versions c" in query:
            return Rows([(CFG,1,"a"*64,{"model_id":"valid/model"},T)])
        if "FROM sqg_generations g" in query:
            return Rows([(GEN,PUB,CFG,"completed",T,OUTBOX)])
        if "FROM qv_runs r" in query:
            return Rows([(QV,GEN,19,22,"semantic_local",T,OUTBOX)])
        if "FROM sp_handoff_events e" in query:
            return Rows([(INBOX,QV,19,1,"awaiting_collector",T)])
        raise AssertionError("Unexpected SQL "+query[:100])

class OwnerOverviewSP14(unittest.TestCase):
    def test_reads_actual_config_generation_qv_and_inbox_in_one_read_only_tx(self):
        db=Database()
        result=read_sp_ui_overview(PID,connect=lambda:db)
        self.assertEqual(result["project_id"],PID)
        self.assertEqual(result["schema_version"],"search-planner-owner-overview/v1")
        self.assertEqual(result["configs"][0]["config_id"],CFG)
        self.assertEqual(result["generations"][0]["source_outbox_id"],OUTBOX)
        self.assertEqual(result["validations"][0]["selected_count"],19)
        self.assertEqual(result["inbox"][0]["status"],"awaiting_collector")
        self.assertEqual(result["provider_requests_in_this_read"],0)
        self.assertEqual(db.transactions,1)
        self.assertIn("READ ONLY",db.calls[0][0])
        self.assertTrue(all(args==(PID,20) for sql,args in db.calls[1:]))
        self.assertTrue(all("project_id=%s" in sql for sql,args in db.calls[1:]))
        self.assertFalse(any("INSERT" in sql or "UPDATE" in sql
                             for sql,args in db.calls))

    def test_limits_and_project_scope_refused_before_database_io(self):
        db=Database()
        for count in (0,51,True,"30",-3):
            with self.subTest(count=count),self.assertRaises(PipelineError):
                read_sp_ui_overview(PID,limit=count,connect=lambda:db)
        with self.assertRaises(PipelineError):
            read_sp_ui_overview("unknown",connect=lambda:db)
        self.assertEqual(db.calls,[])

    def test_model_catalog_is_cached_and_pins_computed_server_side(self):
        db=Database()
        stub=[{
            "model_id":"test/model","display_name":"Verified",
            "catalog_version_id":33,"catalog_sha256":"f"*64,
            "selectable":True,"status":"usable",
            "capabilities":{"supports_structured_outputs":True},
        }]
        pins={
            "prompt_version":"ai-query-v1",
            "raw_schema_version":"ai-query-raw/v1",
            "strategy_version":"thinkqe-facets-mugi-sketches-jina-lexicon-v1",
            "prompt_sha256":"a"*64,"raw_schema_sha256":"b"*64,
        }
        with patch("intelligence.taxonomy_builder.catalog.list_models",
                   return_value=stub) as listing,patch(
                   "intelligence.search_planner.ai_query.prompts.stage_d_fingerprints",
                   return_value=pins):
            result=read_sp_pinned_model_catalog(PID,connect=lambda:db)
        self.assertEqual(result["schema_version"],"search-planner-model-picker/v1")
        self.assertEqual(result["models"],stub)
        self.assertEqual(result["prompt_pins"],pins)
        self.assertEqual(result["available_count"],1)
        self.assertEqual(result["provider_requests_in_this_read"],0)
        self.assertEqual(result["model_downloads_in_this_read"],0)
        self.assertFalse(result["spending_authorized"])
        self.assertEqual(db.transactions,1)
        self.assertIn("READ ONLY",db.calls[0][0])
        listing.assert_called_once_with(db)

    def test_unknown_project_cannot_read_cached_models(self):
        class Unknown(Database):
            def execute(self,query,args=None):
                if "FROM project_registry" in query:
                    return Rows([])
                return super().execute(query,args)
        db=Unknown()
        with self.assertRaises(PipelineError):
            read_sp_pinned_model_catalog(PID,connect=lambda:db)


if __name__=="__main__":
    unittest.main()
