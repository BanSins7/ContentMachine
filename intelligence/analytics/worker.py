"""Manual safe operations: python -m intelligence.analytics.worker --once | --limit 20 | --purge."""
from __future__ import annotations
import argparse
import json
from .queue import process_one, purge_unreadable

def main(argv=None):
    parser=argparse.ArgumentParser(description="Local qualified analytics database worker")
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once",action="store_true",help="Claim at most one analytics job")
    mode.add_argument("--limit",type=int,help="Run up to N jobs (1..100)")
    mode.add_argument("--purge",action="store_true",help="Delete now unreadable cached snapshots")
    parser.add_argument("--worker-id",default="manual-analytics")
    args=parser.parse_args(argv)
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
