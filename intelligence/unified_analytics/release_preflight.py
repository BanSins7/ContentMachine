"""UA-MOD-32: read-only, fail-closed UAE release/merge preflight.

This CLI NEVER fetches/remotes, merges, cherry-picks, checks out, stages,
updates any branch, mutates PostgreSQL, starts providers, or asserts tests
passed. It compares *locally available* git refs and their actual changed
filenames from main, current protected UA085 gate, branch lineage and
shared-surface conflicts, leaving all executable approval gates BLOCKED.

Usage (from checked-out ContentMachine repository):
  python -m intelligence.unified_analytics.release_preflight

Results are local audit evidence, NOT CI approvals or a release certificate.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable

SCHEMA="uae-release-preflight/v1"
ROOT=Path(__file__).resolve().parents[2]
BASE_REF="main"
UAE_REF="feature/unified-analytics-full-module"
PEERS=(
    "feature/group-a-full-module",
    "feature/group-b-full-module",
    "feature/group-c-full-module",
    "feature/sre-production-v1-20261023",
    "feature/patterns-insights-full-module",
)
SHARED_SURFACES=frozenset(("app.py","site/index.html","site/projects.js"))
UAE_EXACT=frozenset((
    "app.py","site/index.html","site/projects.js",
    ".github/workflows/unified-analytics-module.yml",
    "docs/UA_LEGACY_PORT_MATRIX.md",
))
MIGRATION=re.compile(r"intelligence/migrations/(08[0-9]|090)_unified_analytics_[a-z0-9_]+\.sql\Z")
MIGRATION_ANY=re.compile(r"intelligence/migrations/(\d{3})_[A-Za-z0-9_]+\.sql\Z")
SHA=re.compile(r"[0-9a-f]{40}\Z")
MAX_FILES=1000
GUARD="intelligence/migrations/085_unified_analytics_transaction_guards.sql"
NO_QUALIFIED="IF NEW.status = 'qualified' THEN"
NO_COMPLETE="IF NEW.checked IS TRUE OR NEW.is_current IS TRUE"
FIXED_BLOCKERS=(
    "independently_approved_peer_ports_not_verified",
    "production_positive_qualified_e2e_not_verified",
    "disposable_pg17_regression_not_verified",
    "windows_linux_execution_not_verified",
    "10k_50k_qualified_throughput_not_verified",
    "source_and_downstream_erasure_not_complete",
    "operator_canary_rollback_signoff_missing",
)


class PreflightError(Exception):
    def __init__(self,code:str):
        self.code=code
        super().__init__(code)


def allowed_uae_path(path:str)->bool:
    if type(path) is not str or not path or "\n" in path:
        return False
    if path in UAE_EXACT:
        return True
    if path.startswith("intelligence/unified_analytics/"):
        return path.endswith(".py")
    if path.startswith("tests/test_unified_analytics_"):
        return path.endswith(".py")
    if path.startswith("tests/node/test_unified_analytics_"):
        return path.endswith(".cjs")
    if path.startswith("docs/UNIFIED_ANALYTICS_"):
        return path.endswith((".md",".txt"))
    if path.startswith("site/unified-analytics-studio."):
        return path.endswith((".js",".css"))
    return MIGRATION.fullmatch(path) is not None


def migration_id(path:str)->str|None:
    match=MIGRATION_ANY.fullmatch(path) if type(path) is str else None
    return match.group(1) if match else None


def release_baseline_gate(guard_text:str)->list[str]:
    if type(guard_text) is not str or not guard_text:
        return ["unapproved_gate_unreadable"]
    missing=[]
    if NO_QUALIFIED not in guard_text:
        missing.append("qualified_approval_migration_requires_separate_review")
    if NO_COMPLETE not in guard_text:
        missing.append("complete_evidence_proof_requires_separate_review")
    return missing


def _git(root:Path,*args:str)->str:
    """Strict argument-only git invocation: no shell and no raw stderr."""
    try:
        result=subprocess.run(
            ["git","-C",str(root),*args],
            capture_output=True,text=True,check=False,
            timeout=12,
        )
    except (OSError,ValueError,subprocess.TimeoutExpired):
        raise PreflightError("git_unavailable") from None
    if result.returncode!=0:
        raise PreflightError("git_ref_or_history_unavailable")
    if len(result.stdout)>700000:
        raise PreflightError("git_output_too_large")
    return result.stdout.rstrip("\n")


def _paths(result:str)->tuple[str,...]:
    # git path quoting can escape file names. UAE controlled file paths
    # are ASCII; refuse ambiguous or transformed git quote output.
    if type(result) is not str or not result:
        return ()
    arr=tuple(result.splitlines())
    if (len(arr)>MAX_FILES or len(set(arr))!=len(arr)
        or any(not p or p.startswith('"') or "\t" in p or
               p.startswith("/") or p.startswith("../") or
               p.endswith("/..") or "/../" in p or "\\" in p for p in arr)):
        raise PreflightError("git_file_inventory_invalid")
    return arr


def _changed(git:Callable[...,str],base:str,head:str)->tuple[str,...]:
    # Include deletions: removing a peer-owned file is a scope violation too.
    return _paths(git("diff","--name-only","--diff-filter=ACMRD",
                      f"{base}...{head}"))


def _head(git:Callable[...,str],ref:str)->str:
    sha=git("rev-parse","--verify",ref+"^{commit}").strip()
    if SHA.fullmatch(sha) is None:
        raise PreflightError("git_commit_untrusted")
    return sha


def inspect(*,root:Path=ROOT,git:Callable[...,str]|None=None,
            guard_text:str|None=None)->dict[str,Any]:
    """Read-only peer-aware scope audit. Never returns a green merge verdict.

    git is an injected read-only git-command adapter for isolated tests.
    """
    do_git=git if git is not None else lambda *args:_git(root,*args)
    failures=[]
    local_refs={}
    try:
        main_sha=_head(do_git,BASE_REF)
        uae_sha=_head(do_git,UAE_REF)
        local_refs={"main":main_sha,"uae":uae_sha}
        changed=_changed(do_git,BASE_REF,UAE_REF)
    except PreflightError as error:
        failures.append(error.code)
        changed=()
    unowned=tuple(p for p in changed if not allowed_uae_path(p))
    if unowned:failures.append("non_uae_changes_require_review")
    if not changed:failures.append("unverified_uae_diff")
    if len(changed)>MAX_FILES:
        failures.append("uae_change_scope_too_large")

    peer_summary=[]
    number_collisions={}
    peer_migrations={}
    exact_migration_conflicts=set()
    project_migrations=tuple(x for x in changed if migration_id(x) is not None)
    for peer in PEERS:
        try:
            sha=_head(do_git,peer)
            paths=_changed(do_git,BASE_REF,peer)
        except PreflightError:
            peer_summary.append({
                "branch":peer,"local_head":None,
                "diff_verified":False,
                "shared_surface_overlap":[],
                "exact_migration_overlap":[],
                "numeric_prefix_overlap":[],
            })
            failures.append("peer_git_ref_missing")
            continue
        overlapping=sorted(set(paths).intersection(changed))
        # Even if exact file conflicts absent, compare owners with other
        # peers before cherry-picking several branches together.
        migration_paths=tuple(x for x in paths if migration_id(x) is not None)
        peer_migrations[peer]=migration_paths
        exact=sorted(set(project_migrations).intersection(migration_paths))
        n_shared=sorted({migration_id(x) for x in project_migrations}
                        .intersection(migration_id(x) for x in migration_paths))
        number_collisions[peer]=n_shared
        exact_migration_conflicts.update(exact)
        if overlapping:
            failures.append("manual_shared_file_reconciliation_required")
        if exact:
            failures.append("same_name_migration_requires_reconciliation")
        peer_summary.append({
            "branch":peer,"local_head":sha,"diff_verified":True,
            "shared_surface_overlap":overlapping,
            "exact_migration_overlap":exact,
            "numeric_prefix_overlap":n_shared,
            "different_name_migration_count":len(migration_paths),
        })

    # Peer-peer prefix reuse is NOT a duplicate migration name, since the
    # existing migration engine tracks the full .sql filename, but lexical
    # prefix ordering and FKs must be tested on a combined disposable PG17.
    peer_prefix_overlaps=[]
    for i,name in enumerate(PEERS):
        left=peer_migrations.get(name)
        if left is None:continue
        for other in PEERS[i+1:]:
            right=peer_migrations.get(other)
            if right is None:continue
            prefixes=sorted(
                {migration_id(x) for x in left}.intersection(
                    migration_id(x) for x in right
                )
            )
            identical=sorted(set(left).intersection(right))
            if prefixes or identical:
                peer_prefix_overlaps.append({
                    "a":name,"b":other,
                    "numeric_prefix_overlap":prefixes,
                    "exact_filenames":identical,
                })
                if prefixes:failures.append("peer_migration_order_unverified")
                if identical:failures.append("peer_same_name_migration_conflict")

    if guard_text is None:
        try:
            guard_text=(root/GUARD).read_text(encoding="utf-8")
        except (OSError,UnicodeError):
            guard_text=""
    failures.extend(release_baseline_gate(guard_text))
    # Safe gate status: presence of both vetoes means positive E2E is
    # *not* possible using current migration, which blocks release.
    still_fenced=(NO_QUALIFIED in guard_text and NO_COMPLETE in guard_text)
    if still_fenced:
        failures.append("ua85_positive_qualified_intentionally_blocked")

    # Stable, read-only report; no production DSNs/tokens/media filenames
    # beyond explicitly version-controlled source paths.
    blockers=sorted(set(FIXED_BLOCKERS+tuple(failures)))
    return {
        "schema_version":SCHEMA,
        "verdict":"blocked",
        "ready_for_merge":False,
        "git":local_refs,
        "uae_diff":{
            "changed_file_count":len(changed),
            "non_uae_paths":list(unowned[:50]),
            "shared_host_paths":sorted(set(changed)&SHARED_SURFACES),
            "uae_migration_names":list(project_migrations),
        },
        "peer_compatibility":peer_summary,
        "peer_migration_prefix_overlaps":peer_prefix_overlaps,
        "numeric_prefixes_are_not_version_identity":True,
        "known_positive_qualification_veto":still_fenced,
        "blockers":blockers,
        "verified_ci_results":False,
        "verified_pg17_result":False,
        "verified_approved_qdp_sre_abc":False,
        "measured_qualified_throughput":False,
        "canary_and_rollback_approved":False,
    }


def main()->int:
    try:
        result=inspect()
    except Exception:
        print(json.dumps({"schema_version":SCHEMA,
                          "verdict":"blocked","ready_for_merge":False,
                          "blockers":["audit_unavailable"]},
                         sort_keys=True,separators=(",",":")))
        return 2
    print(json.dumps(result,sort_keys=True,separators=(",",":")))
    return 2


if __name__=="__main__":
    raise SystemExit(main())
