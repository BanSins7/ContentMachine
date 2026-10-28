"""GC-MOD-29 reproducible, opt-in, disposable PostgreSQL 17 load benchmark.

Never starts automatically or in normal unit/CI runs. The 10k population is
real SQL inserted into an EXPLICIT disposable PostgreSQL 17 database (NOT a
fake list or timing estimate). An independent --smoke mode uses 64 rows and
CANNOT be reported as a 10k benchmark. No C7 scores, reviewed cohorts or
production source data are generated/recomputed.

Run only with:
 GC_TEST_DISPOSABLE_DB=YES GC_PERF_ALLOW_WRITE=YES
 CM_TEST_PG_DSN=postgresql://.../contentmachine_gc_performance
 python -m intelligence.group_c.performance --benchmark --output /tmp/gc29.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Mapping
from uuid import uuid4

from .cohort_rules import CohortRules, PostgreSQLCohortCandidates
from .contracts import ContractError
from .durable_worker import PostgreSQLGroupCWorker, WorkerPolicy

BENCHMARK_VERSION="group-c-performance/v1"
MIN_POSTS=10000
MAX_POSTS=100000
SMOKE_POSTS=64
DB_PREFIXES=("contentmachine_gc_","gc_perf_")

# Exact substantive shape of GC09 latest-raw cohort query, including
# max_candidates+1 overflow probe and latest metadata LATERAL. No prefilter
# by quality or metric version before finding the latest raw observation.
SQL={
    "cohort_candidates": """WITH latest AS (
        SELECT DISTINCT ON (s.video_id) s.id AS snapshot_id,s.video_id,
               pv.project_id,s.source_id,s.source_policy_version_id,
               s.source_metric_definition,s.source_metric_version,
               s.observed_at,s.collected_at,s.views,s.likes,s.comments,s.shares,
               s.data_quality_status,s.precision,s.observation_time_assumed,
               s.expires_at
          FROM metric_snapshots s
          JOIN sre_project_videos pv ON pv.video_id=s.video_id
         WHERE pv.project_id=%s AND s.video_id<>%s
           AND s.source_id=%s AND s.observed_at<=%s
         ORDER BY s.video_id,s.observed_at DESC,s.id DESC
    )
    SELECT l.*,v.platform,v.creator_id,v.c_content_type,v.content_kind,
           c.topic_id,m.id AS metadata_id,
           m.source_policy_version_id AS metadata_policy_version_id,
           m.published_at,m.fetched_at,m.is_current,m.deleted_at,
           m.expires_at AS metadata_expires_at
      FROM latest l JOIN videos v ON v.id=l.video_id
      LEFT JOIN creators c ON c.id=v.creator_id
      LEFT JOIN LATERAL (
          SELECT x.id,x.source_policy_version_id,x.published_at,
                 x.fetched_at,x.is_current,x.deleted_at,x.expires_at
            FROM source_video_metadata x
           WHERE x.video_id=l.video_id AND x.source_id=%s
           ORDER BY x.fetched_at DESC,x.id DESC LIMIT 1
      ) m ON TRUE
     ORDER BY l.video_id LIMIT %s""",
    # Exact expensive structural shape of GC28 metric freshness pulse.
    "latest_project_observation": """SELECT
          count(*) FILTER(WHERE latest.observed_at IS NULL) AS missing,
          count(*) FILTER(WHERE latest.observed_at IS NOT NULL
            AND latest.observed_at<now()-(%s::integer*interval '1 second')) AS stale
      FROM sre_project_videos pv
      LEFT JOIN LATERAL(
          SELECT ms.observed_at FROM metric_snapshots ms
           WHERE ms.video_id=pv.video_id
             AND ms.source_metric_definition='platform_post_views'
           ORDER BY ms.observed_at DESC,ms.id DESC LIMIT 1
      ) latest ON TRUE
     WHERE pv.project_id=%s""",
    # This is an EXPLAIN-only no-lock scan probe. Separately exercise actual
    # GC21 worker.claim() and its PG advisory lock/budget below.
    "scoped_ready_queue": """SELECT j.id
        FROM group_c_jobs j
        JOIN group_c_runs r ON r.id=j.run_id
          AND r.project_id=j.project_id AND r.video_id=j.video_id
       WHERE j.project_id=%s AND j.job_kind='score'
         AND j.state='queued' AND j.available_at<=clock_timestamp()
         AND j.attempts<j.max_attempts
         AND r.state IN('queued','running','partial')
         AND NOT EXISTS (
             SELECT 1 FROM group_c_jobs active
              WHERE active.project_id=j.project_id AND active.run_id=j.run_id
                AND active.id<>j.id AND active.state='running'
                AND active.lease_until>clock_timestamp()
         )
       ORDER BY j.available_at,j.id LIMIT 1""",
    "latest_metadata": """SELECT id,source_policy_version_id,
                                     published_at,fetched_at,is_current,
                                     deleted_at,expires_at
        FROM source_video_metadata
       WHERE video_id=%s AND source_id=%s
       ORDER BY fetched_at DESC,id DESC LIMIT 1""",
}

EXPECTED_INDEXES=(
    "idx_gc29_raw_source_video_latest",
    "idx_gc29_observation_latest",
    "idx_gc29_metadata_source_latest",
    "idx_gc29_worker_project_ready",
    "idx_gc29_worker_project_lease",
    "idx_gc29_outbox_project_ready",
)


@dataclass(frozen=True)
class BenchmarkPolicy:
    post_count:int=MIN_POSTS
    queued_jobs:int=1000
    warm_repeats:int=5

    def __post_init__(self):
        if (type(self.post_count) is not int
                or not MIN_POSTS<=self.post_count<=MAX_POSTS
                or type(self.queued_jobs) is not int
                or not 2<=self.queued_jobs<=self.post_count
                or type(self.warm_repeats) is not int
                or not 1<=self.warm_repeats<=30):
            raise ContractError("invalid_benchmark_policy",
                                "Bounded >=10k dataset, queue and warm repeats required")


@dataclass(frozen=True)
class SyntheticPopulation:
    project_id:str
    source_id:str
    creator_id:int
    source_policy_version_id:int
    target_video_id:int
    target_snapshot_id:int
    target_metadata_id:int
    post_count:int
    observation_count:int
    queued_job_count:int


@dataclass(frozen=True)
class PlanMeasure:
    name:str
    first_pass_ms:float
    median_repeated_ms:float
    min_repeated_ms:float
    max_repeated_ms:float
    first_planning_ms:float
    total_shared_hit_blocks:int
    total_shared_read_blocks:int
    root_actual_rows:float
    plan_node_types:tuple[str,...]
    chosen_indexes:tuple[str,...]
    seq_scan_nodes:int
    source:str="real_pg17_explain_analyze_buffers"
    os_cache_cold_verified:bool=False


@dataclass(frozen=True)
class BackpressureMeasure:
    initial_queued:int
    claims_granted:int
    claims_denied_by_budget:int
    final_running:int
    final_queued:int
    claim_timings_ms:tuple[float,...]
    max_running_per_project:int
    action:str="actual_gc21_worker_claim_no_score_execution"


@dataclass(frozen=True)
class BenchmarkReport:
    schema_version:str
    produced_at:str
    dataset_posts:int
    dataset_observations:int
    dataset_queued_jobs:int
    qualifies_10k:bool
    database_kind:str
    pg_major:int
    cohort_overflow_confirmed:bool
    query_measures:tuple[PlanMeasure,...]
    backpressure:BackpressureMeasure
    installed_indexes:tuple[str,...]
    caveats:tuple[str,...]


def _require_disposable(conn:Any)->None:
    if (os.getenv("GC_TEST_DISPOSABLE_DB")!="YES"
            or os.getenv("GC_PERF_ALLOW_WRITE")!="YES"
            or getattr(conn,"autocommit",None) is not True):
        raise ContractError("unsafe_performance_database",
                            "Explicit disposable autocommit PG17 + write opt-in required")
    db,major=conn.execute(
        "SELECT current_database(),current_setting('server_version_num')::int/10000",
    ).fetchone()
    # int division is numeric in PostgreSQL; compare exactly to 17 after
    # coercion, not a lexicographic version prefix.
    if type(db) is not str or not db.startswith(DB_PREFIXES) or int(major)!=17:
        raise ContractError("unsafe_performance_database",
                            "Dedicated named disposable PostgreSQL17 required")


def _integer_count(cursor)->int:
    return int(cursor.fetchone()[0])


def seed(conn:Any,*,post_count:int,queued_jobs:int)->SyntheticPopulation:
    """Synthetic data and queue, never real scored C7 or reviewer signatures."""
    _require_disposable(conn)
    if (type(post_count) is not int or not SMOKE_POSTS<=post_count<=MAX_POSTS
            or type(queued_jobs) is not int or not 2<=queued_jobs<=post_count):
        raise ContractError("invalid_benchmark_policy","Bounded synthetic fixture required")
    from intelligence import migrate
    migrate.apply_migrations(conn)
    token=uuid4().hex
    project_id=uuid4().hex
    source="gc29-"+token
    # Fixture is only constructed in a guarded disposable database; records
    # remain for operator's post-benchmark inspection until DB is dropped.
    conn.execute(
        """INSERT INTO project_registry(
             id,name,profile_revision,profile_hash,created_at,updated_at)
           VALUES(%s,'GC29 synthetic disposable',1,%s,now(),now())""",
        (project_id,"f"*64),
    )
    conn.execute("INSERT INTO sre_workspaces(project_id) VALUES(%s)",(project_id,))
    conn.execute(
        """INSERT INTO sources(id,provider,source_type,status,
             can_store_metadata,can_store_metrics,can_derive_scores,retention_days)
           VALUES(%s,'gc29-synthetic','manual','enabled',TRUE,TRUE,TRUE,30)""",
        (source,),
    )
    policy=_integer_count(conn.execute(
        """INSERT INTO source_policy_versions(
             source_id,version,is_active,permissions,retention_policy)
           VALUES(%s,1,TRUE,
             '{"can_store_metadata":true,"can_store_metrics":true,"can_derive_scores":true}'::jsonb,
             '{"retention_days":30}'::jsonb) RETURNING id""",
        (source,),
    ))
    creator=_integer_count(conn.execute(
        "INSERT INTO creators(platform,platform_creator_id) "
        "VALUES('youtube',%s) RETURNING id",
        (uuid4().hex,),
    ))
    prefix="gc29_"+token+"_"
    conn.execute(
        """INSERT INTO videos(
             platform,platform_video_id,canonical_url,content_kind,c_content_type,creator_id)
           SELECT 'youtube',%s||g::text,'https://youtube.com/watch?v='||%s||g::text,
                  'video','video',%s
             FROM generate_series(1,%s) g""",
        (prefix,prefix,creator,post_count),
    )
    conn.execute(
        """CREATE TEMP TABLE gc29_video_ids ON COMMIT PRESERVE ROWS AS
           SELECT id AS video_id FROM videos
           WHERE platform='youtube' AND creator_id=%s
             AND left(platform_video_id,length(%s))=%s""",
        (creator,prefix,prefix),
    )
    count=_integer_count(conn.execute("SELECT count(*) FROM gc29_video_ids"))
    if count!=post_count:
        raise ContractError("performance_fixture_mismatch",
                            "Synthetic project video count mismatch")
    conn.execute(
        """INSERT INTO sre_project_videos(project_id,video_id)
           SELECT %s,video_id FROM gc29_video_ids""",
        (project_id,),
    )
    conn.execute(
        """INSERT INTO source_video_metadata(
             video_id,source_id,source_policy_version_id,fetched_at,published_at)
           SELECT video_id,%s,%s,
                  clock_timestamp()-interval '1 hour',
                  clock_timestamp()-interval '5 days'
             FROM gc29_video_ids""",
        (source,policy),
    )
    conn.execute(
        """INSERT INTO metric_snapshots(
             video_id,source_id,source_policy_version_id,
             observed_at,collected_at,views,likes,comments,shares,
             source_metric_definition,source_metric_version,
             data_quality_status,precision,observation_time_assumed)
           SELECT v.video_id,%s,%s,
                  clock_timestamp()-interval '4 hours'
                      + (n.seq * interval '2 hours'),
                  clock_timestamp()-interval '3 hours'
                      + (n.seq * interval '2 hours'),
                  100+mod(v.video_id,10000)+n.seq*100,10,3,2,
                  'platform_post_views','v3','ok','exact',FALSE
             FROM gc29_video_ids v CROSS JOIN generate_series(0,1) AS n(seq)""",
        (source,policy),
    )
    observations=_integer_count(conn.execute(
        """SELECT count(*) FROM metric_snapshots s
           JOIN gc29_video_ids v ON v.video_id=s.video_id
           WHERE s.source_id=%s""",(source,),
    ))
    if observations!=post_count*2:
        raise ContractError("performance_fixture_mismatch",
                            "Two raw snapshots per synthetic publication required")
    # Populate actual original QDP inbox -> GC21 runs -> jobs lineage.
    conn.execute(
        """INSERT INTO group_c_inbox(
             event_id,project_id,video_id,qdp_run_id,analysis_group,
             producer_schema,input_sha256,payload_sha256,state,accepted_at)
           SELECT gen_random_uuid(),%s,v.video_id,gen_random_uuid(),'C',
                  'qdp-handoff/v1',
                  md5(v.video_id::text)||md5('in'||v.video_id::text),
                  md5('payload'||v.video_id::text)||md5('signature'||v.video_id::text),
                  'accepted',now()
             FROM gc29_video_ids v ORDER BY v.video_id LIMIT %s""",
        (project_id,queued_jobs),
    )
    conn.execute(
        """INSERT INTO group_c_runs(
             id,project_id,video_id,inbox_event_id,qdp_input_sha256,
             source_id,idempotency_sha256)
           SELECT gen_random_uuid(),i.project_id,i.video_id,i.event_id,
                  i.input_sha256,%s,
                  md5('run'||i.event_id::text)||md5('nonce'||i.event_id::text)
             FROM group_c_inbox i WHERE i.project_id=%s""",
        (source,project_id),
    )
    conn.execute(
        """INSERT INTO group_c_jobs(
             project_id,run_id,video_id,job_kind,idempotency_sha256)
           SELECT r.project_id,r.id,r.video_id,'score',r.idempotency_sha256
             FROM group_c_runs r WHERE r.project_id=%s""",
        (project_id,),
    )
    jobs=_integer_count(conn.execute(
        """SELECT count(*) FROM group_c_jobs
           WHERE project_id=%s AND state='queued'""",
        (project_id,),
    ))
    if jobs!=queued_jobs:
        raise ContractError("performance_fixture_mismatch",
                            "Queue fixture count mismatch")
    selected=conn.execute(
        "SELECT video_id FROM gc29_video_ids ORDER BY video_id LIMIT 1",
    ).fetchone()
    target_video=selected[0]
    snapshot=_integer_count(conn.execute(
        """SELECT id FROM metric_snapshots WHERE video_id=%s AND source_id=%s
           ORDER BY observed_at DESC,id DESC LIMIT 1""",
        (target_video,source),
    ))
    metadata=_integer_count(conn.execute(
        """SELECT id FROM source_video_metadata WHERE video_id=%s
           AND source_id=%s ORDER BY fetched_at DESC,id DESC LIMIT 1""",
        (target_video,source),
    ))
    for table in ("videos","sre_project_videos","metric_snapshots",
                  "source_video_metadata","group_c_runs","group_c_jobs",
                  "group_c_inbox"):
        # Allowed fixed SQL identifier list, never derived from CLI.
        conn.execute("ANALYZE "+table)
    return SyntheticPopulation(
        project_id=project_id,source_id=source,creator_id=creator,
        source_policy_version_id=policy,
        target_video_id=target_video,target_snapshot_id=snapshot,
        target_metadata_id=metadata,post_count=count,
        observation_count=observations,queued_job_count=jobs,
    )


def _plan_nodes(root:Mapping[str,Any]):
    yield root
    for item in root.get("Plans",()):
        if isinstance(item,Mapping):
            yield from _plan_nodes(item)


def parse_plan(label:str,first:Mapping[str,Any],
               warm:list[Mapping[str,Any]])->PlanMeasure:
    """Redact original SQL/source IDs: only aggregate plan counters escape."""
    if label not in SQL or not isinstance(first,Mapping) or not warm:
        raise ContractError("invalid_benchmark_report","Known measured SQL plans required")
    for obj in [first,*warm]:
        if not isinstance(obj,Mapping) or not isinstance(obj.get("Plan"),Mapping):
            raise ContractError("invalid_benchmark_report","PostgreSQL JSON plan required")
        for key in ("Planning Time","Execution Time"):
            val=obj.get(key)
            if type(val) not in (int,float) or val<0:
                raise ContractError("invalid_benchmark_report","Measured timings required")
    nodes=list(_plan_nodes(first["Plan"]))
    types=tuple(str(n.get("Node Type","unknown")) for n in nodes)
    indexes=tuple(sorted({n["Index Name"] for n in nodes
                          if type(n.get("Index Name")) is str}))
    return PlanMeasure(
        name=label,
        first_pass_ms=float(first["Execution Time"]),
        median_repeated_ms=statistics.median(
            float(p["Execution Time"]) for p in warm),
        min_repeated_ms=min(float(p["Execution Time"]) for p in warm),
        max_repeated_ms=max(float(p["Execution Time"]) for p in warm),
        first_planning_ms=float(first["Planning Time"]),
        # PostgreSQL parent node buffers include descendant work; summing
        # node counters double-counts the same I/O. Record ROOT only.
        total_shared_hit_blocks=int(first["Plan"].get("Shared Hit Blocks",0)),
        total_shared_read_blocks=int(first["Plan"].get("Shared Read Blocks",0)),
        root_actual_rows=float(first["Plan"].get("Actual Rows",0)),
        plan_node_types=types,
        chosen_indexes=indexes,
        seq_scan_nodes=sum(t=="Seq Scan" for t in types),
        os_cache_cold_verified=False,
    )


def _explain(conn:Any,name:str,params:tuple)->dict:
    if name not in SQL:
        raise ContractError("invalid_benchmark_query","Unreviewed SQL not allowed")
    # EXPLAIN ANALYZE executes an actual SELECT, not a synthetic estimated plan.
    cur=conn.execute(
        "EXPLAIN (ANALYZE TRUE, BUFFERS TRUE, FORMAT JSON, TIMING TRUE) "
        +SQL[name],params,
    )
    value=cur.fetchone()[0]
    if type(value) is str:
        value=json.loads(value)
    if not isinstance(value,list) or len(value)!=1 or not isinstance(value[0],dict):
        raise ContractError("invalid_benchmark_report","PostgreSQL JSON EXPLAIN required")
    return value[0]


def _cohort_rules(pop:SyntheticPopulation)->CohortRules:
    cutoff=datetime.now(timezone.utc)-timedelta(minutes=1)
    return CohortRules(
        project_id=pop.project_id,target_video_id=pop.target_video_id,
        purpose="creator",creator_id=pop.creator_id,topic_id=None,
        platform="youtube",content_type="video",
        source_id=pop.source_id,source_policy_version_id=pop.source_policy_version_id,
        metric_definition="platform_post_views",metric_version="v3",
        evaluated_as_of=cutoff,
        published_from=cutoff-timedelta(days=9),
        published_before=cutoff-timedelta(days=1),
        min_age_hours=Decimal("0"),max_age_hours=Decimal("168"),
        max_candidates=500,
    )


def profile(conn:Any,pop:SyntheticPopulation,*,warm_repeats:int=5)->BenchmarkReport:
    _require_disposable(conn)
    if (not isinstance(pop,SyntheticPopulation) or type(warm_repeats) is not int
            or not 1<=warm_repeats<=30):
        raise ContractError("invalid_benchmark_policy","Synthetic population/repeat count required")
    # Refuse to label a report "10k" from an invented dataclass or an
    # interrupted/deleted seed. Count actual physical project-scoped SQL rows.
    physical=conn.execute(
        """SELECT count(*) FROM sre_project_videos pv
           JOIN videos v ON v.id=pv.video_id
           WHERE pv.project_id=%s AND v.creator_id=%s""",
        (pop.project_id,pop.creator_id),
    ).fetchone()[0]
    raw=conn.execute(
        """SELECT count(*) FROM metric_snapshots m
           JOIN sre_project_videos pv ON pv.video_id=m.video_id
           WHERE pv.project_id=%s AND m.source_id=%s
             AND m.source_policy_version_id=%s
             AND m.source_metric_definition='platform_post_views'
             AND m.source_metric_version='v3'""",
        (pop.project_id,pop.source_id,pop.source_policy_version_id),
    ).fetchone()[0]
    pending=conn.execute(
        """SELECT count(*) FROM group_c_jobs
           WHERE project_id=%s AND state='queued' AND job_kind='score'""",
        (pop.project_id,),
    ).fetchone()[0]
    if (physical!=pop.post_count or raw!=pop.observation_count
            or raw!=2*physical or pending!=pop.queued_job_count):
        raise ContractError("performance_fixture_mismatch",
                            "Measured report requires actual 2-per-post raw SQL corpus and queue")
    cutoff=datetime.now(timezone.utc)-timedelta(minutes=1)
    inputs={
        "cohort_candidates":(
            pop.project_id,pop.target_video_id,pop.source_id,cutoff,
            pop.source_id,501),
        "latest_project_observation":(21600,pop.project_id),
        "scoped_ready_queue":(pop.project_id,),
        "latest_metadata":(pop.target_video_id,pop.source_id),
    }
    measures=[]
    for name,params in inputs.items():
        first=_explain(conn,name,params)
        warm=[_explain(conn,name,params) for _ in range(warm_repeats)]
        measures.append(parse_plan(name,first,warm))

    # The real Group C algorithm MUST reject a 10k+ peer population rather
    # than silently applying its 500 max_candidates to a subset. A 64-row
    # smoke fixture cannot make that claim.
    overflow=False
    if pop.post_count>=MIN_POSTS:
        try:
            PostgreSQLCohortCandidates().select(
                conn,rules=_cohort_rules(pop),
                target_snapshot_id=pop.target_snapshot_id,
                target_metadata_id=pop.target_metadata_id,
            )
        except ContractError as exc:
            if exc.code!="cohort_population_overflow":
                raise
            overflow=True
        if not overflow:
            raise ContractError("performance_overflow_missing",
                                "10k cohort must fail closed, not truncate")
    # Exercise real GC21 claim+advisory lock+project budget. No C7 run_once
    # (would require signed human approval), no score computation.
    worker=PostgreSQLGroupCWorker(WorkerPolicy(
        max_running_per_project=2,max_running_global=16,
        lease_seconds=180,
    ))
    claim_ms=[]
    granted=denied=0
    for k in range(4):
        start=perf_counter_ns()
        lease=worker.claim(conn,project_id=pop.project_id,worker_id=f"gc29-worker-{k}")
        claim_ms.append((perf_counter_ns()-start)/1e6)
        granted+=int(lease is not None)
        denied+=int(lease is None)
    current=conn.execute(
        """SELECT count(*) FILTER(WHERE state='queued'),
                  count(*) FILTER(WHERE state='running')
           FROM group_c_jobs WHERE project_id=%s""",
        (pop.project_id,),
    ).fetchone()
    running=int(current[1]); queued=int(current[0])
    if (granted!=2 or denied!=2 or running!=2
            or queued!=pop.queued_job_count-2):
        raise ContractError("performance_backpressure_mismatch",
                            "GC21 project concurrency budget must hold")
    index_rows=conn.execute(
        """SELECT indexname FROM pg_indexes
           WHERE schemaname='public' AND indexname=ANY(%s)""",
        (list(EXPECTED_INDEXES),),
    ).fetchall()
    installed=tuple(sorted(r[0] for r in index_rows))
    if installed!=tuple(sorted(EXPECTED_INDEXES)):
        raise ContractError("performance_index_missing","Required GC29 index missing")
    return BenchmarkReport(
        schema_version=BENCHMARK_VERSION,
        produced_at=datetime.now(timezone.utc).isoformat(),
        dataset_posts=pop.post_count,
        dataset_observations=pop.observation_count,
        dataset_queued_jobs=pop.queued_job_count,
        qualifies_10k=pop.post_count>=MIN_POSTS and overflow,
        database_kind="explicitly_opted_in_disposable_pg17",
        pg_major=17,cohort_overflow_confirmed=overflow,
        query_measures=tuple(measures),
        backpressure=BackpressureMeasure(
            initial_queued=pop.queued_job_count,
            claims_granted=granted,claims_denied_by_budget=denied,
            final_running=running,final_queued=queued,
            claim_timings_ms=tuple(claim_ms),
            max_running_per_project=2,
        ),
        installed_indexes=installed,
        caveats=(
            "First pass is a first-sample proxy; OS/database cold cache was NOT established.",
            "Repeated EXPLAIN ANALYZE timings reflect this synthetic dataset only.",
            "Query sample throughput is NOT independently established; no production SLO or QPS claim.",
            "Cohort cap 500 deliberately rejects 10k candidates instead of silently truncating.",
            "Queue pressure exercises claim/advisory lock/budget, not signed C7 execution.",
            "Read plans are from exact structural SQL shapes; optional host-owned collector needed.",
            "The disposable database and test fixtures must be dropped by its local owner.",
        ),
    )


def _json_report(report:BenchmarkReport)->str:
    if not isinstance(report,BenchmarkReport):
        raise ContractError("invalid_benchmark_report","Typed measured report required")
    return json.dumps(asdict(report),indent=2,ensure_ascii=False,
                      sort_keys=True,allow_nan=False)+"\n"


def main()->None:
    ap=argparse.ArgumentParser(description="Group C opt-in disposable PG17 load QA")
    m=ap.add_mutually_exclusive_group(required=True)
    m.add_argument("--benchmark",action="store_true",
                   help="seed actual >=10,000 source posts and benchmark")
    m.add_argument("--smoke",action="store_true",
                   help="seed only 64 posts; not a 10k performance claim")
    ap.add_argument("--post-count",type=int,default=MIN_POSTS)
    ap.add_argument("--queue-count",type=int,default=1000)
    ap.add_argument("--warm-repeats",type=int,default=5)
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    if args.benchmark:
        policy=BenchmarkPolicy(args.post_count,args.queue_count,args.warm_repeats)
        count,queue,repeats=policy.post_count,policy.queued_jobs,policy.warm_repeats
    else:
        if args.post_count!=MIN_POSTS:
            ap.error("--smoke cannot be combined with --post-count")
        if not 1<=args.warm_repeats<=30:
            ap.error("warm repeat count out of bounds")
        if not 2<=args.queue_count<=MAX_POSTS:
            ap.error("--queue-count must be 2..100000")
        count,queue,repeats=SMOKE_POSTS,min(args.queue_count,SMOKE_POSTS),args.warm_repeats
    dsn=os.getenv("CM_TEST_PG_DSN")
    if not dsn or os.getenv("GC_TEST_DISPOSABLE_DB")!="YES" \
            or os.getenv("GC_PERF_ALLOW_WRITE")!="YES":
        ap.error("CM_TEST_PG_DSN + GC_TEST_DISPOSABLE_DB=YES + GC_PERF_ALLOW_WRITE=YES required")
    import psycopg
    with psycopg.connect(dsn,autocommit=True,connect_timeout=5) as conn:
        _require_disposable(conn)
        pop=seed(conn,post_count=count,queued_jobs=queue)
        report=profile(conn,pop,warm_repeats=repeats)
    # The only user-visible report of timing values is an actual measured file.
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(_json_report(report),encoding="utf-8")
    print("Measured synthetic disposable PG17:",args.output,
          "posts=",report.dataset_posts,
          "qualifies_10k=",report.qualifies_10k,
          "OS_cold_verified=",False)


if __name__=="__main__":
    main()
