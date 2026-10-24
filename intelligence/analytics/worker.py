"""Manual safe operations: python -m intelligence.analytics.worker --once | --limit 20 | --purge."""
from __future__ import annotations
import argparse
import json
import time
from .queue import process_one, purge_unreadable

def main(argv=None):
    parser=argparse.ArgumentParser(description="Local qualified analytics database worker")
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once",action="store_true",help="Claim at most one analytics job")
    mode.add_argument("--limit",type=int,help="Run up to N jobs (1..100)")
    mode.add_argument("--purge",action="store_true",help="Delete now unreadable cached snapshots")
    mode.add_argument("--watch",action="store_true",help="Explicit opt-in periodic local retention cleanup and bounded jobs")
    parser.add_argument("--interval-minutes",type=int,default=60,
                        help="Polling cadence for --watch, 1..1440 minutes (default 60)")
    parser.add_argument("--worker-id",default="manual-analytics")
    args=parser.parse_args(argv)
    if not 1<=args.interval_minutes<=1440:
        parser.error("--interval-minutes must be 1..1440")
    if args.watch:
        # This is an explicit foreground process. App boot does not silently
        # create a daemon, spend money or mutate source/provider systems.
        try:
            while True:
                report=purge_unreadable()
                processed=0
                for _ in range(100):
                    if process_one(worker_id=args.worker_id) is None:
                        break
                    processed+=1
                print(json.dumps({"maintenance":report,"jobs_processed":processed},
                                 default=str,ensure_ascii=False),flush=True)
                time.sleep(args.interval_minutes*60)
        except KeyboardInterrupt:
            return 0
    if args.purge:
        result=purge_unreadable()
        print(json.dumps(result,sort_keys=True))
        return 0
    limit=1 if args.once else args.limit
    if limit is None or not 1<=limit<=100:
        parser.error("--limit must be 1..100")
    result=[]
    for _ in range(limit):
        job=process_one(worker_id=args.worker_id)
        if job is None: break
        result.append({k:str(v) if k=="id" else v for k,v in job.items()})
    print(json.dumps({"processed":len(result),"jobs":result},default=str,ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
