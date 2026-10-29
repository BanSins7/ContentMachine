"""SRE M43: pinned, local-only merge rehearsal without checkout, push or network.

This command inspects existing local Git objects for the exact main and SRE
SHAs supplied by an operator. It DOES NOT fetch, merge a branch, edit main,
run migrations, create a commit, or claim production readiness.

Example (after explicitly fetching both SHA objects into a local clone):
    python -m scripts.sre_main_merge_rehearsal \
      --main-sha <40-hex> --sre-sha <40-hex> \
      --repo . --expected-main-ref refs/remotes/origin/main

git merge-tree --write-tree writes only disposable Git OBJECTS, never refs or
working-tree files. The result is a preliminary conflict check, NOT proof of
Python, SQL migration or browser integration acceptance.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

VERSION = "sre-m43-merge-rehearsal/v1"
SHA = re.compile(r"[0-9a-f]{40}\Z")
MIGRATION = re.compile(r"intelligence/migrations/([0-9]{3})_[^/]+\.sql\Z")
MAX_OUTPUT = 12000


class RehearsalBlocked(RuntimeError):
    pass


def _git(repo: Path, *args: str, timeout: int = 120):
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RehearsalBlocked("local_git_unavailable") from exc


def _needed_sha(sha: str, field: str) -> str:
    if type(sha) is not str or SHA.fullmatch(sha) is None:
        raise RehearsalBlocked(field + "_must_be_exact_40_hex")
    return sha


def _require_ok(response, reason: str) -> str:
    if response.returncode != 0:
        raise RehearsalBlocked(reason)
    return response.stdout.strip()


def _paths(repo: Path, first: str, second: str):
    output = _git(repo, "diff", "--name-only", "-z", first, second)
    if output.returncode:
        raise RehearsalBlocked("cannot_compare_git_trees")
    return {p for p in output.stdout.split("\0") if p}


def _migration_paths(repo: Path, commit: str):
    result = _git(
        repo, "ls-tree", "-r", "-z", "--name-only", commit,
        "intelligence/migrations",
    )
    if result.returncode:
        raise RehearsalBlocked("cannot_list_migrations")
    return {p for p in result.stdout.split("\0") if MIGRATION.fullmatch(p)}


def _prefix_collisions(main: set[str], sre: set[str]):
    left = defaultdict(list)
    right = defaultdict(list)
    for path in sorted(main - sre):
        left[MIGRATION.fullmatch(path).group(1)].append(path)
    for path in sorted(sre - main):
        right[MIGRATION.fullmatch(path).group(1)].append(path)
    return [
        {"prefix": prefix, "main_files": left[prefix],
         "sre_files": right[prefix]}
        for prefix in sorted(left.keys() & right.keys())
    ]


def assess(repo: Path, main_sha: str, sre_sha: str, *,
           expected_main_ref: str | None = None) -> dict:
    """Inspect exact known objects. No checkout, merge commit or networking."""
    root = Path(repo).resolve()
    main_sha = _needed_sha(main_sha, "main_sha")
    sre_sha = _needed_sha(sre_sha, "sre_sha")
    _require_ok(_git(root, "rev-parse", "--show-toplevel"),
                "not_a_local_git_repository")
    for sha in (main_sha, sre_sha):
        kind = _require_ok(_git(root, "cat-file", "-t", sha),
                           "missing_pinned_commit_object")
        if kind != "commit":
            raise RehearsalBlocked("expected_commit_not_other_git_object")
    if expected_main_ref is not None:
        if (not expected_main_ref.startswith("refs/remotes/")
                or not re.fullmatch(r"refs/remotes/[A-Za-z0-9._/-]+",
                                    expected_main_ref)):
            raise RehearsalBlocked("expected_main_ref_must_be_remote_tracking")
        found = _require_ok(_git(root, "rev-parse", "--verify",
                                 expected_main_ref + "^{commit}"),
                            "missing_local_main_tracking_ref")
        if found != main_sha:
            raise RehearsalBlocked("main_moved_fetch_and_repin_before_rehearsal")
    base = _require_ok(_git(root, "merge-base", main_sha, sre_sha),
                       "no_common_ancestor")
    if not SHA.fullmatch(base):
        raise RehearsalBlocked("invalid_merge_base_sha")
    main_edits = _paths(root, base, main_sha)
    sre_edits = _paths(root, base, sre_sha)
    common = sorted(main_edits & sre_edits)
    main_migrations = _migration_paths(root, main_sha)
    sre_migrations = _migration_paths(root, sre_sha)
    rehearsal = _git(
        root, "merge-tree", "--write-tree", "--messages",
        main_sha, sre_sha, timeout=180,
    )
    if rehearsal.returncode not in (0, 1):
        raise RehearsalBlocked("merge_tree_unavailable_or_failed")
    lines = rehearsal.stdout.splitlines()
    maybe_tree = lines[0].strip() if lines else ""
    clean = rehearsal.returncode == 0 and SHA.fullmatch(maybe_tree) is not None
    if rehearsal.returncode == 0 and not clean:
        raise RehearsalBlocked("merge_tree_missing_result")
    # Git output is diagnostic ONLY. Parse just the tree identity. Never infer
    # conflict-free from a PR mergeable flag or an overlap count.
    conflicts = ("\n".join(lines[1:]))[:MAX_OUTPUT]
    collisions = _prefix_collisions(main_migrations, sre_migrations)
    return {
        "schema_version": VERSION,
        "main_sha": main_sha, "sre_sha": sre_sha,
        "merge_base_sha": base,
        "merge_tree_status": "clean" if clean else "conflicted",
        "merge_tree_sha": maybe_tree if clean else None,
        "merge_tree_diagnostics": conflicts if not clean else "",
        "main_changed_paths": len(main_edits),
        "sre_changed_paths": len(sre_edits),
        "both_changed_paths": common,
        "migration_prefix_collisions": collisions,
        "migration_name_collision_is_automatic_git_conflict": False,
        "migration_order_and_checksum_acceptance": "not_executed",
        "python_linux_windows_node22_pg17_smoke": "not_executed",
        "merge_executed": False, "push_performed": False,
        "production_ready": False,
        "remaining_gate": (
            "fix_merge_conflicts_in_sre_branch" if not clean
            else "run_full_m43_disposable_pg_and_host_acceptance"
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--main-sha", required=True)
    parser.add_argument("--sre-sha", required=True)
    parser.add_argument("--expected-main-ref", default=None)
    args = parser.parse_args(argv)
    try:
        report = assess(
            args.repo, args.main_sha, args.sre_sha,
            expected_main_ref=args.expected_main_ref,
        )
    except RehearsalBlocked as exc:
        print(json.dumps({"schema_version": VERSION, "status": "blocked",
                          "reason": str(exc), "production_ready": False},
                         sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2))
    return 0 if report["merge_tree_status"] == "clean" else 2


if __name__ == "__main__":
    sys.exit(main())
