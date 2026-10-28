"""UA-MOD-29: one-shot, separately authorized source retention operator CLI.

Read-only preview (no deletion), explicit bounded invalidation, and a two-
stage destructive UAE purge requiring preview hash, --apply, exact project
AND source confirmation. Never auto-iterate projects/sources or start worker.
Uses UA28 operator credential env, NOT Studio/browser tokens.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .contracts import AnalyticsBoundaryError
from .ops_cli import (
    OPS_TOKEN_ENV,OPS_MAP_ENV,OPS_DSN_ENV,
    authorize,_db_from_env
)
from .ops_observability import new_trace_id
from .source_lifecycle import (
    LIFECYCLE_WIRE,MAX_SWEEP,SourceLifecycle,_check
)

CLI_WIRE="uae-retention-cli/v1"


class RetentionCLIError(Exception):
    def __init__(self,code:str):
        self.code=code
        super().__init__(code)


def _fail(code:str)->None:
    raise RetentionCLIError(code)


def parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(
        prog="python -m intelligence.unified_analytics.retention_cli",
        description="Project-authorized UAE-only source lifecycle"
    )
    p.add_argument("--project",required=True)
    p.add_argument("--source",required=True)
    ops=p.add_mutually_exclusive_group(required=True)
    ops.add_argument("--preview",action="store_true")
    ops.add_argument("--invalidate",action="store_true")
    ops.add_argument("--purge",action="store_true")
    p.add_argument("--limit",type=int,default=50)
    p.add_argument("--apply",action="store_true")
    p.add_argument("--confirm-project")
    p.add_argument("--confirm-source")
    p.add_argument("--plan-sha256")
    return p


def main(argv:list[str]|None=None,*,connect:Any=None,
         environ:dict[str,str]|None=None,out:Any=None)->int:
    p=parser()
    a=p.parse_args(argv)
    env=dict(os.environ) if environ is None else environ
    output=out if out is not None else sys.stdout
    try:_check(a.project,a.source)
    except AnalyticsBoundaryError: _fail("retention_scope_invalid")
    if type(a.limit) is not int or not 1<=a.limit<=MAX_SWEEP:
        _fail("retention_limit_invalid")
    if a.preview:
        if (a.apply or a.confirm_project is not None
            or a.confirm_source is not None or a.plan_sha256 is not None):
            _fail("retention_readonly_flags_invalid")
    else:
        if (not a.apply or a.confirm_project!=a.project
            or a.confirm_source!=a.source):
            _fail("retention_confirmation_required")
        if a.invalidate and a.plan_sha256 is not None:
            _fail("retention_invalid_flags")
        if a.purge and a.plan_sha256 is None:
            _fail("retention_plan_required")
    if not authorize(a.project,env):
        _fail("retention_operator_denied")
    trace=new_trace_id()
    try:
        conn=connect() if connect is not None else _db_from_env(env)
        if not callable(getattr(conn,"__enter__",None)):
            _fail("retention_db_context_required")
        with conn as db:
            lifecycle=SourceLifecycle(db)
            if a.preview:
                plan=lifecycle.preview(a.project,a.source)
                data={
                    "mode":"preview","schema_version":LIFECYCLE_WIRE,
                    "plan_sha256":plan.scope_sha256,
                    "counts":plan.counts,
                    "project_wide_collateral":plan.project_wide_collateral,
                    "writes_performed":False,
                }
            elif a.invalidate:
                outcome=lifecycle.invalidate_unreadable(
                    a.project,a.source,limit=a.limit
                )
                data={
                    "mode":"invalidate","schema_version":LIFECYCLE_WIRE,
                    "affected":outcome.affected,
                    "idempotent_noop":outcome.idempotent_noop,
                    "qualified":False,
                }
            else:
                outcome=lifecycle.purge(
                    a.project,a.source,expected_plan_sha256=a.plan_sha256,
                    confirm_project=a.confirm_project,
                    confirm_source=a.confirm_source,
                )
                data={
                    "mode":"purge","schema_version":LIFECYCLE_WIRE,
                    "affected":outcome.affected,
                    "idempotent_noop":outcome.idempotent_noop,
                    "qualified":False,
                    "external_source_data_deleted":False,
                }
        output.write(json.dumps({
            "cli_schema_version":CLI_WIRE,
            "trace_id":trace,"result":data,
        },sort_keys=True,separators=(",",":"))+"\n")
        return 0
    except RetentionCLIError:raise
    except AnalyticsBoundaryError as exc:
        raise RetentionCLIError("retention_authoritative_operation_denied") from None
    except Exception as exc:
        raise RetentionCLIError("retention_storage_unavailable") from exc


def command_line()->int:
    try:
        return main()
    except RetentionCLIError as exc:
        sys.stderr.write("UAE retention command refused: "+exc.code+"\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("UAE retention operation interrupted\n")
        return 130


if __name__=="__main__":
    raise SystemExit(command_line())


__all__=(
    "CLI_WIRE","RetentionCLIError","parser","main","command_line",
)
