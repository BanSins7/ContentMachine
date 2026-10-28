"""UA31 QA runner exact file whitelist, safe invocation, no secret logs.

Injects fake subprocess (does not execute Python tests or PostgreSQL) so
the default runner itself can be tested without any connected services.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import qa_runner as qa

ENV={
    "CM_TEST_PG_DSN":"postgresql://local/disposable",
    "CM_DB_DSN":"postgresql://local/production",
    "SECRET":"do-not-print",
}


class RunnerTests(unittest.TestCase):
    def test_pg_requires_explicit_confirmation(self):
        with self.assertRaises(ValueError):
            qa.validate_pg(True,False,ENV)

    def test_pg_requires_separate_test_dsn(self):
        with self.assertRaises(ValueError):
            qa.validate_pg(True,True,{})

    def test_pg_refuses_equal_prod_dsn(self):
        with self.assertRaises(ValueError):
            qa.validate_pg(True,True,{
                "CM_TEST_PG_DSN":"postgresql://same/db",
                "CM_DB_DSN":"postgresql://same/db",
            })

    def test_pg_refuses_equal_worker_dsn(self):
        with self.assertRaises(ValueError):
            qa.validate_pg(True,True,{
                "CM_TEST_PG_DSN":"postgresql://same/db",
                "CM_UAE_WORKER_DSN":"postgresql://same/db",
            })

    def test_pg_refuses_equal_operator_dsn(self):
        with self.assertRaises(ValueError):
            qa.validate_pg(True,True,{
                "CM_TEST_PG_DSN":"postgresql://same/db",
                "CM_UAE_OPS_DSN":"postgresql://same/db",
            })

    def test_default_run_clears_test_dsn_for_child_processes(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append((argv,kwargs))
            self.assertNotIn("CM_TEST_PG_DSN",kwargs["env"])
            self.assertNotIn("SECRET",str(argv))
            self.assertFalse(any("/" in arg and arg.endswith(".py")
                                 for arg in argv if arg.startswith("-p")))
            return SimpleNamespace(returncode=0)
        counter=iter(range(100))
        report=qa.execute(pg=False,confirm=False,env=ENV,
                          runner=runner,clock=lambda:next(counter))
        self.assertTrue(report["all_passed"])
        self.assertEqual(len(calls),len(qa.PURE_FILES))
        self.assertFalse(report["qualified_positive_e2e_verified"])
        self.assertFalse(report["qualified_50k_load_verified"])

    def test_pg_runner_explicit_still_checks_real_pg_separately(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append(kwargs["env"].get("CM_TEST_PG_DSN"))
            return SimpleNamespace(returncode=0)
        counter=iter(range(100))
        report=qa.execute(pg=True,confirm=True,env=ENV,
                          runner=runner,clock=lambda:next(counter))
        self.assertTrue(report["all_passed"])
        self.assertEqual(len(calls),len(qa.PURE_FILES)+len(qa.PG_FILES))
        self.assertTrue(all(x==ENV["CM_TEST_PG_DSN"] for x in calls))

    def test_failed_test_returns_redacted_summary_no_raw_traceback(self):
        def runner(argv,**kwargs):
            return SimpleNamespace(returncode=1,stderr="postgresql://secret:bad")
        report=qa.execute(pg=False,confirm=False,env=ENV,
                          runner=runner,clock=lambda:1.0)
        self.assertFalse(report["all_passed"])
        self.assertEqual(len(report["results"]),1)
        self.assertEqual(report["results"][0]["status"],"failed")
        self.assertNotIn("postgresql://",str(report))
        self.assertNotIn("SECRET",str(report))

    def test_timeout_returns_fail_without_trace(self):
        import subprocess
        def runner(argv,**kwargs):
            raise subprocess.TimeoutExpired(argv,1,stderr=b"password=secret")
        report=qa.execute(pg=False,confirm=False,env=ENV,
                          runner=runner,clock=lambda:1.0)
        self.assertFalse(report["all_passed"])
        self.assertEqual(report["results"][0]["status"],"timeout")
        self.assertNotIn("password=secret",str(report))

    def test_oserror_returns_sanitized_unavailable(self):
        def runner(argv,**kwargs):
            raise OSError("password=secret")
        report=qa.execute(pg=False,confirm=False,env=ENV,
                          runner=runner,clock=lambda:1.0)
        self.assertEqual(report["results"][0]["status"],"runner_unavailable")
        self.assertNotIn("password=secret",str(report))

    def test_invalid_boollike_flags_rejected(self):
        for opt,confirm in ((1,True),(True,1),("true",False)):
            with self.assertRaises(ValueError):
                qa.validate_pg(opt,confirm,ENV)

    def test_exact_test_file_whitelist(self):
        self.assertTrue(all(x.startswith("test_unified_analytics_")
                            and x.endswith(".py") for x in qa.PURE_FILES+qa.PG_FILES))
        self.assertEqual(len(set(qa.PURE_FILES+qa.PG_FILES)),
                         len(qa.PURE_FILES)+len(qa.PG_FILES))

    def test_all_qa_outcomes_no_false_production_qualification(self):
        def runner(argv,**kwargs):
            return SimpleNamespace(returncode=0)
        counter=iter(range(100))
        for pg in (False,True):
            report=qa.execute(pg=pg,confirm=pg,env=ENV,
                              runner=runner,clock=lambda:next(counter))
            self.assertFalse(report["qualified_positive_e2e_verified"])
            self.assertFalse(report["qualified_50k_load_verified"])


if __name__=="__main__":
    unittest.main()
