"""Operator-only CLI for the trusted QV -> Collector publication stage.

Default is read-only inspect, never a provider call. --publish explicitly
performs the atomic database publication/ACK; --pending additionally requires
CM_COLLECTOR_QV_CONSUMER_ENABLED=1. Not an automatic daemon.
"""
from __future__ import annotations

import argparse
import json

from .query_publisher import (
    DEFAULT_SHARD_SIZE, MAX_SHARD_SIZE, prepare_qv_publication,
    publish_qv, publish_due_qv,
)
from .qv_reader import read_trusted_qv
from .contracts import CollectorError, PROJECT_RE, require_id, require_uuid
from .adapters.main_qv import MainQVAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description="Trusted Collector QV publisher; no DTK calls")
    parser.add_argument("--project-id", required=True, help="32-character project hex identifier")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--outbox-id", help="Exact production QV event UUID")
    group.add_argument("--pending", action="store_true",
                       help="Explicit opt-in to consume due events (requires consumer flag)")
    parser.add_argument("--shard-size", type=int, default=DEFAULT_SHARD_SIZE)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--publish", action="store_true",
                        help="Commit manifest/shards and ACK event; omitted means read-only")
    args = parser.parse_args()
    try:
        require_id(args.project_id, "project_id", PROJECT_RE)
        if not 1 <= args.shard_size <= MAX_SHARD_SIZE:
            parser.error("Shard size must be 1..50")
        if not 1 <= args.limit <= 100:
            parser.error("Limit must be 1..100")
        if args.pending:
            if args.publish:
                report = publish_due_qv(args.project_id, limit=args.limit,
                                        shard_size=args.shard_size)
            else:
                events = MainQVAdapter().pending_validated_outbox(
                    args.project_id, limit=args.limit)
                report = {"schema_version": "collector.qv_pending_preview/v1",
                          "project_id": args.project_id,
                          "outbox_ids": [x["outbox_id"] for x in events],
                          "publish": False, "provider_calls": 0}
        else:
            oid = require_uuid(args.outbox_id, "outbox_id")
            if args.publish:
                report = publish_qv(args.project_id, oid,
                                    shard_size=args.shard_size)
            else:
                source = read_trusted_qv(args.project_id, oid, allow_acknowledged=True)
                plan = prepare_qv_publication(source, shard_size=args.shard_size)
                report = {"schema_version": "collector.qv_publication_preview/v1",
                          "project_id": source.project_id,
                          "outbox_id": oid, "status": plan.status,
                          "selected_count": plan.selected_count,
                          "coverage_count": plan.coverage_count,
                          "shard_count": plan.shard_count,
                          "manifest_sha256": plan.manifest_sha256,
                          "publish": False, "provider_calls": 0}
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    except CollectorError as exc:
        parser.exit(status=2, message="Collector: " + exc.code + ": " + str(exc) + "\n")


if __name__ == "__main__":
    main()
