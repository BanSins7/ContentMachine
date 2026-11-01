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
PROFILE_DATASET="gd_m7n5v2gq296pex2f5m"  # Bright Data public TikTok Posts by Profile example
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

def _submit_profile(record:dict, state_path:Path, *, request=None)->dict:
    """Exactly one provider POST per durable attempt; no hidden retries."""
    transport=request or bd._api
    try:
        result=transport("POST","/datasets/v3/trigger",bd._token(),
                         query={"dataset_id":record["dataset_id"],"format":"json"},
                         payload=[{"url":record["profile_url"],"start_date":""}])
    except bd.MarketError as exc:
        if exc.code=="brightdata_http_400":
            # An explicit HTTP 400 is a rejection, not an ambiguous submission.
            # Save that fact without saving potentially sensitive provider bodies.
            record.update(state="rejected_http_400",provider_http_status=400)
            _replace(state_path,record)
        raise
    sid=result.get("snapshot_id") if type(result) is dict else None
    if type(sid) is not str or not bd.SNAPSHOT_RE.fullmatch(sid):
        raise ValueError("profile_trigger_ambiguous_do_not_retry")
    record.update(state="submitted",snapshot_id=sid)
    _replace(state_path,record)
    return {"state":"submitted","snapshot_id":sid,
            "username":record["username"],"profile_dataset_id":record["dataset_id"],
            "max_paid_profile_triggers":record["trigger_count_max"],
            "local_state":str(state_path)}


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
    return _submit_profile(record,state_path,request=request)


def retry_http_400(username:str, *, request=None)->dict:
    """Explicit one-time recovery for a *confirmed* HTTP 400 on an old dataset.

    The caller attests that the previous API response was HTTP 400; unlike a
    timeout or HTTP 5xx, a rejected request has not returned a snapshot. This
    never retries the same dataset and never retries automatically.
    """
    handle,_=canonical_tiktok_profile(username)
    path=_path(handle)
    record=_read(path)
    if record.get("username")!=handle or record.get("snapshot_id") is not None:
        raise ValueError("profile_retry_not_allowed_snapshot_exists")
    if record["state"] not in ("trigger_uncertain_no_auto_retry","rejected_http_400"):
        raise ValueError("profile_retry_not_allowed_state")
    if not bd.readiness()["ready"]:
        raise ValueError("brightdata_provider_not_ready_for_paid_profile_collection")
    new_dataset=_dataset()
    if record["dataset_id"]==new_dataset:
        raise ValueError("profile_retry_requires_different_dataset_id_check_override")
    if record.get("retries_after_explicit_http_400",0)!=0:
        raise ValueError("profile_retry_already_used")
    record.update(
        previous_dataset_id=record["dataset_id"],
        previous_http_status_confirmed_by_operator=400,
        dataset_id=new_dataset,
        retries_after_explicit_http_400=1,
        trigger_count_max=2,
        state="trigger_uncertain_no_auto_retry",
        provider_http_status=None,
    )
    # Persist the new attempt *before* submitting; crashing cannot cause an
    # implicit second charge. The original dataset remains in the audit record.
    _replace(path,record)
    return _submit_profile(record,path,request=request)

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



def _missing_marketing_schema_tables(conn)->list[str]:
    """Read-only guard: never create a paid batch against incomplete MI schema."""
    required=("mi_video_analyses","mi_carousel_analyses",
              "mi_brightdata_numeric_fields")
    query="SELECT "+",".join("to_regclass(%s)" for _ in required)
    found=conn.execute(query,required).fetchone()
    if found is None or len(found)!=len(required):
        raise ValueError("top100_schema_inspection_invalid")
    return [name for name,table in zip(required,found) if table is None]


