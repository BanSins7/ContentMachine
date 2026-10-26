"""PSI Stage14 deterministic technical release gate.

Run after CI PostgreSQL migrations:
    python -m scripts.psi_release_gate --strict-ci

No paid APIs, URL fetches, media downloads or source mutations. This is a
technical QA gate; it CANNOT grant human merge/release approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "intelligence" / "migrations"
PSI_VERSIONS = [f"{n:03d}" for n in range(30, 42)]
REQUIRED = [
    "intelligence/project_sources/repository.py",
    "intelligence/project_sources/file_library.py",
    "intelligence/project_sources/document_jobs.py",
    "intelligence/project_sources/web_jobs.py",
    "intelligence/project_sources/video_jobs.py",
    "intelligence/project_sources/transcript_jobs.py",
    "intelligence/project_sources/vision_jobs.py",
    "intelligence/project_sources/timeline_jobs.py",
    "intelligence/project_sources/evidence_jobs.py",
    "intelligence/project_sources/context_service.py",
    "intelligence/taxonomy_builder/context_binding.py",
    "site/project-context.js",
    "site/context-quote.js",
    "tests/test_psi_stage14_migrations.py",
    "tests/test_psi_stage14_documents_db.py",
    "tests/test_psi_stage14_platforms.py",
    "tests/test_psi_stage14_web_db.py",
    "docs/PSI_STAGE14_RELEASE_GATE.md",
]
TABLES = [
    "project_sources", "project_source_versions", "project_media_assets",
    "source_extraction_jobs", "source_processing_outputs", "project_evidence",
    "project_evidence_grounding", "project_context_snapshots",
    "project_context_evidence_pins", "project_context_approvals",
    "tb_context_quote_bindings", "tb_context_run_bindings"
]


class ReleaseBlocked(Exception):
    pass


def manifest():
    files = sorted(MIGRATIONS.glob("[0-9]*.sql"))
    found = [p.name[:3] for p in files if p.name[:3] in PSI_VERSIONS]
    if found != PSI_VERSIONS:
        raise ReleaseBlocked("Missing/duplicate PSI migration stage 030..041: " + repr(found))
    missing = [f for f in REQUIRED if not (ROOT / f).is_file()]
    if missing:
        raise ReleaseBlocked("Release-required files absent: " + repr(missing))
    # Match intelligence.migrate.apply_migrations EXACTLY. Python text mode
    # normalizes Windows CRLF to LF before UTF-8 SHA256; raw read_bytes()
    # falsely marks every checkout-normalized SQL file as modified.
    return {p.name: hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            for p in files}


def legacy_unchanged():
    """Old SQL migrations must not be rewritten by this PR.

    Requires a full git checkout. Does not guess if origin/main is unavailable.
    """
    def git(*args):
        try:
            value = subprocess.run(["git", *args], cwd=ROOT, check=True,
                                   capture_output=True, text=True, timeout=15)
            return value.stdout.strip()
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
            raise ReleaseBlocked("Could not independently compare baseline migrations: " +
                                 " ".join(args)) from exc
    baseline=git("merge-base","origin/main","HEAD")
    # New SRE/other-module migrations must be additive and keep their own
    # checksums. Only modification/deletion/rename/type-change of an existing
    # migration is a historical rewrite; git --name-only without a diff filter
    # incorrectly rejects newly added, independent 016_* files.
    changed=git("diff","--name-only","--diff-filter=DMRT",baseline,"HEAD",
                "--","intelligence/migrations")
    altered=[path for path in changed.splitlines()
             if path.startswith("intelligence/migrations/") and
                path.rsplit("/",1)[-1][:3].isdigit() and
                int(path.rsplit("/",1)[-1][:3])<30]
    if altered:
        raise ReleaseBlocked("Historical migration changed: " + repr(altered))
    return baseline[:12]


def pg_invariants(dsn, hashes):
    try:
        import psycopg
        conn=psycopg.connect(dsn,connect_timeout=6)
    except Exception as exc:
        raise ReleaseBlocked("CI PostgreSQL 17 is unavailable: " +
                             type(exc).__name__) from exc
    with conn:
        ver=int(conn.execute("SHOW server_version_num").fetchone()[0])
        if ver//10000 != 17:
            raise ReleaseBlocked("Expected PostgreSQL 17, got major " + str(ver//10000))
        applied={v:h for v,h in conn.execute(
            "SELECT version,sha256 FROM schema_migrations").fetchall()}
        absent=[v for v in hashes if v not in applied]
        altered=[v for v,h in hashes.items() if v in applied and applied[v]!=h]
        if absent or altered:
            raise ReleaseBlocked("Unapplied/modified migrations " +
                                 json.dumps({"absent":absent,"modified":altered}))
        for table in TABLES:
            if conn.execute("SELECT to_regclass(%s)",("public."+table,)).fetchone()[0] is None:
                raise ReleaseBlocked("Missing project table: " + table)
        # SQL-level immutability protects historical Context/quote/run pins.
        for table in ("tb_context_quote_bindings","tb_context_run_bindings",
                      "project_context_snapshots","project_context_approvals"):
            row=conn.execute("""SELECT count(*) FROM pg_trigger t JOIN pg_class c
                        ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                        WHERE n.nspname='public' AND c.relname=%s AND NOT t.tgisinternal""",
                        (table,)).fetchone()
            if not row or row[0]<1:
                raise ReleaseBlocked("No immutable trigger for " + table)
        for table in ("tb_context_quote_bindings","tb_context_run_bindings"):
            row=conn.execute("""SELECT count(*) FROM pg_constraint x JOIN pg_class c
                  ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                  WHERE n.nspname='public' AND c.relname=%s AND x.contype='f'""",
                  (table,)).fetchone()
            if not row or row[0]<2:
                raise ReleaseBlocked("Insufficient project-scoped FK constraints: " + table)
        return {"postgres_major":17,"applied_migrations":len(applied),
                "verified_project_tables":len(TABLES),
                "verified_immutable_bindings":4}


def run(*, strict_ci=True, enforce_git=True):
    hashes=manifest()
    output={"gate":"psi-stage14/1",
            "legacy_migrations":legacy_unchanged() if enforce_git else "NOT_VERIFIED",
            "manifest_count":len(hashes),
            "required_files":len(REQUIRED)}
    dsn=os.environ.get("CM_TEST_PG_DSN")
    if not dsn:
        if strict_ci:
            raise ReleaseBlocked("CM_TEST_PG_DSN required: never mark DB gate green without PG")
        output["database"]="NOT_VERIFIED"
    else:
        output.update(pg_invariants(dsn,hashes))
    output["technical_gate"]="pass" if output.get("postgres_major")==17 and enforce_git else "partial"
    output["merge_approval"]="REQUIRES_EXPLICIT_HUMAN_REVIEW"
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict-ci",action="store_true",help="Require real PostgreSQL17")
    parser.add_argument("--skip-git",action="store_true",
                        help="Local diagnostics only; cannot pass technical gate")
    args=parser.parse_args()
    try:
        report=run(strict_ci=args.strict_ci,enforce_git=not args.skip_git)
        print(json.dumps(report,ensure_ascii=False,sort_keys=True))
        if args.strict_ci and report["technical_gate"]!="pass":
            raise ReleaseBlocked("Technical gate not fully verified")
    except ReleaseBlocked as exc:
        print("PSI RELEASE BLOCKED: "+str(exc),file=sys.stderr)
        return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
