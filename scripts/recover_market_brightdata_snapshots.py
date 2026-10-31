"""Read-only Bright Data snapshot discovery + evidence-matched recovery.

For an ambiguous/failed single URL POST, NEVER submit another POST.
--list uses only GET /datasets/v3/snapshots. --verify uses GET list,
progress and snapshot/download; it associates a snapshot with local state
ONLY when dataset, API provenance, creation clock and EXACT video URL
in the downloaded one-record observation all match. Never prints raw data.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

from intelligence import config
from intelligence.market_intelligence import brightdata_provider as bd
from intelligence.market_intelligence.contracts import MarketError
from scripts.probe_market_brightdata_one import _safe_read, _replace_state

LIMIT=100
VALID_STATES={"starting","running","ready","failed","canceled"}


def _utc(value):
    if type(value) is not str:
        raise ValueError("snapshot_timestamp_invalid")
    try:
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
        if dt.tzinfo is None:
            raise ValueError()
        return dt.astimezone(timezone.utc)
    except (ValueError,OverflowError):
        raise ValueError("snapshot_timestamp_invalid") from None


def _read_only_token() -> str:
    """Local token read for provider GET; never toggles billable collection."""
    token=config.setting("CM_BRIGHTDATA_API_TOKEN","").strip()
    if not token or any(ord(c)<33 or ord(c)>126 for c in token):
        raise ValueError("brightdata_read_only_token_missing_or_invalid")
    return token


def _fetch_snapshots(dataset_id: str, start: datetime, *, request=None) -> list:
    if type(dataset_id) is not str or not bd.DATASET_RE.fullmatch(dataset_id):
        raise ValueError("snapshot_dataset_invalid")
    if request is None:
        request=bd._api
    # GET only: no triggers, scraping, billing initiation or changes to jobs.
    response=request("GET","/datasets/v3/snapshots",_read_only_token(),query={
        "dataset_id":dataset_id,
        "from_date":(start-timedelta(days=1)).date().isoformat(),
        "limit":LIMIT,
        "trigger_type":"API",
    })
    rows=response if type(response) is list else (
        response.get("snapshots") if type(response) is dict else None)
    if type(rows) is not list or len(rows)>LIMIT:
        raise ValueError("snapshot_list_response_invalid")
    return rows


def _select(states: list[dict], rows: list, *, now=None) -> dict:
    if not states:
        raise ValueError("no_states")
    if any(s.get("platform")!=states[0].get("platform")
           or s.get("dataset_id")!=states[0].get("dataset_id")
           or s.get("url")!=states[0].get("url") for s in states):
        raise ValueError("states_different_video_or_dataset")
    oldest=min(_utc(s.get("started_at")) for s in states)
    now=now or datetime.now(timezone.utc)
    earliest=oldest-timedelta(minutes=15)
    entries={}
    for row in rows:
        if type(row) is not dict:
            continue
        snap_id=row.get("id") or row.get("snapshot_id")
        if type(snap_id) is not str or not bd.SNAPSHOT_RE.fullmatch(snap_id):
            continue
        if row.get("dataset_id")!=states[0]["dataset_id"]:
            continue
        try:
            when=_utc(row.get("created") or row.get("created_at"))
        except ValueError:
            continue
        if not earliest<=when<=now+timedelta(minutes=5):
            continue
        trigger=row.get("trigger")
        origin=trigger.get("type") if type(trigger) is dict else None
        # This probe submits with API; no manual/control-panel jobs are valid.
        if origin is not None and str(origin).upper()!="API":
            continue
        status=row.get("status")
        if status not in VALID_STATES:
            continue
        entries[snap_id]={
            "snapshot_id":snap_id,
            "created_at":when.isoformat(),
            "status":status,
            "trigger_type":origin or "not_provided",
        }
    return {
        "candidate_count":len(entries),
        "candidates":sorted(entries.values(),key=lambda x:x["created_at"]),
        "listing_limit":LIMIT,
        "listing_truncated_possible":len(rows)==LIMIT,
        "matching_source_url_verified":False,
        "paid_trigger_attempted":False,
        "only_get_requests":True,
    }


def list_candidates(*,state_files:list[Path],request=None) -> dict:
    states=[_safe_read(p) for p in state_files]
    if not states:
        raise ValueError("missing_state_paths")
    start=min(_utc(s.get("started_at")) for s in states)
    rows=_fetch_snapshots(states[0]["dataset_id"],start,request=request)
    return _select(states,rows)



def diagnose_history(*,state_files:list[Path],request=None) -> dict:
    """Only GET: compare unfiltered dataset history against strict matching.

    The original list uses trigger_type=API and from_date. If that returned 0,
    this checks whether the provider has any entries for the same dataset
    without those two filters. Never prints unvetted provider record contents.
    """
    states=[_safe_read(p) for p in state_files]
    if not states:
        raise ValueError("missing_state_paths")
    if any(s.get("platform")!=states[0].get("platform")
           or s.get("dataset_id")!=states[0].get("dataset_id")
           or s.get("url")!=states[0].get("url") for s in states):
        raise ValueError("states_different_video_or_dataset")
    dataset=states[0].get("dataset_id")
    if type(dataset) is not str or not bd.DATASET_RE.fullmatch(dataset):
        raise ValueError("snapshot_dataset_invalid")
    request=request or bd._api
    # No from_date and no trigger_type query, to isolate API filtering.
    response=request("GET","/datasets/v3/snapshots",_read_only_token(),
                     query={"dataset_id":dataset,"skip":0,"limit":200})
    rows=response if type(response) is list else (
        response.get("snapshots") if type(response) is dict else None)
    if type(rows) is not list or len(rows)>200:
        raise ValueError("snapshot_history_response_invalid")
    oldest=min(_utc(s["started_at"]) for s in states)
    earliest=oldest-timedelta(minutes=15)
    now=datetime.now(timezone.utc)
    tally={
        "malformed_or_missing_id":0,
        "different_or_missing_dataset_id":0,
        "missing_or_invalid_created_at":0,
        "outside_attempt_time_window":0,
        "non_api_trigger":0,
        "unrecognized_status":0,
        "potential_api_snapshot_metadata":0,
    }
    for item in rows:
        if type(item) is not dict:
            tally["malformed_or_missing_id"]+=1
            continue
        ident=item.get("id") or item.get("snapshot_id")
        if type(ident) is not str or not bd.SNAPSHOT_RE.fullmatch(ident):
            tally["malformed_or_missing_id"]+=1
            continue
        if item.get("dataset_id")!=dataset:
            tally["different_or_missing_dataset_id"]+=1
            continue
        try:
            when=_utc(item.get("created") or item.get("created_at"))
        except ValueError:
            tally["missing_or_invalid_created_at"]+=1
            continue
        if not earliest<=when<=now+timedelta(minutes=5):
            tally["outside_attempt_time_window"]+=1
            continue
        trigger=item.get("trigger")
        origin=trigger.get("type") if type(trigger) is dict else None
        if origin is not None and str(origin).upper()!="API":
            tally["non_api_trigger"]+=1
            continue
        if item.get("status") not in VALID_STATES:
            tally["unrecognized_status"]+=1
            continue
        tally["potential_api_snapshot_metadata"]+=1
    filtered=_select(states,rows,now=now)
    return {
        "read_only":True,
        "paid_post_requests":0,
        "dataset_id":dataset,
        "query_filters":"dataset_id_only",
        "unfiltered_dataset_history_rows_received":len(rows),
        "history_list_limit":200,
        "history_truncation_possible":len(rows)==200,
        "reason_counts":tally,
        "potential_candidates":filtered["candidates"],
        "video_url_verified":False,
        "caution":"Metadata and timestamps alone do not prove a snapshot is for your video.",
    }


def verify_and_attach(*,state_file:Path,snapshot_id:str,
                      request=None,progress=None,download=None)->dict:
    state=_safe_read(state_file)
    if state.get("snapshot_id") is not None:
        raise ValueError("snapshot_already_attached")
    if type(snapshot_id) is not str or not bd.SNAPSHOT_RE.fullmatch(snapshot_id):
        raise ValueError("invalid_candidate_snapshot_id")
    listing=list_candidates(state_files=[state_file],request=request)
    candidates={x["snapshot_id"]:x for x in listing["candidates"]}
    candidate=candidates.get(snapshot_id)
    if candidate is None:
        raise ValueError("snapshot_not_in_verified_time_and_dataset_listing")
    if progress is None:
        progress=bd.progress
    if download is None:
        download=bd.download
    status=progress(snapshot_id)
    if status!="ready":
        raise ValueError("candidate_snapshot_not_ready_"+status)
    raw=download(snapshot_id)
    if type(raw) is not list or len(raw)!=1 or type(raw[0]) is not dict:
        raise ValueError("candidate_not_exactly_one_video_record")
    found=bd.normalize_record(state["platform"],raw[0])
    if found is None or found.get("url")!=state["url"]:
        raise ValueError("candidate_video_url_does_not_match")
    state["snapshot_id"]=snapshot_id
    state["state"]="ready"
    state["recovery_kind"]="verified_one_video_snapshot_from_read_only_listing"
    state["recovery_verified_at"]=datetime.now(timezone.utc).isoformat()
    state["recovery_snapshot_created_at"]=candidate["created_at"]
    _replace_state(state_file,state)
    return {
        "state":"ready","snapshot_id":snapshot_id,
        "video_url_exact_match":True,
        "dataset_and_creation_time_match":True,
        "paid_trigger_attempted":False,
        "only_get_requests":True,
        "source_state_updated":True,
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list",action="store_true")
    mode.add_argument("--verify",action="store_true")
    mode.add_argument("--diagnose",action="store_true")
    parser.add_argument("--state",action="append",type=Path,required=True)
    parser.add_argument("--snapshot-id")
    args=parser.parse_args(argv)
    try:
        paths=[p.resolve() for p in args.state]
        if args.list:
            result=list_candidates(state_files=paths)
        elif args.diagnose:
            result=diagnose_history(state_files=paths)
        else:
            if len(paths)!=1 or not args.snapshot_id:
                raise ValueError("verify_requires_one_state_and_snapshot_id")
            result=verify_and_attach(state_file=paths[0],
                                     snapshot_id=args.snapshot_id)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,MarketError,OSError) as exc:
        if isinstance(exc,MarketError):
            code=exc.code
        elif isinstance(exc,OSError):
            code="local_file_error"
        else:
            code=str(exc)
        parser.exit(2,"Blocked: "+code+"\n")
    return 0


if __name__=="__main__":
    sys.exit(main())
