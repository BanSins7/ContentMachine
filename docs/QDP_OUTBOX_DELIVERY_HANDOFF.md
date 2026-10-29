# QDP FIX-12 — durable outbox delivery and consumer ACK handoff

**Status:** QDP-owned code **CODE_IMPLEMENTED; QA_PASS** on code SHA `5283884425a0326b40fd182607f74b453f11621e`. [GitHub Actions 40319126522](https://github.com/BanSins7/ContentMachine/actions/runs/40319126522) passed **197/197 tests**, zero failures/errors/skips, PostgreSQL 17 migrations, Python and JavaScript syntax.

## API and strictly bounded ownership

`intelligence.qualified_data.outbox_delivery` exports:

- `claim_delivery(consumer,connect=...,worker_id=...)` — QDP producer-side single lease claim.
- `verify_delivery_event(row,consumer)` — matches immutable `qdp-event/v1` JSON hash, exact project/run/video/group and pinned input SHA; constructs canonical `QDPHandoffPointer`.
- `dispatch_once(consumer,receiver=...,connect=...,worker_id=...)` — one event; requires explicit trusted receiver and rechecks current `read_current_preparation`; no implicit paid task.
- `verify_receipt(receipt,pointer,consumer,payload_sha256)` — require `state=committed`, exact event+consumer+hash, stable 8–160-character `receipt_key`.
- `settle_delivery(lease,receipt=... | error_code=...,connect=...)` — fenced ACK, bounded retry, or dead letter. No duplicate ACK across leases.

Consumers supported: `group_a`, `group_b`, `group_c`. No default receiver is installed. `receiver=None` raises QDPError `consumer_unavailable` (503) **before claiming**, preserving pending delivery. The receiver protocol used by dispatch has method `receive(pointer,event_payload_sha256)` that MUST return only after its own durable inbox+idempotent job transaction commits. The port is explicitly configured at integration; QDP does not call OpenRouter/DTK, download media, create Group tasks, or mark Group analyses finished.

## Receipt contract

A trusted peer adapter must return:

```json
{
  "state": "committed",
  "event_id": "<canonical event UUID>",
  "consumer": "group_a",
  "payload_sha256": "<actual QDP event SHA256>",
  "receipt_key": "<durable idempotent consumer reference>"
}
```

This JSON is a **shape example**, not a fabricated actual receipt. Each consumer MUST store a unique `(event_id,payload_sha256)` binding within its own DB transaction and atomically insert at most one job per event; same event+same hash replay returns the same receipt, same event with different hash rejects. Never ACK simply because an HTTP transport returned 200/queued. No consumer job completion is implied by `qdp_delivery_receipts`.

## Delivery lifecycle

1. QDP Worker (FIX-11) atomically stores immutable A/B/C preparation facts, `qdp_outbox` event, and one `qdp_outbox_deliveries` row for each ready Group.
2. FIX-12 claim uses `FOR UPDATE OF d SKIP LOCKED` with `attempts<max_attempts`; increments `lease_generation` and `attempts`, sets a 90-second lease.
3. Dispatcher validates `qdp-event/v1` SHA and event identity and calls `read_current_preparation` to prove **current** SRE/D1 and group rights, source observation/media or metrics. If invalidated, only this consumer is dead-lettered; others unaffected.
4. Explicit concrete receiver writes its own inbox+job transaction. Only when `receive` returns **committed** proof does the dispatcher atomically insert `qdp_delivery_receipts` and transition this `qdp_outbox_deliveries` to `acknowledged`, with owner/generation/time fence.
5. Failures retry with 30,60,120,...≤1800 seconds. Expired leases can be reclaimed; stale generation cannot ACK. Exhausted/invalid permanently transition to `dead_letter`. One consumer's failure never forces another Group to fail.

If the consumer commits and QDP crashes before ACK, the next attempt MUST be safely idempotent at the consumer inbox. The dispatcher makes no exactly-once claim about raw network delivery, only **at-most-one durable consumer job** when that consumer honors its own unique key.

## Immutable per-attempt audit

`044_qdp_delivery_attempt_journal.sql` creates `qdp_delivery_attempt_log`: event UUID, consumer, lease generation, attempt number, transition (`leased`, `retry_scheduled`, `acknowledged`, `dead_letter`), reason and timestamp. Trigger forbids direct mutation. `045_qdp_delivery_journal_cascade_fix.sql` permits cascading deletion only when the parent delivery row no longer exists (e.g., an authorized QDP run lifecycle purge), avoiding a denial-of-service during parent cleanup. Both migrations are forward-only and included in required QDP schema checksum preflight.

## Verified QA and remaining integration limitations

- [40319126522](https://github.com/BanSins7/ContentMachine/actions/runs/40319126522) **197/197** on `5283884425a0326b40fd182607f74b453f11621e`: 9 pure tests plus 8 database delivery tests, alongside all older QDP tests, PostgreSQL 17 and syntax PASS.
- Tests simulate a real consumer **only in a disposable PostgreSQL table** `qdp_test_consumer_jobs` written by a separately committed receiving transaction, not a production Group table.
- Tested 50 direct duplicate `receive` calls and 50 producer replays→1 simulated consumer job; ten concurrent QDP dispatchers→1 ACK; crash after receiver COMMIT before QDP ACK; wrong hash/uncommitted receipt; lease reclaim/fence; dead-letter; Group A ACK with Group B temporary failure; source rights revocation; immutable journal/cascade cleanup.
- **Still BLOCKED_EXTERNAL:** concrete Group A/B/C receiver adapters, combined peer migrations, real LIVE SRE event to Group execution end-to-end. FIX-13 integrates Group A, FIX-14 Group B/C. Do not describe this as a production-ready external consumer hookup.

No modifications to `main` or peer module branches. Old applied QDP migration files preserved byte-for-byte.
