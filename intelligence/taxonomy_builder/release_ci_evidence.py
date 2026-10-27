"""FIX-11-B1: GitHub-backed RELEASE EVIDENCE, never automatic merge.

This command is deliberately separate from CI: a running workflow cannot
prove its own completion. It checks fresh GitHub Actions statuses for the
exact PR head SHA, every job and the latest run attempt. Even all-green
CI does not approve T28/T29 or permission to merge main.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

REQUIRED_WORKFLOWS=(
    "RTB module contract regression",
    "ContentMachine checks",
    "Windows D1 smoke",
    "Knowledge B Stage14 fast QA",
)
DEFAULT_REPOSITORY="BanSins7/ContentMachine"
SHA_RE=re.compile(r"^[a-f0-9]{40}$")
REPO_RE=re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
MAX_BODY=3_000_000


def _success_run(item,head):
    return (
        item.get("head_sha")==head
        and item.get("event")=="pull_request"
        and item.get("status")=="completed"
        and item.get("conclusion")=="success"
        and isinstance(item.get("id"),int)
    )


def evaluate(*,pr,runs,jobs_by_id,main_sha,behind):
    """Evaluate observed GitHub API records, no inferred or user-supplied PASS."""
    issues=[]
    head=pr.get("head",{}).get("sha")
    if not isinstance(head,str) or not SHA_RE.fullmatch(head):
        issues.append("invalid_pr_head_sha")
    if pr.get("state")!="open" or pr.get("merged") is True:
        issues.append("pr_not_open_or_already_merged")
    if pr.get("base",{}).get("ref")!="main":
        issues.append("pr_not_targeting_main")
    if not isinstance(main_sha,str) or not SHA_RE.fullmatch(main_sha):
        issues.append("main_sha_unverified")
    if behind is None or not isinstance(behind,int) or behind<0:
        issues.append("latest_main_comparison_missing")
    elif behind:
        issues.append("feature_behind_latest_main")
    statuses={}
    # The newest run at a given head SHA is authoritative; do NOT accept a
    # passing older attempt when a newer attempt failed, skipped or queued.
    for name in REQUIRED_WORKFLOWS:
        matching=[r for r in runs if r.get("name")==name and
                  r.get("head_sha")==head and r.get("event")=="pull_request"]
        matching.sort(key=lambda r:(str(r.get("created_at") or ""),
                                    r.get("run_attempt",0),
                                    r.get("id",0)),reverse=True)
        if not matching:
            issues.append("missing_required_workflow:"+name)
            statuses[name]={"status":"missing","conclusion":None,"jobs":[]}
            continue
        run=matching[0]
        summary={"id":run.get("id"),"status":run.get("status"),
                 "conclusion":run.get("conclusion"),"attempt":run.get("run_attempt")}
        if not _success_run(run,head):
            issues.append("workflow_not_success:"+name)
        jobs=jobs_by_id.get(run["id"],())
        summary["jobs"]=[{"name":j.get("name"),"status":j.get("status"),
                          "conclusion":j.get("conclusion")} for j in jobs]
        if not jobs:
            issues.append("workflow_jobs_missing:"+name)
        for j in jobs:
            if j.get("run_id")!=run["id"] or j.get("status")!="completed" or j.get("conclusion")!="success":
                issues.append("workflow_job_not_success:"+name+":"+str(j.get("name")))
        statuses[name]=summary
    return {
        "schema_version":"rtb-release-ci-evidence/v1",
        "repository":DEFAULT_REPOSITORY,
        "pr_number":pr.get("number"),
        "head_sha":head,
        "main_sha":main_sha,
        "behind_main":behind,
        "workflows":statuses,
        "observed_workflow_success":not issues,
        # Never claim integration-approved: SP V2, PG historical upgrade,
        # security review and the human merge decision are separate gates.
        "manual_release_approval_required":True,
        "rtb_sp_v2_t28_t29_verified":False,
        "release_ready":False,
        "issues":issues,
    }


def _api(path,token):
    if not path.startswith("/") or "//" in path:
        raise ValueError("invalid_api_path")
    headers={"Accept":"application/vnd.github+json",
             "X-GitHub-Api-Version":"2022-11-28",
             "User-Agent":"ContentMachine-RTB-Release-Audit"}
    if token: headers["Authorization"]="Bearer "+token
    request=urllib.request.Request("https://api.github.com"+path,headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=15) as response:
            raw=response.read(MAX_BODY+1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("github_api_http_error_"+str(exc.code)) from None
    except (TimeoutError,urllib.error.URLError):
        raise RuntimeError("github_api_unreachable") from None
    if len(raw)>MAX_BODY:raise RuntimeError("github_api_response_too_large")
    try:return json.loads(raw.decode("utf-8"))
    except (ValueError,UnicodeError):
        raise RuntimeError("github_api_invalid_json") from None


def fetch_live(repo,pr_number,token):
    if not REPO_RE.fullmatch(repo) or not 1<=pr_number<=999999:
        raise ValueError("invalid_repo_or_pr")
    prefix="/repos/"+repo
    pr=_api(prefix+"/pulls/"+str(pr_number),token)
    head=pr.get("head",{}).get("sha","")
    if not SHA_RE.fullmatch(head):raise RuntimeError("github_pr_head_invalid")
    main=_api(prefix+"/git/ref/heads/main",token)
    main_sha=main.get("object",{}).get("sha")
    if not isinstance(main_sha,str) or not SHA_RE.fullmatch(main_sha):
        raise RuntimeError("github_main_head_unverified")
    # All current PR-source-head attempts, not stale runs on the merge SHA.
    runlist=_api(prefix+"/actions/runs?event=pull_request&head_sha="+
                 quote(head)+"&per_page=100",token)
    if runlist.get("total_count",0)>100:
        raise RuntimeError("too_many_runs_for_single_head_sha_requires_pagination")
    runs=runlist.get("workflow_runs",[])
    relevant={r.get("id"):r for r in runs
              if r.get("name") in REQUIRED_WORKFLOWS and r.get("head_sha")==head}
    jobs={}
    for id in relevant:
        if not isinstance(id,int):continue
        data=_api(prefix+"/actions/runs/"+str(id)+"/jobs?per_page=100",token)
        if data.get("total_count",0)>100:
            raise RuntimeError("job_pagination_required")
        jobs[id]=data.get("jobs",[])
    # Compare the actual feature head to current main; any nonzero behind
    # prevents a release qualification regardless of temporary merge SHA.
    compare=_api(prefix+"/compare/"+quote(head)+"..."+quote(main_sha),token)
    behind=int(compare.get("ahead_by",0))  # main commits ahead of feature head
    report=evaluate(pr=pr,runs=runs,jobs_by_id=jobs,main_sha=main_sha,behind=behind)
    report["repository"]=repo
    report["github_api_live_verified"]=True
    return report


def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo",default=DEFAULT_REPOSITORY)
    ap.add_argument("--pr",type=int,default=32)
    ap.add_argument("--json-out",type=Path)
    args=ap.parse_args(argv)
    token=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN","")
    try:
        report=fetch_live(args.repo,args.pr,token)
    except (ValueError,RuntimeError,KeyError,TypeError) as exc:
        report={"schema_version":"rtb-release-ci-evidence/v1",
                "github_api_live_verified":False,
                "observed_workflow_success":False,
                "manual_release_approval_required":True,
                "release_ready":False,
                "issues":[str(exc)]}
    if args.json_out:
        args.json_out.parent.mkdir(parents=True,exist_ok=True)
        args.json_out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",
                                 encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report.get("observed_workflow_success") else 1


if __name__=="__main__":
    raise SystemExit(main())
