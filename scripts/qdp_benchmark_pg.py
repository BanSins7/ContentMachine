#!/usr/bin/env python3
"""FIX-17 measured PostgreSQL 17 synthetic 10k-row read-scale proxy.

This is NOT an end-to-end 10,000-video SRE/QDP run. It measures indexed
keyset/diagnostic SQL over disposable temp relations with QDP-like identity
and delivery keys. One report/CI artifact records exact timings and plans;
no invented SLO or claim of real media/Group processing.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime,timezone
from pathlib import Path


def _nodes(plan):
    result=[]
    def walk(node):
        result.append({"node_type":node.get("Node Type"),
                       "index_name":node.get("Index Name"),
                       "actual_rows":node.get("Actual Rows"),
                       "actual_loops":node.get("Actual Loops")})
        for child in node.get("Plans",[]):
            walk(child)
    walk(plan)
    return result


def run(dsn, *, videos=10000):
    if os.environ.get("QDP_DISPOSABLE_TEST_DB")!="YES":
        raise RuntimeError("Only explicitly disposable PostgreSQL is accepted")
    if videos!=10000:
        raise ValueError("Bench sample is fixed at exactly 10,000 videos")
    import psycopg
    with psycopg.connect(dsn,connect_timeout=5,autocommit=True) as c:
        server=int(c.execute("SHOW server_version_num").fetchone()[0])
        if not 170000<=server<180000:
            raise RuntimeError("PG17 required")
        with c.transaction():
            c.execute("""CREATE TEMP TABLE qdp_bench_items (
                  video_id bigint NOT NULL, run_id uuid NOT NULL,
                  status text NOT NULL, group_code text NOT NULL,
                  PRIMARY KEY (run_id, video_id)) ON COMMIT DROP""")
            c.execute("""INSERT INTO qdp_bench_items
                 SELECT n,'11111111-1111-4111-8111-111111111111'::uuid,
                   CASE WHEN n%7=0 THEN 'blocked' ELSE 'ready' END,
                   CASE WHEN n%3=0 THEN 'C' ELSE 'A' END
                 FROM generate_series(1,10000) AS n""")
            c.execute("""CREATE TEMP TABLE qdp_bench_deliveries (
                  event_id bigint PRIMARY KEY,video_id bigint NOT NULL,
                  consumer text NOT NULL,state text NOT NULL,
                  run_after timestamptz NOT NULL) ON COMMIT DROP""")
            c.execute("""INSERT INTO qdp_bench_deliveries
                 SELECT n*10+k,n,
                   CASE k WHEN 1 THEN 'group_a' WHEN 2 THEN 'group_b' ELSE 'group_c' END,
                   CASE WHEN n%9=0 THEN 'dead_letter'
                        WHEN n%5=0 THEN 'acknowledged'
                        ELSE 'pending' END,
                   now()-interval '1 hour'
                 FROM generate_series(1,10000) n
                 CROSS JOIN generate_series(1,3) k""")
            c.execute("""CREATE INDEX qdp_bench_delivery_claim
                          ON qdp_bench_deliveries(consumer,state,run_after,event_id)
                          WHERE state IN ('pending','leased')""")
            c.execute("ANALYZE qdp_bench_items")
            c.execute("ANALYZE qdp_bench_deliveries")
            count=c.execute("SELECT count(*) FROM qdp_bench_items").fetchone()[0]
            deliveries=c.execute("SELECT count(*) FROM qdp_bench_deliveries").fetchone()[0]
            assert (count,deliveries)==(10000,30000)
            queries={
                "items_keyset_page_50":("""
                    SELECT video_id,status FROM qdp_bench_items
                    WHERE run_id='11111111-1111-4111-8111-111111111111'::uuid
                      AND video_id>9950 ORDER BY video_id LIMIT 50""",50),
                "delivery_claim_1":("""
                    SELECT event_id FROM qdp_bench_deliveries
                    WHERE consumer='group_a' AND state='pending'
                      AND run_after<=now()
                    ORDER BY run_after,event_id LIMIT 1""",1),
                "readiness_grouped_counts":("""
                    SELECT group_code,status,count(*) FROM qdp_bench_items
                    GROUP BY group_code,status ORDER BY group_code,status""",None),
                "delivery_grouped_counts":("""
                    SELECT consumer,state,count(*) FROM qdp_bench_deliveries
                    GROUP BY consumer,state ORDER BY consumer,state""",None),
            }
            evidence={}
            for name,(sql,expected) in queries.items():
                report=c.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "+sql).fetchone()[0][0]
                plan=report["Plan"]
                evidence[name]={
                    "planning_ms":report["Planning Time"],
                    "execution_ms":report["Execution Time"],
                    "actual_output_rows":plan["Actual Rows"],
                    "nodes":_nodes(plan),
                    "sql":sql.strip(),
                }
                if expected is not None and plan["Actual Rows"]!=expected:
                    raise AssertionError(f"{name}: unexpected rows: {plan['Actual Rows']}")
            return {
                "schema_version":"qdp-synthetic-10k-benchmark/v1",
                "measured_at":datetime.now(timezone.utc).isoformat(),
                "database":"PostgreSQL 17 disposable",
                "fixture":"TEMP SYNTHETIC QDP-LIKE READ INDEX PROXY, NOT ACTUAL SRE VIDEO PIPELINE",
                "video_rows":count,"consumer_delivery_rows":deliveries,
                "paid_calls":0,"measured_queries":evidence,
                "full_qualified_pipeline_10k_status":"NOT_MEASURED",
                "throughput_videos_per_second":None,
                "slo_claim":None,
            }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args()
    dsn=os.environ.get("CM_TEST_PG_DSN")
    if not dsn or os.environ.get("QDP_DISPOSABLE_TEST_DB")!="YES":
        parser.error("Requires disposable CM_TEST_PG_DSN and QDP_DISPOSABLE_TEST_DB=YES")
    report=run(dsn)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    summary={name:{"execution_ms":x["execution_ms"],
                   "node_types":[v["node_type"] for v in x["nodes"]]}
             for name,x in report["measured_queries"].items()}
    print("QDP_BENCH_REPORT="+json.dumps({
        "schema_version":report["schema_version"],
        "video_rows":report["video_rows"],
        "consumer_delivery_rows":report["consumer_delivery_rows"],
        "queries":summary,
        "full_qualified_pipeline_10k_status":"NOT_MEASURED",
    }))

if __name__=="__main__":
    main()
