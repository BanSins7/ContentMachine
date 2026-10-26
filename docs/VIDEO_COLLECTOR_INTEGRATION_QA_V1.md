# VC-MOD-15 — reproducible Collector QA matrix and real PostgreSQL17 integration

**Module**: `feature/video-collector-v1` → draft PR #33. **Code/tests implemented. Exact-head CI and genuine PostgreSQL17 execution NOT VERIFIED YET.** A committed test script and a queued Action do **not** mean PASS.

## What this stage actually tests

A new `tests/test_video_collector_pg15_integration.py` uses `CM_TEST_PG_DSN` **only when explicitly configured**. It opens one **fresh, randomly named, disposable PostgreSQL schema per test**, creates minimal parent-side D1/QV reference tables and executes the **real unmodified** Collector migrations:

```text
054_collector_planning.sql
055_collector_worker.sql
056_collector_qv_manifest.sql
057_collector_discovery_outbox.sql
```

These fixtures are deliberately marked **parent-contract stand-ins**, not real C9/SP/QV inference. Full existing repo migrations and old-main→Collector additive upgrades are separately checked by `tests/test_video_collector_schema_v1.py` and `intelligence.migrate` in CI.

The actual runtime components exercised on genuine PG SQL are:

```text
Pinned offline DTK v5.1.7 Page[Content] fixture
   → Response Inspector (no network)
   → protected released Collector task / valid DB lease and policy
   → ingest_page() production SQL
   → canonical D1 videos / source_video_metadata / discoveries
   → immutable collector_ingest_pages checkpoint
   → collector_ingest_page_videos + collector_page_video_queries
   → collector_video_hypotheses
   → pending collector_sre_outbox (not_evaluated)
   → finish_from_checkpoint() committed ACK in separate SQL TX
   → collector_provider_tasks completed / jobs completed
   → search_runs + collector_runs finalization
```

### Seven real-Postgres tests added (not yet executed in a confirmed CI run)

1. **Atomic D1 + terminal receipt**: real canonical video/discovery/metadata and QV links; exactly one page and SRE event; 14-day reviewed retention on all three relevant tables; no SRE classification claim; terminal state changes only after true D1.
2. **Replay/conflict**: exactly one immutable checkpoint for identical SHA; altered provider page/hash yields 409 rather than another video or SRE event.
3. **Immutable and tenant composite FK**: PostgreSQL rejects UPDATE of receipt and mismatched project/page association.
4. **Expired observation**: legacy/old provider response beyond the reviewed retention deadline is blocked before any video/receipt/outbox write.
5. **Lease / worker ownership**: wrong Worker or stale generation is denied before D1 persistence.
6. **Two concurrent SRE dispatchers**: PostgreSQL `SKIP LOCKED`/row state allows exactly one project-scoped claim, one generation increment and one attempt count, with no SRE inbox fabricated.
7. **Crash after D1 COMMIT before ACK**: `finish_from_checkpoint(ctx,receipt=None)` recovers from real committed checkpoint without a second provider call, video write or SRE event. No Mock DTK POST.

Each test uses a fresh schema and drops it after completion. No production tables or provider API are touched. Its dynamic UTC observation avoids hard-coded test dates expiring after 14 days.

### QV 0/1/20/21/480 selected candidates

The existing `test_video_collector_qv_publisher_v1.py` suite verifies these **offline** sizes, no-queries, no truncation, deterministic ordering, 20/50 shard sizes, replay and post-commit ACK. `test_video_collector_qv_reader_v1.py` tests trusted DB-read provenance with controlled DB stubs. These are included in the release gate and must PASS on the exact commit before any production claim.

**Not yet proved**: full **real** upstream RTB C9→SP→SQG→semantic QV→480 query publisher→24 Collector shards in a single PostgreSQL fixture, or actual provider delivery. The tests exercise **real PG Collector migrations and downstream D1** plus separately tested upstream QV planner contracts. Treat as **partial integration coverage** rather than full external end-to-end success.

