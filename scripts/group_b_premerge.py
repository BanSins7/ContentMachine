#!/usr/bin/env python3
"""GB-MOD-29 exact checkout tree scope audit, NEVER a merge command.

Checks real git diff and writes a source-scoped evidence report. The
standalone audit cannot elevate to production-ready without verified
independent QA, actual video/carousel gold quality, source rights and
A/QDP/Group C/UAE compatibility. Never uses GitHub credentials.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from intelligence.group_b.release import audit_scope,gate_release

def _git(*args):
    v=subprocess.run(["git",*args],cwd=ROOT,capture_output=True,
                     text=True,timeout=30,check=False)
    if v.returncode:
        raise RuntimeError("git_checkout_or_base_ref_missing")
    return v.stdout.strip()

def _report_data(scope,release,main_ref,head_ref):
    return {
        "schema_version":"group-b-premerge-audit/v1",
        "observed_at":datetime.now(timezone.utc).isoformat(),
        "main_ref":main_ref,"head_ref":head_ref,
        "main_sha":scope.main_sha,"group_b_sha":scope.group_b_sha,
        "branch_scope":scope.state.value,
        "changed_files":scope.scope_changes,
        "scope_blockers":list(scope.blocking),
        "release_state":release.state.value,
        "production_merge_ready":release.can_merge,
        "release_blockers":list(release.blocking),
        "signed_peer_and_real_model_proof_seen":False,
        "real_provider_calls":0,
        "merged":False,
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--base",default="origin/main")
    p.add_argument("--head",default="HEAD")
    p.add_argument("--report",default="qa-evidence/group-b-premerge.json")
    a=p.parse_args()
    if a.base not in ("origin/main","main") or a.head!="HEAD":
        raise SystemExit("base/head outside pinned audited refs")
    try:
        base=_git("rev-parse",a.base)
        head=_git("rev-parse",a.head)
        # Diff from merge base. A changed path outside the standalone
        # allowlist is a release blocker, even if the same path exists on main.
        name_status=_git("diff","--name-status","--no-renames",
                         a.base+"..."+a.head)
        additions=[]
        deleted=[]
        for row in name_status.splitlines():
            fields=row.split("\t",1)
            if len(fields)!=2:
                raise RuntimeError("git_diff_not_parseable")
            status,path=fields
            if status=="D":deleted.append(path)
            elif status in ("A","M","T"):additions.append(path)
            else:raise RuntimeError("unsupported_git_diff_status")
        scope=audit_scope(additions,main_sha=base,group_b_sha=head,
                          deleted_paths=deleted,verified_git_diff=True)
        release=gate_release(scope,None,trusted_attestor=None)
        data=_report_data(scope,release,a.base,a.head)
        exit_code=0 if scope.state.value=="audited" else 1
    except (RuntimeError,ValueError,subprocess.TimeoutExpired):
        data={
            "schema_version":"group-b-premerge-audit/v1",
            "main_ref":a.base,"head_ref":a.head,
            "branch_scope":"blocked","scope_blockers":["git_unavailable_or_diff_invalid"],
            "release_state":"blocked","production_merge_ready":False,
            "release_blockers":["real_release_evidence_missing",
                                "trusted_release_attestor_not_installed"],
            "signed_peer_and_real_model_proof_seen":False,
            "real_provider_calls":0,"merged":False,
        }
        exit_code=1
    target=Path(a.report)
    if not target.is_absolute():target=ROOT/target
    target.parent.mkdir(exist_ok=True,parents=True)
    target.write_text(json.dumps(data,sort_keys=True,indent=2)+"\n",
                      encoding="utf-8")
    print("Group B diff scope:",data["branch_scope"],
          "| release:",data["release_state"],
          "| production merge ready:",False)
    return exit_code

if __name__=="__main__":
    raise SystemExit(main())
