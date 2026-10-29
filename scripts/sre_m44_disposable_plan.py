"""M44 manual offline generator for bounded synthetic chaos/load fixture plans.

No SRE LIVE writer, no PostgreSQL, no network, no inference, no external data.
Prints a reproducible synthetic plan, not evidence of M44 chaos/load PASS.
"""
from __future__ import annotations

import argparse
import json
import sys
from uuid import UUID, uuid5

from intelligence.relevance.m44_chaos_plan import (
    M44PlanBlocked, iter_bounded_pairs, plan_chaos,
)

_NAMESPACE = UUID("61e0caac-41ea-40de-9efc-58a9880e8a30")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--videos", type=int, choices=(100, 1000, 10000),
                        required=True)
    parser.add_argument("--leaves", type=int, required=True)
    parser.add_argument("--chunk", type=int, default=100)
    parser.add_argument("--seed", default="sre-m44-synthetic-only")
    parser.add_argument("--enumerate", action="store_true",
                        help="stream all synthetic pairs with a rolling SHA-256, never DB")
    args = parser.parse_args(argv)
    if not 1 <= args.leaves <= 48 or not 1 <= len(args.seed) <= 128:
        parser.error("leaves 1..48 and seed 1..128 characters required")
    import hashlib
    seed_sha = hashlib.sha256(args.seed.encode("utf-8")).hexdigest()
    keys = sorted(str(uuid5(_NAMESPACE, seed_sha + ":" + str(i)))
                  for i in range(args.leaves))
    try:
        plan = plan_chaos(args.videos, keys, chunk_size=args.chunk,
                          seed_sha256=seed_sha)
        if args.enumerate:
            checksum = hashlib.sha256()
            chunks = pairs = 0
            for item in iter_bounded_pairs(args.videos, keys,
                                           chunk_size=args.chunk):
                chunks += 1
                for video_id, category_key in item["pairs"]:
                    checksum.update(f"{video_id}:{category_key}\n".encode("utf-8"))
                    pairs += 1
            if chunks != plan["chunk_count"] or pairs != plan["total_expected_pairs"]:
                raise M44PlanBlocked("incomplete streamed synthetic pair count")
            plan["enumerated_pairs"] = pairs
            plan["stream_sha256"] = checksum.hexdigest()
        print(json.dumps(plan, sort_keys=True, ensure_ascii=False, indent=2))
        return 0
    except M44PlanBlocked as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc),
                          "release_gate": "not_executed"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
