"""FIX-11-A: fail-closed source isolation audit for standalone RTB PR.

Use git's own merge base for changes against latest main. The audit does not
merge and cannot be used as proof of runtime/PG/provider readiness.

Example:
  python -m intelligence.taxonomy_builder.release_boundary --base origin/main
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OWNED_PREFIXES=(
    "intelligence/taxonomy_builder/",
    "docs/RTB_",
)
ALLOWED_SHARED={
    "app.py",
    "site/index.html",
    "site/projects.js",
    "site/taxonomy-workspace.css",
}
ALLOWED_NEW={
    ".github/workflows/rtb-module-contracts.yml",
    "site/taxonomy-deep-v2.js",
}
MIGRATION_RE=re.compile(r"intelligence/migrations/05[4-9]_tb_deep_v2_[a-z0-9_]+[.]sql$")
TEST_RE=re.compile(r"tests/test_taxonomy_[a-z0-9_]+[.](?:py|cjs)$")
EXPECTED_MIGRATIONS={
    "intelligence/migrations/054_tb_deep_v2_research_binding.sql",
    "intelligence/migrations/055_tb_deep_v2_budget_ledger.sql",
    "intelligence/migrations/056_tb_deep_v2_human_reviews.sql",
    "intelligence/migrations/057_tb_deep_v2_expansion_consent.sql",
    "intelligence/migrations/058_tb_deep_v2_search_readiness.sql",
    "intelligence/migrations/059_tb_deep_v2_publication_sre.sql",
}
EXPECTED_HOOKS={
    "app.py":(
        "from intelligence.taxonomy_builder.deep_v2_http import handle_get as rtb_v2_get",
        "from intelligence.taxonomy_builder.deep_v2_http import handle_post as rtb_v2_post",
        "if rtb_v2_get(self, url):",
        "if rtb_v2_post(self, route):",
        "'/taxonomy-deep-v2.js': 'taxonomy-deep-v2.js'",
    ),
    "site/index.html":(
        'id="projectDetailsFields"',
        'id="tbV2Panel"',
        'src="/taxonomy-deep-v2.js"',
    ),
    "site/projects.js":(
        "document.dispatchEvent(new CustomEvent('cm-taxonomy-deep-v2-project-open'",
    ),
    "site/taxonomy-workspace.css":(
        ".tb-v2-panel {",
        ".tb-v2-review-node {",
    ),
}

def allowed(path):
    return (
        path in ALLOWED_SHARED or path in ALLOWED_NEW or
        path.startswith(OWNED_PREFIXES) or
        bool(MIGRATION_RE.fullmatch(path)) or
        bool(TEST_RE.fullmatch(path))
    )

def _subsequence(original,modified):
    """Every legacy line must stay byte-identical and in the same order."""
    iterator=iter(modified)
    return all(any(line==candidate for candidate in iterator)
               for line in original)

def _canonical_shared(path,source):
    lines=source.splitlines()
    if path=="app.py":
        # Exactly one extension to the existing legacy static map is allowed.
        route=", '/taxonomy-deep-v2.js': 'taxonomy-deep-v2.js'"
        lines=[line.replace(route,"") if "files = {" in line else line
               for line in lines]
    return lines

def check_shared(old,new):
    issues=[]
    for path,requirements in EXPECTED_HOOKS.items():
        current=new.get(path)
        previous=old.get(path)
        if not isinstance(current,str) or not isinstance(previous,str):
            issues.append("shared_file_unavailable:"+path)
            continue
        for text in requirements:
            if current.count(text)!=1:
                issues.append("hook_absent_or_duplicate:"+path+":"+text[:50])
        # A file may contain existing legacy hooks, but every original line
        # must survive without changing content. app.py gets one known static
        # map extension; the new B9 routes are additions only.
        old_lines=_canonical_shared(path,previous)
        new_lines=_canonical_shared(path,current)
        if not _subsequence(old_lines,new_lines):
            issues.append("legacy_shared_lines_changed_or_removed:"+path)
        if path=="app.py" and current.count(
            "'/taxonomy-deep-v2.js': 'taxonomy-deep-v2.js'")!=1:
            issues.append("deep_v2_static_asset_not_mounted_exactly_once")
    return issues

def inspect(changed,*,main_shared,branch_shared,branch_files=None):
    """Audit supplied diff (testable without git or external resources)."""
    changed=list(changed)
    violations=[]
    if len(changed)!=len(set(changed)):
        violations.append("duplicate_changed_path")
    for path in changed:
        if not isinstance(path,str) or path.startswith("/") or ".." in Path(path).parts:
            violations.append("invalid_change_path:"+str(path))
        elif not allowed(path):
            violations.append("foreign_module_or_unreviewed_file:"+path)
    missing=EXPECTED_MIGRATIONS-set(changed)
    if missing:
        violations.extend("missing_standalone_migration:"+p for p in sorted(missing))
    if branch_files is not None:
        missing_files={*EXPECTED_MIGRATIONS,*EXPECTED_HOOKS,
                       "intelligence/taxonomy_builder/qa_matrix.py",
                       "intelligence/taxonomy_builder/migration_audit.py",
                       "site/taxonomy-deep-v2.js"}-set(branch_files)
        violations.extend("standalone_file_missing:"+p
                          for p in sorted(missing_files))
    violations.extend(check_shared(main_shared,branch_shared))
    return {
        "schema_version":"rtb-release-boundary/v1",
        "changed_count":len(changed),
        "owned_change_count":sum(path.startswith(OWNED_PREFIXES) for path in changed),
        "migration_count":sum(bool(MIGRATION_RE.fullmatch(path)) for path in changed),
        "shared_integration_count":sum(path in ALLOWED_SHARED for path in changed),
        "foreign_change_count":sum(not allowed(path) for path in changed),
        "violations":violations,
        "source_isolation_passed":not violations,
        "ci_success_verified":False,
        "postgres_verified":False,
        "release_ready":False,
    }

def _git(*args):
    result=subprocess.run(["git",*args],cwd=ROOT,check=True,
                          capture_output=True,text=True)
    return result.stdout

def inspect_git(*,base="origin/main",head="HEAD"):
    # In Actions pull_request checkout, HEAD may be a synthetic merge SHA.
    # Compare the actual user PR head to main, not the temporary merge commit.
    for ref in (base,head):
        if not re.fullmatch(r"[A-Za-z0-9_./-]+",ref) or ref.startswith("-"):
            raise ValueError("invalid_git_ref")
    changed=_git("diff","--name-only",base+"..."+head).splitlines()
    old={path:_git("show",base+":"+path) for path in EXPECTED_HOOKS}
    new={path:_git("show",head+":"+path) for path in EXPECTED_HOOKS}
    files=set(_git("ls-tree","-r","--name-only",head).splitlines())
    report=inspect(changed,main_shared=old,branch_shared=new,branch_files=files)
    report["base_ref"]=base
    report["base_sha"]=_git("rev-parse",base).strip()
    report["head_sha"]=_git("rev-parse",head).strip()
    report["git_merge_base"]=_git("merge-base",base,head).strip()
    report["behind_main"]=int(_git("rev-list","--count",head+".."+base).strip())
    if report["behind_main"]:
        report["violations"].append("behind_latest_main")
        report["source_isolation_passed"]=False
    return report

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--base",default="origin/main")
    parser.add_argument("--head",default="HEAD",
                        help="Actual PR branch head SHA, not synthetic GitHub merge commit")
    parser.add_argument("--json-out",type=Path)
    args=parser.parse_args(argv)
    try:
        report=inspect_git(base=args.base,head=args.head)
    except (OSError,ValueError,subprocess.CalledProcessError) as exc:
        report={"schema_version":"rtb-release-boundary/v1","source_isolation_passed":False,
                "violations":["cannot_audit_current_diff:"+str(exc)],
                "release_ready":False}
    if args.json_out:
        args.json_out.parent.mkdir(parents=True,exist_ok=True)
        args.json_out.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",
                                 encoding="utf-8")
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return 0 if report["source_isolation_passed"] else 1

if __name__=="__main__":
    raise SystemExit(main())
