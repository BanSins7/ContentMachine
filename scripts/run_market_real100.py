"""REAL Marketing 100 MP4 -> copied Group A/B -> observed Group C runner.

No synthetic videos, no automatically inferred rights, no QDP.
Use:
  python -m scripts.run_market_real100 --projects
  python -m scripts.run_market_real100 --preflight --project PROJECT_ID
  python -m scripts.run_market_real100 --run --project PROJECT_ID --paid-metrics
  python -m scripts.run_market_real100 --watch --project PROJECT_ID

--preflight and --watch are READ-ONLY with respect to provider costs.
--run pins exactly 100 canonical Discovery IDs and queues authorized download.
--paid-metrics is an explicit opt-in for Bright Data provider POSTs, scoped
only to those 100 video IDs. Group B may also incur paid model calls according
to the deployment's enabled and rights-checked settings.

A snapshot with missing C0 provenance or partial B is NEVER labelled a
complete real-world Group C/100-video certification.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import time

from projects import list_projects
from intelligence import config
from intelligence.market_intelligence import batch
from intelligence.market_intelligence import brightdata_jobs
from intelligence.market_intelligence import brightdata_provider
from intelligence.market_intelligence import media_worker
from intelligence.market_intelligence.replica_vision import _enabled as vision_enabled
from intelligence.market_intelligence.contracts import MarketError
from intelligence.project_sources.repository import RegistryProblem

ROOT=Path(__file__).resolve().parents[1]
REPORTS=ROOT/"marketing-qa"/"reports"
SCOPE=100


def _project_id(user_value: str | None) -> str:
    if user_value:
        return user_value
    known=list_projects()
    if len(known)==1:
        return known[0]["id"]
    raise ValueError("project_required_use_projects_listing")


def _rights(conn, project_id, videos) -> dict:
    """Do not assume an account's rights propagate to every child video."""
    granted=0
    transfer=0
    absent=0
    denied=0
    reasons=Counter()
    for ident,_,platform,native,_ in videos:
        row=batch._source_for_video(conn,project_id,{
            "platform":platform,"native_id":native})
        if not row:
            absent+=1
            reasons["source_missing"]+=1
        elif not all(row[1:4]):
            denied+=1
            reasons["fetch_store_or_analyze_denied"]+=1
        else:
            granted+=1
            if len(row)>4 and row[4]:
                transfer+=1
    return {
        "approved_fetch_store_analyze":granted,
        "approved_send_to_llm":transfer,
        "missing_video_source":absent,
        "video_rights_incomplete":denied,
        "blockers":dict(reasons),
    }


def preflight(project_id: str, *, connection=None) -> dict:
    """Read-only provider inspection. _project may synchronize local project profile."""
    batch._project(project_id)
    db=connection or batch.d1.connect()
    with db as conn:
        rows=conn.execute(batch.CANDIDATES_SQL,
                          (project_id,project_id)).fetchall()
        chosen=batch.balanced_selection(rows,SCOPE)
        rights=_rights(conn,project_id,chosen)
    provider=brightdata_provider.readiness()
    tools={name:bool(shutil.which(name)) for name in ("ffmpeg","ffprobe")}
    roots=[p for p in config.setting("CM_MARKET_MEDIA_ROOTS","").split(
        __import__("os").pathsep) if p]
    selected=len(chosen)
    return {
        "project_id":project_id,
        "requested":SCOPE,
        "selected_real_video_ids":selected,
        "missing_to_100":SCOPE-selected,
        "platform_counts":dict(Counter(v[2] for v in chosen)),
        "videos":[{"video_id":v[0],"platform":v[2]} for v in chosen],
        "video_ids":[v[0] for v in chosen],
        "rights":rights,
        "local_tools":tools,
        "media_roots_configured":bool(roots),
        "brightdata_readiness":provider,
        "group_b_real_model_ready":bool(vision_enabled()),
        "can_start_real_media":(
            selected==SCOPE and rights["approved_fetch_store_analyze"]==SCOPE
            and rights["approved_send_to_llm"]==SCOPE
            and bool(roots) and all(tools.values())
            and bool(vision_enabled())
        ),
        "real_group_c_full_certified":False,
        "c0_warning":"Bright Data public post counters lack verified source observation time; C0/C7 cannot be marked certified merely by downloading 100 videos.",
        "external_requests":0,
        "paid_triggers":0,
    }


def _run_marker(project_id,video_ids):
    digest=sha256(json.dumps(video_ids,separators=(",",":")).encode()).hexdigest()[:20]
    return REPORTS/("real100-"+project_id+"-"+digest+"-launched.json")


def _write_report(project_id,report):
    REPORTS.mkdir(parents=True,exist_ok=True)
    path=REPORTS/("real100-"+project_id+"-progress.json")
    temp=path.with_suffix(".pending")
    temp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",
                    encoding="utf-8")
    temp.replace(path)
    return path


