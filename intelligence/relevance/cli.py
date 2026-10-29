"""M41 standalone SRE CLI, opt-in and no paid providers.

python -m intelligence.relevance.cli self-check [--database]
python -m intelligence.relevance.cli migration-diagnosis [--database]
python -m intelligence.relevance.cli preview --project ... --publication ... --content-ids 1,2
python -m intelligence.relevance.cli worker-once --project ... --allow-offline-fixture
python -m intelligence.relevance.cli worker-loop --project ... --control-dir ...
                                          --allow-offline-fixture
python -m intelligence.relevance.cli stop/status/recover-stale --control-dir ...

Passive import/help/self-check (without --database) do NOT connect a DB,
read files outside source manifest, initialize ONNX, call a provider, run
workers, or open HTTP sockets. The only job mode is project-scoped S4
OFFLINE_FIXTURE; no M14 LIVE claims, paid Opus, finance or QDP delivery.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from collections.abc import Mapping

from .config import load_settings
from .v1_contracts import SREContractError,canonical_uuid,project_id,reject
from .worker_control import WorkerControl,WorkerControlBlocked

VERSION="sre-standalone-cli/v1"
EXIT_SUCCESS=0
EXIT_BLOCKED=2
EXIT_ERROR=3
CSV_IDS=re.compile(r"[1-9][0-9]{0,15}(?:,[1-9][0-9]{0,15}){0,49}\Z")
M41_FIXTURE_NOTICE="OFFLINE_FIXTURE_ONLY_NOT_LIVE_OR_PAID"


def _parser():
    p=argparse.ArgumentParser(
        prog="python -m intelligence.relevance.cli",
        description="SRE standalone operator CLI. Defaults disabled; zero paid POST.",
    )
    sp=p.add_subparsers(dest="command",required=True)

    pre=sp.add_parser("self-check",help="side-effect-free config/source diagnosis")
    pre.add_argument("--database",action="store_true",
        help="explicit read-only PostgreSQL schema/ledger preflight")
    mig=sp.add_parser("migration-diagnosis",
        help="source manifest; --database queries ledger READ ONLY")
    mig.add_argument("--database",action="store_true")

    preview=sp.add_parser("preview",help="approved C9 + D1 read-only preview")
    preview.add_argument("--project",required=True)
    preview.add_argument("--publication",required=True)
    preview.add_argument("--content-ids",required=True,
        help="1..50 selected SRE content BIGINT IDs separated by commas")
    for command in ("worker-once","worker-loop","start"):
        sub=sp.add_parser(command,help="EXPLICIT OFFLINE_FIXTURE ONLY; no LIVE or paid work")
        sub.add_argument("--project",required=True)
        sub.add_argument("--allow-offline-fixture",action="store_true",
            help="operator acknowledges simulated labels, never genuine LIVE output")
        sub.add_argument("--lease-seconds",type=int,default=60)
        if command in ("worker-loop","start"):
            sub.add_argument("--control-dir",required=True,
                help="pre-existing private owner-only directory (0700 on Unix)")
            sub.add_argument("--poll-seconds",type=int,default=3)
            sub.add_argument("--max-cycles",type=int,default=0,
                help="0=foreground until stop, or 1..100000 max polling cycles")
    for command in ("stop","status","recover-stale"):
        sub=sp.add_parser(command,help="cooperative file control, NEVER kill PID")
        sub.add_argument("--control-dir",required=True)
        if command=="recover-stale":
            sub.add_argument("--acknowledge-audited-stale",action="store_true",
                help="verify owner truly dead; operator separately audits DB intents")
    return p


def _csv(raw):
    if type(raw) is not str or CSV_IDS.fullmatch(raw) is None:
        reject("content_ids","1..50 comma-delimited positive SRE BIGINTs")
    ids=[int(x) for x in raw.split(",")]
    if any(x>9223372036854775807 for x in ids) or ids!=sorted(set(ids)):
        reject("content_ids","unique ascending PostgreSQL BIGINTs required")
    return ids


def _public_settings(source):
    data=load_settings(source)
    public=data.public_dict()
    # Do not disclose which provider credential is present to a caller.
    public.pop("provider_key_configured",None)
    public.pop("database_configured",None)
    return data,public


def _sql_blockers():
    """Known malformed M28 migration SQL, no edit or hash workaround.

    This exact parser defect is already forensically identified; marker is
    specific so a later checksum-safe repair won't produce false PASS.
    This is NOT a general SQL syntax validator.
    """
    root=Path(__file__).resolve().parents[1]/"migrations"
    file=root/"062_sre_live_opus_receipts.sql"
    try:
        data=file.read_text(encoding="utf-8")
    except Exception:
        return ["migration_062_source_unreadable"]
    if "CHECK(evidence_sha256 ~ '^[0-9a-f]{64} BIGINT NOT NULL" in data:
        return ["migration_062_known_malformed_check_sql"]
    return []


def _connect_explicit(settings,*,connect=None):
    if not settings.database_enabled or not settings.database_configured:
        reject("database","explicit CM_DB_ENABLED=1 and CM_DB_DSN required",
               "blocked_config")
    if connect is not None:
        return connect()
    try:
        import psycopg
        return psycopg.connect(settings._database_dsn,
                               connect_timeout=3,autocommit=True)
    except Exception:
        raise SREContractError("blocked_config","database",
                               "PostgreSQL connection unavailable") from None


def _db_preflight(conn,*,migrations=True):
    from .opus_dispatch_intent import _committable
    _committable(conn)
    if migrations:
        from .migration_guard import preflight_read_only
        result=preflight_read_only(conn)
        if result["status"]!="ready" or result["pending"] or result["checksum_mismatch"]:
            reject("database","migration ledger not fully installed and SHA matched",
                   "blocked_config")
        if _sql_blockers():
            reject("database","known malformed migration 062 detected",
                   "blocked_config")
    return True


def _source_diagnosis():
    from .migration_guard import assess_applied,MigrationBlocked
    try:
        manifest=assess_applied(None)
    except MigrationBlocked:
        return {"status":"blocked","reasons":["migration_manifest_invalid"]}
    problems=_sql_blockers()
    return {"status":"blocked" if problems else "source_manifest_valid",
            "manifest_fingerprint":manifest["manifest_fingerprint"],
            "next_sql_prefix":manifest["next_sre_migration_prefix"],
            "known_sql_blockers":problems,
            "database_installed_confirmed":False}


def _self_check(source,*,database=False,connect=None):
    settings,public=_public_settings(source)
    source_report=_source_diagnosis()
    response={
        "schema_version":VERSION,"command":"self-check",
        "settings":public,"source_migrations":source_report,
        "paid_post_calls":0,"live_worker_enabled":False,
    }
    if not database:
        response["status"]="disabled" if not settings.flags["enabled"] else (
            "blocked_migrations" if source_report["known_sql_blockers"] else
            "requires_database_and_project_verification")
        return response
    if source_report["known_sql_blockers"]:
        response["status"]="blocked_migrations"
        return response
    with _connect_explicit(settings,connect=connect) as conn:
        _db_preflight(conn)
        from .health import check_read_only_schema
        if not check_read_only_schema(conn):
            response["status"]="blocked_schema"
        else:
            response["status"]="requires_project_rights_and_runtime_proof"
        response["database_ledger_checked"]=True
    return response


def _migration_diagnosis(source,*,database=False,connect=None):
    report=_source_diagnosis()
    # M42: exact on-disk 062 Git blob and optionally installed ledger are
    # separate from the broad M05 migration preflight. Neither path repairs
    # a schema, rewrites a checksum or claims production readiness.
    from .m42_migration_062_audit import inspect_062_source, inspect_062_installed
    local_062=inspect_062_source()
    if not database:
        return {"schema_version":VERSION,"command":"migration-diagnosis",
                **report,"m28_062_audit":local_062,"paid_post_calls":0}
    settings=load_settings(source)
    with _connect_explicit(settings,connect=connect) as conn:
        from .migration_guard import preflight_read_only
        _db_preflight(conn,migrations=False)
        applied=preflight_read_only(conn)
        installed_062=inspect_062_installed(conn)
    blocked=(bool(report["known_sql_blockers"]) or
             installed_062["status"]=="blocked" or
             applied["status"]!="ready" or bool(applied["pending"]))
    return {
        "schema_version":VERSION,"command":"migration-diagnosis",
        "status":"blocked" if blocked else "source_and_ledger_consistent",
        "source":report,"installed_status":applied["status"],
        "m28_062_audit":installed_062,
        "applied_count":applied["applied_count"],
        "pending":applied["pending"],"checksum_mismatch":applied["checksum_mismatch"],
        "retroactive_additions":applied["retroactive_additions"],
        "paid_post_calls":0,
    }


def _preview(source,args,*,connect=None):
    settings=load_settings(source)
    if not settings.flags["enabled"]:
        reject("enabled","CM_SRE_ENABLED=1 required for explicit preview","feature_disabled")
    pid=project_id(args.project)
    pub=canonical_uuid(args.publication,"publication_id")
    ids=_csv(args.content_ids)
    with _connect_explicit(settings,connect=connect) as conn:
        _db_preflight(conn)
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            from .dataset_preview import read_dataset_preview
            preview=read_dataset_preview(conn,pid,pub,ids)
    # No raw source body; original preview is bounded, right-scoped and
    # includes explicit blocked entries for source access changes.
    return {"schema_version":VERSION,"command":"preview",
            "project_id":pid,"preview":preview,
            "paid_post_calls":0,"execution_allowed":False}


def _worker_gate(source,args):
    settings=load_settings(source)
    pid=project_id(args.project)
    if (not args.allow_offline_fixture or not settings.flags["enabled"]
            or not settings.flags["offline_worker"]):
        reject("worker","explicit --allow-offline-fixture plus "
               "CM_SRE_ENABLED=1 and CM_SRE_OFFLINE_WORKER=1 required",
               "feature_disabled")
    seconds=args.lease_seconds
    if type(seconds) is not int or not 5<=seconds<=3600:
        reject("lease_seconds","expected 5..3600")
    return settings,pid,seconds


def _work_once(conn,pid,seconds,*,runner=None):
    if runner is None:
        from .offline_scoped_worker import work_once_for_project
        runner=work_once_for_project
    result=runner(conn,pid,lease_seconds=seconds)
    if (type(result) is not dict or
            result.get("paid_post_calls")!=0 or
            result.get("live_results_written")!=0 or
            result.get("project_id")!=pid):
        reject("worker","untrusted scoped worker receipt","invalid_evidence")
    return result


def _worker_once(source,args,*,connect=None,runner=None):
    settings,pid,seconds=_worker_gate(source,args)
    with _connect_explicit(settings,connect=connect) as conn:
        _db_preflight(conn)
        outcome=_work_once(conn,pid,seconds,runner=runner)
    return {"schema_version":VERSION,"command":"worker-once",
            "execution_mode":"offline_fixture","notice":M41_FIXTURE_NOTICE,
            "result":outcome,"paid_post_calls":0}


def _worker_loop(source,args,*,connect=None,runner=None,
                 sleep=time.sleep,control_factory=WorkerControl):
    settings,pid,seconds=_worker_gate(source,args)
    if (type(args.poll_seconds) is not int or
            not 1<=args.poll_seconds<=60 or
            type(args.max_cycles) is not int or
            not 0<=args.max_cycles<=100000):
        reject("worker_loop","poll 1..60 and max-cycles 0..100000 required")
    # Fully verify database first, THEN acquire the cooperative foreground
    # owner file. No idle daemon is spawned from import or passive CLI use.
    with _connect_explicit(settings,connect=connect) as conn:
        _db_preflight(conn)
        ctrl=control_factory(args.control_dir)
        own=ctrl.claim(pid)
        cycles=0;processed=0;status="stopped"
        try:
            while True:
                if ctrl.stop_requested(own):
                    status="operator_stop_requested";break
                if args.max_cycles and cycles>=args.max_cycles:
                    status="max_cycles_reached";break
                result=_work_once(conn,pid,seconds,runner=runner)
                cycles+=1
                processed+=result["processed"]
                if result["status"]=="drained":
                    sleep(args.poll_seconds)
        except KeyboardInterrupt:
            status="keyboard_interrupt"
        finally:
            # Guaranteed cooperative cleanup on normal exceptions; after a
            # hard power loss files intentionally remain for explicit audit.
            ctrl.release(own)
    return {
        "schema_version":VERSION,"command":"worker-loop",
        "status":status,"processed":processed,"cycles":cycles,
        "project_id":pid,"execution_mode":"offline_fixture",
        "notice":M41_FIXTURE_NOTICE,
        "paid_post_calls":0,"live_results_written":0,
    }


def run(args,*,environ=None,connect=None,runner=None,
        control_factory=WorkerControl,sleep=time.sleep):
    source=os.environ if environ is None else environ
    if not isinstance(source,Mapping):
        reject("environment","expected config mapping")
    command=args.command
    if command=="self-check":
        return _self_check(source,database=args.database,connect=connect)
    if command=="migration-diagnosis":
        return _migration_diagnosis(source,database=args.database,connect=connect)
    if command=="preview":
        return _preview(source,args,connect=connect)
    if command=="worker-once":
        return _worker_once(source,args,connect=connect,runner=runner)
    if command in ("worker-loop","start"):
        return _worker_loop(source,args,connect=connect,runner=runner,
                            control_factory=control_factory,sleep=sleep)
    if command in ("stop","status","recover-stale"):
        controller=control_factory(args.control_dir)
        if command=="stop":out=controller.request_stop()
        elif command=="status":out=controller.status()
        else:out=controller.recover_stale(
            acknowledged=args.acknowledge_audited_stale)
        return {"schema_version":VERSION,"command":command,
                **out,"paid_post_calls":0}
    reject("command","unsupported SRE CLI command")


def main(argv=None,*,environ=None,connect=None,runner=None,
         control_factory=WorkerControl,sleep=time.sleep,emit=None):
    if emit is None:emit=print
    args=_parser().parse_args(argv)
    try:
        data=run(args,environ=environ,connect=connect,runner=runner,
                 control_factory=control_factory,sleep=sleep)
    except (SREContractError,WorkerControlBlocked) as exc:
        code=(exc.code if isinstance(exc,SREContractError) else str(exc))
        # Codes and our own controlled labels only; do NOT echo DB/vendor/env
        # exceptions, content previews, source paths or supplied secrets.
        emit(json.dumps({"schema_version":VERSION,"status":"blocked",
                         "reason_code":code,"paid_post_calls":0},
                        ensure_ascii=False,sort_keys=True))
        return EXIT_BLOCKED
    except Exception:
        emit(json.dumps({"schema_version":VERSION,"status":"blocked",
                         "reason_code":"operation_failed",
                         "paid_post_calls":0},sort_keys=True))
        return EXIT_ERROR
    emit(json.dumps(data,ensure_ascii=False,sort_keys=True,default=str))
    return EXIT_BLOCKED if data.get("status") in (
        "blocked","blocked_migrations","blocked_schema") else EXIT_SUCCESS


if __name__=="__main__":
    raise SystemExit(main())
