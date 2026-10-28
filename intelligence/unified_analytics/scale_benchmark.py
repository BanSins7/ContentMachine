"""UA31 opt-in disposable PostgreSQL17 load benchmark (10k/50k UNQUALIFIED).

This is NOT a benchmark of qualified UAE snapshots or real QDP/SRE/A/B/C.
Generated rows have status='blocked', no reviewed/complete evidence, and no
payload envelope. This measures bulk project-scoped UAE storage/index scans
only, not all seven-reference joins/producer verification or end-to-end latency.
Run from repository root:
    python -m intelligence.unified_analytics.scale_benchmark --rows 10000 --confirm-disposable
    python -m intelligence.unified_analytics.scale_benchmark --rows 50000 --confirm-disposable
Requires CM_TEST_PG_DSN distinct from CM_DB_DSN; cleanup is always scoped to
the generated project/source/video. Never auto-runs on import or CI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from uuid import uuid4
from typing import Any


BENCHMARK_WIRE="uae-scale-benchmark/v1"
ROWS_ALLOWED=(10000,50000)
BATCH_LIMIT=5000
PAGE_LIMIT=50
REQUEST_PREFIX="ua31.load."
BATCH_SQL="""
WITH new_requests AS (
 INSERT INTO uae_qualification_requests(
  project_id,video_id,source_id,source_policy_version_id,
  qdp_run_id,qdp_input_sha256,content_kind,request_key,request_sha256
 )
 SELECT %s,%s,%s,%s,%s::uuid,%s,'video',
        'ua31.load.'||g::text,%s
 FROM generate_series(%s::int,%s::int) g
 RETURNING id,request_key
)
INSERT INTO uae_unified_snapshots(
 project_id,request_id,video_id,source_id,source_policy_version_id,
 qdp_run_id,qdp_input_sha256,content_kind,content_type,
 snapshot_version,status,scope_sha256,inputs_sha256,decision_sha256,
 blockers,readiness,verified_as_of
)
SELECT %s,r.id,%s,%s,%s,%s::uuid,%s,'video','video',
 split_part(r.request_key,'.',3)::int,
 'blocked',%s,%s,%s,
 '[{"code":"qualification_effective_approval_unavailable"}]'::jsonb,
 '{}'::jsonb,clock_timestamp()-interval '1 second'
FROM new_requests r
"""
PAGE_SQL="""
SELECT id FROM uae_unified_snapshots
WHERE project_id=%s AND status IN ('partial','blocked')
 AND (%s::bigint IS NULL OR id<%s::bigint)
ORDER BY id DESC LIMIT %s
"""
COUNT_SQL="""
SELECT count(*)::bigint AS n FROM uae_unified_snapshots WHERE project_id=%s
"""
QUALIFIED_SQL="""
SELECT count(*)::bigint AS n FROM uae_unified_snapshots
WHERE project_id=%s AND status='qualified'
"""
PROJECT_SQL="""
INSERT INTO project_registry(id,name,profile_revision,profile_hash,
 created_at,updated_at)
VALUES(%s,'UAE31 disposable load probe',1,%s,now(),now())
"""
SOURCE_SQL="""
INSERT INTO sources(id,provider,source_type,status,retention_days,
 can_store_metrics,can_derive_scores,can_analyze_media,can_store_metadata)
VALUES(%s,'local','manual','enabled',30,TRUE,TRUE,TRUE,TRUE)
"""
POLICY_SQL="""
INSERT INTO source_policy_versions(source_id,version,is_active,reviewed_at,
 permissions,retention_policy)
