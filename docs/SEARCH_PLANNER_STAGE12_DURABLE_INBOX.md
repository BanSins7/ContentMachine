# ContentMachine · SP-12: durable SP inbox and optional Collector handoff

**Date:** 2026-10-26  
**Branch:** `feature/search-planner-full-integration`  
**Code:** IMPLEMENTED in independent SP module.  
**QA:** offline tests committed, GitHub-hosted focused/full jobs on exact HEAD have not started (queued). Real PostgreSQL17 fresh/upgrade, concurrency, crash and genuine Collector compatibility remain **NOT VERIFIED**. Do not interpret [x] code as production release.

## Verified invariants and actual source of truth

SP-11 provides `search-planner-handoff-projection/v1` using the immutable production QV/C9/SQG source reader. The trusted entry point `ingest_qv_to_sp_inbox(project_id, validation_run_id)` obtains that exact projection server-side; **client JSON is never an ingestion source**. The inbox is independent of whether the unmerged Collector branch exists.

Inside one PostgreSQL transaction:
1. Lock `qv_outbox` with `SELECT ... FOR UPDATE OF o`, project and `embedding_mode='semantic_local'` constrained. Verify real QV generation, versioned event pointer, selected count and original run ID.
2. Check unique `(project,qv_outbox_id)` inbox identity; insert immutable event, all chunks, and each selected query's complete original SQG/QV/linked hypothesis provenance. **0 selected** inserts a terminal `no_work` event with 0 chunks but retains the entire QV coverage in the event snapshot.
3. Re-read the saved event, all chunks and selected links from the same connection; enforce identical canonical payloads, SHA-256, original candidate IDs, selected count and linked fact/hypothesis/decision ancestry. On a conflict, abort with 409 — no silent repair or replacement.
4. Only after those verifications, update `qv_outbox.delivered_at` in the **same transaction**. This ACK means **SP inbox durability only**, not Collector receipt. If the transaction rolls back, both the event and QV ACK roll back. A previously ACKed QV without a durable SP inbox is rejected.
5. Replaying the same event uses the existing authoritative event and compares every byte-semantic record; new inbox/chunk/lineage rows are not created. The QV ACK increments only for a first successful ingest. The source QV row lock serializes simultaneous workers.

In the production implementation, one complete inbox snapshot is authoritative even for 117 queries (50+50+17). All original UUIDs, QV decisions, source facts and hypothesis aliases survive. No alternative Query Validation registry or duplicate provider registry is created.

## Additive migration

`intelligence/migrations/055_sp_handoff_inbox.sql` creates five SP-owned tables:
- `sp_handoff_events`: unique QV outbox and QV run/project, exact SHA-pinned immutable projection, selected/chunk counts and state.
- `sp_handoff_chunks`: original JSON chunk / digest and unique `(event_id,chunk_index)`.
- `sp_handoff_query_links`: selected original candidate UUID → exact chunk and complete provenance JSON; unique `(event_id,candidate_id)`.
- `sp_delivery_attempts`: one stable idempotency key per chunk, with durable `uncertain` reservation before an external intake call.
- `sp_delivery_receipts`: append-only independently re-read Collector durable intake receipt, linked to a specific attempt and chunk; IDs, contract, accepted_at and SHA persisted.

Source event payload, chunks, facts, attempts' immutable attributes and receipts are write-protected with triggers. Attempt transitions are one-way `uncertain→accepted/incompatible/needs_attention`; no destructive reset. Event status may change as a separate lifecycle field; the projection itself never changes. Historical SQL migrations 015/016 and SP10 054 are unmodified.

## Lifecycle and precise delivery/receipt semantics

SP inbox supports the status vocabulary `pending_planning`, `awaiting_owner_commit`, `committed_held`, `awaiting_collector`, `delivering`, `collector_accepted`, `incompatible`, `needs_attention`, `no_work`, `cancelled`.

New nonempty inbox events start as **`awaiting_collector`**; lack of a truly installed Collector leaves them there. An actual Collector intake requires two independent things beyond QV validation:
1. Server-owned owner action guard approving `search_planner.handoff.deliver` for exact project/event, and
2. an existing immutable, approved HELD plan for the same project/QV with at least one real planned task. **SP10 approval alone does not authorize provider execution.**

`deliver_next_held_chunk()` sends **at most one** chunk per explicit invocation. If no adapter is installed it performs **zero** Collector/provider calls, does not create an attempt and reports the stored pending state.