def status_report(project_id):
    state=batch.status(project_id)
    videos=state.get("videos") or []
    counts=state.get("counts") or {}
    metric=brightdata_jobs.status(project_id)
    jobs=metric.get("jobs") or []
    # A/B analysis_partial means a persisted media result, not full verified B.
    # Count source-scoped completions honestly and expose C0 coverage separately.
    source_count=sum(v.get("state")=="analysis_partial" for v in videos)
    snapshot=state.get("group_c_snapshot_id")
    report={
        "schema_version":"marketing-real100-progress/v1",
        "checked_at":datetime.now(timezone.utc).isoformat(),
        "project_id":project_id,
        "batch_id":state.get("batch_id"),
        "requested":state.get("requested"),
        "selected":state.get("selected"),
        "download_and_analysis_states":counts,
        "media_analyses_persisted":source_count,
        "metric_jobs":dict(Counter(j.get("state","unknown") for j in jobs)),
        "metric_job_count_in_provider_status":len(jobs),
        "group_c_state":state.get("group_c_state"),
        "group_c_coverage":state.get("group_c_coverage"),
        "group_c_snapshot_id":snapshot,
        "full_100_mp4_a_b_c_certified":False,
        "still_blocked_or_partial":[
            {"video_id":v.get("video_id"),"platform":v.get("platform"),
             "state":v.get("state"),"creative_state":v.get("creative_state"),
             "acquisition_state":v.get("acquisition_state"),
             "acquisition_reason":v.get("acquisition_reason")}
            for v in videos if v.get("state")!="analysis_partial"
        ][:100],
        "notice":"analysis_partial is not full visual semantic certification; strict C0 requires source-verified timing.",
    }
    return report


def watch(project_id: str, *, once=False,interval=20):
    # A one-shot status query must never start a worker or a billable job.
    if not once:
        batch.start_worker()
        media_worker.start_worker()
        # Resume ONLY already persisted provider jobs. No queue_metrics here.
        brightdata_jobs.start_worker()
    while True:
        report=status_report(project_id)
        output=_write_report(project_id,report)
        print(json.dumps({
            "batch_id":report["batch_id"],
            "selected":report["selected"],
            "video_states":report["download_and_analysis_states"],
            "media_analyses_persisted":report["media_analyses_persisted"],
            "brightdata_job_states":report["metric_jobs"],
            "group_c_state":report["group_c_state"],
            "group_c_coverage":report["group_c_coverage"],
            "full_certified":False,
            "progress_report":str(output),
        },ensure_ascii=False),flush=True)
        if once:
            return report
        time.sleep(interval)


def run(project_id: str, *,paid_metrics=False,connection=None):
    checked=preflight(project_id,connection=connection)
    if not checked["can_start_real_media"]:
        raise ValueError("real100_preflight_failed_check_100_sources_rights_ffmpeg_roots_and_B_model")
    if paid_metrics and not checked["brightdata_readiness"]["ready"]:
        raise ValueError("brightdata_paid_metrics_disabled_or_rights_unverified")
    ids=checked["video_ids"]
    marker=_run_marker(project_id,ids)
    marker.parent.mkdir(parents=True,exist_ok=True)
    # Do not replay a billable batch after an uncertain process termination.
    with marker.open("x",encoding="utf-8") as fp:
        json.dump({"schema_version":"marketing-real100-trigger-guard/v1",
                   "project_id":project_id,"video_ids_sha256":
                   sha256(json.dumps(ids).encode()).hexdigest(),
                   "paid_metrics_requested":paid_metrics,
                   "created_at":datetime.now(timezone.utc).isoformat(),
                   "state":"submitted_no_automatic_paid_retry"},fp)
    started=batch.start_batch(project_id,SCOPE)
    if started.get("selected")!=SCOPE:
        raise ValueError("batch_selected_not_exactly_100")
    if paid_metrics:
        # Explicitly scoped; do not trigger unrelated videos registered in project.
        scheduled=brightdata_jobs.queue_metrics(project_id,ids)
        if not scheduled.get("enabled") or not scheduled.get("scoped_to_video_ids"):
            raise ValueError("scoped_brightdata_queue_not_ready")
    else:
        scheduled={"enabled":False,"queued":0,"reason":"no_paid_metrics_requested"}
    print(json.dumps({
        "real_video_batch":started,
        "metric_collection":scheduled,
        "launch_marker":str(marker),
        "warning":"100% complete cannot be certified until real video and C0/B evidence are verified.",
    },ensure_ascii=False),flush=True)
    return started


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    actions=parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--projects",action="store_true")
    actions.add_argument("--preflight",action="store_true")
    actions.add_argument("--run",action="store_true")
    actions.add_argument("--watch",action="store_true")
    actions.add_argument("--status",action="store_true")
    parser.add_argument("--project",help="Project ID; automatic when exactly one saved project")
    parser.add_argument("--paid-metrics",action="store_true",
                        help="Explicit opt-in to possible Bright Data billing, scoped to pinned 100 IDs")
    parser.add_argument("--interval",type=int,default=20)
    args=parser.parse_args(argv)
    if args.paid_metrics and not args.run:
        parser.error("--paid-metrics is accepted ONLY with --run")
    if not 5<=args.interval<=120:
        parser.error("--interval must be 5..120 seconds")
    try:
        if args.projects:
            print(json.dumps({"projects":list_projects()},ensure_ascii=False,indent=2))
            return 0
        project_id=_project_id(args.project)
        if args.preflight:
            print(json.dumps(preflight(project_id),ensure_ascii=False,indent=2))
        elif args.status:
            print(json.dumps(watch(project_id,once=True),ensure_ascii=False,indent=2))
        elif args.run:
            run(project_id,paid_metrics=args.paid_metrics)
            watch(project_id,interval=args.interval)
        else:
            watch(project_id,interval=args.interval)
    except KeyboardInterrupt:
        print("Local monitor stopped. Jobs remain persisted; use --watch to resume without new POSTs.")
    except FileExistsError:
        parser.exit(2,"BLOCKED: real100_launch_marker_already_exists_no_repeat_charge; use --watch\n")
    except (MarketError,RegistryProblem,ValueError,OSError) as exc:
        reason=exc.code if isinstance(exc,MarketError) else (
            "project_unavailable" if isinstance(exc,RegistryProblem)
            else "local_io_error" if isinstance(exc,OSError) else str(exc))
        parser.exit(2,"BLOCKED: "+reason+"\n")
    return 0


if __name__=="__main__":
    sys.exit(main())
