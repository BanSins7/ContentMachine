"""VC-MOD-14: one safe operator entrypoint for the independent Collector module.

python -m intelligence.collector <command>

Default commands are offline/read-only. Database operations require explicit
--db-read/--apply and destructive metadata scrub also requires project-bound
confirmation. This module never imports or instantiates the SRE receiver and
never submits to DTK except through the *existing* explicitly enabled Worker.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from intelligence import config
from .contracts import CollectorError, PROJECT_RE, require_id

SCHEMA_VERSION="collector.operator_cli/v1"
_REQUIRED_MIGRATIONS=(
    "054_collector_planning.sql",
    "055_collector_worker.sql",
    "056_collector_qv_manifest.sql",
    "057_collector_discovery_outbox.sql",
)
_FLAG_NAMES=(
    "CM_DB_ENABLED",
    "CM_COLLECTOR_QV_CONSUMER_ENABLED",
    "CM_COLLECTOR_LIVE_ENABLED",
    "CM_DTK_ARCHIVE_DISABLED_CONFIRMED",
    "CM_COLLECTOR_SRE_DELIVERY_ENABLED",
)
_TRUTH={"1","true","yes","on"}


def _flags() -> dict:
    """Never return DSNs, passwords, tokens, raw .env or API endpoint."""
    return {name:config.setting(name,"0").strip().lower() in _TRUTH
            for name in _FLAG_NAMES}


def _project(value: str) -> str:
    return require_id(value,"project_id",PROJECT_RE)


def _migration_files() -> list[tuple[str,str]]:
    from intelligence.migrate import MIGRATIONS
    return [(name,hashlib.sha256((MIGRATIONS/name).read_bytes()).hexdigest())
            for name in _REQUIRED_MIGRATIONS]


def diagnose(*, database=False,connect=None) -> dict:
    """No network unless --db-read: then SELECT-only on locally configured PG."""
    if type(database) is not bool:
        raise CollectorError("invalid_diagnostic","database must be a boolean",400)
    checks={"module_importable":True,"python_cli_entrypoint":True,
            "external_provider_calls":0,"external_sre_calls":0,
            "flags":_flags()}
    from intelligence.migrate import MIGRATIONS
    existing=[]
    try:
        for name,sha in _migration_files():
            existing.append({"version":name,"sha256":sha,
                             "path_present":True})
    except OSError:
        checks["migrations"]={"ok":False,"reason":"module_migrations_missing"}
    else:
        checks["migrations"]={"ok":True,"files":existing,
                             "migration_directory_exists":MIGRATIONS.is_dir()}
    flags=checks["flags"]
    checks["live_execution_possible_by_flags"]=bool(
        flags["CM_DB_ENABLED"] and flags["CM_COLLECTOR_LIVE_ENABLED"]
        and flags["CM_DTK_ARCHIVE_DISABLED_CONFIRMED"]
    )
    # Flag checks alone are not sufficient for authorization, source rights,
    # provider zero-charge attestation, or an active quota ledger window.
    checks["live_execution_authorized"]=False
    checks["sre_receiver_installed"]=False
    checks["sre_delivery_confirmed"]=False
    if not database:
        checks["database"]={"checked":False,"reason":"opt_in_db_read_required"}
        checks["healthy"]=bool(checks["migrations"]["ok"])
        return {"schema_version":SCHEMA_VERSION,"command":"doctor",**checks}
    if not config.enabled() and connect is None:
        raise CollectorError("db_disabled","CM_DB_ENABLED is required for DB diagnostics",409)
    from .repository import _connection
    with _connection(connect) as conn:
        heartbeat=conn.execute("SELECT 1").fetchone()
        if heartbeat!=(1,):
            raise CollectorError("db_not_ready","Database heartbeat failed",503)
        rows=conn.execute(
            "SELECT version,sha256 FROM schema_migrations WHERE version=ANY(%s)",
            (list(_REQUIRED_MIGRATIONS),),
        ).fetchall()
    applied={name:sha for name,sha in rows}
    mismatched=[x["version"] for x in existing
                if applied.get(x["version"]) is not None
                   and applied[x["version"]]!=x["sha256"]]
    missing=[name for name in _REQUIRED_MIGRATIONS if name not in applied]
    checks["database"]={
        "checked":True,"connected":True,
        "vc_migrations_applied":len(_REQUIRED_MIGRATIONS)-len(missing),
        "vc_migrations_expected":len(_REQUIRED_MIGRATIONS),
        "missing_versions":missing,"checksum_mismatch_versions":mismatched,
        "read_only":True,
    }
    checks["healthy"]=bool(checks["migrations"]["ok"] and
                           not mismatched and not missing)
    return {"schema_version":SCHEMA_VERSION,"command":"doctor",**checks}


def _confirm(project_id,confirm_project,apply: bool):
    pid=_project(project_id)
    if apply and confirm_project!=pid:
        raise CollectorError("operator_confirmation_required",
                             "An exact matching --confirm-project is required",409)
    return pid


def _owner_scope(command: str,project_id: str,*,apply=False,confirm_project=None,
                 limit=50,connect=None) -> dict:
    """Backend-only explicit commands; never public HTTP handlers."""
    pid=_confirm(project_id,confirm_project,apply)
    from . import recovery
    if command=="cancel-reconcile":
        result=recovery.reconcile_cancelled(pid,limit=limit,
                                           dry_run=not apply,connect=connect)
    elif command=="retention-audit":
        if apply:
            raise CollectorError("invalid_operator_command",
                                 "Retention audit is always read-only",400)
        result=recovery.retention_audit(pid,limit=limit,connect=connect)
    elif command=="retention-scrub":
        if not apply:
            audit=recovery.retention_audit(pid,limit=limit,connect=connect)
            result={"schema_version":"collector.retention_scrub_preview/v1",
                    "project_id":pid,"dry_run":True,
                    "due_metadata":sum(x["action_required"]=="scrub_expired"
                                       for x in audit["metadata"]),
                    "has_more":audit["has_more"],"provider_calls":0,
                    "deleted_audit_rows":0}
        else:
            result=recovery.scrub_expired_metadata(
                pid,limit=limit,confirm=True,connect=connect)
    elif command=="sre-recover":
        if not apply:
            from .repository import _connection
            with _connection(connect) as conn:
                resultrow=conn.execute(
                    """SELECT COUNT(*) FROM collector_sre_outbox
                       WHERE project_id=%s AND state='leased'
                         AND lease_until<now()""",(pid,),
                ).fetchone()
            result={"schema_version":"collector.sre_recovery_preview/v1",
                    "project_id":pid,"expired_leases":resultrow[0],
                    "dry_run":True,"external_calls":0}
        else:
            from .sre_delivery import recover_expired
            result=recover_expired(pid,connect=connect)
    else:
        raise CollectorError("invalid_operator_command","Unknown operator command",400)
    return {"schema_version":SCHEMA_VERSION,"command":command,"result":result,
            "operator_apply":bool(apply),"external_provider_calls":0}


def _parser() -> argparse.ArgumentParser:
    parser=argparse.ArgumentParser(
        prog="python -m intelligence.collector",
        description="Operator-only Video Collector module (safe by default)"
    )
    sub=parser.add_subparsers(dest="command",required=True)
    doctor=sub.add_parser("doctor",help="offline flags/schema readiness, no secrets")
    doctor.add_argument("--db-read",action="store_true",
                        help="opt-in PostgreSQL SELECT-only migration checksum check")
    worker=sub.add_parser("worker",help="existing gated DTK Worker; explicit opt-in")
    worker.add_argument("--once",action="store_true")
    worker.add_argument("--interval",type=float,default=4.0)
    pub=sub.add_parser("publisher-help",
                       help="print the existing QV publisher command (no publication)")
    for name in ("cancel-reconcile","retention-audit","retention-scrub",
                 "sre-recover"):
        p=sub.add_parser(name)
        p.add_argument("--project-id",required=True)
        p.add_argument("--limit",type=int,default=50)
        if name!="retention-audit":
            p.add_argument("--apply",action="store_true",
                           help="make bounded PostgreSQL changes")
            p.add_argument("--confirm-project",default=None,
                           help="must match exact --project-id to apply")
    return parser


def main(argv=None,*,connect=None) -> int:
    parser=_parser()
    args=parser.parse_args(argv)
    try:
        if args.command=="doctor":
            result=diagnose(database=args.db_read,connect=connect)
        elif args.command=="worker":
            from .worker import main as worker_main
            # Existing Worker enforces CM_COLLECTOR_LIVE_ENABLED, archive
            # attestation, database rights and quota; never bypass it.
            return worker_main(["--interval",str(args.interval)]
                               +( ["--once"] if args.once else []))
        elif args.command=="publisher-help":
            result={"schema_version":SCHEMA_VERSION,"command":"publisher-help",
                    "existing_cli":"python -m intelligence.collector.publisher_cli",
                    "publish_requires_explicit_flag":True,
                    "provider_calls":0}
        else:
            result=_owner_scope(
                args.command,args.project_id,
                apply=getattr(args,"apply",False),
                confirm_project=getattr(args,"confirm_project",None),
                limit=args.limit,connect=connect,
            )
        print(json.dumps({"ok":True,**result},ensure_ascii=False,
                         indent=2,sort_keys=True,default=str))
        if args.command=="doctor" and result.get("healthy") is False:
            return 2
        return 0
    except (CollectorError,OSError,RuntimeError,ValueError) as exc:
        # NEVER return raw psycopg exceptions or connection DSN; sensitive
        # server error details are intentionally not included.
        code=exc.code if isinstance(exc,CollectorError) else "operator_error"
        print(json.dumps({"ok":False,"error_code":code},sort_keys=True),
              file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
