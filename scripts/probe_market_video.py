"""Inspect an actual user MP4 through Marketing A -> B, offline.

Only reads the specified file. Forces all provider/model calls OFF, validates
source bytes/FFmpeg and report hashes. No API calls, uploads, or writes to
project data. Group C requires actual provider metric snapshots and is tested
separately; it must not be faked from arbitrary video files.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

# Executing "python scripts/probe_market_video.py" adds scripts/ to sys.path,
# not the repository root. Retain module invocation support as well.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def run(file:Path, *, local_models:bool=False):
    # Never send the user's MP4 to a remote provider from this test tool.
    # --local-models enables only preinstalled SHA-pinned local models.
    os.environ["CM_MARKET_VISION_ENABLED"]="0"
    os.environ["CM_MARKET_MEDIA_TO_PROVIDER_ALLOWED"]="0"
    if not local_models:
        for key in ("CM_MARKET_VAD_ENABLED","CM_MARKET_ASR_ENABLED",
                    "CM_MARKET_AUDIO_EVENTS_ENABLED"):
            os.environ[key]="0"
        for key in ("CM_MARKET_TESSERACT_PIN","CM_MARKET_TESSERACT_BINARY",
                    "CM_MARKET_TESSERACT_DATA"):
            os.environ[key]=""
    from intelligence.market_intelligence.replica_media import analyze_media
    from intelligence.market_intelligence.group_a_report import verify_group_a_report
    from intelligence.market_intelligence.extended_b import assemble_b1_b8
    from intelligence.market_intelligence.group_b_report import (
        build_group_b_report,verify_group_b_report,verify_group_b_payload)
    from intelligence.market_intelligence.native_evidence import (
        make_receipt,verify_receipt)
    from intelligence.market_intelligence.media_worker import METHOD
    if not file.is_file() or file.suffix.lower()!=".mp4":
        raise ValueError("Input must be an existing MP4")
    if file.stat().st_size>1024*1024*1024:
        raise ValueError("Test MP4 must be <=1 GB")
    sha=hashlib.sha256()
    with file.open("rb") as reader:
        for chunk in iter(lambda:reader.read(1024*1024),b""):
            sha.update(chunk)
    digest=sha.hexdigest()
    tech,visual=analyze_media(file,digest,roots=[str(file.parent)],
       project_id="b"*32,srt_rows=[],allow_provider=False)
    a=visual.get("group_a_report")
    if not verify_group_a_report(a,digest):
        raise AssertionError("A report SHA verification failed")
    b=assemble_b1_b8(
        visual=visual,inferred=visual.get("model_result") or {},
        measurements=tech,transcript=[],extras=visual.get("b_extra_result"))
    report=build_group_b_report(source_sha256=digest,group_a=a,visual=visual,
         group_b=b,duration_sec=tech.measurement.duration_sec)
    if not (verify_group_b_report(report,digest,a["report_sha256"])
            and verify_group_b_payload(report,b)):
        raise AssertionError("B1-B8 report verification failed")
    b["native_report"]=report
    receipt=make_receipt(project_id="b"*32,video_id=77,
         source_id="test-source",source_version_id="test-source-version",
         media_asset_id="test-asset",media_sha256=digest,
         duration_sec=tech.measurement.duration_sec,
         visual=visual,group_b=b,method=METHOD)
    if not verify_receipt(receipt,media_sha256=digest):
        raise AssertionError("A-to-B handoff receipt failed")
    return {
       "schema_version":"marketing-real-mp4-offline-check/v1",
       "local_models_requested":local_models,
       "video_sha256":digest,
       "duration_sec":str(tech.measurement.duration_sec),
       "sampled_frames":len(visual.get("frames",[])),
       "group_a_report_sha256":a["report_sha256"],
       "group_a_execution_state":a["execution_state"],
       "group_a_stages":{
          key:{"state":step["state"],"reason":step.get("reason")}
          for key,step in a["stages"].items()},
       "group_b_report_sha256":report["report_sha256"],
       "group_b_blocks":{
           key:{"state":value["state"],"reason":value.get("reason")}
           for key,value in report["blocks"].items()},
       "handoff_sha256":receipt.get("evidence_sha256") or receipt.get("receipt_sha256"),
       "group_c":"not_run_without_exact_D1_metric_snapshots",
       "paid_requests":0,"online_model_calls":0,
       "a_b_contracts_passed":True,
       "full_video_semantic_certified":False,
    }

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--video",required=True,type=Path)
    p.add_argument("--output",type=Path)
    p.add_argument("--local-models",action="store_true",
                   help="Enable only preinstalled SHA-pinned local ASR/VAD/OCR/audio models; never make provider calls.")
    args=p.parse_args()
    report=run(args.video.resolve(),local_models=args.local_models)
    txt=json.dumps(report,indent=2,ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(txt+"\n",encoding="utf-8")
        print("Wrote checked offline A/B results to",args.output)
    print(txt)
if __name__=="__main__":
    sys.exit(main() or 0)
