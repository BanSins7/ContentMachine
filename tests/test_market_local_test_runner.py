"""Regression tests for zero-spend, non-destructive Marketing QA harness."""
from __future__ import annotations
from argparse import Namespace
import json
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_market_tests as local


class MarketQASafety(unittest.TestCase):
    def test_direct_video_cli_imports_marketing_outside_repo_cwd(self):
        # Regression: "python scripts/probe_market_video.py" used to fail
        # with "No module named intelligence", even with a valid MP4.
        # Probe a nonexistent file, so no media processing or paid API occurs.
        with tempfile.TemporaryDirectory() as directory:
            missing=Path(directory)/"does-not-exist.mp4"
            result=subprocess.run(
                [sys.executable,
                 str(local.ROOT/"scripts"/"probe_market_video.py"),
                 "--video",str(missing)],
                cwd=directory,env=local.environment(),
                text=True,capture_output=True,timeout=45,check=False)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("Input must be an existing MP4",result.stderr)
        self.assertNotIn("ModuleNotFoundError",result.stderr)

    def test_parent_provider_credentials_not_forwarded(self):
        fake={
            "OPENROUTER_API_KEY":"do-not-use-this-key",
            "BRIGHTDATA_API_KEY":"do-not-use-this-key",
            "PGPASSWORD":"do-not-use-this-key",
            "CM_DB_DSN":"postgresql://production",
            "SOME_TOKEN":"do-not-use-this-key",
        }
        with patch.dict(local.os.environ,fake):
            env=local.environment()
        self.assertNotIn("OPENROUTER_API_KEY",env)
        self.assertNotIn("BRIGHTDATA_API_KEY",env)
        self.assertNotIn("PGPASSWORD",env)
        self.assertNotIn("SOME_TOKEN",env)
        self.assertEqual(env["CM_DB_DSN"],"")
        self.assertEqual(env["CM_DB_ENABLED"],"0")
        self.assertEqual(env["CM_MARKET_VISION_ENABLED"],"0")
        self.assertEqual(env["CM_MARKET_ASR_ENABLED"],"0")
        self.assertEqual(env["CM_MARKET_AUDIO_EVENTS_ENABLED"],"0")

    def test_only_explicit_disposable_db_is_injected(self):
        dsn="postgresql://test-only@127.0.0.1:56789/disposable"
        env=local.environment(dsn)
        self.assertEqual(env["CM_DB_DSN"],dsn)
        self.assertEqual(env["CM_DB_ENABLED"],"1")

    def test_unittest_skip_does_not_count_as_pass(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(local,"REPORTS",Path(root)):
                tool=local.Acceptance(Namespace(full=False,db=False,video=None))
                ok=tool.run("simulated_skip",
                    [sys.executable,"-c",
                     "print('Ran 1 test in 0.001s\\nOK (skipped=1)')"],
                     require_tests=True,timeout=15)
                self.assertFalse(ok)
                self.assertEqual(tool.steps[-1]["status"],"SKIP")
                report=json.loads((tool.dir/"report.json").read_text("utf-8"))
                self.assertFalse(report["all_gates_passed"])
                self.assertFalse(report["production_db_used"])
                self.assertFalse(report["production_100_video_e2e_certified"])

    def test_real_failed_subprocess_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(local,"REPORTS",Path(root)):
                tool=local.Acceptance(Namespace(full=False,db=False,video=None))
                result=tool.run("simulated_failure",
                    [sys.executable,"-c","raise RuntimeError('real failure')"],
                    timeout=15)
                self.assertFalse(result)
                self.assertEqual(tool.steps[-1]["status"],"FAIL")
                self.assertTrue((tool.dir/"simulated_failure.log").is_file())


if __name__=="__main__":
    unittest.main()
