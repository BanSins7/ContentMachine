# UA-MOD-18 — Dedicated Python Worker, explicit scheduler and atomic fenced snapshot

**Branch:** `feature/unified-analytics-full-module` · **Entrypoint:** `intelligence/unified_analytics/worker.py` · **Tests:** `tests/test_unified_analytics_worker.py`, `tests/test_unified_analytics_worker_pg17.py` · **Queue dependency:** UA-MOD-17 / migration 088.

## Scope and guarantees

The Python worker handles only UAE-owned durable qualification jobs, with **no automatic startup**, API/webhook activation, provider/LLM requests, Group C score recomputation, Patterns publication or writes to peer-owned Group A/B/C/SRE/QDP tables. External analytics approvals and user-visible read authorization are explicitly **not** inferred from a queue job or a QDP receipt.

The UA-MOD-12 `JoinedContextAssembler` requires complete, same-project/same-video/currently pinned `JoinedCandidatePins`. Automatic source-backed discovery across every upstream peer is **not ready**. Therefore the default CLI refuses to claim or consume jobs in `--once` or `--watch` without a trusted server-side `CandidateResolver`. Operators can run `--recover-only` for explicit expired-lease maintenance without any resolver. This does **not** fabricate blocked snapshots or drain retries for missing peer readiness.

## Worker API and invocation

`WorkerLimits` validates project ID, worker identity, max jobs (1–10000), per-claim batch (1–25), lease (5–900 seconds), poll interval (0.25–60 seconds), retry wait (0–3600), PG17 lock/statement timeouts, and maintenance batch (1–100). A statement timeout must fit within the lease with at least five seconds of margin. Connections must be injected, **IDLE**, `autocommit=True` psycopg `dict_row` and authenticated to an exact authorized project.

`QualificationWorker(connect_factory, limits, resolver=trusted_resolver)` provides `run(watch=False, recover_only=False)`, `process(lease)` and `recover()`. The trusted resolver is responsible for DB-backed discovery only; the existing JoinedContextAssembler re-reads and verifies all seven source/QDP/SRE/A/B/C plus D1 claims before any historical write. Resolver presence is not proof of availability or approval. Empty/missing/noncanonical candidate pools fail closed and never create a fabricated snapshot.

`run(watch=False)` performs one bounded claim batch; `run(watch=True)` repeatedly polls only under explicit opt-in, stopping on `max_jobs` or stop event. Idle `SIGINT` or `SIGTERM` stops future claims; in-progress SQL is bounded by a statement timeout and lease deadline. An unprocessed claimed job is deliberately left to expire and be reclaimed, rather than being falsely acknowledged. `recover()` reclaims expired jobs only; deleting expired source media or snapshots is deferred to UA-MOD-29.

CLI modes:

```sh
# Explicit local maintenance only; no source candidate discovery required:
CM_UAE_WORKER_DSN='postgresql://...your-disposable-db...' \
  python -m intelligence.unified_analytics.worker \
  --recover-only --project <32-lowercase-hex-project> --worker uae.worker.1

# --once / --watch are accepted by the programmatic main() ONLY when
# a separately audited trusted resolver is explicitly injected:
# main(["--once", "--project", project, "--worker", "uae.worker.1"],
#      resolver=trusted_server_side_resolver)
```

No DSN defaults to the application's `CM_DB_DSN`. CLI does not expose an arbitrary `--resolver-module` import, HTTP listener, daemon install, cron hook or workflow that silently starts jobs. An operator controls connection credentials and project authorization. Any future resolver implementation must be audited before CLI production activation.

## Atomic fenced write contract

The worker makes two distinct, short database actions: (1) claims a job using UA17 `SKIP LOCKED`, and (2) verifies+persists+acknowledges it **in one new PG17 REPEATABLE READ transaction**.

1. Lock the exact job row `FOR UPDATE OF j`. Compare project, job UUID, pinned request UUID, job SHA, worker, lease token, monotonic lease generation, deadline, stored request SHA and UA17 current project/source/policy/QDP readiness. No valid lease means no candidate reads.
2. Use PostgreSQL `clock_timestamp()` for fresh `as_of`, never caller timestamps; resolve server-side pins under that same connection, then re-run existing UA12 joined checks. No trust based on preverified fields or a reviewed Group C score that has not been source-backed.
3. Materialize the **unapproved** immutable draft through `ImmutableSnapshotAssembler` and write the seven-evidence snapshot using UA15 `ImmutableSnapshotRepository._write` *inside that same transaction*. Qualifying or publishing a video is forbidden at this stage. The worker does not call the public `assemble_and_save()` because it opens another transaction and would introduce stale-lease commits.
4. Call UA17 `SUCCEED_SQL` in the same transaction. It again verifies unexpired token/generation/worker, current source+QDP readiness, and that the persisted snapshot belongs to the same request, is uninvalidated, and has status `partial` or `blocked`. If the CAS produces no success row, raise, rolling back **the entire snapshot write and job acknowledgement**. PostgreSQL job row lock blocks a concurrent cancel/recovery until commit, and lease expiry within the transaction still aborts the acknowledgement.
5. Exceptions contain only bounded machine reason codes; retry/dead-letter accounting occurs outside the snapshot transaction. If a worker crashes before commit both writes roll back; if it crashes after commit both exist. A missing dependency cannot result in `qualified` or a successful job. A retry can independently re-read real source evidence; UA15 immutable replay protects duplicate snapshots.

This design prevents the classic race “persist snapshot; lose lease; stale worker sends ACK” by making the historical write and fenced ACK atomic. A source rights change after commit is still checked at **UA-MOD-19 read time** and via UA-MOD-16 invalidation; no historical snapshot is a publication grant.

## Test and release status

Authored `tests/test_unified_analytics_worker.py`: bounded CLI and configuration, no implicit candidates, lock-before-read, transactional commit, lost-token rollback, mid-write cancellation rollback, crash/failure redaction, controlled one-batch run, maintenance and graceful stop. Authored `tests/test_unified_analytics_worker_pg17.py`: conditional disposable PostgreSQL 17 rollback of a same-transaction temporary insert if lease ACK rejects the snapshot, and cancelled-lease negative check. The PG17 test reuses the UA17 synthetic fixture but does not represent real peer consent, source-backed Group C review or production snapshot correctness.

```sh
python -m compileall -q intelligence/unified_analytics
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
# Optional real PG17: set CM_TEST_PG_DSN to isolated disposable PG17.
```

**Honest release blockers:** Python and PG17 tests must actually run; the 088 queue migration and its triggers have not been execution-confirmed here. Candidate discovery is not source-backed end-to-end yet, peer SRE/QDP/A/B/C effective approvals cannot be asserted, no secure daemon or automatic worker startup is enabled, and public read/publish is still prohibited. This implementation is **not** permission to merge PR #38 or activate production workers without explicit integration QA.

**Next stage:** UA-MOD-19 — authoritative read-side visibility and current rights checks for historical snapshots. Keep release blockers and real PostgreSQL testing open.
