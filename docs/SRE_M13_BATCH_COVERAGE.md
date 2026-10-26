# SRE M13 — Exact LIVE Batch Manifest & Coverage Accounting

**2026-10-26 · branch:** `feature/sre-production-v1-20261023` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22).

**Status:** implementation and negative tests committed; actual GitHub Actions acceptance **PENDING**. No provider dispatch, money reservation, paid execution, classification inference, or main merge.

## Objective / sources of truth

Given an existing **M12 unapproved** `sre_live_runs(state='pending_consent')` and its pinned D1/S3 content versions and human-reviewed RTB C9 hypothesis UUIDs, materialize immutable, deterministic batch manifests with a physically represented record for **every** expected item × hypothesis pair. It is impossible to silently omit a hypothesis or turn its absence into a `no_match`.

The authoritative inputs come from the project-owned PostgreSQL M12 run manifest and corresponding `sre_live_items`; self-signed HTTP JSON is not accepted. The source SRE project owner must be authenticated by the host; this is an internal port. M08 active rights and actual spending authorization remain mandatory at future external-send boundaries. Materialization alone **never** grants permission to execute.

## Files and additive migration

- `intelligence/relevance/live_batch_manifest.py`: `make_batch_manifest(...)` produces deterministic ordered inputs and expected pairs with a canonical input SHA; `materialize_live_batches(conn,project_id,run_id,batch_size=20)` atomically partitions and persists exactly once under a project/row lock; `get_live_batch_coverage` exposes only tenant-scoped counts and statuses; `summarize_pair_coverage` checks exhaustive one-to-one observed pairs for future verified worker output.
- **New** `intelligence/migrations/055_sre_live_batch_coverage.sql` following 054 (no legacy migrations edited): `sre_live_batch_members` and `sre_live_expected_pairs`; required composite `batch/run/project/item` FKs, uniqueness of item membership per run, no duplicate C9 UUID per item. Inserts check the actual `sre_live_items.expected_hypothesis_keys` and M12 run's pinned keys in the same project. Membership, category identities and creation timestamps are immutable. Pair deletion is denied.
- PostgreSQL BEFORE UPDATE settlement fences on `sre_live_items`, `sre_live_batches` and `sre_live_runs` check **physical expected pair counts**, all assigned items, and measured evaluation coverage. An item/batch/run cannot transition to `completed` with any unresolved or blocked C9 pair. `partial` requires at least one evaluated pair and less than 100% evaluated. A status cannot be inferred from presence of text alone.
- `intelligence/relevance/migration_guard.py` requires 055 and now reports next free prefix `056`; main-only upgrade simulations explicitly exclude 016, 017, 054, 055 from the baseline. Existing applied migration SHA records never rewrite.

## Deterministic batch identity

Input sorting: videos by canonical `videos.id`, then unique item ID; leaves are the exact sorted canonical approved C9 UUID list. Each chunk of at most `MAX_BATCH_SIZE=25` videos gets an immutable manifest containing run ID, project, previous M12 run-manifest SHA, ordinal, source-backed video ID/content version/SHA/context SHA, full leaf list and **every pair with initial `not_evaluated` state**. Batch input SHA covers run SHA + item pins + keys; manifest SHA covers the whole manifest; batch ID is UUID5 of run ID + ordinal + input SHA.

Repeated `materialize_live_batches` with the same batch size, source plan and per-video inputs verifies **all** existing batch JSON/SHA, memberships and exact pair rows and returns `replayed=True`; it cannot append duplicates. A changed partition size or tampered/missing row is rejected. Two simultaneous callers serialize on the LIVE run's PostgreSQL row lock. No batching is allowed after the run leaves `pending_consent`.

Batch table rows currently use the M11 `queued` status but are **not** authorized execution: the parent run remains `pending_consent`, and the API returns `execution_allowed=False`. Future paid workers must enforce parent run, budget, lease, rights and consent rather than claiming a queued batch is sufficient.

## Pair status semantics and settlement

New SQL pair rows initially permit **only** `not_evaluated`, `blocked_rights` and `blocked_budget` — not unverified `match`, `no_match` or `uncertain`. A future LIVE model-response/evidence/attempt migration will extend the status protocol **only after trusted provenance checks**. This conservative restriction means **no LIVE run may be completed in M13**, by design: there is no fabricated model result.

`summarize_pair_coverage(expected,observed)` is a *pure transport accounting* helper. It rejects duplicate, unexpected or missing pair IDs; marks explicitly unresolved and blocked counts; it can inspect `match/no_match/uncertain` **without asserting those decisions were made by a provider**. A 2/3 leaf result is `partial`, never completed. Any future worker must separately validate evidence and then persist via audited transactions.

`get_live_batch_coverage` checks the persisted matrix, never exposes raw source content or assumes that a still-valid M12 grant remains valid at dispatch.

## QA / negative tests

- `tests/test_sre_live_batch_manifest.py`: fake PostgreSQL commands, deterministic replay, changed partition conflict, missing/tampered physical member/pair, wrong tenant, invalid batch size/UUID, 100 videos × 20 leaf hypotheses = **2,000 unique expected pairs**, 2/3 semantic labels fail full coverage, not_evaluated and blocked statuses remain explicit.
- `tests/test_sre_live_coverage_schema.py`: SQL structure, tenant-composite FKs, guard triggers, no destructive/paid operations.
- `tests/test_sre_offline_pg.py`: real disposable PostgreSQL17 approved C9 + D1/M08 project fixture; two videos × three hypotheses = six pair rows in two immutable batches, no model-result rows, direct SQL `completed`/`partial` denial, disallowed extra key and DELETE, tenant denial, changed batch size, concurrent identical materialization using two connections.
- `tests/test_sre_migration_guard.py` and `tests/test_sre_migration_guard_pg.py`: manifest next-056 and new/main-only 055 installation with immutable old checksums, no-op reapply.
- `.github/workflows/sre-contracts.yml`: Linux+Windows M13 pure suites and PostgreSQL17 integration.

Local opt-in commands (requires a disposable PostgreSQL17 DSN only for PG tests):

```sh
python -m unittest discover -s tests -p test_sre_live_batch_manifest.py -v
python -m unittest discover -s tests -p test_sre_live_coverage_schema.py -v
CM_TEST_PG_DSN='<DISPOSABLE_PG17_DSN>' python -m unittest discover -s tests -p test_sre_offline_pg.py -v
CM_TEST_PG_DSN='<DISPOSABLE_PG17_DSN>' python -m unittest discover -s tests -p test_sre_migration_guard_pg.py -v
```

**QA truth:** these source files exist, but no PASS is claimed until the GitHub Actions runs complete on the exact final SHA. Prior M01–M12 acceptance remains separate and open.

**Next M14:** LIVE claim/lease heartbeat and fencing with actual consent/rights checks; work only after the next explicit user request.