def preflight_selected(username:str, project_id:str, *, paid_metrics=False)->dict:
    """Read-only check of the exact immutable TikTok top-100, not project rotation.

    This does not contact Bright Data, create a media job, configure directories,
    or launch AI. Other registered project videos are deliberately irrelevant.
    """
    import importlib.util
    import shutil
    from collections import Counter
    from intelligence.market_intelligence import batch
    from intelligence.market_intelligence.replica_vision import _enabled
    from intelligence.market_intelligence.tiktok_top100_register import read_selection
    from scripts.run_market_real100 import _project_id

    project=_project_id(project_id)
    selected,selection_sha=read_selection(username,root=REPORTS)
    posts=selected["posts"]
    natives=[p["native_id"] for p in posts]
    with batch.d1.connect() as conn:
        missing_schema=_missing_marketing_schema_tables(conn)
        rows=conn.execute("""
            SELECT id,platform_video_id,canonical_url
            FROM videos
            WHERE platform='tiktok' AND platform_video_id=ANY(%s)
        """,(natives,)).fetchall()
        by_native={native:(ident,url) for ident,native,url in rows}
        ids=[]
        missing=0
        wrong_url=0
        rights=Counter()
        for p in posts:
            found=by_native.get(p["native_id"])
            if not found:
                missing+=1
                continue
            ident,canonical=found
            if canonical!=p["url"]:
                wrong_url+=1
                continue
            ids.append(ident)
            source=batch._source_for_video(conn,project,{
                "platform":"tiktok","native_id":p["native_id"]})
            if source is None:
                rights["missing_source"]+=1
            elif not all(source[1:4]):
                rights["fetch_store_analyze_denied"]+=1
            elif not source[4]:
                rights["llm_transfer_denied"]+=1
            else:
                rights["approved"]+=1

    binaries={tool:bool(shutil.which(tool)) for tool in ("ffmpeg","ffprobe")}
    gallery=importlib.util.find_spec("gallery_dl") is not None
    model=bool(_enabled())
    provider=bd.readiness()
    kinds=dict(Counter(p["content_kind"] for p in posts))
    exactly_pinned=(
        missing==0 and wrong_url==0 and len(ids)==100
        and len(set(ids))==100 and rights["approved"]==100
    )
    ready=(not missing_schema and exactly_pinned and all(binaries.values()) and
           (kinds.get("photo_carousel",0)==0 or gallery) and model and
           (not paid_metrics or bool(provider["ready"])))
    return {
        "project_id":project,
        "username":selected["username"],
        "selection_sha256":selection_sha,
        "snapshot_id":selected["profile_snapshot_id"],
        "requested":100,
        "selected_exact_tiktok_posts":len(ids),
        "selected_content_kinds":kinds,
        "platform_counts":{"tiktok":len(ids)},
        "selection_order_preserved":len(ids)==100 and len(set(ids))==100,
        "pinned_video_ids_sha256":sha256(
            json.dumps(ids,separators=(",",":")).encode("utf-8")).hexdigest() if len(ids)==100 else None,
        "missing_selected_posts_in_db":missing,
        "selected_posts_with_different_db_url":wrong_url,
        "selected_rights":{
            "approved_fetch_store_analyze_and_llm":rights["approved"],
            "missing_source":rights["missing_source"],
            "fetch_store_analyze_denied":rights["fetch_store_analyze_denied"],
            "llm_transfer_denied":rights["llm_transfer_denied"],
        },
        "ffmpeg_ready":binaries["ffmpeg"],
        "ffprobe_ready":binaries["ffprobe"],
        "gallery_dl_ready":gallery,
        "group_b_real_model_ready":model,
        "missing_marketing_schema_tables":missing_schema,
        "migration_required":bool(missing_schema),
        "migration_command":"python -m intelligence.migrate" if missing_schema else None,
        "media_roots_configured_automatically_on_launch":True,
        "paid_metrics_requested":paid_metrics,
        "paid_metrics_provider_ready":provider["ready"],
        "can_launch_selected_top100":ready,
        "external_requests":0,
        "billable_requests":0,
    }

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
    # Registration is idempotent and keeps --all usable without a separate
    # manual --register step. No media or provider jobs are started here.
    project=_project_id(project_id)
    registered=register(username,project)
    scoped_preflight=preflight_selected(username,project,paid_metrics=paid_metrics)
    if not scoped_preflight["can_launch_selected_top100"]:
        raise ValueError("top100_selected_preflight_failed_run_--preflight-selected")
    if registered["video_ids"] and len(registered["video_ids"])!=100:
        raise ValueError("top100_pinned_registration_size_changed")
    # The username-only path configures both storage roots automatically.
    # Never trust or add provider-supplied paths.
    configure_local_media_roots()
    if paid_metrics and not bd.readiness()["ready"]:
        raise ValueError("brightdata_metrics_not_ready")
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


