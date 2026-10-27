"""FIX-10/11: reproducible LOCAL handoff, without GitHub Actions or provider calls.

Run from a checkout of feature/rtb-module-v2:
    python -m intelligence.taxonomy_builder.local_release_handoff
    python -m intelligence.taxonomy_builder.local_release_handoff --run-basic

This tool audits SOURCE/BOUNDARIES only. It never attests PostgreSQL, full
browser E2E, CI, remote state or permission to merge.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from intelligence.taxonomy_builder import migration_audit, qa_matrix, release_boundary

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_FILES = (
    "intelligence/taxonomy_builder/research_input.py",
    "intelligence/taxonomy_builder/deep_v2_http.py",
    "intelligence/taxonomy_builder/deep_v2_publication.py",
    "intelligence/taxonomy_builder/publication_export_v2.py",
    "intelligence/taxonomy_builder/qa_matrix.py",
    "intelligence/taxonomy_builder/migration_audit.py",
    "intelligence/taxonomy_builder/release_boundary.py",
    "intelligence/taxonomy_builder/release_ci_evidence.py",
    "intelligence/taxonomy_builder/local_release_handoff.py",
    "site/taxonomy-deep-v2.js",
    "docs/RTB_FULL_MODULE_IMPLEMENTATION_PLAN_RU.md",
    "docs/RTB_IMPLEMENTATION_PROGRESS.md",
    "docs/RTB_FIX10_T01_T36_QA_RU.md",
    "docs/RTB_FIX11_RELEASE_QUALIFICATION_RU.md",
    "docs/RTB_FIX10_FIX11_LOCAL_HANDOFF_RU.md",
) + tuple(sorted(release_boundary.EXPECTED_MIGRATIONS))
BASIC_SUITES = (
    "test_taxonomy_v2_qa_matrix.py",
    "test_taxonomy_release_boundary.py",
    "test_taxonomy_release_ci_evidence.py",
    "test_taxonomy_deep_v2_migration_audit.py",
    "test_taxonomy_local_release_handoff.py",
)


def source_fingerprints(root=ROOT, paths=REQUIRED_FILES):
    """Deterministic SHA-256 of exact source bytes, without network or DB."""
    hashes = {}
    problems = []
    for rel in paths:
        candidate = (root / rel).resolve()
        if not candidate.is_relative_to(root.resolve()):
            problems.append("outside_repository:" + rel)
        elif not candidate.is_file():
            problems.append("missing_source:" + rel)
        else:
            hashes[rel] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return hashes, problems


def basic_tests(root=ROOT, runner=None):
    """Explicit small offline allowlist; no paid E2E or PostgreSQL."""
    execute = runner or subprocess.run
    results = []
    for suite in BASIC_SUITES:
        command = ["python", "-m", "unittest", "discover",
                   "-s", "tests", "-p", suite, "-v"]
        result = execute(command, cwd=root, check=False)
        results.append({"suite": suite, "exit_code": result.returncode})
    return results


def snapshot(*, root=ROOT, git_check=True, run_basic=False, base="origin/main",
             head="HEAD", fingerprints=None, qa_report=None,
             migration_report=None, boundary_report=None, tests=None):
    """A code handoff report; deferred verification is NOT PASS."""
    hashes, problems = fingerprints if fingerprints is not None else source_fingerprints(root)
    qa = qa_report if qa_report is not None else qa_matrix.audit()
    migrations = migration_report if migration_report is not None else migration_audit.inspect_files()
    boundary = boundary_report
    if git_check and boundary is None:
        try:
            boundary = release_boundary.inspect_git(base=base, head=head)
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            boundary = {"source_isolation_passed": False,
                        "violations": ["git_source_check_unavailable:" + str(exc)]}
    issues = list(problems)
    if qa.get("case_count") != 36 or not qa.get("reference_check_passed"):
        issues.append("t01_t36_source_registry_invalid")
    if qa.get("partial"):
        issues.append("partial_rtb_test_coverage:" + ",".join(qa["partial"]))
    if set(qa.get("external_gaps", ())) != {"T28", "T29"}:
        issues.append("unexpected_downstream_scope")
    if not migrations.get("static_valid"):
        issues.append("rtb_migration_source_invalid")
    if git_check and not boundary.get("source_isolation_passed"):
        issues.append("release_boundary_invalid")
    results = tests if tests is not None else (basic_tests(root) if run_basic else [])
    if any(item["exit_code"] != 0 for item in results):
        issues.append("basic_local_tests_failed")
    return {
        "schema_version": "rtb-local-handoff/v1",
        "base_ref": base if git_check else None,
        "head_ref": head if git_check else None,
        "head_sha": boundary.get("head_sha") if boundary else None,
        "base_sha": boundary.get("base_sha") if boundary else None,
        "code_handoff_valid": not issues and git_check,
        "source_only_valid": not issues,
        "source_hashes_sha256": hashes,
        "t01_t36": {"mapped": qa.get("mapped"),
                    "external_gaps": qa.get("external_gaps"),
                    "reference_check_passed": qa.get("reference_check_passed")},
        "migration_source_valid": migrations.get("static_valid"),
        "source_isolation": boundary if git_check else "NOT_CHECKED",
        "basic_tests": results,
        "local_pg17_qa": "DEFERRED_TO_OWNER_PC",
        "local_browser_qa": "DEFERRED_TO_OWNER_PC",
        "external_search_planner_t28_t29": "EXTERNAL_NOT_VERIFIED",
        "github_ci": "NOT_REQUIRED_FOR_CODE_HANDOFF",
        "paid_provider_invoked": False,
        "merge_authorized": False,
        "release_ready": False,
        "issues": issues,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--without-git", action="store_true",
                        help="source-only inventory, NOT a merge qualification")
    parser.add_argument("--run-basic", action="store_true",
                        help="run small offline stdlib unit test allowlist")
    parser.add_argument("--json-out", type=Path,
                        default=Path("reports/rtb_fix10_fix11_local_handoff.json"))
    args = parser.parse_args(argv)
    result = snapshot(git_check=not args.without_git, run_basic=args.run_basic,
                      base=args.base, head=args.head)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    print(json.dumps({key: val for key, val in result.items()
                      if key != "source_hashes_sha256"},
                     indent=2, ensure_ascii=False))
    return 0 if result["code_handoff_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
