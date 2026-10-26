# QDP-FIX-17 — Observability, Scaling and Worker Lifecycle

**Code status:** CODE_IMPLEMENTED, QDP-owned, on `feature/qualified-data-pipeline-v1-20261023`.
**Verified QA:** [GitHub Actions 40384940760](https://github.com/BanSins7/ContentMachine/actions/runs/40384940760) on exact code SHA `e76c859f2e048e94a7372c0da1c62090de05124a`: **257/257 PASS** (0 failures/errors/skips), PostgreSQL 17/Python/JS PASS, real pinned Group A/C intake CI PASS. Later UI code commit `bddaef54` was verified by [40385308081](https://github.com/BanSins7/ContentMachine/actions/runs/40385308081): **258/258 PASS** with real pinned Group A/C peer tests also PASS.

## Operational contract

`observability.snapshot(project,run_id=None,limit=20,connect=None)` and `GET /api/projects/:pid/qualified-data/observability?run_id=<optional>&limit=1..50` produce a **read-only repeatable-read PostgreSQL snapshot**. All queries filter `project_id`, an optional canonical QDP run UUID, and only QDP `jobs.job_type='qdp.prepare_video'`. The oldest unresolved delivery list is capped at 50 and includes event ID/type/group/attempt count without raw payload, secrets, original metadata or video bytes.

The response separates **six concepts**:

- `runs.qualified_snapshots` is the historical sum over QDP runs; repeated runs can count the same canonical video multiple times. Never present it as distinct project video count or external LIVE throughput.
- `items_by_historical_status` is the QDP item state at the time of the run; it does not replace current source policy checks.
- `readiness_by_group.historical_ready` vs `currently_ready` uses authoritative `qdp_group_current_preparations` to react to human veto, SRE/source rights, media/metric expiry and operator invalidation without rewriting old snapshots.
- `delivery_by_state` captures `pending/leased/acknowledged/dead_letter`, overdue lease and due-now backlog and oldest unresolved timestamp. `acknowledged` means a real consumer committed its **inbox and initial job**, never that video analysis completed.
- `worker_jobs_by_state` counts only QDP preparation jobs and due/expired state. No Discovery/Collector/Group jobs are counted.
- `revocations` is committed QDP invalidation facts. `analysis_completed=null`, `analysis_completed_status='unavailable_without_verified_peer_results'` must not silently become a completed analytics number.

This is a project-local diagnostics API under the existing loopback Host/Origin/CSRF controls. It is read-only and makes **zero paid/provider calls**. `site/qualified-data.js` renders it through `textContent` only.

## Indexes / forward-only SQL

Additive `intelligence/migrations/048_qdp_operations_indexes.sql` supports project/run/video keyset, project run event pages, next consumer due event, historical ready group access, and invalidation audit. It does not edit checksum-locked QDP 016/040..047, SRE, Group or Collector schemas.

## Bounded lifecycle and backpressure

`purge_revoked(connect=None,limit=100)` now has explicit `limit=1..1000` and transactional `FOR UPDATE OF e SKIP LOCKED`. Each worker maintenance sweep purges at most 100 inaccessible QDP snapshots and the next sweep continues cleanup. This prevents materializing arbitrarily large expired-source datasets into a single Python list. It does **not** guarantee a particular retention deadline under indefinite worker downtime; deployments must schedule and monitor cleanup.

`stop_worker(timeout_seconds=5)` raises on invalid timeouts, signals the existing local stop event, bounds join time to 30 seconds, and reports `still_running` rather than claiming success on an unfinished transaction. `worker_status()` returns local thread alive/stop requested and configured database flags, without credentials. It does **not** start the worker and does not claim supervisor-managed process restart.

Suggested operator checks: nonzero `expired_leases` when worker is expected online, growing `dead_letter`, `pending` revocations without peer `revoke()`, stale QDP worker jobs, and divergence of historical/current readiness. There are **no invented alert thresholds or achieved SLOs**.

## Actual synthetic 10,000-row PostgreSQL 17 benchmark

**Verified run:** [40384940760](https://github.com/BanSins7/ContentMachine/actions/runs/40384940760); evidence available as `qdp-benchmark.json` inside job `qdp-qa-40384940760` artifact, plus server log `QDP_BENCH_REPORT`.

`scripts/qdp_benchmark_pg.py --report qdp-benchmark.json` requires `CM_TEST_PG_DSN`, guard `QDP_DISPOSABLE_TEST_DB=YES`, and server version PostgreSQL 17. It generates **ephemeral TEMP synthetic proxy tables**, not real SRE/D1 video metadata, with **10,000 QDP-like item rows** and **30,000 QDP-like consumer delivery rows**, indexed according to the same keyset/consumer claim patterns. Actual `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` captured:

| Measured query | Rows in proxy | Observed execution ms | Plan nodes |
| --- | ---: | ---: | --- |
| Item keyset, last 50 | 10,000 | **0.109 ms** | Limit → Index Scan |
| First due group_a delivery | 30,000 | **0.084 ms** | Limit → Index Only Scan |
| Group readiness status counts | 10,000 | **2.724 ms** | Seq Scan → Aggregate → Sort |
| Delivery state/consumer counts | 30,000 | **7.932 ms** | Seq Scan → Aggregate → Sort |

These are **one CI sample**, a measurement of SQL execution inside PostgreSQL, excluding the full HTTP request, data ingestion, SRE classification, QDP enrichment/worker, media rights and all Group A/B/C analysis. **Do not extrapolate** to average/p95, 10k videos per month, production throughput, latency SLO or real full pipeline. `full_qualified_pipeline_10k_status='NOT_MEASURED'`, `throughput_videos_per_second=null` and `slo_claim=null` in the JSON deliberately enforce that separation.

## Validation and remaining release gates

`tests/test_qdp_observability.py` checks bounds, bad origins, no paid/completed claims, GET/POST scoping, stop timeout and benchmark guard. PG17 `tests/test_qualified_data_db.py` checks exact current vs historical counters after source revocation, project scoping and retention batch correctness. `tests/test_qdp_schema_migrations.py` verifies migration 048 is additive and checksum-checked. CI runs separate pinned real Group A and Group C durable inbox regressions.

Further FIX-18/19 work: full 10k real pipeline capacity/retention tests, P95/P99 with repeated independent samples, actual multi-branch integration with Group B contract update, UAE verified output, Group A/C `revoke()`, SRE LIVE in production, long-running supervisor restarts and manual browser UX acceptance.


## Follow-up verified exact UI code CI and second measured sample

[GitHub Actions 40385308081](https://github.com/BanSins7/ContentMachine/actions/runs/40385308081) on QDP code `bddaef54615f8057cab2de486e3747eba4c69ebd` passed **258/258 PG17/Python/JS**, 0 failures/errors/skips and separate real GA/GC acceptance jobs PASS. The second independent synthetic 10k/30k EXPLAIN sample measured **0.072 ms** 50-row keyset, **0.091 ms** 1-row due delivery, **2.678 ms** grouped readiness, **7.869 ms** grouped delivery. Individual-run measurements only, not SLO or 10k real video throughput. `qdp-benchmark.json` is attached to that workflow's QA artifact.
