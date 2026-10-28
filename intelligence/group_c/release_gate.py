"""GC-MOD-32: fail-closed, read-only final release/audit gate.

Offline preflight never merges, modifies migrations, calls providers, runs CI,
reads production secrets or treats test doubles as signed peer approvals.
The published Draft PR remains DRAFT/BLOCKED until independently verified
external release evidence and combined PostgreSQL17 migrations exist.

From a *clean* local checkout with main available:
 python -m intelligence.group_c.release_gate --repo-root . \
   --base-ref main --report gc32-release-audit.json --audit-only
Exit 0 in --audit-only mode means the audit ran, NOT merge-ready.
Without --audit-only, current known blockers make exit 2.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any,Mapping

VERSION="group-c-final-release-gate/v1"
MIGRATIONS=tuple(
    f"intelligence/migrations/{i:03d}_"+name+".sql"
    for i,name in enumerate((
        "group_c_runtime","group_c_cohort_freezes","group_c_human_reviews",
        "group_c_config_activation","group_c_calculation_receipts",
        "group_c_quality_reports","group_c_retention_revocation",
        "group_c_monitoring_recovery","group_c_performance_indexes",
    ),start=56)
)
BASELINE_PROTECTED=(
    "intelligence/migrations/003_performance_c.sql",
    "intelligence/migrations/004_performance_c_integrity.sql",
)
PEER_FILE="docs/GROUP_C_STAGE32_PEER_AUDIT.json"
REQUIRED_RUNBOOKS=(
    "docs/GROUP_C_IMPLEMENTATION_TRACKER.md",
    "docs/GROUP_C_INTEGRATION_CONTRACT.md",
    "docs/GROUP_C_STAGE29_PERFORMANCE_QA.md",
    "docs/GROUP_C_STAGE30_DECIMAL_MATH_GOLDSET.md",
    "docs/GROUP_C_STAGE31_CROSSPLATFORM_E2E.md",
    "docs/GROUP_C_STAGE32_RELEASE_GATE.md",
    ".github/workflows/group-c-module.yml",
)
PREFIX=re.compile(r"intelligence/migrations/([0-9]{3})_[\w-]+\.sql\Z")
HEX_SHA=re.compile(r"[0-9a-f]{40}\Z")

class ReleaseAuditError(RuntimeError):
    """Invalid or ambiguous release audit input; never silently pass."""


def _git(repo:Path,*args:str)->str:
    try:
        p=subprocess.run(
            ["git","-C",str(repo),*args],capture_output=True,
            text=True,check=True,timeout=15,
        )
        return p.stdout.strip()
    except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
        raise ReleaseAuditError("Required local Git ref/check unavailable; no release claim") from exc


def allowed_change(path:str)->bool:
    """Review only the actual module ownership path family; no peer edits."""
    if type(path) is not str or not path or ".." in Path(path).parts:
        return False
    if path.startswith("intelligence/group_c/"):
        return True
    if path in MIGRATIONS or path in (
        ".github/workflows/group-c-module.yml",
        "tests/group_c_independent_oracle.py",
        "tests/test_performance_c_schema.py",
        "tests/fixtures/group_c_math_gold_v1.json",
    ):
        return True
    if path.startswith("tests/test_group_c_") and path.endswith((".py",".cjs")):
        return True
    if path.startswith("docs/GROUP_C_") and path.endswith((".md",".txt",".json")):
        return True
    if path in ("site/group-c-studio.html","site/group-c-studio.css",
                "site/group-c-studio.js"):
        return True
    return False


def check_changed_paths(changed:Mapping[str,str])->tuple[str,...]:
    """Only changes owned by this module are permitted."""
    if not isinstance(changed,Mapping):
        raise ReleaseAuditError("Diff path mapping required")
    forbidden=[]
    for path,status in changed.items():
        if not allowed_change(path) or type(status) is not str or not status:
            forbidden.append(path)
        elif path in BASELINE_PROTECTED:
            forbidden.append(path)
    return tuple(sorted(forbidden))


def inspect_migration_inventory(data:Mapping[str,Any])->dict:
    """Never confuse overlapping numeric prefixes with identical file paths.

    Real merge hazards include lexicographic combined migration order,
    dependencies across schema owners, and non-CONCURRENTLY index lock budget.
    Neither prefix overlap nor absence of identical names proves a safe merge.
    """
    if not isinstance(data,Mapping) or data.get("schema_version")!="group-c-peer-migrations/v1":
        raise ReleaseAuditError("Known peer migration manifest version required")
    group=data.get("group_c")
    peers=data.get("peers")
    if (not isinstance(group,Mapping)
            or not isinstance(group.get("migrations"),list)
            or not isinstance(peers,list) or len(peers)<5):
        raise ReleaseAuditError("Complete reviewed peer migration inventories required")
    owned=group["migrations"]
    if tuple(owned)!=MIGRATIONS:
        raise ReleaseAuditError("GC56-64 inventory changed; re-review migration plan")
    rows=[]
    for item in [group,*peers]:
        name=item.get("owner","Group C") if item is not group else "Group C"
        paths=item.get("migrations")
        if not isinstance(name,str) or not isinstance(paths,list) or not paths:
            raise ReleaseAuditError("Peer owner and migration files required")
        for path in paths:
            if type(path) is not str or PREFIX.fullmatch(path) is None:
                raise ReleaseAuditError("Invalid migration path in peer inventory")
            rows.append((path,name,int(PREFIX.fullmatch(path).group(1))))
    names=defaultdict(set)
    numbers=defaultdict(set)
    for path,owner,n in rows:
        names[path].add(owner)
        numbers[n].add(owner)
    dup=tuple(sorted(path for path,owners in names.items() if len(owners)>1))
    overlapping=tuple(sorted(n for n,owners in numbers.items()
                             if "Group C" in owners and len(owners)>1))
    combined=tuple(sorted(names.keys()))
    return {
        "exact_filename_collisions":dup,
        "shared_group_c_numeric_prefixes":overlapping,
        "combined_migration_count":len(combined),
        "combined_order_preview":combined,
        "merge_order_not_verified":True,
        "combined_fresh_pg17_not_verified":True,
        "combined_upgrade_pg17_not_verified":True,
        "requires_separate_db_sre_lock_budget_review":True,
    }


def _load_manifest(repo:Path)->dict:
    path=repo/PEER_FILE
    try:
        obj=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError) as exc:
        raise ReleaseAuditError("Required pinned GitHub peer inventory missing/invalid") from exc
    if not isinstance(obj,dict):
        raise ReleaseAuditError("Invalid peer inventory")
    return obj


def _diff_paths(repo:Path,base_ref:str)->dict:
    if not isinstance(base_ref,str) or not re.fullmatch(r"[A-Za-z0-9._/\-]+",base_ref):
        raise ReleaseAuditError("Canonical explicit local baseline required")
    # No network access, branch checkout, git checkout, fetch or merge here.
    lines=_git(repo,"diff","--name-status",base_ref+"...HEAD","--").splitlines()
    result={}
    for line in lines:
        fields=line.split("\t")
        if len(fields)<2:
            raise ReleaseAuditError("Unexpected git diff name-status format")
        action=fields[0]
        # For rename/copy Git emits source and destination. Validate BOTH.
        if not re.fullmatch(r"[ACDMRTUXB][0-9]*",action):
            raise ReleaseAuditError("Unexpected git diff action")
        for path in fields[1:]:
            result[path]=action
    return result


def inspect_repo(repo:Path,base_ref:str)->dict:
    root=Path(repo).resolve()
    if not (root/"intelligence"/"migrate.py").is_file():
        raise ReleaseAuditError("Content Machine source checkout required")
    sha=_git(root,"rev-parse","HEAD")
    if HEX_SHA.fullmatch(sha) is None:
        raise ReleaseAuditError("Canonical 40-char HEAD SHA required")
    if _git(root,"status","--porcelain"):
        raise ReleaseAuditError("Uncommitted changes: no exact-SHA release audit")
    changed=_diff_paths(root,base_ref)
    protected={}
    for path in BASELINE_PROTECTED:
        # Strict Git blob object identity, including final newlines and bytes.
        # git show with strip loses trailing bytes and is insufficient here.
        base_blob=_git(root,"rev-parse",base_ref+":"+path)
        local_blob=_git(root,"hash-object",str(root/path))
        if not re.fullmatch(r"[0-9a-f]{40}",base_blob) or base_blob!=local_blob:
            raise ReleaseAuditError("Original immutable baseline migration changed: "+path)
        protected[path]=hashlib.sha256((root/path).read_bytes()).hexdigest()
    missing=[p for p in (*MIGRATIONS,*REQUIRED_RUNBOOKS,PEER_FILE)
             if not (root/p).is_file()]
    forbidden=check_changed_paths(changed)
    if missing or forbidden:
        raise ReleaseAuditError("Missing required release files or out-of-bound changes")
    return {
        "git_head_sha":sha,
        "base_ref":base_ref,
        "changed_file_count":len(changed),
        "modified_paths":tuple(sorted(changed)),
        "preserved_baseline_migration_sha256":protected,
        "gc_migrations":MIGRATIONS,
        "allowed_scope_only":True,
        "clean_checkout":True,
    }


def evaluate_release(repo_facts:Mapping[str,Any],
                     peer_manifest:Mapping[str,Any],
                     *,local_e2e:Mapping[str,Any]|None=None)->dict:
    """Fail closed: test doubles and self-reported evidence never qualify peer IAM.

    An external release controller must independently approve all multi-branch
    blockers; this local gate only detects regression and collects evidence.
    """
    if not isinstance(repo_facts,Mapping) or not isinstance(peer_manifest,Mapping):
        raise ReleaseAuditError("Verified local git facts and peer audit required")
    sha=repo_facts.get("git_head_sha")
    if type(sha) is not str or HEX_SHA.fullmatch(sha) is None:
        raise ReleaseAuditError("Exact Git SHA required")
    migration=inspect_migration_inventory(peer_manifest)
    blockers=[]
    if migration["exact_filename_collisions"]:
        blockers.append("migration_exact_filename_conflict")
    if migration["shared_group_c_numeric_prefixes"]:
        blockers.append("migration_prefix_overlap_combined_order_unverified")
    if migration["combined_fresh_pg17_not_verified"]:
        blockers.append("merged_migrations_fresh_pg17_unverified")
    if migration["combined_upgrade_pg17_not_verified"]:
        blockers.append("merged_migrations_upgrade_pg17_unverified")
    if local_e2e is not None:
        if not isinstance(local_e2e,Mapping):
            raise ReleaseAuditError("Local evidence must be a JSON object")
        if (local_e2e.get("git_sha")!=sha or
                local_e2e.get("local_synthetic_e2e_passed") is not True or
                local_e2e.get("tests_skipped")!=0 or
                local_e2e.get("tests_failed")!=0 or
                local_e2e.get("tests_errored")!=0):
            blockers.append("local_exact_sha_e2e_not_passed")
        # Even successful local synthetic tests never certify real peers.
        blockers.append("synthetic_e2e_is_not_production_attestation")
    else:
        blockers.append("local_exact_sha_e2e_missing")
    events=peer_manifest.get("runtime_blockers",[])
    if not isinstance(events,list) or not events:
        raise ReleaseAuditError("Missing external dependency blockers is not approval")
    for issue in events:
        if not isinstance(issue,Mapping) or not issue.get("code"):
            raise ReleaseAuditError("Invalid peer blocker record")
        blockers.append(str(issue["code"]))
    blockers.append("cross_branch_combined_pg17_order_and_ddl_locks_unverified")
    blockers.append("windows_real_pg17_exact_sha_unverified")
    blockers.append("branch_draft_pr37_must_remain_draft")
    blockers=tuple(dict.fromkeys(blockers))
    return {
        "schema_version":VERSION,
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "group_c_git_sha":sha,
        "base_ref":repo_facts.get("base_ref"),
        "module_owned_file_count":repo_facts.get("changed_file_count"),
        "previous_migrations_protected":repo_facts.get("preserved_baseline_migration_sha256"),
        "scope_protected":repo_facts.get("allowed_scope_only") is True,
        "migration_inventory":migration,
        "peer_snapshot_observed_date":peer_manifest.get("observed_utc"),
        "peer_heads_at_snapshot":{p["branch"]:p["head_sha"]
                                 for p in peer_manifest["peers"]},
        "local_synthetic_e2e_evidence_observed":local_e2e is not None,
        "real_qdp_peer_attested":False,
        "real_unified_analytics_peer_attested":False,
        "real_iam_attested":False,
        "cross_platform_pg17_attested":False,
        "github_ci_exact_head_green_attested":False,
        "merge_authorized":False,
        "merge_ready":False,
        "readiness":"BLOCKED_EXTERNAL_AND_QA",
        "blockers":blockers,
        "does_not_change_pr_or_merge":True,
    }


def main()->int:
    ap=argparse.ArgumentParser(description="Read-only Group C final release audit")
    ap.add_argument("--repo-root",default=".")
    ap.add_argument("--base-ref",default="main")
    ap.add_argument("--e2e-evidence",type=Path)
    ap.add_argument("--report",required=True,type=Path)
    ap.add_argument("--audit-only",action="store_true",
                    help="0=report produced, NEVER implies release passed")
    args=ap.parse_args()
    try:
        repo=Path(args.repo_root).resolve()
        facts=inspect_repo(repo,args.base_ref)
        manifest=_load_manifest(repo)
        local=None
        if args.e2e_evidence:
            local=json.loads(args.e2e_evidence.read_text(encoding="utf-8"))
        report=evaluate_release(facts,manifest,local_e2e=local)
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(
            json.dumps(report,indent=2,ensure_ascii=False,
                       sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
    except (ReleaseAuditError,OSError,ValueError) as exc:
        print("GC32 audit cannot certify release:",str(exc),file=sys.stderr)
        return 3
    print("GC32 report written:",args.report,
          "merge_ready=",report["merge_ready"],
          "blocker_count=",len(report["blockers"]))
    return 0 if args.audit_only else (0 if report["merge_ready"] else 2)


if __name__=="__main__":
    sys.exit(main())
