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
                     payload=[{"url":profile,"country":""}])
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

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    choices=ap.add_mutually_exclusive_group(required=True)
    choices.add_argument("--start",action="store_true")
    choices.add_argument("--poll",action="store_true")
    choices.add_argument("--collect",action="store_true")
    ap.add_argument("--username",required=True)
    ap.add_argument("--limit",type=int,default=100)
    args=ap.parse_args(argv)
    try:
        if args.start: result=start(args.username)
        elif args.poll: result=poll(args.username)
        else: result=collect(args.username,limit=args.limit)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError,TypeError) as ex:
        ap.exit(2,"BLOCKED: "+(str(ex) if isinstance(ex,ValueError) else "local_state_invalid")+"\n")
    return 0

if __name__=="__main__":
    sys.exit(main())