With an available trusted adapter, the dispatcher **commits an `uncertain` durable attempt before calling** its one-shot `accept(project_id, handoff, idempotency_key)`. A successful result is *not* trusted until `get_receipt(project_id,idempotency_key)` independently returns the matching versioned durable receipt. This verified receipt must pin exact project, event, chunk index, chunk/query-set SHA, idempotency key, actual `external_query_set_id`, `accepted_at`, `durable=True` and `provider_execution_authorized=False`. Only then are SP receipt, attempt accepted status and event state updated atomically. The event becomes `collector_accepted` **only when all chunks have real distinct receipts**.

If `accept` times out/throws, or the independent receipt is absent/inconsistent, SP stays `needs_attention` with an **uncertain** immutable reservation; no automatic retry or second external POST. An explicit `reconcile_uncertain_chunk()` uses **only get_receipt** and never calls `accept` again. If a durable receipt is found, its integrity/identity is checked and finalization proceeds without a second intake call.

**Pre-delivery compatibility handshake:** every configured adapter must expose a local, versioned `search-planner-collector-adapter-capabilities/v1` manifest declaring exactly `validated_query_set/v1`, `max_queries_per_set=50`, `durable_receipt_readback=True` and `provider_execution_separate=True`. An incompatible adapter is rejected before any owner action or external `accept`. The query set ID in the receipt must be a canonical Collector UUID, not an arbitrary label.

**Critical limitation:** checksum plus a server-injected `get_receipt` is not a cryptographic authentication of an untrusted external service. The eventual production adapter must authenticate transport and independently read the actual Collector durable table/service. A fake/mock adapter is QA only; the current unmerged VC branch does not yet export the requisite authenticated intake+re-read contract. Never infer acceptance from its offline structural `validation_state='accepted'`.

### Safe default and QA mock

- `EmptyCollectorAdapter` — no network, reports unavailable, default everywhere, never creates false receipts.
- `MockCollectorAdapter` — an explicit local fixture that stores/re-reads immutable fake receipts; supports one simulated lost response after persistence, zero real provider tasks and zero paid calls.
- No imports from `intelligence.collector`, no automatic worker scheduling, no paid API POST or video processing.

An accepted Collector **query set** is not a completed video search. Provider execution requires a separate Collector-owned release/rights/cost decision. Neither SP owner approval nor SP inbox ACK grants that.

## Files created or modified

| Path | Purpose |
| --- | --- |
| `intelligence/migrations/055_sp_handoff_inbox.sql` | Durable append-only source snapshot, chunk/link tables, status lifecycle and separate external receipt ledger |
| `intelligence/search_planner/handoff/repository.py` | One-transaction QV lock→inbox→chunks→links→durability check→QV ACK; replay and crash safety; read-only status |
| `intelligence/search_planner/handoff/dispatcher.py` | One explicit intake per call; durable uncertain reservation, owner/approved-plan guard, verified receipt and read-only reconciliation |
| `intelligence/search_planner/handoff/collector_adapter.py` | Versioned receipt validator, Empty default port, QA-only mock |
| `intelligence/search_planner/handoff/__init__.py` | Safe exports |
| `intelligence/search_planner/service.py` | `ingest_verified_qv_handoff`, `ingest_pending_qv_handoffs`, `get_handoff_status`, `deliver_next_handoff_chunk`, `reconcile_handoff_chunk` |
| `intelligence/search_planner/ports.py`, `api_contracts.py`, `__init__.py` | Optional CollectorHandoffPort and public read-only capabilities |
| `tests/test_search_planner_handoff_db.py` | Atomic 117/0/replay/rollback/owner/Collector/lost-response behavior with **transactional in-memory DB double** |
| `tests/test_search_planner_collector_adapter.py` | Durable receipt identity, tampering, absent port, mocked lost response |
| `.github/workflows/sp-stage01-fast.yml` | Stage 12 focused suite and previous regression tests |

## Tests and release gates

Focused tests once runners execute:

```bash
python -m compileall -q intelligence/search_planner/handoff tests/test_search_planner_handoff_db.py tests/test_search_planner_collector_adapter.py
python -m unittest discover -s tests -p 'test_search_planner_handoff_db.py' -v
python -m unittest discover -s tests -p 'test_search_planner_collector_adapter.py' -v
python -m unittest discover -s tests -p 'test_search_planner_stage11_handoff.py' -v
```

**Still OPEN, not claimed PASS:** actual PostgreSQL17 migration fresh/upgrade, two concurrently locked QV consumers, crash before/after DB ACK with durable WAL restart, RLS/project/FK/immutable trigger enforcement, Collector adapter version handshake/authenticated external DB receipts, Windows/Linux all-up CI. A sequential 10-replay fake DB test is NOT a PostgreSQL concurrency test. Real Collector production readiness depends on its independent branch and is not fabricated here.

Next: SP13 HTTP owner ACL and safe bounded requests; SP14 UI; SP15 supervision/metrics; SP16 integrated PG17/Windows/Linux release gates. No merge to main without explicit approval.
