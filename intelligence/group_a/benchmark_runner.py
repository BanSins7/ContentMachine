"""GA22 reproducible observation-only benchmark, no fictitious model scores.

Synthetic FFprobe measurement is ACTUALLY measured locally; this is *not*
end-to-end media pipeline, ASR accuracy or completed 10k/month capacity.
External predictions and human gold must be independently authenticated by
the host before claiming release quality.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import time

from .benchmark_metrics import (
    BenchmarkError,scene_boundaries,score_asr,ocr_recall,
    subtitle_truth,event_intervals,
)
from .benchmark_fixtures import verify_generated_manifest

PROTOCOL="ga22-benchmark-report/v1"
PREDICTIONS="ga22-predictions/v1"
REQUIRED_RELEASE_METRICS=(
    "scene_f1","asr_wer_ru","asr_wer_en","ocr_cer_ru","ocr_cer_en",
    "subtitle_precision","subtitle_recall","event_music_f1",
    "event_sfx_f1","full_pipeline_seconds","peak_rss_bytes",
)


def canonical_sha(value):
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
                             separators=(",",":"),allow_nan=False
                  ).encode("utf-8")).hexdigest()


def _file_sha(path):
    h=sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda:f.read(1024*1024),b""):
            h.update(part)
    return h.hexdigest()


def _tool(executable):
    binary=shutil.which(executable)
    if binary is None:
        raise BenchmarkError("probe_tool_missing")
    command=[binary,"-version"]
    p=subprocess.run(command,capture_output=True,stdin=subprocess.DEVNULL,
                     timeout=10,check=False)
    if p.returncode:
        raise BenchmarkError("probe_tool_missing")
    return binary,p.stdout.decode("utf-8",errors="replace").splitlines()[0][:200]


def profile_probe(root,manifest,*,ffprobe="ffprobe",repetitions=3):
    """Synchronous real FFprobe subprocess per source asset.

    CPU time is process-level wall time; physical CPU/RSS/GPU is NOT measured.
    Input bytes are SHA-verified before every measurement and after it.
    No model is loaded or downloaded. Only media duration/format is extracted.
    """
    if type(repetitions) is not int or not 1<=repetitions<=20:
        raise BenchmarkError("invalid_benchmark_repetitions")
    binary,version=_tool(ffprobe)
    root=Path(root)
    assets={}
    for c in manifest["cases"]:
        for a in c["assets"]:
            if a["name"].endswith(".mp4"):
                assets[a["name"]]=a
    if not assets:
        raise BenchmarkError("no_generated_video_assets")
    measured=[]
    for name,meta in sorted(assets.items()):
        file=root/name
        if not file.is_file() or file.is_symlink() or _file_sha(file)!=meta["sha256"]:
            raise BenchmarkError("asset_changed_before_benchmark")
        for run in range(repetitions):
            start=time.perf_counter_ns()
            try:
                r=subprocess.run(
                    [binary,"-v","error","-show_entries",
                     "format=duration,size:stream=codec_type,codec_name",
                     "-of","json",str(file)],
                    stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,timeout=15,check=False
                )
            except (OSError,subprocess.TimeoutExpired) as exc:
                raise BenchmarkError("ffprobe_benchmark_failed") from exc
            elapsed=(time.perf_counter_ns()-start)/1_000_000_000
            if r.returncode or len(r.stdout)>65536:
                raise BenchmarkError("ffprobe_benchmark_failed")
            try:
                obj=json.loads(r.stdout)
                duration=float(obj["format"]["duration"])
            except (ValueError,KeyError,TypeError) as exc:
                raise BenchmarkError("ffprobe_invalid_media_duration") from exc
            if not math.isfinite(duration) or not 0<duration<600:
                raise BenchmarkError("ffprobe_invalid_media_duration")
            if _file_sha(file)!=meta["sha256"]:
                raise BenchmarkError("asset_changed_during_benchmark")
            measured.append({
                "stage":"ffprobe_only","file":name,
                "media_sha256":meta["sha256"],
                "input_bytes":meta["byte_size"],"duration_sec":round(duration,3),
                "wall_sec":round(elapsed,6),
                "media_seconds_per_wall_second":round(duration/max(elapsed,1e-9),4),
                "peak_rss_bytes":None,"gpu_peak_bytes":None,
                "cpu_seconds":None,"output_bytes":len(r.stdout),
                "repeat":run+1,
                "observation":"real_local_ffprobe_subprocess",
            })
    values=[r["wall_sec"] for r in measured]
    p95=sorted(values)[math.ceil(.95*len(values))-1] if len(values)>=20 else None
    return {
        "method":"measured_ffprobe_only_not_full_pipeline",
        "environment":{
            "ffprobe":version,"python":sys.version.split()[0],
            "os":platform.system(),"arch":platform.machine(),
        },
        "samples":measured,
        "summary":{
            "invocations":len(values),
            "median_wall_sec":round(statistics.median(values),6),
            "p95_wall_sec":p95,
            "p95_reason":None if p95 is not None else "requires_at_least_20_samples",
            "input_bytes_total":sum(r["input_bytes"] for r in measured),
            "ffprobe_output_bytes_total":sum(r["output_bytes"] for r in measured),
            "full_pipeline_coverage":False,
            "peak_rss_bytes":None,"gpu_peak_bytes":None,
        },
    }


def evaluate_predictions(manifest,submitted,*,verify_prediction=None):
    """Score only original sample-to-source-SHA-pinned extractor predictions.

    If the caller has no independent verifier, results remain unverified and
    are NOT human-gold release scores even if numerical values look good.
    """
    if type(submitted) is not dict or set(submitted)!={"schema_version","cases"} or (
        submitted["schema_version"]!=PREDICTIONS or type(submitted["cases"]) is not list
        or len(submitted["cases"])>128
    ):
        raise BenchmarkError("prediction_contract_invalid")
    origin={c["id"]:c for c in manifest["cases"]}
    seen=set()
    scores=[]
    for c in submitted["cases"]:
        if type(c) is not dict or set(c)!={"id","asset_sha256","engine","predictions"}:
            raise BenchmarkError("prediction_fields_invalid")
        ident=c["id"]
        if ident in seen or ident not in origin:
            raise BenchmarkError("prediction_case_unknown_or_duplicate")
        seen.add(ident)
        gold=origin[ident]
        allowed={a["sha256"] for a in gold["assets"]}
        if c["asset_sha256"] not in allowed:
            raise BenchmarkError("prediction_asset_sha_mismatch")
        engine=c["engine"]
        if (type(engine) is not dict or set(engine)!={"name","version","model_sha256"}
            or any(type(engine[k]) is not str or not engine[k] for k in ("name","version"))
            or type(engine["model_sha256"]) is not str
            or len(engine["model_sha256"])!=64
            or any(x not in "0123456789abcdef" for x in engine["model_sha256"])):
            raise BenchmarkError("prediction_model_unpinned")
        pred=c["predictions"]
        if type(pred) is not dict or set(pred)-{
            "scene_boundaries_sec","subtitle_presence","ocr_lines",
            "audio_events","asr_transcript"
        }:
            raise BenchmarkError("prediction_unknown_fields")
        verdict={
            "id":ident,"asset_sha256":c["asset_sha256"],
            "metrics":{},"available_streams":sorted(pred),
            "independently_verified":False,
            "gold_classification":"synthetic_constructed_not_human_gold",
        }
        if verify_prediction is not None:
            if not callable(verify_prediction):
                raise BenchmarkError("prediction_verifier_invalid")
            verdict["independently_verified"]=(
                verify_prediction({"case":gold,"prediction":c}) is True
            )
        if "scene_boundaries_sec" in pred:
            verdict["metrics"]["scene"]=scene_boundaries(
                gold["expected"]["scene_boundaries_sec"],
                pred["scene_boundaries_sec"]
            )
        if "subtitle_presence" in pred:
            verdict["metrics"]["subtitle"]=subtitle_truth(
                [gold["expected"]["subtitle_presence"]],
                [pred["subtitle_presence"]]
            )
        if "ocr_lines" in pred:
            verdict["metrics"]["ocr_lines"]=ocr_recall(
                gold["expected"]["ocr_lines"],pred["ocr_lines"]
            )
            if len(gold["expected"]["ocr_lines"])==1 and len(pred["ocr_lines"])==1:
                verdict["metrics"]["ocr_cer"]=score_asr(
                    gold["expected"]["ocr_lines"][0],pred["ocr_lines"][0]
                )["cer"]
        if "audio_events" in pred:
            verdict["metrics"]["audio_events"]=event_intervals(
                gold["expected"]["audio_events"],pred["audio_events"]
            )
        if "asr_transcript" in pred:
            reference=gold["expected"]["asr_reference"]
            if reference is None:
                verdict["metrics"]["asr"]={
                    "status":"not_scored_no_real_speech_gold"
                }
            else:
                verdict["metrics"]["asr"]=score_asr(reference,pred["asr_transcript"])
        scores.append(verdict)
    return {
        "status":"scored_submitted_predictions_not_release_certified",
        "gold_provenance":manifest["gold_provenance"],
        "evaluated_cases":len(scores),
        "missing_cases":sorted(set(origin)-seen),
        "results":scores,
        "all_prediction_origins_verified":bool(scores) and all(
            x["independently_verified"] for x in scores
        ),
        "human_real_media_accuracy_certified":False,
    }


def planning_estimate_10k(*,full_pipeline_media_seconds,
                           worker_slots,utilization=.65):
    """A scenario, NOT observed throughput; missing full E2E is explicit."""
    if full_pipeline_media_seconds is None:
        return {
            "status":"unavailable_no_measured_full_pipeline",
            "target_assets_per_30d":10000,
            "estimated_workers_required":None,
            "estimated_assets_per_30d":None,
            "observed_capacity_claim":False,
        }
    if (type(full_pipeline_media_seconds) not in (float,int) or
        type(full_pipeline_media_seconds) is bool or
        not math.isfinite(full_pipeline_media_seconds) or
        full_pipeline_media_seconds<=0 or
        type(worker_slots) is not int or not 1<=worker_slots<=128 or
        type(utilization) not in (float,int) or type(utilization) is bool or
        not 0<utilization<=1):
        raise BenchmarkError("capacity_parameters_invalid")
    seconds=30*24*3600
    per_slot=seconds*utilization/full_pipeline_media_seconds
    return {
        "status":"hypothetical_model_only_not_observed",
        "target_assets_per_30d":10000,
        "assumed_pipeline_wall_sec_per_asset":full_pipeline_media_seconds,
        "assumed_worker_slots":worker_slots,
        "assumed_utilization":utilization,
        "estimated_workers_required":math.ceil(10000/per_slot),
        "estimated_assets_per_30d":math.floor(worker_slots*per_slot),
        "observed_capacity_claim":False,
    }


def load_calibration(path):
    """Release thresholds are ONLY set after independent real gold baseline."""
    raw=json.loads(Path(path).read_text(encoding="utf-8"))
    if (type(raw) is not dict or raw.get("schema_version")!="ga22-calibration/v1"
        or set(raw)!={"schema_version","status","reference_corpus_sha256",
                      "independent_review_verified","thresholds"}
        or raw["status"]!="not_calibrated"
        or raw["reference_corpus_sha256"] is not None
        or raw["independent_review_verified"] is not False
        or type(raw["thresholds"]) is not dict
        or set(raw["thresholds"])!=set(REQUIRED_RELEASE_METRICS)
        or any(value is not None for value in raw["thresholds"].values())):
        raise BenchmarkError("unverified_quality_thresholds_must_remain_null")
    return raw


def build_report(manifest,gold_path,root,*,calibration_path,
                 predictions=None,verify_prediction=None,
                 ffprobe="ffprobe",probe_repetitions=3):
    verify_generated_manifest(root,manifest,gold_path)
    calibration=load_calibration(calibration_path)
    probe=profile_probe(
        root,manifest,ffprobe=ffprobe,repetitions=probe_repetitions
    )
    scoring=(None if predictions is None else evaluate_predictions(
        manifest,predictions,verify_prediction=verify_prediction
    ))
    output={
        "schema_version":PROTOCOL,
        "status":"synthetic_fixture_probe_measured_release_gates_blocked",
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "gold_definition_sha256":manifest["source_gold_sha256"],
        "generated_asset_sha256s":{
            a["name"]:a["sha256"] for c in manifest["cases"] for a in c["assets"]
        },
        "fixture_truth":"synthetic_constructed_not_independent_human_gold",
        "media_rights":manifest["rights"],
        "measurement":probe,
        "quality":scoring,
        "calibration":calibration,
        "planning_estimate":planning_estimate_10k(
            full_pipeline_media_seconds=None,worker_slots=1
        ),
        "release_quality_gate":"NOT_VERIFIED",
        "real_asr_wer_cer":"NOT_MEASURED_NO_LICENSED_HUMAN_SPEECH",
        "real_ocr_accuracy":"NOT_MEASURED_NO_INDEPENDENT_HUMAN_GOLD",
        "physical_cpu_gpu_memory":"NOT_MEASURED",
        "full_pipeline_bottlenecks":"UNKNOWN_ONLY_FFPROBE_TIMED",
        "independent_runtime_e2e":"NOT_VERIFIED",
    }
    output["report_sha256"]=canonical_sha(output)
    return output
