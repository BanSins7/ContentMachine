"""Prepare TikTok top-100 BY OBSERVED VIEWS, including Photo Mode.

Usage:
 python -m scripts.prepare_market_tiktok_top100 --start --username @handle
 python -m scripts.prepare_market_tiktok_top100 --poll --username @handle
 python -m scripts.prepare_market_tiktok_top100 --collect --username @handle

--start is a single opt-in potentially BILLABLE Bright Data by-profile POST.
--poll and --collect only READ the same snapshot. An uncertain trigger is
never automatically retried. Selection is top among returned profile records;
a profile snapshot is not proof that all historical posts were enumerated.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

from intelligence import config
from intelligence.market_intelligence import brightdata_provider as bd
from intelligence.market_intelligence.tiktok_top100 import (
    canonical_tiktok_profile,rank_profile_posts)

ROOT=Path(__file__).resolve().parents[1]
REPORTS=ROOT/"marketing-qa"/"reports"
PROFILE_DATASET="gd_lj71gn6l68bz7y9hc"
SCHEMA="marketing-tiktok-profile-snapshot-job/v1"

def _path(username):
    handle,_=canonical_tiktok_profile(username)
    return REPORTS/("tiktok-top100-"+handle+"-state.json")

def _read(path):
    value=json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict or value.get("schema_version")!=SCHEMA:
        raise ValueError("top100_state_invalid")
    return value

def _write_new(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("x",encoding="utf-8") as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2)
        stream.write("\n")

def _replace(path,data):
    staged=path.with_suffix(".pending")
    with staged.open("x",encoding="utf-8") as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2)
        stream.write("\n")
    staged.replace(path)

def _dataset():
    value=config.setting("CM_MARKET_TIKTOK_PROFILE_POSTS_DATASET",PROFILE_DATASET).strip()
    if not bd.DATASET_RE.fullmatch(value):
        raise ValueError("tiktok_profile_dataset_invalid")
    return value

def start(username:str, *, request=None)->dict:
    handle,profile=canonical_tiktok_profile(username)
    if not bd.readiness()["ready"]:
        raise ValueError("brightdata_provider_not_ready_for_paid_profile_collection")
    state_path=_path(handle)
    dataset=_dataset()
    record={"schema_version":SCHEMA,"username":handle,"profile_url":profile,
            "dataset_id":dataset,"state":"trigger_uncertain_no_auto_retry",
            "snapshot_id":None,"created_at":datetime.now(timezone.utc).isoformat(),
            "trigger_count_max":1,"might_be_billable":True}
    # Atomic exclusive creation before any provider POST.
    _write_new(state_path,record)
    transport=request or bd._api
    result=transport("POST","/datasets/v3/trigger",bd._token(),
                     query={"dataset_id":dataset,"format":"json"},
                     payload=[{"url":profile,"start_date":""}])
    sid=result.get("snapshot_id") if type(result) is dict else None
    if type(sid) is not str or not bd.SNAPSHOT_RE.fullmatch(sid):
        raise ValueError("profile_trigger_ambiguous_do_not_retry")
    record.update(state="submitted",snapshot_id=sid)
    _replace(state_path,record)
    return {"state":"submitted","snapshot_id":sid,"username":handle,
            "profile_dataset_id":dataset,"max_paid_profile_triggers":1,
            "local_state":str(state_path)}

def poll(username:str, *, progress=None)->dict:
    state=_read(_path(username))
    sid=state.get("snapshot_id")
    if type(sid) is not str or not bd.SNAPSHOT_RE.fullmatch(sid):
        raise ValueError("snapshot_id_not_saved_no_paid_retry")
    state_now=(progress or bd.progress)(sid)
    if state_now not in ("starting","running","ready","failed"):
        raise ValueError("profile_snapshot_unknown_status")
    if state_now!=state["state"]:
        state["state"]=state_now
        _replace(_path(username),state)
    return {"username":state["username"],"snapshot_id":sid,"status":state_now,
            "paid_requests":0}

def collect(username:str, *, fetch=None,limit=100)->dict:
    path=_path(username)
    state=_read(path)
    sid=state.get("snapshot_id")
    if type(sid) is not str or not bd.SNAPSHOT_RE.fullmatch(sid):
        raise ValueError("snapshot_id_not_saved_no_paid_retry")
    if state["state"] not in ("ready","completed"):
        raise ValueError("profile_snapshot_not_ready_poll_first")
    if state["state"]=="completed":
        raise ValueError("top100_selection_already_saved")
    records=(fetch or _download)(sid)
    ranked=rank_profile_posts(state["username"],records,limit=limit)
    ranked["profile_snapshot_id"]=sid
    ranked["profile_dataset_id"]=state["dataset_id"]
    ranked["provider_observation_time_verified"]=False
    ranked["full_profile_history_verified"]=False
    output=REPORTS/("tiktok-top100-"+state["username"]+"-selection.json")
    # Immutable selection file and digest, no second provider POST.
    _write_new(output,ranked)
    raw=output.read_bytes()
    state.update(state="completed",selection_file=str(output),
                 selection_sha256=sha256(raw).hexdigest(),
                 selected=ranked["selected"],ready=ranked["ready"])
    _replace(path,state)
    return {"state":"completed","username":state["username"],
            "selected":ranked["selected"],"ready":ranked["ready"],
            "rejected":ranked["rejected"],
            "photo_carousels":sum(p["content_kind"]=="photo_carousel" for p in ranked["posts"]),
            "videos":sum(p["content_kind"]=="video" for p in ranked["posts"]),
            "selection_sha256":state["selection_sha256"],"selection_file":str(output),
            "paid_requests":0,"provider_coverage":"unverified_full_profile_history"}

def _download(sid):
    result=bd._api("GET","/datasets/v3/snapshot/"+sid,bd._read_only_token(),
                   query={"format":"json"})
    if type(result) is not list or len(result)>5000 or any(type(x) is not dict for x in result):
        raise ValueError("profile_snapshot_records_invalid")
    return result

def register(username,project_id):
    from intelligence.market_intelligence.tiktok_top100_register import register_selection
    from scripts.run_market_real100 import _project_id
    return register_selection(_project_id(project_id),username,root=REPORTS)

def configure_local_media_roots():
    """Marketing's carousel root plus the existing verified TikTok MP4 archive.

    The canonical TikTok video downloader writes to ~/Downloads/TikTokArchive.
    Without that second trusted root, its verified MP4 is rejected at the
    Group A/B file guard despite passing acquisition SHA checks.
    """
    import os
    image_root=ROOT/"marketing-qa"/"media"
    archive=Path.home()/"Downloads"/"TikTokArchive"
    roots=[p for p in config.setting("CM_MARKET_MEDIA_ROOTS","").split(os.pathsep) if p]
    for target in (image_root,archive):
        if target.is_symlink():
            raise ValueError("media_root_symlink_not_allowed")
        target.mkdir(parents=True,exist_ok=True)
        trusted=str(target.resolve(strict=True))
        if trusted not in roots:
            roots.append(trusted)
    for root in roots:
        path=Path(root)
        if not path.is_absolute() or not path.is_dir() or path.is_symlink():
            raise ValueError("media_root_not_trusted")
    os.environ["CM_MARKET_MEDIA_ROOTS"]=os.pathsep.join(roots)
    return {"roots_configured":len(roots),"photos_root_exists":True,
            "tiktok_mp4_archive_root_exists":True}


def launch(username,project_id,*,paid_metrics=False,interval=20):
    """Start the exact 100-source media batch and keep workers alive locally.

    Model inference may be billable when configured. Bright Data metrics
    require the single explicit --paid-metrics switch on this launch.
    """
    from intelligence.market_intelligence import batch,brightdata_jobs
    from intelligence.market_intelligence.replica_vision import _enabled
    from scripts.run_market_real100 import _project_id,watch
    import shutil
    import importlib.util
    from hashlib import sha256 as sha256_calc

    if not _enabled():
        raise ValueError("vision_model_not_ready_use_existing_vision_readiness")
    if any(not shutil.which(tool) for tool in ("ffmpeg","ffprobe")):
        raise ValueError("ffmpeg_or_ffprobe_not_installed")
    if importlib.util.find_spec("gallery_dl") is None:
        raise ValueError("gallery_dl_not_installed")
    # The username-only path configures both storage roots automatically.
    # Never trust or add provider-supplied paths.
    configure_local_media_roots()
    if paid_metrics and not bd.readiness()["ready"]:
        raise ValueError("brightdata_metrics_not_ready")
    project=_project_id(project_id)
    # Exact source registration is idempotent and does not contact providers.
    registered=register(username,project)
    ids=registered["video_ids"]
    from intelligence.market_intelligence.tiktok_top100_register import read_selection
    selection,source_sha=read_selection(username,root=REPORTS)
    marker=REPORTS/("tiktok-top100-"+selection["username"]+"-"+project+"-"+source_sha[:16]+"-launched.json")
    # Durable exclusive marker is written before any model/billable worker.
    _write_new(marker,{
        "schema_version":"marketing-tiktok-top100-launch-guard/v1",
        "project_id":project,"profile_snapshot_id":selection["profile_snapshot_id"],
        "selection_sha256":source_sha,"video_ids":ids,
        "paid_metrics_requested":paid_metrics,
        "state":"submitted_no_automatic_paid_retry",
    })
    started=batch.start_batch(project,100,pinned_video_ids=ids)
    if started.get("selected")!=100:
        raise ValueError("top100_batch_scope_mismatch")
    metric={"enabled":False,"queued":0,"reason":"paid_metrics_not_selected"}
    if paid_metrics:
        metric=brightdata_jobs.queue_metrics(project,ids)
        if not metric.get("enabled") or not metric.get("scoped_to_video_ids"):
            raise ValueError("top100_brightdata_scope_not_started")
    print(json.dumps({
        "state":"launched","source_username":selection["username"],
        "selected":100,"photo_carousels":registered["photo_carousels"],
        "regular_videos":registered["regular_videos"],
        "batch_id":started["batch_id"],"metric_collection":metric,
        "launch_guard":str(marker),
        "full_group_c_certified":False,
    },ensure_ascii=False,indent=2),flush=True)
    watch(project,interval=interval,resume_paid=False)

def check(username,project_id,*,paid_metrics=False):
    """Local readiness only; no network, provider POST or model call."""
    import importlib.util
    import shutil
    from intelligence.market_intelligence.replica_vision import _enabled
    from scripts.run_market_real100 import _project_id
    handle,profile=canonical_tiktok_profile(username)
    project=_project_id(project_id)
    provider=bd.readiness()
    external_model=bool(_enabled())
    tools={name:bool(shutil.which(name)) for name in ("ffmpeg","ffprobe")}
    gallery=importlib.util.find_spec("gallery_dl") is not None
    dataset=_dataset()
    state_path=_path(handle)
    previous=_read(state_path) if state_path.exists() else None
    state=(previous or {}).get("state","not_started")
    good=(external_model and all(tools.values()) and gallery
          and bool(provider["ready"]))
    return {"username":handle,"profile_url":profile,"project_id":project,
            "dataset_id":dataset,"provider_ready":provider["ready"],
            "vision_model_ready":external_model,"ffmpeg_ready":tools["ffmpeg"],
            "ffprobe_ready":tools["ffprobe"],"gallery_dl_ready":gallery,
            "state":state,"snapshot_saved":bool(previous and previous.get("snapshot_id")),
            "new_paid_profile_trigger_possible":not state_path.exists(),
            "paid_metric_collection_requested":paid_metrics,
            "ready_for_username_top100":bool(good and state!="trigger_uncertain_no_auto_retry"),
            "external_requests":0,"billable_requests":0}


def run_all(username,project_id,*,allow_paid=False,paid_metrics=False,interval=20):
    """One command: create/reuse one profile snapshot, select, register, launch.

    No repeated paid POST if a previous process ended after submission.
    Existing persisted snapshot is polled and reused; uncertain submission
    fails closed for manual inspection, never automatically recharged.
    """
    if not allow_paid:
        raise ValueError("explicit_provider_cost_opt_in_required_for_--all")
    import time
    handle,_=canonical_tiktok_profile(username)
    path=_path(handle)
    # Never submit a potentially paid profile request if local media/AI tools
    # and provider credentials are not ready. A completed snapshot can still
    # be inspected offline even when the current model has been disabled.
    if not path.exists():
        readiness=check(handle,project_id,paid_metrics=paid_metrics)
        if not readiness["ready_for_username_top100"]:
            raise ValueError("top100_local_preflight_not_ready_run_--check_first")
        started=start(handle)
        print(json.dumps({"phase":"profile_snapshot_submitted",
                          "snapshot_id":started["snapshot_id"],
                          "max_paid_profile_triggers":1},ensure_ascii=False),flush=True)
    state=_read(path)
    if state["state"]=="trigger_uncertain_no_auto_retry":
        raise ValueError("profile_trigger_uncertain_check_provider_no_auto_retry")
    if state["state"]!="completed":
        for attempt in range(90):
            report=poll(handle)
            if report["status"]=="failed":
                raise ValueError("provider_profile_snapshot_failed")
            if report["status"]=="ready":
                break
            if attempt%6==0:
                print(json.dumps({"phase":"waiting_profile_snapshot",
                                  "status":report["status"],
                                  "snapshot_id":report["snapshot_id"]}),flush=True)
            time.sleep(interval)
        else:
            raise ValueError("profile_snapshot_pending_resume_same_--all_no_new_post")
        collected=collect(handle)
        print(json.dumps({"phase":"top100_selected",
                          "selected":collected["selected"],
                          "regular_videos":collected["videos"],
                          "photo_carousels":collected["photo_carousels"]},
                         ensure_ascii=False),flush=True)
        if not collected["ready"]:
            raise ValueError("fewer_than_100_real_view_ranked_public_posts")
    return launch(handle,project_id,paid_metrics=paid_metrics,interval=interval)


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    choices=ap.add_mutually_exclusive_group(required=True)
    choices.add_argument("--start",action="store_true")
    choices.add_argument("--poll",action="store_true")
    choices.add_argument("--collect",action="store_true")
    choices.add_argument("--register",action="store_true",
                         help="Register the exact 100 in D1 with a scoped public research policy")
    choices.add_argument("--launch",action="store_true",
                         help="Start the selected 100 local media analyses and monitor")
    choices.add_argument("--all",action="store_true",
                         help="One-command by-profile discovery, top100, registration and analysis")
    choices.add_argument("--check",action="store_true",
                         help="No-cost local readiness check for username top100")
    ap.add_argument("--username",required=True)
    ap.add_argument("--accept-provider-costs",action="store_true",
                    help="Allow one potentially billable profile discovery trigger in --all")
    ap.add_argument("--project",help="Project ID (auto when exactly one saved project)")
    ap.add_argument("--paid-metrics",action="store_true",
                    help="Explicit one-time opt-in to potentially billable Bright Data counter collection")
    ap.add_argument("--interval",type=int,default=20)
    ap.add_argument("--limit",type=int,default=100)
    args=ap.parse_args(argv)
    if args.paid_metrics and not (args.launch or args.all or args.check):
        ap.error("--paid-metrics is allowed only with --launch/--all/--check")
    if args.accept_provider_costs and not args.all:
        ap.error("--accept-provider-costs is only for --all")
    if not 5<=args.interval<=120:
        ap.error("--interval must be 5..120")
    try:
        if args.check: result=check(args.username,args.project,paid_metrics=args.paid_metrics)
        elif args.start: result=start(args.username)
        elif args.poll: result=poll(args.username)
        elif args.collect: result=collect(args.username,limit=args.limit)
        elif args.register: result=register(args.username,args.project)
        elif args.all: return run_all(args.username,args.project,
                                     allow_paid=args.accept_provider_costs,
                                     paid_metrics=args.paid_metrics,
                                     interval=args.interval)
        else: return launch(args.username,args.project,
                            paid_metrics=args.paid_metrics,interval=args.interval)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError,TypeError) as ex:
        ap.exit(2,"BLOCKED: "+(str(ex) if isinstance(ex,ValueError) else "local_state_invalid")+"\n")
    return 0

if __name__=="__main__":
    sys.exit(main())
