"""Isolated Bright Data one-video acquisition probe (no project DB writes).

Start is a potentially BILLABLE one-time trigger, never done by default.
A unique local state file is created BEFORE any provider POST so an ambiguous
timeout/crash never leads to an automatic paid duplicate. Later progress and
collect commands ONLY use the saved snapshot ID. Raw provider payload, tokens,
comments and captions are not stored. Only source-observed numeric metrics are.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile

from intelligence.discovery.normalize import canonical_video_url, CandidateError
from intelligence.market_intelligence import brightdata_provider as bd
from intelligence.market_intelligence import brightdata_preflight as preflight
from intelligence.market_intelligence.contracts import MarketError
from intelligence.market_intelligence.metric_fields import metric_coverage

SCHEMA="market-brightdata-single-url-test/v1"
MAX_STATE_BYTES=40_000


def _safe_read(path: Path) -> dict:
    if not path.is_file() or not 0<path.stat().st_size<=MAX_STATE_BYTES:
        raise ValueError("probe_state_missing_or_too_large")
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
    except (ValueError,UnicodeError):
        raise ValueError("probe_state_invalid_json") from None
    if (type(value) is not dict or value.get("schema_version")!=SCHEMA
            or value.get("platform") not in bd.DATASETS
            or type(value.get("url")) is not str
            or type(value.get("snapshot_id")) not in (str,type(None))):
        raise ValueError("probe_state_invalid")
    try:
        plat,native,canonical=canonical_video_url(value["url"])
    except (CandidateError,ValueError):
        raise ValueError("probe_state_url_invalid") from None
    if plat!=value["platform"] or canonical!=value["url"]:
        raise ValueError("probe_state_url_not_canonical")
    if value.get("snapshot_id") is not None and not bd.SNAPSHOT_RE.fullmatch(value["snapshot_id"]):
        raise ValueError("probe_state_snapshot_invalid")
    return value


def _replace_state(path: Path, value: dict):
    """Write complete state to sibling temp file then atomically replace."""
    fd,tmp=tempfile.mkstemp(prefix=".market-bd-",suffix=".json",dir=str(path.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(value,f,ensure_ascii=False,indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _authorize(platform:str):
    ready=bd.readiness()
    if not ready["ready"]:
        raise ValueError("metrics_disabled_or_rights_unverified")
    check=preflight.check()
    if check.get("status")!="authenticated":
        raise ValueError("catalog_authentication_not_verified")
    target=(check.get("targets") or {}).get(platform) or {}
    if not target.get("listed") or not target.get("video_dataset"):
        raise ValueError("video_dataset_unavailable_in_catalog")
    if target.get("id")!=bd.dataset_id(platform):
        raise ValueError("catalog_dataset_mismatch")


def start(*,platform:str,url:str,state_file:Path,consent:bool)->dict:
    if not consent:
        raise ValueError("paid_trigger_requires_explicit_consent")
    if platform not in bd.DATASETS:
        raise ValueError("invalid_platform")
    try:
        parsed,native,canonical=canonical_video_url(url)
    except (CandidateError,ValueError):
        raise ValueError("invalid_public_video_url") from None
    if parsed!=platform:
        raise ValueError("platform_url_mismatch")
    if state_file.exists():
        raise ValueError("probe_state_already_exists_no_double_trigger")
    _authorize(platform)
    state_file.parent.mkdir(parents=True,exist_ok=True)
    report={
        "schema_version":SCHEMA,"platform":platform,
        "url":canonical,  # public URL, local-only, needed to check normalized result
        "dataset_id":bd.dataset_id(platform),
        "started_at":datetime.now(timezone.utc).isoformat(),
        "state":"trigger_uncertain_no_automatic_retry",
        "snapshot_id":None,
        "trigger_attempted":True,
        "billable_when_provider_succeeds":True,
        "max_trigger_requests":1,
    }
    # Must claim the unique path BEFORE any paid HTTP request.
    with state_file.open("x",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    try:
        sid=bd.trigger(platform,[canonical])
    except MarketError:
        # Leave the pre-created no-retry marker. The remote request may have
        # succeeded even if the response or network was lost.
        raise
    report["snapshot_id"]=sid
    report["state"]="submitted"
    _replace_state(state_file,report)
    return report


def poll(*,state_file:Path)->dict:
    report=_safe_read(state_file)
    sid=report["snapshot_id"]
    if not sid:
        raise ValueError("snapshot_unknown_no_automatic_retry")
    value=bd.progress(sid)
    report["state"]=value
    report["last_checked_at"]=datetime.now(timezone.utc).isoformat()
    _replace_state(state_file,report)
    return report


def collect(*,state_file:Path,metrics_file:Path)->dict:
    report=_safe_read(state_file)
    if not report.get("snapshot_id"):
        raise ValueError("snapshot_unknown_no_automatic_retry")
    if metrics_file.exists():
        raise ValueError("metrics_output_already_exists")
    state=bd.progress(report["snapshot_id"])
    if state!="ready":
        raise ValueError("snapshot_not_ready_"+state)
    results=bd.download(report["snapshot_id"])
    matches=[]
    for item in results:
        normalized=bd.normalize_record(report["platform"],item)
        if normalized is not None and normalized.get("url")==report["url"]:
            matches.append(normalized)
    if len(matches)!=1:
        raise ValueError("single_video_metrics_not_unambiguous")
    row=matches[0]
    fields=row.get("numeric_fields") or {}
    output={
        "schema_version":"market-brightdata-single-video-metrics/v1",
        "provider":"brightdata",
        "snapshot_id":report["snapshot_id"],
        "dataset_id":report["dataset_id"],
        "platform":report["platform"],
        "url_sha256":sha256(report["url"].encode("utf-8")).hexdigest(),
        # This is the LOCAL time when the downloaded JSON was saved.
        # Bright Data's TikTok Posts output does not provide verified
        # platform measurement time. Never use this as source C0 evidence.
        "observed_at":datetime.now(timezone.utc).isoformat(),
        "observation_time_assumed":True,
        "observed_at_origin":"local_json_saved_at_not_source_observed_at",
        "provider_observed_at":None,
        "published_at":row.get("published_at"),
        "views":row.get("views"),"likes":row.get("likes"),
        "comments":row.get("comments"),"shares":row.get("shares"),
        "saves":row.get("saves"),
        "numeric_fields":fields,
        "metric_coverage":metric_coverage(fields),
        "not_full_group_c_certification":True,
        "not_a_second_metric_snapshot":True,
        "collection_state":"one_observation_only",
    }
    metrics_file.parent.mkdir(parents=True,exist_ok=True)
    with metrics_file.open("x",encoding="utf-8") as f:
        json.dump(output,f,ensure_ascii=False,indent=2)
        f.write("\n")
    report["state"]="metrics_saved"
    report["metrics_file_sha256"]=sha256(metrics_file.read_bytes()).hexdigest()
    _replace_state(state_file,report)
    return output


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start",action="store_true")
    mode.add_argument("--poll",action="store_true")
    mode.add_argument("--collect",action="store_true")
    parser.add_argument("--platform",choices=tuple(bd.DATASETS))
    parser.add_argument("--url")
    parser.add_argument("--state",type=Path,required=True)
    parser.add_argument("--metrics-output",type=Path)
    parser.add_argument("--allow-one-paid-trigger",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.start:
            if not args.platform or not args.url:
                raise ValueError("start_requires_platform_and_url")
            report=start(platform=args.platform,url=args.url,
                         state_file=args.state.resolve(),
                         consent=args.allow_one_paid_trigger)
            print("Trigger submitted: snapshot ID saved locally")
            print("Snapshot:",report["snapshot_id"])
        elif args.poll:
            report=poll(state_file=args.state.resolve())
            print("Snapshot:",report["snapshot_id"])
            print("Status:",report["state"])
        else:
            if args.metrics_output is None:
                raise ValueError("collect_requires_metrics_output")
            output=collect(state_file=args.state.resolve(),
                           metrics_file=args.metrics_output.resolve())
            print("One video metric observation saved")
            print("Counters:",{k:output[k] for k in
                ("views","likes","comments","shares","saves")})
            print("Numeric field count:",output["metric_coverage"]["field_count"])
            print("Watch time present:",output["metric_coverage"]["has_watch_time_field"])
            print("Retention present:",output["metric_coverage"]["has_retention_field"])
    except (ValueError,MarketError,OSError) as exc:
        # Provider MarketError messages are sanitized by the transport layer.
        if isinstance(exc,MarketError):
            reason=exc.code
        elif isinstance(exc,OSError):
            reason="local_file_error"
        else:
            reason=str(exc)
        parser.exit(2,"Blocked: "+reason+"\n")
    return 0


if __name__=="__main__":
    sys.exit(main())
