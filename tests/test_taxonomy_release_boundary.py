"""FIX-11-A source boundary audit; pure fake git-diff fixtures, no networking."""
from __future__ import annotations
from copy import deepcopy
import unittest
from unittest.mock import patch

from intelligence.taxonomy_builder import release_boundary as rel


def _shared():
    baseline={
        "app.py": "class Handler:\n"
                  "    def do_GET(self):\n"
                  "        files = {'/': 'index.html', '/taxonomy-deep.js': 'taxonomy-deep.js'}\n"
                  "        if url.path == '/api/state':\n"
                  "            return None\n",
        "site/index.html": '<div id="projectDetailsFields"></div>\n'
                           '<script defer src="/taxonomy-deep.js"></script>\n',
        "site/projects.js": "function renderDetails(project) {\n"
                            "    show('details');\n}\n",
        "site/taxonomy-workspace.css": ".tb-studio { display: block; }\n",
    }
    branch={
        "app.py": "class Handler:\n"
                  "    def do_GET(self):\n"
                  "        from intelligence.taxonomy_builder.deep_v2_http import handle_get as rtb_v2_get\n"
                  "        if rtb_v2_get(self, url):\n"
                  "            return None\n"
                  "        files = {'/': 'index.html', '/taxonomy-deep.js': 'taxonomy-deep.js', '/taxonomy-deep-v2.js': 'taxonomy-deep-v2.js'}\n"
                  "        from intelligence.taxonomy_builder.deep_v2_http import handle_post as rtb_v2_post\n"
                  "        if rtb_v2_post(self, route):\n"
                  "            return None\n"
                  "        if url.path == '/api/state':\n"
                  "            return None\n",
        "site/index.html": '<div id="projectDetailsFields"></div>\n'
                           '<details id="tbV2Panel">B9</details>\n'
                           '<script defer src="/taxonomy-deep-v2.js"></script>\n'
                           '<script defer src="/taxonomy-deep.js"></script>\n',
        "site/projects.js": "function renderDetails(project) {\n"
                            "    document.dispatchEvent(new CustomEvent('cm-taxonomy-deep-v2-project-open',{}));\n"
                            "    show('details');\n}\n",
        "site/taxonomy-workspace.css":
            ".tb-studio { display: block; }\n"
            ".tb-v2-panel { display:block; }\n"
            ".tb-v2-review-node { border: 1px solid; }\n",
    }
    return baseline,branch


def _changed():
    return [
        *sorted(rel.EXPECTED_MIGRATIONS),
        *sorted(rel.ALLOWED_SHARED),
        "site/taxonomy-deep-v2.js",
        ".github/workflows/rtb-module-contracts.yml",
        "intelligence/taxonomy_builder/research_input.py",
        "intelligence/taxonomy_builder/qa_matrix.py",
        "intelligence/taxonomy_builder/migration_audit.py",
        "intelligence/taxonomy_builder/release_boundary.py",
        "tests/test_taxonomy_deep_v2_ui.cjs",
        "docs/RTB_IMPLEMENTATION_PROGRESS.md",
    ]