def existing_launched_batch(username,project_id,*,status_reader=None)->dict:
    """Verify the existing immutable 100-post scope; never create another batch.

    Checks the launch guard, unchanged Bright Data selection, and the newest
    persisted project batch, including the ordered canonical TikTok URLs.
    """
    from intelligence.market_intelligence import batch
    from intelligence.market_intelligence.tiktok_top100_register import read_selection
    from scripts.run_market_real100 import _project_id
    project=_project_id(project_id)
    selection,source_sha=read_selection(username,root=REPORTS)
    handle=selection["username"]
    marker=REPORTS/("tiktok-top100-"+handle+"-"+project+"-"+source_sha[:16]+"-launched.json")
    if not marker.is_file():
        raise ValueError("top100_launch_guard_missing_do_not_relaunch")
    data=json.loads(marker.read_text(encoding="utf-8"))
    ids=data.get("video_ids")
    if (data.get("schema_version")!="marketing-tiktok-top100-launch-guard/v1"
        or data.get("project_id")!=project
        or data.get("profile_snapshot_id")!=selection["profile_snapshot_id"]
        or data.get("selection_sha256")!=source_sha
        or type(ids) is not list or len(ids)!=100 or len(set(ids))!=100
        or any(type(v) is not int or v<=0 for v in ids)):
        raise ValueError("top100_launch_guard_mismatch_no_automatic_retry")
    state=(status_reader or batch.status)(project)
    videos=state.get("videos") or []
    if (not state.get("batch_id") or len(videos)!=100
        or [v.get("video_id") for v in videos]!=ids
        or [v.get("url") for v in videos]!=[p["url"] for p in selection["posts"]]
        or any(v.get("platform")!="tiktok" for v in videos)):
        raise ValueError("top100_existing_batch_scope_mismatch_no_relaunch")
    return {"project_id":project,"username":handle,"batch_id":state["batch_id"],
            "selection_sha256":source_sha,"launch_guard":str(marker),
            "videos":videos,"state":state}


def diagnose_existing(username,project_id,*,status_reader=None)->dict:
    """Provider-free source-scoped diagnostic, including failure reasons."""
    from collections import Counter
    active=existing_launched_batch(username,project_id,status_reader=status_reader)
    videos=active["videos"]
    by_kind=Counter(("photo_carousel" if "/photo/" in v["url"] else "video",
                     v.get("state","unknown")) for v in videos)
    reasons=Counter()
    samples=[]
    for v in videos:
        if v.get("state") in ("unavailable","failed","carousel_failed","rights_required"):
            reason=str(v.get("carousel_error") or v.get("acquisition_reason") or "reason_unrecorded")
            kind="photo_carousel" if "/photo/" in v["url"] else "video"
            reasons[(kind,v["state"],reason[:160])]+=1
            if len(samples)<8:
                samples.append({"video_id":v["video_id"],"kind":kind,
                                "state":v["state"],"reason":reason[:160]})
    return {"project_id":active["project_id"],"batch_id":active["batch_id"],
            "selected":100,"by_kind_and_state":[
                {"kind":kind,"state":state,"count":count}
                for (kind,state),count in sorted(by_kind.items())],
            "failure_reasons":[
                {"kind":kind,"state":state,"reason":reason,"count":count}
                for (kind,state,reason),count in sorted(reasons.items())],
            "failure_samples":samples,
            "launch_guard_reused":True,"new_batch_created":False,
            "provider_requests":0,"model_requests":0}



def _uuid_keyerror_reason(value)->bool:
    """Recognize the exact local-worker KeyError, never platform error text."""
    import re
    return (type(value) is str and bool(re.fullmatch(
        r"'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'",
        value)))


