"""M01/M04 regression for independent forward-only migration compatibility.

New module SQL files with a historical numeric prefix are ADDITIONS, not
rewrites of already-applied PSI/other module migrations. Previously tracked
files modified/removed/renamed before 030 still fail the PSI release gate.
"""
from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from scripts.psi_release_gate import ReleaseBlocked, legacy_unchanged

BASE_SHA = "1111111111111111111111111111111111111111"


class MigrationGateCompatibility(unittest.TestCase):
    def run_git(self, changed):
        commands = []
        def fake_run(args, **kwargs):
            commands.append(tuple(args))
            if "merge-base" in args:
                return subprocess.CompletedProcess(args,0,stdout=BASE_SHA+"\n",stderr="")
            if "diff" in args:
                return subprocess.CompletedProcess(args,0,stdout=changed,stderr="")
            raise AssertionError("Unexpected git command")
        with patch("scripts.psi_release_gate.subprocess.run",side_effect=fake_run):
            return commands,legacy_unchanged()

    def test_additive_sre_016_and_017_do_not_trip_legacy_rewrite(self):
        calls, result=self.run_git("")
        self.assertEqual(result,BASE_SHA[:12])
        self.assertEqual(len(calls),2)
        self.assertIn("--diff-filter=DMRT",calls[1])
        self.assertIn("--name-status",calls[1])
        self.assertIn("intelligence/migrations",calls[1])

    def test_modified_historical_migration_is_still_blocked(self):
        for path in ["intelligence/migrations/001_foundation.sql",
                     "intelligence/migrations/016_query_validation.sql",
                     "intelligence/migrations/017_sre_offline_run_engine.sql"]:
            with self.subTest(path=path):
                with self.assertRaises(ReleaseBlocked):
                    self.run_git(path+"\n")

    def test_historical_rename_checks_both_old_and_new_paths(self):
        with self.assertRaises(ReleaseBlocked):
            self.run_git("R100\\tintelligence/migrations/010_old.sql\\t"
                         "intelligence/migrations/055_new.sql\\n")

    def test_psi_newer_030_to_053_is_not_historical(self):
        calls,sha=self.run_git("intelligence/migrations/030_psi_stage.sql\n")
        self.assertEqual(sha,BASE_SHA[:12])


if __name__ == "__main__":
    unittest.main()
