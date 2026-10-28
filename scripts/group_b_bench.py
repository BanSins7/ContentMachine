#!/usr/bin/env python3
"""GB-MOD-28 synthetic CPU batch planning microbenchmark, zero paid I/O.

This is NOT a model benchmark and cannot prove QDP/GA/video extraction
throughput. No secrets, network calls, provider metadata, files or DB data.
Reports real on-run timings (never prefilled fake measurements).
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import statistics
import sys
from time import perf_counter_ns
import tracemalloc
from uuid import UUID

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from intelligence.group_b.monitoring import ThroughputPolicy,plan_queue_batch


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--rounds",type=int,default=100)
    ap.add_argument("--report",default="qa-evidence/group-b-synthetic-perf.json")
    args=ap.parse_args()
    if type(args.rounds) is not int or not 10<=args.rounds<=500:
        raise SystemExit("rounds must be 10..500")
    policy=ThroughputPolicy(max_batch_jobs=1000,max_running_local=4)
    # Deterministic UUIDs; no database or real model input.
    rows=[(str(UUID(int=i+1)),"paid" if i%9==0 else "local")
          for i in range(1000)]
    samples=[]
    tracemalloc.start()
    for _ in range(args.rounds):
        t0=perf_counter_ns()
        output=plan_queue_batch(policy,rows)
        elapsed_ms=(perf_counter_ns()-t0)/1_000_000.0
        samples.append(elapsed_ms)
        if (output.total!=1000 or
            len(output.paid_deferred)!=112 or
            output.max_concurrency!=4 or
            sum(len(w) for w in output.local_waves)!=888):
            raise SystemExit("synthetic batch invariants failed")
    _,peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    sorted_ms=sorted(samples)
    metrics={
        "schema_version":"group-b-synthetic-performance/v1",
        "commit_sha":os.environ.get("GITHUB_SHA","unverified_local_checkout"),
        "measured_utc":datetime.now(timezone.utc).isoformat(),
        "synthetic_only":True,
        "real_model_throughput_measured":False,
        "sample_runs":args.rounds,
        "jobs_per_run":1000,
        "cpu_jobs":888,
        "paid_jobs_deferred":112,
        "max_cpu_wave":4,
        "p50_ms":round(statistics.median(samples),4),
        "p95_ms":round(sorted_ms[min(len(sorted_ms)-1,
                    max(0,int(args.rounds*0.95)-1))],4),
        "max_ms":round(max(samples),4),
        "memory_peak_bytes":peak,
        "paid_provider_posts":0,
    }
    target=Path(args.report)
    if not target.is_absolute():
        target=ROOT/target
    target.parent.mkdir(exist_ok=True,parents=True)
    target.write_text(json.dumps(metrics,sort_keys=True,indent=2,
                      allow_nan=False)+"\n",encoding="utf-8")
    print("Synthetic 1000-job planning p50/p95 ms:",
          metrics["p50_ms"],metrics["p95_ms"],
          "(NOT actual model throughput, 112 paid jobs deferred)")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
