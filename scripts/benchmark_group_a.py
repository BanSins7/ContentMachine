#!/usr/bin/env python3
"""GA22: synthetic only FFprobe benchmark; no model weights/download/LLM.

Run from repo root. Generated files are always written to a new empty folder;
reports identify observed probe timings vs missing full pipeline/model gold.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from intelligence.group_a.benchmark_fixtures import generate_synthetic_corpus
from intelligence.group_a.benchmark_runner import build_report
from intelligence.group_a.benchmark_metrics import BenchmarkError

ROOT=Path(__file__).resolve().parents[1]
GOLD=ROOT/"tests/fixtures/group_a_benchmark_gold_v1.json"
CALIBRATION=ROOT/"tests/fixtures/group_a_benchmark_calibration_v1.json"


def run(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True,
                        help="New EMPTY output directory (no overwrites)")
    parser.add_argument("--gold",type=Path,default=GOLD)
    parser.add_argument("--calibration",type=Path,default=CALIBRATION)
    parser.add_argument("--ffmpeg",default="ffmpeg")
    parser.add_argument("--ffprobe",default="ffprobe")
    parser.add_argument("--repetitions",type=int,default=3)
    parser.add_argument("--predictions",type=Path,default=None,
                        help="Optional real pinned-model observations; unauthenticated unless host verifies")
    args=parser.parse_args(argv)
    output=args.output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        parser.error("Output must be a new empty directory; no overwrite permitted")
    output.mkdir(parents=True,exist_ok=True)
    try:
        corpus=generate_synthetic_corpus(
            gold_path=args.gold,output_dir=output,ffmpeg=args.ffmpeg
        )
        prediction=None
        if args.predictions is not None:
            if args.predictions.stat().st_size>2_000_000:
                raise BenchmarkError("predictions_file_too_large")
            prediction=json.loads(args.predictions.read_text(encoding="utf-8"))
        report=build_report(
            corpus,args.gold,output,calibration_path=args.calibration,
            predictions=prediction,ffprobe=args.ffprobe,
            probe_repetitions=args.repetitions,
        )
        (output/"report.json").write_text(
            json.dumps(report,sort_keys=True,indent=2,ensure_ascii=False)+"\n",
            encoding="utf-8"
        )
        print(json.dumps({
            "schema_version":report["schema_version"],
            "status":report["status"],
            "report_sha256":report["report_sha256"],
            "ffprobe_samples":report["measurement"]["summary"]["invocations"],
            "median_probe_wall_sec":report["measurement"]["summary"]["median_wall_sec"],
            "release_quality_gate":report["release_quality_gate"],
            "output_manifest":str(output/"manifest.json"),
            "output_report":str(output/"report.json"),
        },ensure_ascii=False))
        return 0
    except (BenchmarkError,OSError,ValueError) as exc:
        print("GA22: "+(exc.code if isinstance(exc,BenchmarkError)
                      else "benchmark_failed"),
              file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(run())