class RTBReleaseBoundaryTest(unittest.TestCase):
    def test_independently_integrable_allowlisted_change_set(self):
        old,new=_shared()
        files=set(_changed())
        report=rel.inspect(_changed(),main_shared=old,
                           branch_shared=new,branch_files=files)
        self.assertTrue(report["source_isolation_passed"],report["violations"])
        self.assertEqual(report["migration_count"],6)
        self.assertEqual(report["shared_integration_count"],4)
        self.assertEqual(report["foreign_change_count"],0)
        self.assertFalse(report["release_ready"])
        self.assertFalse(report["ci_success_verified"])

    def test_foreign_sp_sre_group_edits_are_hard_failures(self):
        old,new=_shared()
        for path in (
            "intelligence/search_planner/query_builder.py",
            "intelligence/relevance/sre_builder.py",
            "intelligence/knowledge_extraction/knowledge_snapshot.py",
            "intelligence/migrations/060_new_unknown.sql",
            "site/other-team.js",
            ".github/workflows/search-planner.yml",
        ):
            with self.subTest(path=path):
                out=rel.inspect(_changed()+[path],
                                main_shared=old,branch_shared=new)
                self.assertFalse(out["source_isolation_passed"])
                self.assertIn("foreign_module_or_unreviewed_file:"+path,
                              out["violations"])

    def test_replaced_old_flat_deep_dashboard_route_fails(self):
        old,new=_shared()
        bad=deepcopy(new)
        bad["app.py"]=bad["app.py"].replace(
            "        if url.path == '/api/state':\n", "")
        report=rel.inspect(_changed(),main_shared=old,branch_shared=bad)
        self.assertIn("legacy_shared_lines_changed_or_removed:app.py",
                      report["violations"])

    def test_deleted_old_legacy_script_and_css_rule_fail(self):
        old,new=_shared()
        bad=deepcopy(new)
        bad["site/index.html"]=bad["site/index.html"].replace(
            '<script defer src="/taxonomy-deep.js"></script>\n',"")
        bad["site/taxonomy-workspace.css"]=bad["site/taxonomy-workspace.css"].replace(
            ".tb-studio { display: block; }\n","")
        report=rel.inspect(_changed(),main_shared=old,branch_shared=bad)
        self.assertIn("legacy_shared_lines_changed_or_removed:site/index.html",
                      report["violations"])
        self.assertIn("legacy_shared_lines_changed_or_removed:site/taxonomy-workspace.css",
                      report["violations"])

    def test_duplicate_paid_hook_and_missing_guard_path_fail(self):
        old,new=_shared()
        bad=deepcopy(new)
        bad["app.py"]+="        if rtb_v2_post(self, route):\n"
        paths=set(_changed())
        paths.discard("intelligence/migrations/054_tb_deep_v2_research_binding.sql")
        report=rel.inspect(_changed(),main_shared=old,branch_shared=bad,
                           branch_files=paths)
        self.assertTrue(any(x.startswith("hook_absent_or_duplicate:app.py")
                            for x in report["violations"]))
        self.assertIn(
            "standalone_file_missing:intelligence/migrations/054_tb_deep_v2_research_binding.sql",
            report["violations"])

    def test_pr_source_sha_not_synthetic_merge_is_checked_against_main(self):
        old,new=_shared()
        head="a"*40
        base="origin/main"
        commands=[]
        def fake_git(*args):
            commands.append(args)
            if args[:2]==("diff","--name-only"):
                self.assertEqual(args[2],base+"..."+head)
                return "\n".join(_changed())+"\n"
            if args[0]=="show":
                ref,path=args[1].split(":",1)
                return (old if ref==base else new)[path]
            if args[0]=="ls-tree":
                self.assertEqual(args[-1],head)
                return "\n".join(_changed())+"\n"
            if args[0]=="rev-parse":
                return (("b"*40) if args[1]==base else head)+"\n"
            if args[0]=="merge-base":
                return "c"*40+"\n"
            if args[0]=="rev-list":
                self.assertEqual(args[-1],head+".."+base)
                return "2\n"
            raise AssertionError("Unexpected git command "+str(args))
        with patch.object(rel,"_git",side_effect=fake_git):
            report=rel.inspect_git(base=base,head=head)
        self.assertEqual(report["head_sha"],head)
        self.assertEqual(report["behind_main"],2)
        self.assertIn("behind_latest_main",report["violations"])
        self.assertFalse(report["source_isolation_passed"])

    def test_exact_allowed_paths_excludes_traversal_and_wrong_migrations(self):
        old,new=_shared()
        result=rel.inspect(_changed()+[
            "../site/index.html",
            "intelligence/migrations/054_replace_legacy.sql",
        ],main_shared=old,branch_shared=new)
        self.assertFalse(result["source_isolation_passed"])
        self.assertTrue(any("invalid_change_path" in x for x in result["violations"]))
        self.assertTrue(any("foreign_module_or_unreviewed_file" in x
                            for x in result["violations"]))

if __name__=="__main__":
    unittest.main()
