# Search Planner SP-08 — immutable QV Registry and trustworthy outbox

**Date:** 2026-10-26. **Branch:** `feature/search-planner-full-integration`.  
**Implementation:** committed. **QA:** tests added; hosted GitHub Actions runner still queued (verify exact SHA). Real PostgreSQL 17 concurrency/rollback checks still belong to SP-16. No paid API/Collector call or original SQL migration edit.

## Existing foundations reused, not duplicated

The existing `016_query_validation.sql` defines immutable `qv_runs`, `qv_decisions`, `qv_coverage`, and `qv_outbox`. A completed `sqg_outbox` event is consumed by `validate_generation_outbox()` after expensive semantic inference **outside the DB transaction**. A PostgreSQL transaction then locks the upstream SQG event, rechecks source lineage, creates QV run, all decisions, coverage, durable QV event and ACKs SQG **in the same transaction**. The source row lock/unique source_outbox_id guarantee concurrent workers do not create two QV runs. No new migrations were required.

## Concrete SP-08 changes

| Path | Implementation |
| --- | --- |
| `intelligence/search_planner/query_validation/integrity.py` | `verify_validated_bundle`: strict completed schema, authoritative source+selected counts, every selected candidate backed by one selected decision, no duplicate candidates or per-hypothesis ranks, consistent per-hypothesis coverage, honest no-work and dedup source accounting. `verify_outbox_event`: event type/run UUID, schema, mode, selected_count and generation UUID. |
| `query_validation/service.py::get_validated_bundle` | One PostgreSQL REPEATABLE READ READ ONLY transaction; scoped lookup and generation-bound `sqg_candidates` join; source/selected run counts validated before returning a production or test bundle. |
| `query_validation/service.py::pending_validated_outbox` | Verify every pending QV event against its source `qv_runs` `generation_id`, `embedding_mode` and `selected_count`, fail on malformed pointer; project-scoped and production-only by default. |
| `query_validation/service.py::validate_generation_outbox` | Idempotent reuse only after verifying upstream SQG ACK and actual durable corresponding QV outbox; refuse phantom reused success with missing outbox or conflicting immutable fingerprint. Concurrent replay path applies same check under locked source row. |
| `query_validation/service.py::ack_validated_outbox` | **Important change:** default-deny. Previously arbitrary caller could ACK production QV with no proof the next module had saved it. Now a trusted server-constructed `verified_ingestion(conn,pid,outbox_id,run_id)` callback must confirm a durable receipt in the exact same DB transaction. No callback or false callback = 403/409 and rollback. The real verified receiver/inbox is **future SP-12**; until then delivery remains pending, never forged. |
| `tests/test_search_planner_stage08_registry.py` | Offline tests for honest 0 work, duplicate UUID/rank, mismatched per-hyp coverage, wrong saved source counts, tampered payload pointers, project scope and SQL join, ACK idempotency under locked transaction, no ACK without durable proof, stored replay requiring actual QV outbox. |
| `.github/workflows/sp-stage01-fast.yml` | Runs SP-01–SP-08 focused QA, retains cancellation of stale SP-focused runs. |

## QV → downstream invariant

A QV event is **not** a Collector execution. The existing `qv_outbox` stays pending even when selected_count=0 (the eventual SP Inbox must ingest a truthful `no_work` event and atomically ACK). Producing the QV event is not proof of source search. No caller-supplied `accepted=True`, `delivered=True`, or `validation_state=accepted` is a reliable receipt.

At SP-12, the SP-owned durable inbox must implement the `verified_ingestion` callback against the same PostgreSQL transaction, locking source QV and inserting/verifying immutable inbox data before allowing ACK. This is deliberately not pretended in SP-08. If the inbox is absent, QV delivery stays outstanding and is safely replayable.

## QA status and test runner investigation

Focused unit:
```bash
python -m unittest discover -s tests -p 'test_search_planner_stage08_registry.py' -v
python -m unittest discover -s tests -p 'test_search_planner_query_validation.py' -v
python -m unittest discover -s tests -p 'test_search_planner_stage07_links.py' -v
```

With a disposable PostgreSQL17:
```bash
CM_TEST_PG_DSN='postgresql://.../disposable' python -m unittest discover -s tests -p 'test_taxonomy_deep_db.py' -v
```

**Not verified as PASS:** the GitHub-hosted runner has remained queued with `conclusion=null` on prior SP branch commits (also affected other module branches). Queueing means no Python tests ran, not Python test failure. Concurrency group in our SP-owned focused workflow now cancels superseded PR jobs, but cannot force hosted runners. Local git clone still fails DNS resolution in this environment. Check Actions repository/organization limits, GitHub-hosted runner availability and billing/quota before claiming it fixed. Real concurrency, crash/rollback, production embedding model and Windows/Linux full CI must be demonstrated on final SHA in SP-16.

**Stage bookkeeping:** mark SP-08 implementation **[x]** separately from its pending QA. No edits to main, VC or C Corrector branches.