def recover_local_failures(username,project_id)->dict:
    """Requeue ONLY first-attempt, proven local defects in an existing batch.

    No POST, media download, new batch, external model, rights elevation or
    edits to the pinned snapshot. Explicit CLI action only. Retry allowances
    are fixed: 1 for carousel root misconfiguration, 1 for video KeyError.
    The user must stop --watch/--resume before invoking this command.
    """
    from intelligence.market_intelligence import batch
    from intelligence.market_intelligence.carousel_jobs import JOB_TYPE as CAROUSEL_JOB
    active=existing_launched_batch(username,project_id)
    posts=active["videos"]
    # Refuse while any source in this batch is actively being processed.
    busy={"carousel_processing","carousel_queued","downloading","media_awaiting_analysis"}
    if any(v.get("state") in busy for v in posts):
        raise ValueError("top100_recovery_requires_stopped_workers")
    root_checks=configure_local_media_roots()
    if root_checks["roots_configured"]<2:
        raise ValueError("top100_recovery_media_roots_missing")
    project=active["project_id"]
    revived_carousels=0
    revived_videos=0
    skipped=0
    with batch.d1.connect() as conn:
        with conn.transaction():
            missing=_missing_marketing_schema_tables(conn)
            if missing:
                raise ValueError("top100_missing_schema_run_python_-m_intelligence.migrate")
            for v in posts:
                if "/photo/" in v["url"]:
                    row=conn.execute("""
                      SELECT id,state,last_error,attempts,max_attempts,result,payload
                      FROM jobs WHERE job_type=%s AND payload->>'project_id'=%s
                        AND payload->>'video_id'=%s
                      ORDER BY created_at DESC,id DESC LIMIT 1 FOR UPDATE
                    """,(CAROUSEL_JOB,project,str(v["video_id"]))).fetchone()
                    if not row:
                        skipped+=1
                        continue
                    jid,state,error,attempts,max_attempts,result,payload=row
                    if (state=="failed" and error=="carousel_storage_root_not_configured"
                        and attempts==1 and max_attempts==1
                        and type(result) in (type(None),dict)
                        and not (result or {}).get("marketing_local_recovery")
                        and payload.get("url")==v["url"]
                        and payload.get("video_id")==v["video_id"]
                        and payload.get("project_id")==project):
                        changed=conn.execute("""
                          UPDATE jobs SET state='queued',attempts=0,
                            last_error=NULL,locked_by=NULL,lease_until=NULL,
                            run_after=now(),updated_at=now(),
                            result=COALESCE(result,'{}'::jsonb) ||
                                   '{"marketing_local_recovery":"carousel_root_v1"}'::jsonb
                          WHERE id=%s AND state='failed'
                            AND last_error='carousel_storage_root_not_configured'
                            AND attempts=1
                          RETURNING id
                        """,(jid,)).fetchone()
                        revived_carousels+=bool(changed)
                    else: skipped+=1
                elif "/video/" in v["url"]:
                    row=conn.execute("""
                      SELECT j.id,j.state,j.attempts,j.max_attempts,j.error_code,
                             j.error_message,r.unavailable_reason,r.canonical_url,
                             r.native_id
                      FROM project_video_acquisition_runs r
                      JOIN source_extraction_jobs j
                        ON j.id=r.job_id AND j.project_id=r.project_id
                      WHERE r.project_id=%s AND r.platform='tiktok'
                        AND r.native_id=%s
                      ORDER BY r.created_at DESC,r.job_id DESC
                      LIMIT 1 FOR UPDATE OF j,r
                    """,(project,v["native_id"])).fetchone()
                    if not row:
                        skipped+=1
                        continue
                    jid,state,attempts,max_attempts,error_code,message,reason,url,native=row
                    if (state=="unavailable" and attempts==1
                        and max_attempts>=2
                        and error_code in ("unavailable","failed")
                        and _uuid_keyerror_reason(reason)
                        and (message is None or message==reason)
                        and url==v["url"] and native==v["native_id"]):
                        changed=conn.execute("""
                          UPDATE source_extraction_jobs SET state='queued',
                            error_code=NULL,error_message=NULL,lease_until=NULL,
                            updated_at=clock_timestamp()
                          WHERE id=%s AND state='unavailable' AND attempts=1
                          RETURNING id
                        """,(jid,)).fetchone()
                        if changed:
                            conn.execute("""
                              UPDATE project_video_acquisition_runs SET
                                phase='queued',unavailable_reason=NULL,
                                progress=NULL,finished_at=NULL
                              WHERE project_id=%s AND job_id=%s
                            """,(project,jid))
                            revived_videos+=1
                    else: skipped+=1
                else:
                    raise ValueError("top100_recovery_unexpected_media_kind")
    return {"state":"known_local_failures_requeued","project_id":project,
            "batch_id":active["batch_id"],"pinned_selection_unchanged":True,
            "carousel_root_failures_requeued":revived_carousels,
            "video_worker_keyerrors_requeued":revived_videos,
            "not_requeued":skipped,
            "new_provider_requests":0,"new_batches":0,"ai_calls":0,
            "next_command":"--resume",
            "notice":"Requeue does not run workers. Resuming may download and invoke paid AI."}


