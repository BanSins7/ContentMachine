"""Windows-first, zero-paid-API Marketing A/B/C acceptance runner.

Usage: python scripts/run_market_tests.py [--full] [--db] [--video FILE]
Offline by default. With --db a *disposable* PostgreSQL 17 Docker container is
created and removed; the normal Content Machine database is never touched.
Writes a machine-readable summary and separate stdout/stderr logs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
REPORTS = ROOT / "marketing-test-reports"
REQUIRED_PYTHON = (3, 11)
HEX = re.compile(r"^[0-9a-f]{40}$")

SUITES = {
    "A_video_audio": ("test_market_group_a_complete.py",
                      "test_market_replica_smoke.py"),
    "B_eight_blocks": ("test_market_group_b_native.py",
                       "test_market_group_b_real_mp4.py",
                       "test_market_replica_execution.py"),
    "C_performance": ("test_market_group_c_native.py",
                      "test_market_group_c_batch_finalize.py",
                      "test_market_extended_abc.py"),
    "handoff_integrity": ("test_market_native_evidence.py",
                          "test_market_batch100.py"),
    "provider_no_charge": ("test_market_brightdata_provider.py",
                           "test_market_brightdata_preflight.py",
                           "test_market_brightdata_setup.py",
                           "test_market_brightdata_numeric_fields.py"),
    "api_registry": ("test_market_intelligence_contracts.py",
                     "test_market_intelligence_http.py",
                     "test_market_independent_analytics.py",
                     "test_market_profile_discovery.py"),
}
METRIC = re.compile(r"Ran (\d+) tests? in")
SKIP = re.compile(r"skipped=(\d+)")


def environment(dsn=None):
    """Safeguard local secrets, paid providers and the user's normal DB."""
    blocked = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_DSN")
    env = {key:val for key,val in os.environ.items()
           if not key.upper().endswith(blocked)
           and key.upper() not in (
              "OPENROUTER_KEY", "DATABASE_URL", "PGHOST", "PGPORT",
              "PGUSER", "PGPASSWORD", "PGDATABASE", "PGSERVICE")}
    env.update({
        "PYTHONDONTWRITEBYTECODE":"1",
        "PYTHONUTF8":"1",
        "CM_DB_ENABLED":"1" if dsn else "0",
        "CM_DB_DSN":dsn or "",
        "CM_MARKET_VISION_ENABLED":"0",
        "CM_MARKET_ASR_ENABLED":"0",
        "CM_MARKET_VAD_ENABLED":"0",
        "CM_MARKET_AUDIO_EVENTS_ENABLED":"0",
        "CM_MARKET_TESSERACT_PIN":"",
        "CM_MARKET_TESSERACT_BINARY":"",
        "CM_MARKET_TESSERACT_DATA":"",
        "CM_COLLECTOR_LIVE_ENABLED":"0",
        "CM_BRIGHTDATA_ENABLED":"0",
        "CM_MARKET_B_TAXONOMY_JSON":"",
        "CM_MARKET_B_TAXONOMY_SHA256":"",
        "CM_QDP_GROUP_A_AUTO_DISPATCH":"0",
        "CM_QDP_GROUP_BC_AUTO_DISPATCH":"0",
        "CM_GROUP_A_B_AUTO_DISPATCH":"0",
    })
    return env


def git_head():
    try:
        r = subprocess.run(["git","rev-parse","HEAD"],cwd=ROOT,
                           text=True,capture_output=True,timeout=5,check=False)
        result = r.stdout.strip()
        return result if r.returncode==0 and HEX.fullmatch(result) else None
    except (OSError,subprocess.TimeoutExpired):
        return None


