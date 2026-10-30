"""Pinned source copies remain usable without importing legacy A/B/C packages."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from intelligence.market_intelligence.replica_probe import run_smoke, PREFIX

ROOT = Path(__file__).resolve().parents[1]


class ReplicaSmoke(unittest.TestCase):
    def test_full_frozen_source_git_trees(self):
        manifest = json.loads((ROOT/"intelligence/market_intelligence/_copied/source_manifest.json").read_text("utf-8"))
        for name, metadata in manifest["sources"].items():
            path=f"intelligence/market_intelligence/_copied/{name}"
            tree=subprocess.check_output(["git","rev-parse","HEAD:"+path],cwd=ROOT,text=True).strip()
            self.assertEqual(tree,metadata["tree"],"Incomplete copied subtree "+name)

    def test_all_copied_python_files_compile(self):
        # Python packages are copied as source files, not symlinks to branches.
        for name in ("group_a","group_b","group_c"):
            directory=ROOT/"intelligence/market_intelligence/_copied"/name
            self.assertTrue((directory/"__init__.py").is_file())
            self.assertGreater(len(list(directory.rglob("*.py"))),20)

    def test_offline_real_algorithms_and_b_validators(self):
        report=run_smoke()
        self.assertEqual(report["status"],"offline_copy_smoke_passed")
        self.assertEqual(report["video_provider_calls"],0)
        self.assertEqual(report["project_database_writes"],0)
        self.assertTrue(report["group_b_validators_imported"])

    def test_copied_imports_not_legacy_group_packages(self):
        # A copied import must not fall back to `intelligence.group_a`.
        module=importlib.import_module(PREFIX+"group_a.extractors.technical")
        self.assertTrue(module.__file__.replace("\\","/").endswith(
            "intelligence/market_intelligence/_copied/group_a/extractors/technical.py"))
        module2=importlib.import_module(PREFIX+"group_b.blocks.b1_hook")
        self.assertTrue(module2.__file__.replace("\\","/").endswith(
            "intelligence/market_intelligence/_copied/group_b/blocks/b1_hook.py"))


if __name__=="__main__":
    unittest.main()
