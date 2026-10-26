"""VC-MOD-16: fail-closed static PR scope and release-gate test evidence."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts import collector_release_audit as audit


class AuditTests(TestCase):
    def test_only_collector_and_explicit_app_integration_paths_allowed(self):
        accepted=[
            ("A","intelligence/collector/worker.py"),
            ("A","intelligence/collector/sre_delivery.py"),
            ("A","intelligence/connectors/dtk.py"),
            ("A","intelligence/migrations/054_collector_planning.sql"),
            ("M","site/index.html"),
            ("M","app.py"),
            ("M",".github/workflows/tests.yml"),
        ]
        self.assertTrue(audit.verify_paths(accepted)["scope_clean"])

    def test_other_features_are_not_silently_included(self):
        data=audit.verify_paths([
            ("M","intelligence/relevance/service.py"),
            ("M","intelligence/qdp/http_api.py"),
            ("M",".github/workflows/ke-stage14-fast.yml"),
            ("M","intelligence/migrations/058_unrelated.sql"),
        ])
        self.assertFalse(data["scope_clean"])
        self.assertIn("intelligence/relevance/service.py",data["unapproved_paths"])
        self.assertIn("058_unrelated.sql",data["unapproved_migrations"])

    def test_any_deleted_or_renamed_parent_path_is_blocked(self):
        val=audit.verify_paths([("D","intelligence/collector/worker.py")])
        self.assertFalse(val["scope_clean"])
        self.assertEqual(val["deleted_or_renamed_paths"],
                         ["intelligence/collector/worker.py"])

    def _fixture(self,tmp,*,flag="0",missing=None):
        root=Path(tmp)
        for name in audit.REQUIRED_PATHS:
            if name==missing:continue
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text("test",encoding="utf-8")
        env=root/"examples/collector.env.example"
        env.parent.mkdir(parents=True,exist_ok=True)
        env.write_text("\n".join(name+"="+flag
                                 for name in audit._SAFE_FLAG_NAMES)+"\n",
                       encoding="utf-8")
        return root

    @staticmethod
    def _fakegit(*args):
        if args[:2]==("rev-parse","HEAD"):return "a"*40
        if args[:2]==("rev-parse","--verify"):return "b"*40
        if args[0]=="merge-base":return "b"*40
        if args[0]=="diff":
            return "\n".join([
                "A\tintelligence/collector/worker.py",
                "A\tintelligence/migrations/054_collector_planning.sql",
            ])
        if args[0]=="rev-list":return "0"
        return None

    def test_clean_static_package_pass_is_not_live_or_merge_ready(self):
        with TemporaryDirectory() as tmp:
            root=self._fixture(tmp)
            with patch.object(audit,"ROOT",root),patch.object(
                audit,"_git",side_effect=self._fakegit):
                result=audit.audit()
        self.assertTrue(result["static_source_gate_passed"])
        self.assertFalse(result["merge_ready"])
        self.assertFalse(result["exact_head_linux_pg17_ci_passed"])
        self.assertFalse(result["full_upstream_c9_sp_qv_db_e2e_verified"])
        self.assertEqual(result["behind_main_commits"],0)
        self.assertEqual(len(result["migration_files"]["sha256"]),4)
        self.assertEqual(result["external_provider_calls"],0)

    def test_behind_main_is_not_passed_as_merge_candidate(self):
        def outdated(*args):
            if args[0]=="rev-list":return "3"
            return self._fakegit(*args)
        with TemporaryDirectory() as tmp:
            root=self._fixture(tmp)
            with patch.object(audit,"ROOT",root),patch.object(
                 audit,"_git",side_effect=outdated):
                result=audit.audit()
        self.assertEqual(result["behind_main_commits"],3)
        self.assertFalse(result["static_source_gate_passed"])
        self.assertFalse(result["merge_ready"])

    def test_unavailable_base_does_not_become_success(self):
        with TemporaryDirectory() as tmp:
            root=self._fixture(tmp)
            with patch.object(audit,"ROOT",root),patch.object(
                audit,"_git",return_value=None):
                report=audit.audit()
        self.assertFalse(report["static_source_gate_passed"])
        self.assertFalse(report["merge_ready"])

    def test_missing_module_or_live_enabled_flags_block_static_audit(self):
        for opts in ({"missing":"intelligence/collector/worker.py"},
                     {"flag":"1"}):
            with self.subTest(opts=opts),TemporaryDirectory() as tmp:
                root=self._fixture(tmp,**opts)
                with patch.object(audit,"ROOT",root),patch.object(
                    audit,"_git",side_effect=self._fakegit):
                    result=audit.audit()
                self.assertFalse(result["static_source_gate_passed"])

    def test_env_secret_variable_values_are_never_returned(self):
        with TemporaryDirectory() as tmp:
            root=self._fixture(tmp)
            path=root/"examples/collector.env.example"
            path.write_text(path.read_text()+"CM_DTK_API_KEY=REAL_SECRET_123\n")
            with patch.object(audit,"ROOT",root),patch.object(
                 audit,"_git",side_effect=self._fakegit):
                report=audit.audit()
        self.assertFalse(report["no_credentials_in_example"])
        self.assertFalse(report["static_source_gate_passed"])
        self.assertNotIn("REAL_SECRET_123",str(report))


if __name__=="__main__":
    import unittest
    unittest.main()
