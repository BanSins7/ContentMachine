"""Opt-in paid B1-B8 real multimodal QA for one local MP4.

This is an isolated *test*, never production processing. It reads the selected
SHA-verified MP4, runs local Group A, uploads only bounded 512px sampled frames
and GA11 verified video-time transcript to OpenRouter, and validates the
resulting eight native Group B blocks and A->B receipt. No Bright Data/DB or
Group C calls. Explicit --allow-media-upload required; max 12 provider calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

from intelligence import config
from intelligence.market_intelligence.replica_vision import ENDPOINT

MAX_RESP_BYTES=140_000
MAX_REQUESTS=12
MODEL_NAME=re.compile(r"[a-zA-Z0-9_.:/+-]{3,140}\Z")


class LiveRequestError(RuntimeError):
    """Sanitized provider status (no keys, URLs with tokens, or raw media)."""


class BoundedLiveRequester:
    def __init__(self, *, max_requests=MAX_REQUESTS):
        if type(max_requests) is not int or not 1<=max_requests<=MAX_REQUESTS:
            raise ValueError("invalid_live_b_request_budget")
        self.max_requests=max_requests
        self.attempted=0
        self.completed=0
        self.disabled=False

    def __call__(self, body:bytes):
        if self.disabled:
            raise LiveRequestError("prior_transport_failure")
        if self.attempted>=self.max_requests:
            raise LiveRequestError("request_budget_exceeded")
        if type(body) is not bytes or not 0<len(body)<=15_000_000:
            raise LiveRequestError("request_size_invalid")
        key=config.setting("CM_MARKET_OPENROUTER_API_KEY").strip()
        if not key:
            self.disabled=True
            raise LiveRequestError("openrouter_key_missing")
        self.attempted+=1
        request=urllib.request.Request(
            ENDPOINT,data=body,method="POST",
            headers={
                "Authorization":"Bearer "+key,
                "Content-Type":"application/json",
                "Accept":"application/json",
            },
        )
        try:
            with urllib.request.urlopen(request,timeout=90) as response:
                if response.status!=200:
                    raise LiveRequestError("non_200_response")
                payload=response.read(MAX_RESP_BYTES+1)
            if len(payload)>MAX_RESP_BYTES:
                raise LiveRequestError("response_size_limit")
            parsed=json.loads(payload)
            if type(parsed) is not dict:
                raise LiveRequestError("response_not_object")
            self.completed+=1
            return parsed
        except urllib.error.HTTPError as exc:
            self.disabled=True
            raise LiveRequestError("http_"+str(exc.code)) from None
        except (urllib.error.URLError,TimeoutError,OSError):
            self.disabled=True
            raise LiveRequestError("provider_transport_failed") from None
        except (ValueError,UnicodeError):
            self.disabled=True
            raise LiveRequestError("response_json_invalid") from None
        except LiveRequestError:
            self.disabled=True
            raise


def preflight(file:Path, *, authorized:bool):
    if not authorized:
        raise ValueError("Explicit --allow-media-upload required: sampled frames and transcribed words will be sent to OpenRouter and may incur charges.")
    if not file.is_file() or file.suffix.lower()!=".mp4":
        raise ValueError("Existing local MP4 required")
    if not 0<file.stat().st_size<=1024**3:
        raise ValueError("MP4 must be between 1 byte and 1 GiB")
    model=config.setting("CM_MARKET_VISION_MODEL").strip()
    if not MODEL_NAME.fullmatch(model):
        raise ValueError("CM_MARKET_VISION_MODEL is missing/invalid; configure the existing vision model in .env")
    if not config.setting("CM_MARKET_OPENROUTER_API_KEY").strip():
        raise ValueError("CM_MARKET_OPENROUTER_API_KEY missing in .env; never paste the key into this chat")
    return model


def run(video:Path, *, authorized:bool, max_requests:int=MAX_REQUESTS):
    model=preflight(video,authorized=authorized)
    network=BoundedLiveRequester(max_requests=max_requests)
    # Strictly process-local opt-in, no modification to .env or production rights.
    os.environ["CM_MARKET_VISION_ENABLED"]="1"
    os.environ["CM_MARKET_MEDIA_TO_PROVIDER_ALLOWED"]="1"
    os.environ["CM_MARKET_AUDIO_EVENTS_ENABLED"]="0"

    from intelligence.market_intelligence.replica_media import analyze_media
    from intelligence.market_intelligence.extended_b import assemble_b1_b8
    from intelligence.market_intelligence.group_a_report import verify_group_a_report
    from intelligence.market_intelligence.group_b_report import (
        build_group_b_report,verify_group_b_report,verify_group_b_payload)
    from intelligence.market_intelligence.native_evidence import (
        make_receipt,verify_receipt)
    from intelligence.market_intelligence.media_worker import METHOD

    digest=hashlib.sha256()
    with video.open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""):
            digest.update(chunk)
    sha=digest.hexdigest()
    tech,visual=analyze_media(
        video,sha,roots=[str(video.parent)],
        project_id="b"*32,srt_rows=[],allow_provider=True,
        vision_requester=network,supplement_requester=network,
    )
    a=visual["group_a_report"]
    if not verify_group_a_report(a,sha):
        raise AssertionError("group_a_integrity_failed")
    inferred=visual.get("model_result") or {}
    supplemental=visual.get("b_extra_result") or {}
    blocks=assemble_b1_b8(
        visual=visual,inferred=inferred,measurements=tech,
        transcript=visual.get("asr_segments") or [],
        extras=supplemental)
    report=build_group_b_report(
        source_sha256=sha,group_a=a,visual=visual,
        group_b=blocks,duration_sec=tech.measurement.duration_sec)
    if not (verify_group_b_report(report,sha,a["report_sha256"])
            and verify_group_b_payload(report,blocks)):
        raise AssertionError("group_b_integrity_failed")
    blocks["native_report"]=report
    receipt=make_receipt(
        project_id="b"*32,video_id=77,
        source_id="local-opt-in",source_version_id="local-live-b-v1",
        media_asset_id="local-opt-in-mp4",media_sha256=sha,
        duration_sec=tech.measurement.duration_sec,
        visual=visual,group_b=blocks,method=METHOD)
    if not verify_receipt(receipt,media_sha256=sha):
        raise AssertionError("handoff_integrity_failed")
    brief={
        name:{"state":value["state"],"reason":value.get("reason"),
              "evidence_frames":len(value.get("evidence_frame_refs",[]))}
        for name,value in blocks["blocks"].items()
    }
    # Retain the ACTUAL B1-B8 payload and model diagnostics locally. No MP4,
    # base64 frame bytes, full raw ASR, credential or DB content in the report.
    return {
        "schema_version":"marketing-real-mp4-live-b-check/v1",
        "model":model,
        "video_sha256":sha,
        "duration_sec":str(tech.measurement.duration_sec),
        "sampled_frames":len(visual.get("frames",[])),
        "audio_events_skipped":True,
        "remote_request_attempted":network.attempted,
        "remote_request_completed":network.completed,
        "max_remote_requests":network.max_requests,
        "vision_windows":inferred.get("timeline_windows",[]),
        "vision_successful_windows":inferred.get("successful_windows",0),
        "supplement_windows":supplemental.get("timeline_windows",[]),
        "supplement_successful_windows":supplemental.get("successful_windows",0),
        "live_multimodal_observed":(
            inferred.get("state")=="model_observed_partial"
            and supplemental.get("state")=="model_observed_partial"),
        "fully_sampled_window_inference":(
            inferred.get("state")=="model_observed_partial"
            and supplemental.get("state")=="model_observed_partial"
            and inferred.get("successful_windows",0)>0
            and inferred.get("successful_windows",0)==inferred.get("requested_windows",-1)
            and supplemental.get("successful_windows",0)>0
            and supplemental.get("successful_windows",0)==supplemental.get("requested_windows",-1)),
        "vision_state":inferred.get("state"),
        "vision_reason":inferred.get("reason"),
        "supplement_state":supplemental.get("state"),
        "supplement_reason":supplemental.get("reason"),
        "group_a_sha256":a["report_sha256"],
        "group_b_sha256":report["report_sha256"],
        "group_b_state":report["state"],
        "group_b_blocks":brief,
        "group_b_evidence":blocks["blocks"],
        "group_b_report":report,
        "handoff_verified":True,
        "handoff_sha256":receipt.get("evidence_sha256") or receipt.get("receipt_sha256"),
        "full_video_semantic_certified":False,
        "group_c":"not_run_without_real_provider_metric_snapshots",
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--allow-media-upload",action="store_true",
                        help="Explicitly authorize transmission of sampled MP4 frames and aligned transcript to OpenRouter; paid requests may occur")
    parser.add_argument("--max-requests",type=int,default=MAX_REQUESTS)
    args=parser.parse_args(argv)
    file=args.video.resolve()
    try:
        summary=run(file,authorized=args.allow_media_upload,
                    max_requests=args.max_requests)
    except (ValueError,LiveRequestError) as exc:
        parser.exit(2,"Blocked: "+str(exc)+"\n")
    output=args.output.resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Saved verified B1-B8 report:", output)
    print("OpenRouter requests:", summary["remote_request_attempted"])
    print("Vision windows observed:",summary["vision_successful_windows"])
    print("Supplement windows observed:",summary["supplement_successful_windows"])
    print("Verified A/B handoff:",summary["handoff_verified"])
    for name,item in summary["group_b_blocks"].items():
        print(name,item["state"],item["reason"] or "")
    if not summary["live_multimodal_observed"]:
        print("VERDICT: INCOMPLETE (provider windows unavailable; inspect saved reasons).")
        return 1
    if not summary["fully_sampled_window_inference"]:
        print("VERDICT: PARTIAL (some sampled AI windows failed).")
        return 2
    print("VERDICT: LIVE SAMPLED-FRAME B OBSERVATIONS VERIFIED; NOT WHOLE-VIDEO CERTIFICATION.")
    return 0


if __name__=="__main__":
    sys.exit(main())
