"""M03 deterministic autonomous configuration & readiness; no provider/network/SQL write."""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from intelligence.relevance.config import (
    SRERuntimeSettings, connect_for_probe, load_settings, optional_dependency,
)
from intelligence.relevance.health import (
    HEALTH_VERSION, STATUS, diagnose, check_read_only_schema, NEEDED_TABLES,
)
from intelligence.relevance.v1_contracts import SREContractError


class RuntimeConfigurationTests(unittest.TestCase):
    def test_disabled_default_has_no_optional_feature(self):
        with patch.dict(os.environ, {"CM_SRE_ENABLED": "1"}, clear=True):
            cfg = load_settings({})
        self.assertFalse(cfg.flags["enabled"])
        self.assertFalse(any(cfg.flags.values()))
        self.assertFalse(cfg.database_configured)
        self.assertFalse(cfg.provider_key_configured)
        self.assertEqual(cfg.worker_lease_seconds, 60)
        self.assertEqual(cfg.max_attempts, 3)
        self.assertEqual(cfg.max_batch_items, 100)
        self.assertEqual(cfg.model_id, "anthropic/claude-opus-5.5")
        self.assertIsNone(cfg.local_embedding_model)

    def test_explicit_offline_opt_in_and_boundaries(self):
        cfg=load_settings({
            "CM_SRE_ENABLED":"true", "CM_SRE_OFFLINE_WORKER":"1",
            "CM_SRE_WORKER_LEASE_SECONDS":"3600",
            "CM_SRE_MAX_BATCH_ITEMS":"1000",
            "CM_SRE_MAX_ATTEMPTS":"10", "CM_SRE_LOCAL_EMBEDDING_MODEL":"local/pinned-model-v1",
        })
        self.assertTrue(cfg.flags["offline_worker"])
        self.assertEqual(cfg.worker_lease_seconds,3600)
        self.assertEqual(cfg.max_batch_items,1000)
        self.assertEqual(cfg.max_attempts,10)
        self.assertEqual(cfg.local_embedding_model,"local/pinned-model-v1")
        public=cfg.public_dict()
        public["features"]["enabled"]=False
        self.assertTrue(cfg.flags["enabled"])

    def test_reject_misconfigured_boolean_integer_unknown_key_and_fast_model(self):
        cases=[
            {"CM_SRE_ENABLED":"yes"}, {"CM_SRE_ENABLED":True},
            {"CM_SRE_ENABLED":"TRUE"}, {"CM_SRE_ENABLED":"0","CM_SRE_OFFLINE_WORKER":"1"},
            {"CM_SRE_LIVE_CLASSIFICATION":"1"}, {"CM_SRE_QDP_EVENT_DELIVERY":"1"},
            {"CM_SRE_COLLECTOR_EVENT_INGEST":"1"},
            {"CM_SRE_WORKER_LEASE_SECONDS":"4"}, {"CM_SRE_WORKER_LEASE_SECONDS":"3601"},
            {"CM_SRE_WORKER_LEASE_SECONDS":"60.0"}, {"CM_SRE_WORKER_LEASE_SECONDS":"060"},
            {"CM_SRE_MAX_BATCH_ITEMS":"0"}, {"CM_SRE_MAX_BATCH_ITEMS":"1001"},
            {"CM_SRE_MAX_ATTEMPTS":"0"}, {"CM_SRE_MAX_ATTEMPTS":"11"},
            {"CM_SRE_MODEL_ID":"anthropic/claude-sonnet-4.6"},
            {"CM_SRE_MODEL_ID":"anthropic/claude-opus-5.5 "},
            {"CM_SRE_UNKNOWN":"1"}, {"CM_SRE_LOCAL_EMBEDDING_MODEL":"../relative"},
            {"CM_SRE_LOCAL_EMBEDDING_MODEL":"/etc/passwd"},
            {"CM_SRE_LOCAL_EMBEDDING_MODEL":True}, {"CM_SRE_OPENROUTER_API_KEY":False},
        ]
        for values in cases:
            with self.subTest(values=values),self.assertRaises(SREContractError):
                load_settings(values)

    def test_reject_non_string_config_mapping_keys(self):
        with self.assertRaises(SREContractError):
            load_settings({True:"yes"})
        with self.assertRaises(SREContractError):
            load_settings(["CM_SRE_ENABLED=true"])

    def test_credentials_never_appear_in_repr_public_health_or_error(self):
        password="POSTGRES_PASSWORD_VALUE_SHOULD_NOT_LEAK_2hnd"
        token="sk-or-SRE_SECRET_KEY_DO_NOT_LEAK"
        dsn=f"postgresql://dbadmin:{password}@localhost:5432/db"
        cfg=load_settings({
            "CM_SRE_ENABLED":"1", "CM_DB_ENABLED":"1",
            "CM_DB_DSN":dsn,"CM_SRE_OPENROUTER_API_KEY":token,
        })
        self.assertTrue(cfg.provider_key_configured)
        self.assertTrue(cfg.database_configured)
        public=json.dumps(cfg.public_dict(),ensure_ascii=False)
        health=json.dumps(diagnose(cfg,dependency_probe=lambda _:True),ensure_ascii=False)
        for text in (repr(cfg),public,health):
            self.assertNotIn(token,text)
            self.assertNotIn(password,text)
            self.assertNotIn(dsn,text)
        with patch.dict(sys.modules,{"psycopg":object()}):
            with patch("intelligence.relevance.config.optional_dependency",
                       return_value=True):
                pass

    def test_explicit_postgres_failure_strips_dsn_from_exception(self):
        credential="please_never_log_this_dsn_secret"
        cfg=load_settings({
            "CM_DB_ENABLED":"1","CM_DB_DSN":f"postgres://admin:{credential}@local/db"
        })
        class DBFake:
            @staticmethod
            def connect(*args,**kwargs):
                raise OSError("conn refused user admin: "+credential)
        with patch.dict(sys.modules,{"psycopg":DBFake}):
            with self.assertRaises(SREContractError) as raised:
                connect_for_probe(cfg)
        self.assertNotIn(credential,str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(raised.exception.code,"blocked_config")

    def test_disabled_db_connection_is_denied(self):
        cfg=load_settings({"CM_DB_ENABLED":"0","CM_DB_DSN":"secret"})
        with self.assertRaises(SREContractError):
            connect_for_probe(cfg)

    def test_model_dependency_lookup_is_whitelisted_and_not_imported(self):
        self.assertIsInstance(optional_dependency("psycopg"),bool)
        self.assertIsInstance(optional_dependency("sentence_transformers"),bool)
        self.assertIsInstance(optional_dependency("openai"),bool)
        with self.assertRaises(SREContractError):
            optional_dependency("custom_code_that_should_not_be_loaded")

    def test_import_config_health_has_no_env_read_or_provider_init(self):
        code=(
            "import sys; import intelligence.relevance.config as c; "
            "from intelligence.relevance.health import diagnose; "
            "assert 'psycopg' not in sys.modules; "
            "assert 'requests' not in sys.modules; "
            "assert 'sentence_transformers' not in sys.modules; "
            "assert 'intelligence.collector' not in sys.modules; "
            "assert 'intelligence.qualified_data' not in sys.modules; "
            "assert not c.load_settings({}).flags['enabled']")
        proc=subprocess.run([sys.executable,"-c",code],capture_output=True,
                            text=True,timeout=12)
        self.assertEqual(proc.returncode,0,proc.stderr)


class RuntimeHealthTests(unittest.TestCase):
    def sample(self, extra=None):
        d={"CM_SRE_ENABLED":"1","CM_DB_ENABLED":"true","CM_DB_DSN":"private"}
        d.update(extra or {})
        return load_settings(d)

    def test_disabled_does_not_invoke_any_check(self):
        def forbidden(*_):
            raise AssertionError("no dependency, DB or provider check on disabled")
        result=diagnose(load_settings({}),db_probe=forbidden,
                        policy_probe=forbidden,dependency_probe=forbidden)
        self.assertEqual(result["status"],"disabled")
        self.assertFalse(result["worker_enabled"])

    def test_blocked_config_and_dependency(self):
        cfg=self.sample({"CM_DB_ENABLED":"0"})
        self.assertEqual(diagnose(cfg)["status"],"blocked_config")
        cfg=self.sample()
        self.assertEqual(diagnose(cfg,dependency_probe=lambda _:False)["status"],
                         "blocked_dependency")
        self.assertEqual(diagnose(cfg,dependency_probe=lambda _:True)["status"],
                         "blocked_config")
        self.assertEqual(diagnose(cfg,dependency_probe=lambda _:True,
                                  db_probe=lambda:False)["reason_code"],
                         "database_preflight_failed")

    def test_blocked_policy_and_explicit_offline_available(self):
        cfg=self.sample({"CM_SRE_OFFLINE_WORKER":"1"})
        gate=lambda _:True
        self.assertEqual(diagnose(cfg,dependency_probe=gate,db_probe=lambda:True)
                         ["status"],"blocked_policy")
        self.assertEqual(diagnose(cfg,dependency_probe=gate,
                                  db_probe=lambda:True,policy_probe=lambda:False)
                         ["status"],"blocked_policy")
        result=diagnose(cfg,dependency_probe=gate,
                        db_probe=lambda:True,policy_probe=lambda:True)
        self.assertEqual(result["schema_version"],HEALTH_VERSION)
        self.assertEqual(result["status"],"available")
        self.assertTrue(result["worker_enabled"])
        self.assertTrue(result["project_authorized"])
        self.assertFalse(result["can_start_live_worker"])
        self.assertFalse(result["can_dispatch_provider"])
        self.assertEqual(result["runtime_mode"],"offline_fixture")

    def test_unhandled_callbacks_fail_closed_with_no_error_details(self):
        cfg=self.sample()
        def broken():
            raise OSError("this_sensitive_db_info_must_stay_private")
        result=diagnose(cfg,dependency_probe=lambda _:True,db_probe=broken)
        self.assertEqual(result["status"],"blocked_config")
        self.assertNotIn("sensitive",str(result))
        result=diagnose(cfg,dependency_probe=lambda _:True,
                        db_probe=lambda:True,policy_probe=broken)
        self.assertEqual(result["status"],"blocked_policy")

    def test_status_members_are_constrained(self):
        self.assertEqual(STATUS,{
            "disabled","available","blocked_config","blocked_dependency","blocked_policy",
        })

    def test_schema_probe_uses_explicit_read_only_transaction(self):
        class ReadOnlyTransaction:
            def __enter__(self): return self
            def __exit__(self,*args): return False
        class FakeConnection:
            def __init__(self): self.sql=[]
            def transaction(self): return ReadOnlyTransaction()
            def execute(self,sql,params=None):
                self.sql.append((sql,params))
                if "SELECT c.relname" in sql:
                    return self
                return self
            def fetchall(self):
                return [(name,) for name in NEEDED_TABLES]
        conn=FakeConnection()
        self.assertTrue(check_read_only_schema(conn))
        self.assertEqual(conn.sql[0][0],"SET TRANSACTION READ ONLY")
        self.assertEqual(conn.sql[1][1],(sorted(NEEDED_TABLES),))
        self.assertFalse(any("INSERT" in q[0] or "UPDATE" in q[0] for q in conn.sql))

    def test_missing_database_relation_blocks_health(self):
        class FakeConn:
            def transaction(self):
                class Tx:
                    def __enter__(self): return self
                    def __exit__(self,*x): return False
                return Tx()
            def execute(self,*args,**kwargs): return self
            def fetchall(self): return [("sre_workspaces",)]
        self.assertFalse(check_read_only_schema(FakeConn()))


if __name__ == "__main__":
    unittest.main()