## Reproducible gate (no live providers)

New `scripts/collector_release_gate.py` runs the VC suites from the current checkout, and returns structured JSON without stdout/stderr from failed tests (prevents accidental DSN/token traceback leakage).

```sh
# Offline Linux/Windows, tests and JS syntax only (real PG explicitly skipped)
python -m scripts.collector_release_gate --unit-only

# Strict CI/test-only PostgreSQL17, never a production DSN:
CM_TEST_PG_DSN=postgresql://<TEST_USER>:<TEST_SECRET>@127.0.0.1:5432/<TEST_DB> \
  python -m scripts.collector_release_gate --strict-ci
```

The gate groups tests by `ports_qv`, `planner_owner`, `worker_dtk`, `d1_completion`, `outbox_recovery`, `migration_contracts`. Strict mode requires **PostgreSQL major version 17**, the real-PG tests, unit suites and `node --check` on site scripts. Any failed/timeout/unavailable process returns `passed=false` and exit 2. PG is **never silently passed** when skipped. Offline `--unit-only` can pass while explicitly stating PG not tested and `complete_pipeline_e2e_verified=false`.

Seven new `tests/test_video_collector_release_gate_v1.py` tests prove fake success cannot bypass missing/wrong PG17, failed suites block the gate, live flags stay FALSE, and the suite list remains explicit. The test itself does NOT constitute a live/PG test.

### Test mapping to the original 100 cases

| Original case range | Current dedicated suites / evidence | Verified status |
| --- | --- | --- |
| T01–T28 | QV reader/publisher/ports, `0,1,20,21,480` selected | Code/tests present; exact-head Linux CI pending |
| T29–T42 | registry, manifest planner, strict UI/preview | Present; CI pending |
| T43–T46 | `schema_v1`, `schema_audit` fresh + old-main migrations, PG17 FK/immutability | Present; real CI pending |
| T47–T60 | owner commit, quota reservation, cancellation and lease-fenced Worker | Present; CI pending |
| T61–T69 | provider contract Inspector/Normalizer, nonvideo/private/URL, null metrics | Present; CI pending |
| T70–T81 | actual D1 ingest/replay, ACK, retention and pending SRE; **seven new real PG tests** | Present; PG17 run pending |
| T82–T84 | SRE-ready single outbox, opt-in ACK/lease/retry | Present; production SRE M35 inbox unavailable |
| T85–T95 | real D1 results/trace/UI, tenant scope, operator recovery/diagnostics | Present; CI pending |
| T96–T99 | disposable PG17, concurrency claim/duplicate/cross-project; upstream C9/QV full-run scenario still open | Partial fixture implemented; PG17 run pending |
| T100 | final no-conflict one-PR merge/release gate VC-MOD-16 | Not started |

This is **group-to-suite traceability**, not an attestation that **100 independently executed and passed test cases** exist. Offline suites reuse fixtures and are not replacements for upstream production integration.

### Current gate status and required evidence

- No exact-head GitHub Linux/Windows Actions completion was observed when code was written. Previous workflow cancellations are **not** test failures or passes.
- No access to a running disposable PG17 was available for executing these test files in this editing session. The local CI script, even though committed, is **not** verified as passing at head until Actions execute and their logs are inspected.
- Production DTK zero-billing rights, TikTok network, real SRE authenticated M35 receiver and end-to-end QDP classification are **not** included. SRE remains **not_evaluated**, and no provider requests were made.
- After CI returns PASS, record exact `head SHA`, workflow run URL, timestamp, PG17 version, suite count and verified `passed=true` report. If it FAILS, fix implementation/fixtures and leave QA unchecked.

**VC-MOD-15 implementation status: IMPLEMENTED / QA PENDING.** Do not mark the module production-ready or merge PR #33 on unverified tests. Next VC-MOD-16 must verify all release gates, resolve latest main changes and explicitly document any residual SRE M35 dependency.