def resume_existing(username,project_id,*,interval=20):
    """Re-establish local media roots and resume persisted batch workers only."""
    from intelligence.market_intelligence import batch
    from scripts.run_market_real100 import watch
    active=existing_launched_batch(username,project_id)
    with batch.d1.connect() as conn:
        missing=_missing_marketing_schema_tables(conn)
    if missing:
        raise ValueError("top100_missing_schema_run_python_-m_intelligence.migrate")
    roots=configure_local_media_roots()
    print(json.dumps({
        "state":"resuming_existing_batch_not_relaunching",
        "batch_id":active["batch_id"],"selected":100,
        "media_roots":roots,"launch_guard_reused":True,
        "paid_brightdata_metrics_resumed":False,
        "note":"Existing failed/unavailable acquisition jobs are not automatically retried",
    },ensure_ascii=False,indent=2),flush=True)
    # Generic workers may also claim other queued project jobs; this command
    # only guarantees the identity of the reported batch, not worker isolation.
    return watch(active["project_id"],interval=interval,resume_paid=False)


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
            "ready_for_username_top100":bool(good and state not in ("trigger_uncertain_no_auto_retry","rejected_http_400")),
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
    if state["state"] in ("trigger_uncertain_no_auto_retry","rejected_http_400"):
        raise ValueError("profile_trigger_rejected_or_uncertain_check_provider_no_auto_retry")
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
    choices.add_argument("--retry-http-400",action="store_true",
                         help="Explicitly retry after confirmed HTTP 400 on the previous dataset (one POST)")
    choices.add_argument("--collect",action="store_true")
    choices.add_argument("--register",action="store_true",
                         help="Register the exact 100 in D1 with a scoped public research policy")
    choices.add_argument("--launch",action="store_true",
                         help="Start the selected 100 local media analyses and monitor")
    choices.add_argument("--diagnose",action="store_true",
                         help="Read-only exact launched batch failure reasons; no network/AI")
    choices.add_argument("--resume",action="store_true",
                         help="Restore media roots, resume existing batch workers without new snapshot/batch")
    choices.add_argument("--recover-local-failures",action="store_true",
                         help="Explicit one-time requeue of proven local root/UUID failures only")
    choices.add_argument("--all",action="store_true",
                         help="One-command by-profile discovery, top100, registration and analysis")
    choices.add_argument("--preflight-selected",action="store_true",
                         help="Read-only exact TikTok top-100 project readiness; excludes unrelated sources")
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
    if args.paid_metrics and not (args.launch or args.all or args.check or args.preflight_selected):
        ap.error("--paid-metrics is allowed only with --launch/--all/--check/--preflight-selected")
    if args.accept_provider_costs and not args.all:
        ap.error("--accept-provider-costs is only for --all")
    if not 5<=args.interval<=120:
        ap.error("--interval must be 5..120")
    try:
        if args.check: result=check(args.username,args.project,paid_metrics=args.paid_metrics)
        elif args.preflight_selected: result=preflight_selected(args.username,args.project,
                                                                paid_metrics=args.paid_metrics)
        elif args.start: result=start(args.username)
        elif args.retry_http_400: result=retry_http_400(args.username)
        elif args.poll: result=poll(args.username)
        elif args.collect: result=collect(args.username,limit=args.limit)
        elif args.register: result=register(args.username,args.project)
        elif args.diagnose: result=diagnose_existing(args.username,args.project)
        elif args.resume: return resume_existing(args.username,args.project,interval=args.interval)
        elif args.recover_local_failures: result=recover_local_failures(args.username,args.project)
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
