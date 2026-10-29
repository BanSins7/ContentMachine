"""Windows-first local QA/CI replica using disposable PostgreSQL 17 databases.

Never run integration tests against the user's normal ContentMachine database.
Default: full local technical checks with real temporary PG17 and UI smoke.
--offline: pure syntax/unit/JS checks only, explicitly NOT a release pass.
No cloud, paid OpenRouter/DTK, data acquisition or production model E2E.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SP_DB = "cm_sp16_test"
SP_UPGRADE = "cm_sp16_upgrade"


def _git(*args):
    try:
        p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                           text=True, timeout=10, check=False)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _safe_child_env(dsn=None, *, workspace=None):
    # Test processes cannot receive environment-supplied provider credentials.
    env = {k: v for k, v in os.environ.items()
           if not (k.upper().endswith(("_API_KEY", "_DSN", "_TOKEN")) or
                   k.upper() in ("OPENROUTER_API_KEY", "OPENAI_API_KEY",
                                 "APIFY_TOKEN", "OPENROUTER_KEY",
                                 "DATABASE_URL", "PGHOST", "PGPORT",
                                 "PGUSER", "PGDATABASE", "PGPASSWORD",
                                 "PGSERVICE", "CM_SRE_M42_RUNNER_PASSWORD"))}
    env.update({
        "CI": "true", "CM_NO_BROWSER": "1",
        "CM_COLLECTOR_LIVE_ENABLED": "0",
        "CM_DTK_ARCHIVE_DISABLED_CONFIRMED": "0",
        "CM_COLLECTOR_SRE_DELIVERY_ENABLED": "0",
        "CM_QDP_GROUP_A_AUTO_DISPATCH": "0",
        "CM_QDP_GROUP_BC_AUTO_DISPATCH": "0",
        "CM_GROUP_A_B_AUTO_DISPATCH": "0",
        "CM_WINDOWS_SMOKE_EPHEMERAL_DB": "YES",
        "QDP_DISPOSABLE_TEST_DB": "YES",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    # Force explicit effective settings over any local .env defaults.
    if workspace is not None:
        env["CM_PROJECTS_DIR"] = str(workspace)
    env["CM_DB_ENABLED"] = "1" if dsn else "0"
    env["CM_DB_DSN"] = dsn or ""
    if dsn:
        env["CM_TEST_PG_DSN"] = dsn
    else:
        env.pop("CM_TEST_PG_DSN", None)
    env.pop("CM_SP16_RUN_PG17", None)
    return env


def _report_dir():
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    target = base / "ContentMachineQA" / (
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]
    )
    target.mkdir(parents=True, exist_ok=False)
    return target


class Runner:
    def __init__(self, output, offline, *, gates_only=False):
        self.output = output
        self.offline = offline
        self.gates_only = gates_only
        self.workspace = self.output / "fixture_projects"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.steps = []
        self.created = []
        self.dsn = None
        self.psycopg = None
        self.sql = None
        self.admin_dsn = None
        self.pg_major = None
        self.sha = _git("rev-parse", "HEAD")
        self.started = datetime.now(timezone.utc).isoformat()

    def child_env(self, dsn=None):
        return _safe_child_env(dsn, workspace=self.workspace)

    def record(self, name, status, **details):
        item = {"name": name, "status": status, **details}
        self.steps.append(item)
        print("[{}] {}".format(status, name), flush=True)
        if status != "PASS" and item.get("log"):
            # Only print the path, never raw log content: test output can
            # include fixture data and provider errors with sensitive fields.
            print("[LOG] {}".format(self.output / item["log"]), flush=True)
        return item

    def run(self, name, command, env=None, timeout=1800):
        filename = "{:02d}_{}.log".format(
            len(self.steps) + 1, re.sub(r"[^a-z0-9_-]+", "_", name.lower())
        )
        path = self.output / filename
        tick = time.monotonic()
        print("[RUN] {} | log: {}".format(name, path), flush=True)
        try:
            with path.open("w", encoding="utf-8", errors="replace") as log:
                proc = subprocess.run(
                    command, cwd=ROOT, env=env or self.child_env(),
                    stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, timeout=timeout, check=False,
                )
            code = proc.returncode
            status = "PASS" if code == 0 else "FAIL"
        except KeyboardInterrupt:
            # Persist an honest interrupted step before the main runner cleans
            # the disposable PostgreSQL database and writes report.json.
            self.record(name, "INTERRUPTED", log=filename,
                        duration_s=round(time.monotonic() - tick, 2))
            raise
        except subprocess.TimeoutExpired:
            code, status = None, "TIMEOUT"
        except OSError:
            code, status = None, "ERROR"
        # Logs can contain private user-supplied fixture inputs. Keep local.
        try:
            tail = path.read_text(encoding="utf-8", errors="replace")[-5000:]
            skips = re.findall(r"skipped=(\d+)", tail)
            skipped = int(skips[-1]) if skips else None
        except OSError:
            skipped = None
        self.record(name, status, exit_code=code, duration_s=round(
            time.monotonic() - tick, 2), log=filename, skipped=skipped)
        return status == "PASS"

    def preflight(self):
        node = shutil.which("node")
        self.record("python", "PASS" if sys.version_info >= (3, 11) else "FAIL",
                    version=sys.version.split()[0])
        self.record("node", "PASS" if node else "FAIL",
                    available=bool(node))
        self.record("git_checkout", "PASS" if self.sha and
                    (ROOT / ".git").exists() else "FAIL",
                    sha=self.sha, branch=_git("branch", "--show-current"))
        if not self.offline:
            with socket.socket() as sock:
                sock.settimeout(1)
                available = sock.connect_ex(("127.0.0.1", 8765)) != 0
            self.record("localhost_port_8765_free",
                        "PASS" if available else "FAIL")
        return all(x["status"] == "PASS" for x in self.steps)

    def _admin(self):
        return self.psycopg.connect(self.admin_dsn, autocommit=True,
                                    connect_timeout=5)

    def create_database(self, name):
        if not re.fullmatch(r"cmqa_[0-9a-f]{12}|cm_sp16_test", name):
            raise ValueError("Unsafe test database name")
        with self._admin() as c:
            if c.execute("SELECT 1 FROM pg_database WHERE datname=%s",
                         (name,)).fetchone():
                raise RuntimeError(
                    "Disposable database name already exists: " + name +
                    ". Will not overwrite or reuse it."
                )
            c.execute(self.sql.SQL("CREATE DATABASE {}").format(
                self.sql.Identifier(name)))
        self.created.append(name)

    def prepare_postgres(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        from intelligence.config import dsn
        raw = dsn()
        if not raw:
            raise RuntimeError("Missing CM_DB_DSN in current checkout .env")
        parsed = conninfo_to_dict(raw)
        if parsed.get("dbname") != "contentmachine":
            raise RuntimeError("Unexpected production database name; refused")
        with psycopg.connect(raw, connect_timeout=5) as conn:
            host = conn.info.host
            port = conn.info.port
            dbname = conn.execute("SELECT current_database()").fetchone()[0]
            version = int(conn.execute("SHOW server_version_num").fetchone()[0])
        if (host not in ("127.0.0.1", "localhost", "::1") or
            port != 54329 or dbname != "contentmachine" or
            not 170000 <= version < 180000):
            raise RuntimeError("Requires local PostgreSQL17 at port 54329")
        self.psycopg, self.sql = psycopg, sql
        self.pg_major = 17
        self.admin_dsn = make_conninfo(raw, dbname="postgres")
        with self._admin() as conn:
            if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s",
                            (SP_UPGRADE,)).fetchone():
                raise RuntimeError(
                    "cm_sp16_upgrade already exists; refusing to overwrite it"
                )
        name = "cmqa_" + uuid4().hex[:12]
        self.create_database(name)
        self.dsn = make_conninfo(raw, dbname=name)
        self.record("isolated_postgresql17", "PASS",
                    test_database=name, host="127.0.0.1",
                    production_database_untouched=True)

    def cleanup(self):
        if not self.psycopg or not self.admin_dsn:
            return
        # SP16's own upgrade test intentionally creates, but does not clean
        # up, a sibling DB. Drop it only if it was absent in our preflight
        # and the SP16 stage actually ran within this QA invocation.
        names = list(reversed(self.created))
        if getattr(self, "sp16_executed", False):
            names.insert(0, SP_UPGRADE)
        for name in names:
            try:
                with self._admin() as c:
                    exists = c.execute(
                        "SELECT 1 FROM pg_database WHERE datname=%s",
                        (name,)).fetchone()
                    if exists:
                        c.execute(self.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                            self.sql.Identifier(name)))
                self.record("cleanup_" + name, "PASS")
            except Exception:
                self.record("cleanup_" + name, "FAIL",
                            message="Inspect disposable database manually")

    def execute(self):
        self.preflight()
        if any(step["status"] != "PASS" for step in self.steps):
            return
        env = self.child_env()
        self.run("python_compile", [
            sys.executable, "-m", "compileall", "-q", "app.py",
            "projects.py", "worker", "intelligence", "scripts", "tests",
        ], env=env)
        node = shutil.which("node")
        if node:
            js_files = sorted((ROOT / "site").rglob("*.js"))
            js_files += sorted((ROOT / "intelligence").glob("*/site/*.js"))
            for path in js_files:
                rel = path.relative_to(ROOT).as_posix()
                self.run("js_syntax_" + path.stem, [node, "--check", rel],
                         env=env, timeout=60)
            for path in sorted((ROOT / "tests").glob("test_*.cjs")):
                self.run("ui_" + path.stem, [node, str(path)],
                         env=env, timeout=180)
            for path in sorted((ROOT / "tests").glob("test_*.mjs")):
                self.run("ui_" + path.stem, [node, "--test", str(path)],
                         env=env, timeout=180)
        if not self.offline:
            try:
                self.prepare_postgres()
                env = self.child_env(self.dsn)
                ok = self.run("fresh_postgres_migrations", [
                    sys.executable, "-m", "intelligence.migrate",
                ], env=env, timeout=600)
                if not ok:
                    return
                self.run("migration_idempotent_replay", [
                    sys.executable, "-m", "intelligence.migrate",
                ], env=env, timeout=300)
                self.run("database_health", [
                    sys.executable, "-m", "intelligence.health",
                ], env=env, timeout=90)
                self.run("psi_stage14_strict_ci", [
                    sys.executable, "-m", "scripts.psi_release_gate", "--strict-ci",
                ], env=env, timeout=360)
                collector_env = dict(env, CM_LOCAL_QA_LOG_DIR=str(
                    self.output / "collector_cases"))
                self.run("collector_release_strict_ci", [
                    sys.executable, "-m", "scripts.collector_release_gate", "--strict-ci",
                ], env=collector_env, timeout=1200)
                self.run("qdp_verify_pg17", [
                    sys.executable, "scripts/qdp_verify.py", "--with-postgres",
                    "--report", str(self.output / "qdp_evidence.json"),
                ], env=env, timeout=900)
            except Exception as exc:
                self.record("postgres_setup", "FAIL",
                            reason=type(exc).__name__,
                            detail="Check local PostgreSQL17 and disposable DB availability")
                return
        if self.gates_only:
            # This mode is for resolving Collector/QDP release failures quickly.
            # Deliberately omit long general unittest, SP16 and HTTP acceptance;
            # never claim that the full platform has passed QA.
            return
        self.run("complete_python_unittest_discovery", [
            sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v",
        ], env=env, timeout=3600)
        if not self.offline:
            try:
                self.create_database(SP_DB)
                sp_dsn = self.psycopg.conninfo.make_conninfo(
                    self.dsn, dbname=SP_DB)
                sp_env = self.child_env(sp_dsn)
                sp_env["CM_SP16_RUN_PG17"] = "1"
                if self.run("sp16_migrations", [
                    sys.executable, "-m", "intelligence.migrate",
                ], env=sp_env, timeout=600):
                    self.sp16_executed = True
                    for name in ("stage16_pg17", "stage16_upgrade_pg17",
                                 "stage16_inbox_pg17"):
                        self.run("sp_" + name, [
                            sys.executable, "-m", "unittest", "discover",
                            "-s", "tests", "-p",
                            "test_search_planner_" + name + ".py", "-v",
                        ], env=sp_env, timeout=900)
            except Exception as exc:
                self.record("sp16_isolated_setup", "FAIL",
                            reason=type(exc).__name__)
            # HTTP smoke changes only the new disposable database.
            self.run("windows_http_d1_smoke", [
                sys.executable, "tests/windows_d1_smoke.py",
            ], env=env, timeout=540)

    def finish(self):
        completed = datetime.now(timezone.utc).isoformat()
        failures = [x["name"] for x in self.steps if x["status"] != "PASS"]
        status = ("FAIL" if failures else
                  "INCOMPLETE_OFFLINE" if self.offline else
                  "INCOMPLETE_GATES_ONLY" if self.gates_only else
                  "PASS_TECHNICAL")
        report = {
            "schema_version": "contentmachine.local-qa-ci/v1",
            "started_at": self.started, "completed_at": completed,
            "git_sha": self.sha, "mode": "offline" if self.offline else
            "gates_only" if self.gates_only else "full",
            "status": status, "steps": self.steps, "failed_steps": failures,
            "postgres_major": self.pg_major,
            "python_version": sys.version.split()[0],
            "github_actions_executed": False,
            "live_paid_provider_e2e": "NOT_RUN",
            "full_business_pipeline_e2e": "NOT_VERIFIED",
            "report_directory": str(self.output),
            "warnings": [
                "This is local CI-equivalent testing, not a GitHub Actions run.",
                "Some repository suites are intentionally skipped unless separately opted in.",
                "A green technical gate does not certify real ASR/OCR/provider output or the full pipeline.",
                "Log files are local and may contain test fixture data; inspect before sharing.",
            ],
        }
        (self.output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print("\nQA_STATUS=" + status, flush=True)
        print("QA_REPORT=" + str(self.output / "report.json"), flush=True)
        print("FAILED_STEPS=" + (", ".join(failures) if failures else "none"),
              flush=True)
        return 0 if status == "PASS_TECHNICAL" else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--offline", action="store_true",
                       help="No PostgreSQL; can never claim full QA PASS")
    modes.add_argument("--gates-only", action="store_true",
                       help="Run isolated release gates, skipping full unittest/SP16/HTTP; never full PASS")
    args = parser.parse_args(argv)
    runner = Runner(_report_dir(), offline=args.offline,
                    gates_only=args.gates_only)
    try:
        runner.execute()
    except KeyboardInterrupt:
        runner.record("qa_interrupted", "INTERRUPTED",
                      reason="Operator requested cancellation")
    except Exception as exc:
        runner.record("qa_runner", "FAIL", reason=type(exc).__name__)
    finally:
        runner.cleanup()
    return runner.finish()


if __name__ == "__main__":
    raise SystemExit(main())