VALUES(%s,1,TRUE,clock_timestamp()-interval '1 minute',%s::jsonb,%s::jsonb)
RETURNING id
"""
VIDEO_SQL="""
INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
VALUES('youtube',%s,'https://example.invalid/uae31','video') RETURNING id
"""
RIGHTS='{"can_store_metadata":true,"can_analyze_media":true,"can_store_metrics":true,"can_derive_scores":true}'
RETENTION='{"derived_retention_days":25}'


def validate_args(args:Any,env:dict[str,str])->str:
    """No connection and no modifications until every safety gate passes."""
    if (args.rows not in ROWS_ALLOWED or type(args.rows) is not int
        or type(args.batch) is not int or not 1<=args.batch<=BATCH_LIMIT
        or args.batch>args.rows or args.confirm_disposable is not True):
        raise ValueError("ua31_benchmark_scope_confirmation_required")
    dsn=env.get("CM_TEST_PG_DSN")
    if (type(dsn) is not str or not dsn.strip()
        or dsn==env.get("CM_DB_DSN")
        or dsn==env.get("CM_UAE_WORKER_DSN")
        or dsn==env.get("CM_UAE_OPS_DSN")):
        raise ValueError("ua31_disposable_dsn_required")
    # A human must provision the disposable DB. Never fallback to a product DSN.
    return dsn


def parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rows",type=int,choices=ROWS_ALLOWED,required=True)
    p.add_argument("--batch",type=int,default=2000)
    p.add_argument("--confirm-disposable",action="store_true")
    return p


def _count(db:Any,sql:str,project:str)->int:
    x=db.execute(sql,(project,)).fetchone()
    if not isinstance(x,dict) or type(x.get("n")) is not int or x["n"]<0:
        raise RuntimeError("ua31_benchmark_invalid_count")
    return x["n"]


def _pages(db:Any,project:str,n:int)->int:
    before=None
    seen=0
    previous=None
    while True:
        rows=db.execute(PAGE_SQL,(project,before,before,PAGE_LIMIT)).fetchall()
        if type(rows) is not list or len(rows)>PAGE_LIMIT:
            raise RuntimeError("ua31_benchmark_invalid_page")
        for row in rows:
            value=row.get("id")
            if (type(value) is not int or value<=0
                or (previous is not None and value>=previous)):
                raise RuntimeError("ua31_benchmark_nonmonotonic_keyset")
            previous=value
        seen+=len(rows)
        if not rows:
            break
        before=rows[-1]["id"]
    if seen!=n:
        raise RuntimeError("ua31_benchmark_missing_rows")
    return seen


def run(db:Any,*,rows:int,batch:int,clock:Any=time.perf_counter)->dict:
    """DB must be disposable, PG17, autocommit, dict_row; caller enforces it."""
    nonce=uuid4().hex
    project=nonce
    source="uae31-"+nonce
    h="a"*64
    run_id=str(uuid4())
    video=None
    try:
        with db.transaction():
            db.execute(PROJECT_SQL,(project,h))
            db.execute(SOURCE_SQL,(source,))
            policy=db.execute(POLICY_SQL,(source,RIGHTS,RETENTION)).fetchone()["id"]
            video=db.execute(VIDEO_SQL,(nonce,)).fetchone()["id"]
            db.execute("INSERT INTO sre_workspaces(project_id) VALUES(%s)",(project,))
            db.execute("INSERT INTO sre_project_videos(project_id,video_id) VALUES(%s,%s)",
                       (project,video))
        start=clock()
        for first in range(1,rows+1,batch):
            last=min(rows,first+batch-1)
            with db.transaction():
                db.execute(BATCH_SQL,(
                    project,video,source,policy,run_id,h,h,first,last,
                    project,video,source,policy,run_id,h,h,h,h,
                ))
        write_seconds=clock()-start
        if _count(db,COUNT_SQL,project)!=rows:
            raise RuntimeError("ua31_benchmark_incorrect_insert_count")
        if _count(db,QUALIFIED_SQL,project)!=0:
            raise RuntimeError("ua31_benchmark_forged_qualified")
        start=clock()
        scanned=_pages(db,project,rows)
        page_seconds=clock()-start
        return {
            "schema_version":BENCHMARK_WIRE,
            "fixture":"unqualified_blocked_no_payload_no_peer_approval",
            "qualified_records":0,
            "rows":rows,
            "batch_size":batch,
            "keyset_page_size":PAGE_LIMIT,
            "scanned":scanned,
            "insert_seconds":round(write_seconds,6),
            "insert_rows_per_second":round(rows/max(write_seconds,1e-9),2),
            "keyset_seconds":round(page_seconds,6),
            "keyset_rows_per_second":round(rows/max(page_seconds,1e-9),2),
            "peak_memory_measured":False,
            "worker_throughput_measured":False,
            "qualified_e2e_throughput_measured":False,
            "producer_model_latency_measured":False,
        }
    finally:
        # Cleanup is limited to UUID-like benchmark project/source/video
        # created by this run. Do not delete peer-owned data.
        try:
            with db.transaction():
                db.execute("DELETE FROM project_registry WHERE id=%s",(project,))
                db.execute("DELETE FROM sources WHERE id=%s",(source,))
                if video is not None:
                    db.execute("DELETE FROM videos WHERE id=%s",(video,))
        except Exception:
            raise RuntimeError("ua31_benchmark_cleanup_failed") from None


def main(argv:list[str]|None=None,*,environ:dict[str,str]|None=None)->int:
    opts=parser().parse_args(argv)
    env=dict(os.environ if environ is None else environ)
    try:
        dsn=validate_args(opts,env)
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(dsn,autocommit=True,row_factory=dict_row,
                             connect_timeout=5) as db:
            version=int(db.execute("SHOW server_version_num").fetchone()["server_version_num"])
            if not 170000<=version<180000:
                raise RuntimeError("ua31_postgres17_required")
            from intelligence import migrate
            migrate.apply_migrations(db)
            result=run(db,rows=opts.rows,batch=opts.batch)
        print(json.dumps(result,sort_keys=True,separators=(",",":")))
        return 0
    except KeyboardInterrupt:
        print("UAE31 benchmark interrupted",file=sys.stderr)
        return 130
    except Exception:
        # Storage or SQL errors can embed DSNs/media identifiers.
        print("UAE31 benchmark unavailable; verify isolated PG17 and fixture",file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
