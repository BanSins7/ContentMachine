# QDP FIX-11 — Worker hardening handoff

**CODE_IMPLEMENTED / QA_PASS:** [GitHub Actions 40316720077](https://github.com/BanSins7/ContentMachine/actions/runs/40316720077), exact code SHA `3b623e1b690fc5fc9a5b989ffa467b57fd9e2afb`, 180/180 tests including disposable PostgreSQL 17; 0 errors/failures/skips. Python and JavaScript syntax checks and migrations passed.

## Work ownership

`work_once` claims only `jobs.job_type='qdp.prepare_video'` with `FOR UPDATE SKIP LOCKED` and bounded retries. Claim increments `lease_generation` with 120-second lease. Worker holds its own lease row lock through preparation; final CAS `UPDATE jobs ... RETURNING id` includes job type, owner, generation and non-expired lease. Lost lease rolls back all writes.

`retry_delay_seconds(attempts)`: 30, 60, 120, 240, ... up to 1800 seconds. Exceptions persisted by class name only, no secrets/tracebacks stored.

`reap_exhausted` handles only expired or terminal exhausted QDP leases. Changes stuck `qdp_items.queued` to `blocked/worker_attempts_exhausted`, appends audit event, and recalculates run states: `failed` when all exhausted; `partial` if successful items coexist with failed jobs; `cancelled` remains independent.

## One committed video = one immutable snapshot

The same fenced PostgreSQL transaction commits QDP enrichment and exactly three A/B/C preparation facts, independent ready/blocked statuses, `qdp_events` and `qdp_outbox` facts. Ready groups also get initial pending `qdp_outbox_deliveries` rows. Crash before COMMIT rolls back all video facts. Crash after COMMIT cannot replay a completed job. Ten concurrent workers were tested against one job.

Durable events: `qualified_set_created`, per-group `preparation_ready` or `preparation_blocked`, and terminal `run_completed`. No consumer job runs here, no remote/provider/paid calls.

## Clear FIX-12 boundary

Only the outbox event and initial pending delivery row are present. FIX-12 must implement per-consumer claims, lease/retry/dead letter handling, delivery idempotency, inbox receipt and ACK after its own transaction. Pending is not accepted; acknowledged is not analyzed. Groups must independently recheck current source/media/metric rights before acting.

## QA record

Unit `python -m unittest tests.test_qdp_worker_hardening -v`. Disposable PG17 full suite: `QDP_DISPOSABLE_TEST_DB=YES CM_TEST_PG_DSN=<DISPOSABLE_DATABASE> python scripts/qdp_verify.py --with-postgres`.

[40316427951](https://github.com/BanSins7/ContentMachine/actions/runs/40316427951) and [40316619295](https://github.com/BanSins7/ContentMachine/actions/runs/40316619295) did fail on test fixture/injection issues, corrected before the green run. [40316720077](https://github.com/BanSins7/ContentMachine/actions/runs/40316720077) succeeded with all 180 tests and JSON QA artifact. This is QDP-owned local integration coverage, not live peer E2E.