class Acceptance:
    def __init__(self, args):
        self.args=args
        self.started=datetime.now(timezone.utc).isoformat()
        self.dir=REPORTS / (datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+uuid4().hex[:8])
        self.dir.mkdir(parents=True,exist_ok=False)
        self.steps=[]
        self.container=None
        self.dsn=None
        self.head=git_head()

    def record(self,name,status,**kwargs):
        row={"name":name,"status":status,**kwargs}
        self.steps.append(row)
        print(f"[{status}] {name}",flush=True)
        if row.get("log") and status!="PASS":
            print(f"   log: {self.dir / row['log']}",flush=True)
        self.save()
        return row

    def save(self):
        counts={label:sum(step["status"]==label for step in self.steps)
                for label in ("PASS","FAIL","SKIP","BLOCKED")}
        document={
            "schema_version":"marketing-local-acceptance/v1",
            "git_commit":self.head,
            "started_at":self.started,
            "updated_at":datetime.now(timezone.utc).isoformat(),
            "full_requested":self.args.full,
            "database_requested":self.args.db or self.args.full,
            "external_api_calls_expected":0,
            "production_db_used":False,
            "steps":self.steps,
            "counts":counts,
            "all_gates_passed":counts["FAIL"]==0 and counts["BLOCKED"]==0
               and counts["PASS"]>0 and counts["SKIP"]==0,
            "full_live_provider_coverage_certified":False,
            "production_100_video_e2e_certified":False,
        }
        (self.dir/"report.json").write_text(json.dumps(
            document,indent=2,ensure_ascii=False),encoding="utf-8")

    def run(self,name,cmd,*,env=None,timeout=300,require_tests=False):
        log=name.replace("/","_").replace(" ","_")+".log"
        start=time.monotonic()
        print("[RUN] "+name,flush=True)
        try:
            p=subprocess.run(cmd,cwd=ROOT,env=env or environment(),
                             capture_output=True,text=True,errors="replace",
                             timeout=timeout,check=False)
            output=p.stdout+"\n"+p.stderr
            result="PASS" if p.returncode==0 else "FAIL"
        except subprocess.TimeoutExpired as ex:
            output=(ex.stdout or b"").decode("utf-8",errors="replace") if isinstance(
                ex.stdout,bytes) else ex.stdout or ""
            result="FAIL";output+="\nTIMEOUT after "+str(timeout)+" s"
            p=None
        except OSError as ex:
            result="FAIL";output=type(ex).__name__+" while starting command"
            p=None
        # Redact any value of a sensitive environment setting if it appears
        # in subprocess diagnostics; logs never intentionally include DSNs.
        if self.dsn:
            output=output.replace(self.dsn,"[DISPOSABLE_DSN]")
        (self.dir/log).write_text(output,encoding="utf-8")
        n=METRIC.findall(output);sk=SKIP.findall(output)
        total=int(n[-1]) if n else None
        skipped=int(sk[-1]) if sk else 0
        if require_tests and (total is None or total<=0):
            result="FAIL"
        # unittest process exit(0) does not mean coverage complete when
        # its media/model tests were skipped on this machine.
        if require_tests and result=="PASS" and skipped>0:
            result="SKIP"
        self.record(name,result,log=log,
                    exit_code=p.returncode if p else None,
                    duration_seconds=round(time.monotonic()-start,2),
                    tests_run=total,tests_skipped=skipped)
        return result=="PASS"

    def preflight(self):
        self.record("python_3_11_plus",
          "PASS" if sys.version_info>=REQUIRED_PYTHON else "BLOCKED",
          detected=sys.version.split()[0])
        try:
            import psycopg, jsonschema, PIL
            self.record("python_dependencies","PASS")
        except ImportError:
            self.record("python_dependencies","BLOCKED",
                 help="python -m pip install -r intelligence/requirements.txt")
        required=["ffmpeg","ffprobe","node"]
        for program in required:
            installed=bool(shutil.which(program))
            must=program!="node" or self.args.full
            self.record("tool_"+program,
              "PASS" if installed else "BLOCKED" if must else "SKIP",
              detected=shutil.which(program),
              help=("Install "+program+" and reopen PowerShell") if not installed else None)

    def tests(self):
        if shutil.which("node"):
            self.run("javascript_syntax",["node","--check",
                     "site/market-intelligence.js"],timeout=45)
        self.run("python_compile",[sys.executable,"-m","compileall","-q",
                 "intelligence/market_intelligence"],timeout=90)
        scheduled=set()
        for suite,files in SUITES.items():
            for filename in files:
                scheduled.add(filename)
                if not (TESTS/filename).is_file():
                    self.record(suite+"__"+filename,"FAIL",
                                reason="test_module_missing")
                    continue
                self.run(suite+"__"+filename,[sys.executable,"-m","unittest",
                         "discover","-s","tests","-p",filename,"-v"],
                         timeout=210,require_tests=True)
        # New tests cannot silently disappear from the local acceptance gate.
        for file in sorted(TESTS.glob("test_market_*.py")):
            if file.name not in scheduled:
                self.run("other__"+file.stem,[sys.executable,"-m","unittest",
                        "discover","-s","tests","-p",file.name,"-v"],
                        timeout=210,require_tests=True)
        self.run("independent_replica_probe",
                 [sys.executable,"-m",
                  "intelligence.market_intelligence.replica_probe"],timeout=90)

    def start_database(self):
        if not shutil.which("docker"):
            self.record("docker_postgresql_17","BLOCKED",
                        reason="Docker CLI unavailable")
            return False
        name="cm-mi-test-"+uuid4().hex[:12]
        passwd=secrets.token_urlsafe(32)
        command=["docker","run","--detach","--rm","--name",name,
                "-e","POSTGRES_USER=cmtest",
                "-e","POSTGRES_PASSWORD="+passwd,
                "-e","POSTGRES_DB=cm_market_test",
                "-p","127.0.0.1::5432","postgres:17-alpine"]
        try:
            p=subprocess.run(command,cwd=ROOT,capture_output=True,
                             text=True,timeout=80,check=False)
            if p.returncode!=0:
                self.record("docker_postgresql_17","BLOCKED",
                            reason="Could not launch disposable Docker Postgres 17")
                return False
            self.container=name
            portcmd=subprocess.run(["docker","port",name,"5432/tcp"],
                     capture_output=True,text=True,timeout=15,check=False)
            match=re.search(r"127\.0\.0\.1:(\d+)\b",portcmd.stdout)
            if portcmd.returncode or not match:
                self.record("docker_postgresql_17","BLOCKED",
                            reason="Could not discover isolated container port")
                return False
            port=match.group(1)
            from urllib.parse import quote
            self.dsn="postgresql://cmtest:"+quote(passwd,safe="")+"@127.0.0.1:"+port+"/cm_market_test"
            ready=False
            for _ in range(45):
                check=subprocess.run(["docker","exec",name,"pg_isready",
                    "-U","cmtest","-d","cm_market_test"],
                    capture_output=True,timeout=8,check=False)
                if check.returncode==0:
                    ready=True;break
                time.sleep(2)
            self.record("docker_postgresql_17","PASS" if ready else "BLOCKED",
                        isolated=True,port=int(port))
            return ready
        except (OSError,subprocess.TimeoutExpired):
            self.record("docker_postgresql_17","BLOCKED",
                        reason="Docker daemon unavailable or Docker timeout")
            return False

    def db_tests(self):
        if not self.start_database():
            return
        self.run("fresh_migrations_postgres17",
                 [sys.executable,"-m","intelligence.migrate"],
                 env=environment(self.dsn),timeout=450)
        self.run("repeat_migrations_idempotency",
                 [sys.executable,"-m","intelligence.migrate"],
                 env=environment(self.dsn),timeout=450)
        self.run("marketing_schema_postgres17",
                 [sys.executable,"scripts/check_market_test_schema.py"],
                 env=environment(self.dsn),timeout=90)

    def cleanup(self):
        if self.container:
            try:
                subprocess.run(["docker","rm","--force",self.container],
                        capture_output=True,timeout=35,check=False)
                print("[CLEANUP] Disposable PostgreSQL container stopped",flush=True)
            except (OSError,subprocess.TimeoutExpired):
                self.record("docker_cleanup","FAIL",
                     reason="Automatic cleanup failed; use docker rm -f "+self.container)

    def finish(self):
        self.save()
        counts={status:sum(item["status"]==status for item in self.steps)
                 for status in ("PASS","FAIL","SKIP","BLOCKED")}
        print("\nMarketing test report:",self.dir/"report.json")
        print("PASS={PASS} FAIL={FAIL} SKIP={SKIP} BLOCKED={BLOCKED}".format(**counts))
        if counts["FAIL"] or counts["BLOCKED"]:
            print("VERDICT: FAILED OR BLOCKED. Review local .log files and report.json")
            return 1
        if counts["SKIP"]:
            print("VERDICT: PARTIAL. Some optional/required tests were skipped.")
            return 2
        print("VERDICT: OFFLINE/LOCAL GATES PASSED (not a live 100-video certification).")
        return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full",action="store_true",
                        help="require FFmpeg, Node and disposable PG17")
    parser.add_argument("--db",action="store_true",
                        help="create isolated Postgres17 Docker container and verify migrations")
    parser.add_argument("--video",type=Path,
                        help="inspect a real local MP4 with A->B offline (no LLM)")
    args=parser.parse_args()
    runner=Acceptance(args)
    try:
        runner.preflight()
        if any(x["status"]=="BLOCKED" for x in runner.steps):
            return runner.finish()
        runner.tests()
        if args.db or args.full:
            runner.db_tests()
        if args.video:
            if not args.video.is_file():
                runner.record("real_mp4_a_to_b","BLOCKED",reason="video_not_found")
            else:
                runner.run("real_mp4_a_to_b",
                    [sys.executable,"scripts/probe_market_video.py",
                     "--video",str(args.video.resolve()),
                     "--output",str(runner.dir/"real_video_report.json")],
                    timeout=240)
    except KeyboardInterrupt:
        runner.record("manual_interruption","BLOCKED")
    finally:
        runner.cleanup()
    return runner.finish()


if __name__=="__main__":
    sys.exit(main())
