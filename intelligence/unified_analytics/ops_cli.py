"""UA-MOD-28: explicit project-authorized operations CLI, zero background work.

python -m intelligence.unified_analytics.ops_cli --project <32hex> --inspect
python -m intelligence.unified_analytics.ops_cli --project <32hex> \
    --recover-expired --limit 25 --apply --confirm-project <same32hex>

No project token/DSN in argv! Dedicated CM_UAE_OPS_DSN and
CM_UAE_OPS_PROJECT_TOKEN (64-hex raw 32-byte secret); verifier hashes in
CM_UAE_OPS_TOKENS_SHA256. Separate from Studio's read/write tokens.
Nothing starts on import; --inspect read only and recovery is opt-in.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import hmac
import json
import os
import re
import signal
import sys
import threading
from typing import Any

from .contracts import AnalyticsBoundaryError, PROJECT_RE
from .job_queue import QualificationJobQueue
from .ops_observability import OPS_WIRE,ProjectOpsReader,new_trace_id

OPS_TOKEN_ENV="CM_UAE_OPS_PROJECT_TOKEN"
OPS_MAP_ENV="CM_UAE_OPS_TOKENS_SHA256"
OPS_DSN_ENV="CM_UAE_OPS_DSN"
OPS_CLI_WIRE="uae-ops-cli/v1"
MAX_RECOVER=100
_TOKEN_RE=re.compile(r"[0-9a-f]{64}\Z")


class OpsCLIError(Exception):
    def __init__(self,code:str):
        super().__init__(code)
        self.code=code


def _deny(code:str)->None:
    raise OpsCLIError(code)


def _project(value:Any)->bool:
    return type(value) is str and PROJECT_RE.fullmatch(value) is not None


def _map(env:dict[str,str])->dict[str,str]:
    raw=env.get(OPS_MAP_ENV,"")
    if type(raw) is not str or not raw or len(raw)>20000:return {}
    try:m=json.loads(raw)
    except (TypeError,ValueError,RecursionError):return {}
    if (type(m) is not dict or not 1<=len(m)<=100
        or any(not _project(k) or type(v) is not str
               or _TOKEN_RE.fullmatch(v) is None for k,v in m.items())):
        return {}
    return m


def authorize(project_id:str,env:dict[str,str])->bool:
    """All caller supplied project IDs and secret formats strictly checked."""
    if not _project(project_id) or type(env) is not dict:
        return False
    token=env.get(OPS_TOKEN_ENV)
    if type(token) is not str or _TOKEN_RE.fullmatch(token) is None:
        return False
    expected=_map(env).get(project_id)
    if type(expected) is not str or _TOKEN_RE.fullmatch(expected) is None:
        return False
    return hmac.compare_digest(
        hashlib.sha256(bytes.fromhex(token)).hexdigest(),expected
    )


def _db_from_env(env:dict[str,str])->Any:
    dsn=env.get(OPS_DSN_ENV)
    if type(dsn) is not str or not dsn or len(dsn)>4096:
        _deny("ops_database_configuration_missing")
    try:
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(
            dsn,autocommit=True,row_factory=dict_row,connect_timeout=5,
            options="-c statement_timeout=10000 -c lock_timeout=3000",
        )
    except Exception as exc:
        raise OpsCLIError("ops_database_unavailable") from exc


def inspect(project_id:str,db:Any)->dict:
    """One read-only snapshot, no job IDs, raw evidence or qualified claims."""
    report=ProjectOpsReader(db).inspect(project_id)
    return json.loads(report.safe_json())


def recover_expired(project_id:str,db:Any,*,limit:int,apply:bool,
                    confirmed_project:str|None)->dict:
    """Only explicit manual recovery; never claim producer qualification."""
    if (not _project(project_id)
        or type(limit) is not int or not 1<=limit<=MAX_RECOVER
        or type(apply) is not bool or not apply
        or confirmed_project!=project_id):
        _deny("ops_recovery_confirmation_required")
    result=QualificationJobQueue(db).recover_expired(
        project_id,limit=limit
    )
    # Do not print or persist job IDs; summary is one operation's result.
    if type(result) is not tuple:
        _deny("ops_recovery_result_invalid")
    states={"retry":0,"dead":0}
    for item in result:
        if (type(item) is not tuple or len(item)!=2
            or item[1] not in states):
            _deny("ops_recovery_result_invalid")
        states[item[1]]+=1
    if len(result)>limit:
        _deny("ops_recovery_result_invalid")
    return {
        "schema_version":OPS_CLI_WIRE,
        "operation":"recover_expired",
        "recovered_jobs":len(result),
        "states":states,
        "qualified":False,
        "producer_run_started":False,
    }


def build_parser()->argparse.ArgumentParser:
    parser=argparse.ArgumentParser(
        prog="python -m intelligence.unified_analytics.ops_cli",
        description="Explicit bounded UAE operational inspection/recovery"
    )
    parser.add_argument("--project",required=True)
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--inspect",action="store_true")
    action.add_argument("--recover-expired",action="store_true")
    parser.add_argument("--limit",type=int,default=25)
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--confirm-project")
    return parser


def main(argv:list[str]|None=None,*,connect:Any=None,
         environ:dict[str,str]|None=None,out:Any=None)->int:
    """Allow injectable DB opener/output for tests; project token always auth.

    Do not output project ID, DSN, token, SQL, worker ID, request, snapshots,
    peer records or arbitrary exception strings.
    """
    parser=build_parser()
    args=parser.parse_args(argv)
    output=out if out is not None else sys.stdout
    env=dict(os.environ) if environ is None else environ
    trace=new_trace_id()
    if not _project(args.project):
        _deny("ops_project_invalid")
    if (type(args.limit) is not int or not 1<=args.limit<=MAX_RECOVER):
        _deny("ops_limit_invalid")
    if args.inspect and (args.apply or args.confirm_project is not None):
        _deny("ops_readonly_flags_invalid")
    if args.recover_expired and (
        args.apply is not True or args.confirm_project!=args.project):
        _deny("ops_recovery_confirmation_required")
    if not authorize(args.project,env):
        _deny("ops_project_access_denied")
    try:
        conn=connect() if connect is not None else _db_from_env(env)
        if not callable(getattr(conn,"__enter__",None)):
            _deny("ops_connection_context_required")
        with conn as db:
            data=(inspect(args.project,db) if args.inspect
                  else recover_expired(args.project,db,limit=args.limit,
                                       apply=True,
                                       confirmed_project=args.confirm_project))
        output.write(json.dumps({
            "trace_id":trace,
            "cli_schema_version":OPS_CLI_WIRE,
            "report":data,
        },sort_keys=True,separators=(",",":"))+"\n")
        return 0
    except (OpsCLIError,AnalyticsBoundaryError) as exc:
        code=(exc.code if isinstance(exc,OpsCLIError)
              else "ops_authoritative_read_unavailable")
        raise OpsCLIError(code) from None
    except Exception as exc:
        raise OpsCLIError("ops_operation_unavailable") from exc


def command_line()->int:
    try:
        return main()
    except OpsCLIError as exc:
        # Only this fixed safe code is printed, no exception values.
        sys.stderr.write("UAE operational command denied: "+exc.code+"\n")
        return 2
    except (SystemExit,KeyboardInterrupt):
        raise


if __name__=="__main__":
    raise SystemExit(command_line())


__all__=("OPS_CLI_WIRE","OPS_TOKEN_ENV","OPS_MAP_ENV","OPS_DSN_ENV",
         "OpsCLIError","authorize","inspect","recover_expired",
         "main","command_line")
