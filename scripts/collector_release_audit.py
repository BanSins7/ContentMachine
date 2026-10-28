"""VC-MOD-16: read-only source/merge-scope and deployment-preflight auditor.

python -m scripts.collector_release_audit --base origin/main

A PASS means only *static branch scope and migration packaging* are clean.
It cannot attest GitHub Actions success, true upstream E2E, production DTK
rights/costs, an authenticated SRE M35 inbox, or authorize PR merge.
No network, database writes, provider APIs, or repo mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
MIGRATION_NAMES=tuple(
    f"{n:03d}_{name}.sql" for n,name in (
        (54,"collector_planning"),
        (55,"collector_worker"),
        (56,"collector_qv_manifest"),
        (57,"collector_discovery_outbox"),
    )
)
REQUIRED_PATHS=(
    "intelligence/collector/__main__.py",
    "intelligence/collector/cli.py",
    "intelligence/collector/worker.py",
    "intelligence/collector/qv_reader.py",
    "intelligence/collector/query_publisher.py",
    "intelligence/collector/ingest.py",
    "intelligence/collector/completion.py",
    "intelligence/collector/recovery.py",
    "intelligence/collector/sre_delivery.py",
    "tests/test_video_collector_pg15_integration.py",
    "scripts/collector_release_gate.py",
    "scripts/collector_release_audit.py",
    "docs/VIDEO_COLLECTOR_MASTER_IMPLEMENTATION_PLAN.md",
    "docs/VIDEO_COLLECTOR_MODULE_DEPLOYMENT_V1.md",
    *(f"intelligence/migrations/{name}" for name in MIGRATION_NAMES),
)
_ALLOWED_EXACT={
    "app.py","intelligence/config.py","intelligence/discovery/http_api.py",
    ".github/workflows/tests.yml",
    ".github/workflows/windows-d1-smoke.yml",
    "site/index.html","site/style.css",
    "docs/DOCS_INDEX.md","docs/DTK_CONNECTOR.md",
    "examples/collector.env.example",
    "examples/video_collector_offline_preview.json",
    "scripts/collector_schema_audit.py",
    "scripts/collector_release_gate.py",
    "scripts/collector_release_audit.py",
    "tests/test_dtk_connector.py",
    "tests/fixtures/vc_dtk_inspector_page_v1.json",
    "site/collector.js","site/dtk-tool.js",
}
_ALLOWED_PREFIXES=(
    "intelligence/collector/",
    "intelligence/connectors/",
    "docs/VIDEO_COLLECTOR_",
    "tests/test_video_collector_",
)
_SAFE_FLAG_NAMES=(
    "CM_DB_ENABLED","CM_COLLECTOR_QV_CONSUMER_ENABLED",
    "CM_COLLECTOR_LIVE_ENABLED","CM_DTK_ARCHIVE_DISABLED_CONFIRMED",
    "CM_COLLECTOR_SRE_DELIVERY_ENABLED",
)


def _git(*args):
    try:
        result=subprocess.run(
            ["git",*args],cwd=ROOT,capture_output=True,text=True,
            timeout=20,check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode==0 else None


def _safe_path(path):
    return path in _ALLOWED_EXACT or any(
        path.startswith(prefix) for prefix in _ALLOWED_PREFIXES
    ) or path.startswith("intelligence/migrations/") and (
        path.removeprefix("intelligence/migrations/") in MIGRATION_NAMES
    )


def verify_paths(changes):
    """Pure audit of (git_status, relative_path) pairs."""
    unauthorized=[]
    protected_deletes=[]
    collector_migrations=[]
    for status,path in changes:
        if not _safe_path(path):
            unauthorized.append(path)
        if status[0] in ("D","R","C") and path not in (
            "docs/DOCS_INDEX.md",
        ):
            protected_deletes.append(path)
        if path.startswith("intelligence/migrations/"):
            collector_migrations.append(path.removeprefix("intelligence/migrations/"))
    illegal_migrations=sorted(set(collector_migrations)-set(MIGRATION_NAMES))
    return {
        "scope_clean":not unauthorized and not protected_deletes and not illegal_migrations,
        "unapproved_paths":sorted(set(unauthorized)),
        "deleted_or_renamed_paths":sorted(set(protected_deletes)),
        "unapproved_migrations":illegal_migrations,
        "changed_files":len(changes),
    }


def audit(*, base="origin/main"):
    # Only local checked-out Git data is consulted; never silently fetch.
    current=_git("rev-parse","HEAD")
    main=_git("rev-parse","--verify",base)
    shared=_git("merge-base",base,"HEAD") if main else None
    raw=_git("diff","--name-status","--no-renames",f"{base}...HEAD") if shared else None
    baseline_ok=bool(current and main and shared and raw is not None)
    pairs=[]
    if baseline_ok:
        for line in raw.splitlines():
            parts=line.split("\t")
            if len(parts)==2:
                pairs.append((parts[0],parts[1]))
            elif line:
                baseline_ok=False
    files=verify_paths(pairs)
    files["scope_clean"]=files["scope_clean"] and baseline_ok
    missing=[path for path in REQUIRED_PATHS if not (ROOT/path).is_file()]
    migrations={
        "required":list(MIGRATION_NAMES),
        "present":not any((ROOT/"intelligence/migrations"/name).is_file() is False
                          for name in MIGRATION_NAMES),
        "sha256":{
            name:hashlib.sha256((ROOT/"intelligence/migrations"/name).read_bytes()).hexdigest()
            for name in MIGRATION_NAMES
            if (ROOT/"intelligence/migrations"/name).is_file()
        },
    }
    present_sql=[p.name for p in (ROOT/"intelligence/migrations").glob("*.sql")]
    migrations["no_old_collector_name_collision"]=not any(
        old in present_sql for old in (
            "016_collector_planning.sql","017_collector_worker.sql"
        )
    )
    config={}
    file=ROOT/"examples/collector.env.example"
    if file.is_file():
        for row in file.read_text(encoding="utf-8").splitlines():
            row=row.strip()
            if row and not row.startswith("#") and "=" in row:
                k,v=row.split("=",1)
                config[k.strip()]=v.strip()
    flags_ok=all(config.get(k)=="0" for k in _SAFE_FLAG_NAMES)
    # Never show values of env keys. No implicit provider secrets allowed.
    secret_names=[k for k in config if any(
        part in k for part in ("KEY","PASSWORD","TOKEN","SECRET","DSN")
    )]
    flags_ok=flags_ok and not secret_names
    # If main advanced since this feature branch was cut, stop rather
    # than attesting a stale base revision.
    behind=(int(_git("rev-list","--count",f"HEAD..{base}") or "-1")
            if baseline_ok else None)
    # Source audit is proof only about this checkout's static packaging.
    scope_ok=bool(baseline_ok and behind==0 and files["scope_clean"] and not missing and
                  migrations["present"] and migrations["no_old_collector_name_collision"]
                  and flags_ok)
    return {
        "schema_version":"collector.merge_audit/v1",
        "base_ref":base,"head_sha":current,"base_sha":main,
        "common_ancestor_sha":shared,
        "behind_main_commits":behind,
        "changed_paths":files,"missing_required_paths":missing,
        "migration_files":migrations,
        "default_flags_off":flags_ok,
        "no_credentials_in_example":not secret_names,
        "static_source_gate_passed":scope_ok,
        "exact_head_linux_pg17_ci_passed":False,
        "exact_head_windows_ci_passed":False,
        "full_upstream_c9_sp_qv_db_e2e_verified":False,
        "authenticated_sre_m35_inbox_verified":False,
        "provider_commercial_rights_verified":False,
        "merge_ready":False,
        "external_provider_calls":0,
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description="Read-only VC branch/merge audit")
    parser.add_argument("--base",default="origin/main")
    parser.add_argument("--strict-source",action="store_true",
                        help="nonzero exit when static source/branch gate fails")
    args=parser.parse_args(argv)
    report=audit(base=args.base)
    print(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2))
    return 0 if report["static_source_gate_passed"] or not args.strict_source else 2


if __name__=="__main__":
    raise SystemExit(main())
