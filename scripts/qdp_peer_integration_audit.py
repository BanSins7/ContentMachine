#!/usr/bin/env python3
"""Offline, non-merging audit of the QDP branch against module branches.

Reads local Git objects only. No checkout, network, database, write to Git
refs, external provider, or delivery ACK. Not a substitute for PG17/live QA.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_REFS = {
    "main": "main",
    "qdp": "feature/qualified-data-pipeline-v1-20261023",
    "group_a": "feature/group-a-full-module",
    "group_b": "feature/group-b-full-module",
    "group_c": "feature/group-c-full-module",
    "sre": "feature/sre-production-v1-20261023",
    "collector": "feature/video-collector-dev-20261023",
    "analytics": "feature/unified-analytics-full-module",
    "search_planner": "feature/search-planner-full-integration",
    "patterns": "feature/patterns-insights-full-module",
}
MIGRATION = re.compile(r"^intelligence/migrations/(\d{3})_([^/]+)\.sql$")


class GitAuditError(RuntimeError):
    pass


def git(repo: Path, *args: str, allow_failure: bool = False) -> str | None:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise GitAuditError("git_not_available") from exc
    if p.returncode != 0:
        if allow_failure:
            return None
        raise GitAuditError("git_command_failed: " + " ".join(args[:2]))
    return p.stdout.strip()


def file_at(repo: Path, ref: str, path: str) -> str | None:
    return git(repo, "show", ref + ":" + path, allow_failure=True)


def inventory(repo: Path, ref: str) -> dict[str, str]:
    tree = git(repo, "ls-tree", "-r", ref, "--", "intelligence/migrations") or ""
    result = {}
    for entry in tree.splitlines():
        if "\t" not in entry:
            continue
        meta, path = entry.split("\t", 1)
        if MIGRATION.fullmatch(path):
            result[path.rsplit("/", 1)[-1]] = meta.split()[2]
    return result


def analyze(repo: Path, refs: dict[str, str]) -> dict:
    heads = {name: git(repo, "rev-parse", "--verify", ref + "^{commit}",
                       allow_failure=True) for name, ref in refs.items()}
    if not heads.get("qdp") or not heads.get("main"):
        raise GitAuditError("main_and_qdp_refs_required")
    base = git(repo, "merge-base", refs["main"], refs["qdp"])
    delta = git(repo, "rev-list", "--left-right", "--count",
                refs["main"] + "..." + refs["qdp"])
    behind, ahead = (int(v) for v in delta.split())
    changed = {}
    for name in ("main", "qdp"):
        raw = git(repo, "diff", "--name-only", base + ".." + refs[name]) or ""
        changed[name] = set(raw.splitlines())
    overlapping = sorted(changed["main"] & changed["qdp"])

    migration_sets = {name: inventory(repo, ref)
                      for name, ref in refs.items() if heads[name]}
    owners: dict[str, list[dict]] = {}
    numbers: dict[str, set[str]] = {}
    for name, migrations in migration_sets.items():
        for filename, sha in migrations.items():
            owners.setdefault(filename, []).append({"module": name, "blob": sha})
            numbers.setdefault(filename[:3], set()).add(filename)
    # Same SQL filename with different blobs is a checksum collision.
    collisions = {name: entries for name, entries in owners.items()
                  if len({entry["blob"] for entry in entries}) > 1}
    repeated_numbers = {num: sorted(names) for num, names in numbers.items()
                        if len(names) > 1}
    # Git merge-tree in three-argument mode reads objects without changing refs,
    # index, worktree, or attempting the merge. Marker detection is conservative.
    merge_preview = git(repo, "merge-tree", base, refs["main"], refs["qdp"],
                        allow_failure=True)
    conflict_markers = bool(merge_preview and "<<<<<<<" in merge_preview
                            and ">>>>>>>" in merge_preview)

    qdp_b = file_at(repo, refs["qdp"],
                    "intelligence/qualified_data/integrations/group_b.py") or ""
    b_reader = (file_at(repo, refs["group_b"],
                        "intelligence/group_b/adapters/qdp.py") or "") if heads.get("group_b") else ""
    b_old = ("qdp-preparation/v1" in b_reader
             and "producer_payload_mismatch" in b_reader
             and "qdp-preparation/v2" in qdp_b)
    group_b_status = ("incompatible_v1_reader" if b_old else
                      "unverified_contract" if heads.get("group_b") else
                      "peer_ref_missing")

    a_receiver = (file_at(repo, refs["group_a"],
                          "intelligence/group_a/handoff.py") or "") if heads.get("group_a") else ""
    c_receiver = (file_at(repo, refs["group_c"],
                          "intelligence/group_c/qdp_intake.py") or "") if heads.get("group_c") else ""
    analytics = (file_at(repo, refs["analytics"],
                         "intelligence/unified_analytics/qualification_orchestrator.py") or "") if heads.get("analytics") else ""
    contracts = {
        "group_a": "candidate_requires_live_retest" if "GroupAInboxReceiver" in a_receiver else "unavailable_or_unverified",
        "group_b": group_b_status,
        "group_c": "candidate_requires_live_retest" if "QDPGroupCConsumer" in c_receiver else "unavailable_or_unverified",
        "analytics": "not_completed_results_evidence" if "QualificationOrchestrator" in analytics else "unavailable_or_unverified",
        "sre": "requires_completed_live_run_and_real_publisher",
        "collector": "requires_real_d1_provenance_and_media",
    }
    blockers = []
    if overlapping:
        blockers.append("shared_paths_require_main_reconciliation")
    if conflict_markers:
        blockers.append("merge_tree_conflict_markers")
    if collisions:
        blockers.append("migration_filename_checksum_collision")
    if any(value is None for value in heads.values()):
        blockers.append("required_local_peer_refs_missing")
    if b_old:
        blockers.append("group_b_old_event_preparation_abi")
    blockers += ["no_live_sre_collector_group_execution_proof",
                 "no_verified_completed_analytics_result",
                 "no_full_cross_branch_pg17_upgrade_and_chaos_evidence"]
    return {
        "schema_version": "qdp-integration-audit/v1",
        "refs": {name: {"ref": refs[name], "sha": head, "present": head is not None}
                 for name, head in heads.items()},
        "merge": {"base": base, "ahead": ahead, "behind": behind,
                  "overlapping_paths": overlapping,
                  "merge_tree_conflict_markers": conflict_markers,
                  "note": "Static Git review; GitHub PR mergeability is authoritative"},
        "migrations": {"by_module": {k: sorted(v) for k, v in migration_sets.items()},
                       "conflicting_same_filename": collisions,
                       "repeated_numeric_prefixes": repeated_numbers,
                       "note": "Migration key is entire filename; shared numbers alone are advisory"},
        "contracts": contracts,
        "blockers": sorted(set(blockers)),
        "release_decision": "NO_GO",
        "non_goals": ["no_checkout_or_merge", "no_peer_branch_writes",
                      "no_production_database", "no_provider_or_paid_jobs"],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path("."))
    p.add_argument("--ref", action="append", default=[], metavar="NAME=GIT_REF",
                   help="override a branch name (e.g. main=origin/main)")
    p.add_argument("--output", type=Path, help="optional JSON report path")
    args = p.parse_args(argv)
    refs = dict(DEFAULT_REFS)
    for override in args.ref:
        if "=" not in override:
            p.error("--ref must be NAME=GIT_REF")
        name, value = override.split("=", 1)
        if name not in refs or not value or value.startswith("-"):
            p.error("unknown or unsafe --ref name/value")
        refs[name] = value
    try:
        report = analyze(args.repo.resolve(), refs)
    except GitAuditError as exc:
        print(json.dumps({"error": str(exc), "release_decision": "NO_GO"}),
              file=sys.stderr)
        return 2
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0  # Inspection succeeds; this is never a release approval.


if __name__ == "__main__":
    raise SystemExit(main())
