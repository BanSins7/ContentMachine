"""SP-13 same-content owner config POST is idempotent under DB project lock."""
from __future__ import annotations

import unittest
from intelligence.search_planner.generation_engine.run_manager import register_config_version
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from test_search_planner_engine_contracts import config

PID="a"*32
CID="44444444-4444-4444-8444-444444444444"


class Response:
    def __init__(self,row):self.row=row
    def fetchone(self):return self.row


class Database:
    def __init__(self):
        self.saved=None
        self.revision=0
        self.sql=[]
        self.transactions=0
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def transaction(self):
        self.transactions+=1
        return self
    def execute(self,sql,args=None):
        self.sql.append((sql,args))
        if "FROM tb_model_snapshots WHERE id" in sql:
            return Response(("anthropic/claude-opus-5.5","a"*64,
                             {"supported_parameters":["response_format"]}))
        if "SELECT id FROM project_registry" in sql:
            return Response((PID,))
        if "SELECT id,revision FROM sqg_config_versions" in sql:
            return Response((CID,self.revision) if self.saved else None)
        if "COALESCE(MAX(revision)" in sql:
            return Response((self.revision+1,))
        if "INSERT INTO sqg_config_versions" in sql:
            if self.saved:raise AssertionError("Duplicate DB config insert")
            self.saved=args
            self.revision+=1
            return Response((CID,))
        raise AssertionError("Unknown SQL "+sql[:150])


class ConfigHTTPIdempotency(unittest.TestCase):
    def test_two_identical_requests_reuse_original_approved_immutable_config(self):
        db=Database()
        first=register_config_version(PID,config(),connect=lambda:db)
        second=register_config_version(PID,config(),connect=lambda:db)
        self.assertEqual(first["config_id"],CID)
        self.assertEqual(first["revision"],1)
        self.assertEqual(second["config_id"],CID)
        self.assertEqual(second["revision"],1)
        self.assertTrue(second["reused"])
        self.assertEqual(first["config_sha256"],second["config_sha256"])
        self.assertEqual(sum("INSERT INTO sqg_config_versions" in x[0]
                             for x in db.sql),1)
        self.assertEqual(db.transactions,2)
        self.assertTrue(all("FOR UPDATE" in stmt for stmt,args in db.sql
                            if "FROM project_registry" in stmt))

    def test_same_project_snapshot_mismatch_never_touches_config_table(self):
        class BadDB(Database):
            def execute(self,sql,args=None):
                if "FROM tb_model_snapshots WHERE id" in sql:
                    return Response(("other/model","a"*64,
                                     {"supported_parameters":["response_format"]}))
                return super().execute(sql,args)
        db=BadDB()
        with self.assertRaises(PipelineError):
            register_config_version(PID,config(),connect=lambda:db)
        self.assertFalse(any("INSERT INTO sqg_config_versions" in sql
                             for sql,args in db.sql))


if __name__=="__main__":
    unittest.main()
