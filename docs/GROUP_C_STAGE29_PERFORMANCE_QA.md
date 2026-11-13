# GC-MOD-29 — Load / Performance QA: disposable PG17 10k+ benchmark

**Branch:** \`feature/group-c-full-module\`. **Status:** benchmark harness, exact-shape query plans, index migration, concurrency/backpressure checks, safety guards and documentation **CODE IMPLEMENTED**. **Real PostgreSQL 17 execution, 10k timings, throughput, OS-cold measurements and CI results are NOT VERIFIED**. Full performance acceptance is blocked until the user runs the benchmark on their disposable local PostgreSQL17 instance. Do NOT claim production QPS, latency, memory, indexes chosen by the planner or speedup before examining measured reports.

## Files and actual workload

- \`intelligence/migrations/064_group_c_performance_indexes.sql\` — additive performance indexes, after all previously applied 003/004/056..063. Indexes:
  - \`idx_gc29_raw_source_video_latest\`: source ID + video ID + newest raw observed_at/id. C9 cohort selection explicitly picks **latest raw** before quality/version policy checks, preventing silent fallback to last-good metric.
  - \`idx_gc29_observation_latest\`: latest \`platform_post_views\` per video for GC28 freshness pulse.
  - \`idx_gc29_metadata_source_latest\`: exact video/source latest \`fetched_at,id\` for GC09 cohort metadata LATERAL.
  - \`idx_gc29_worker_project_ready\`: project-scoped score-job queue \`available_at,id\` partial predicate \`state='queued' AND job_kind='score'\`.
  - \`idx_gc29_worker_project_lease\`: project-scoped expired running worker lease by \`lease_until,id\`.
  - \`idx_gc29_outbox_project_ready\`: project-scoped delivery backlog \`created_at,id\` partial predicate pending/failed. **No automatic outbox delivery/replay**.
- \`intelligence/group_c/performance.py\` — *opt-in only* synthetic SQL data seed and measured runner. A 10,000-post run inserts 10,000 real videos + project membership + source metadata, **20,000 raw D1 observations** from one exact source/policy/version, plus default **1,000 accepted QDP inbox events, runs and queued GC21 jobs**, with FK lineage and no synthetic C7, no fake human approval. Imports the existing versioned PostgreSQL migration runner, checks all 6 new indexes and analyzes tables. It profiles actual bound SQL shapes matching:
  - GC09 full \`DISTINCT ON(video_id)\` latest raw candidate set + source scope + metadata \`LATERAL\`, capped at 501 to prove max_candidates=500 detects population overflow instead of truncating silently;
  - GC28 \`sre_project_videos\` → newest observation LATERAL;
  - GC21 project-scoped score job candidate scan + terminal/lease filter;
  - source+post latest publication metadata.
- Each SQL probe uses PostgreSQL \`EXPLAIN (ANALYZE TRUE, BUFFERS TRUE, FORMAT JSON, TIMING TRUE)\`. It records first-pass real execution/planning time, median/min/max of 1..30 repeated plans, root shared-buffer hits/reads, plan node types, planner-chosen indexes and count of Seq Scan nodes. It never forces a particular index choice, assumes all new indexes improve performance, or adds user data to the report. **First-pass ≠ true OS-cold**; seed/ANALYZE, shared buffers, OS cache and concurrency can affect every sample. True cold-cache measurement requires a separate operator-owned isolated PG restart / cache discipline, never an automatic DROP CACHE on a user machine.
- Backpressure exercises **real GC21 \`PostgreSQLGroupCWorker.claim\`**, global advisory claim lock + project concurrency limit=2; 4 claim calls against pending 1,000 job fixture must yield exactly 2 leases and 2 denied budget attempts. No fake job throughput; no calls to \`run_once\` or score production C7.
- Only a confirmed \`post_count>=10000\` + exact physical rows + real **\`cohort_population_overflow\`** can set \`qualifies_10k=true\` in the measured report. 64-post smoke always sets \`false\`.

## Safety contract — disposable PG17 ONLY

Every seed and profile fails closed unless ALL conditions hold:
- \`CM_TEST_PG_DSN\` points to an independently disposable PG17 database;
- DB's \`current_database()\` begins with **\`contentmachine_gc_\`** or **\`gc_perf_\`**;
- connection is autocommit and server major version exactly **17**;
- local operator explicitly sets \`GC_TEST_DISPOSABLE_DB=YES\` AND \`GC_PERF_ALLOW_WRITE=YES\`;
- all counts are canonical integer bounds: full \`post_count=10000..100000\`, queued jobs 2..post_count, warm repetitions 1..30. \`--smoke\` is a separate clearly marked 64-post mode.

This still relies on the operator to confirm the database is truly disposable; a convention and environment variable cannot prove production safety. **Never** point the runner at real Content Machine, production replicas, credentials, managed shared databases or user's historical research assets. The benchmark performs real INSERTs and ANALYZE and deliberately leaves test fixtures behind until the disposable database owner drops the **entire separate test database**. It never deletes immutable raw facts or authorizes legal erasure. No actual production video facts, provider APIs, LLM calls or signed human approvals involved.

### Run locally (Linux/macOS bash)

Use an empty disposable PostgreSQL 17 database with a name such as \`contentmachine_gc_performance\`:

\`\`\`bash
export CM_TEST_PG_DSN='postgresql://<test-user>:<test-password>@127.0.0.1:5432/contentmachine_gc_performance'
export GC_TEST_DISPOSABLE_DB=YES
export GC_PERF_ALLOW_WRITE=YES

# Safe 64-post SQL smoke (NOT a 10k performance claim).
python -m intelligence.group_c.performance --smoke \
  --output ./gc29-smoke-measured.json

# Real 10k synthetic posts, 20k D1 snapshots, 1k queued GC21 jobs.
python -m intelligence.group_c.performance --benchmark \
  --post-count 10000 --queue-count 1000 --warm-repeats 5 \
  --output ./gc29-10000-measured.json
\`\`\`

### Windows PowerShell (same opt-in safety)

\`\`\`powershell
$env:CM_TEST_PG_DSN = 'postgresql://<test-user>:<test-password>@127.0.0.1:5432/contentmachine_gc_performance'
$env:GC_TEST_DISPOSABLE_DB = 'YES'
$env:GC_PERF_ALLOW_WRITE = 'YES'

python -m intelligence.group_c.performance --smoke --output .\gc29-smoke-measured.json
python -m intelligence.group_c.performance --benchmark --post-count 10000 --queue-count 1000 --warm-repeats 5 --output .\gc29-10000-measured.json
\`\`\`

Requires Python 3.11+ and \`psycopg[binary]>=3.2,<4\`; no other packages needed for the benchmark beyond Content Machine itself. To run the small PG17 unit smoke, additionally set \`GC_PERF_RUN_SMALL=YES\`. The 10k unittest **only** runs when \`GC_PERF_RUN_10K=YES\`, in addition to the above guards. It is not enabled in normal or default CI. Do not run both modes against the same already populated test DB if the goal is reproducible controlled first-run/cache comparisons: create a fresh disposable DB for each run and record PG config, CPU/RAM, storage, OS, and test timestamp separately.

## Acceptance matrix

| Area | What to verify on a real disposable PG17 | False claim to avoid |
|---|---|---|
| 10k+ corpus | Report \`dataset_posts >=10000\`, \`dataset_observations=2*posts\`, 1000 queued jobs, \`qualifies_10k=true\` | Python-only mock "benchmark" |
| Frozen cohort population | Actual Group C \`PostgreSQLCohortCandidates.select\` raises \`cohort_population_overflow\` with >500 raw candidate population | Silently comparing just 500 of 10,000 |
| Cohort/metadata/index plans | SQL \`EXPLAIN ANALYZE BUFFERS\`, plan nodes, chosen index names, first/warm timing and root buffer reads/hits | Assuming created index was selected or improved latency |
| Source freshness and project scope | 10k project videos and 20k metrics, no mixing other projects/sources | Cross-project scan assumed safe |
| Worker claim backpressure | Actual two GC21 worker claims then two refusals with budget=2; optional separate-thread PG17 test for claim race | Faking C7 completion or throughput |
| OS cold/warm | **First-pass proxy** plus repeated warm samples; \`os_cache_cold_verified=false\` until externally controlled cold-cache evidence | Calling first query genuinely cold |
| Integration readiness | Synthetic dataset and real PG explain; production IAM/QDP/source rights/Analytics E2E still independent | Production QPS/SLO from disposable fixture |

## QA status and CI

\`tests/test_group_c_performance.py\` includes pure policy/SQL/plan/redaction/DB safety guard tests, a gated 64-post real SQL smoke and a separately gated 10k real PostgreSQL17 test. \`.github/workflows/group-c-module.yml\` adds migration 064 path and **64-post** disposable PG17 fresh/upgrade smoke (no 10k job automatically scheduled, per owner's CI preference). All tests and benchmark runs are **authored, not executed by this stage**; exact code and GitHub file presence verification cannot establish runtime performance. Without actual JSON measurements, **performance evaluation remains QA_NOT_VERIFIED**. A measured report should be stored as a local artifact, not committed with DB passwords, connection URLs, or unredacted operational secrets.

See \`docs/GROUP_C_IMPLEMENTATION_TRACKER.md\`; **next GC-MOD-30 — independent Decimal math gold vectors C0–C7**.
