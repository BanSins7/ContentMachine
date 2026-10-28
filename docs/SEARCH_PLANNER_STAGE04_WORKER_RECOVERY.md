# ContentMachine — SP-04 supervised single-step worker and restart/recovery

Date: 2026-10-26. Branch: feature/search-planner-full-integration.
Status: CODE COMMITTED / EXACT SHA QA PENDING. No live paid calls. Do NOT close full SP-04 until real PG concurrency/recovery, deployed worker supervision and CI requirements pass.

## Existing trusted SQG implementation (reused, NOT duplicated)

| Function | Already implemented contract |
| --- | --- |
| generation_engine.worker.planned_next_request | Read-only next persisted batch; verifies context and model checksum |
| ai_query.live_worker.preflight_live_batch | Active generation consent, exact pinned payload, catalog price, quote, reserve and context window |
| ai_query.live_worker.execute_one_paid_batch | One authorized OpenRouter transport call; POST only AFTER durable dispatch_intent |
| generation_engine.repository.reserve_batch | PostgreSQL serializes generation→consent→batch and prevents duplicate claims |
| generation_engine.repository.record_dispatch_intent | Committed state before HTTP POST; a later crash is billable-ambiguous |
| generation_engine.output_manager.settle_successful_batch | Atomic cost/candidate/coverage/outbox settlement |
| generation_engine.repository.mark_uncertain_attempt | Preserve full reserve; never retry a potentially billed POST |
| generation_engine.recovery_manager.mark_stale_dispatches | Mark only stale dispatch_intent attempts uncertain |
| generation_engine.recovery_manager.request_cancel | Cancel pending and pre-dispatch reserved, preserve uncertain costs |
| generation_engine.repository.release_unsubmitted_attempt | Only exact reserved status may release funds; race against dispatch fails closed |

## New SP-04 module

- intelligence/search_planner/operations.py: GenerationExecutionController, execution_snapshot, unsubmitted_stale_attempt_ids.
- intelligence/search_planner/service.py: generation_execution_status, preflight_generation_execution, execute_generation_batch, recover_generation_dispatches, stale_generation_reservations, release_unsubmitted_generation_attempt, cancel_generation.
- intelligence/search_planner/__init__.py: public exports.
- tests/test_search_planner_operations_stage04.py: isolated Python tests of owner action gating, no-provider read-only projections, one-step worker and safe recovery.
- .github/workflows/sp-stage01-fast.yml: focused SP-01..SP-04 and existing paid transport/fake-worker regression tests.

No existing migration changed; no Collector import; no auto-dispatch daemon started or auto-retry.

## Owner authorization

GenerationExecutionController takes a server-assembled OwnerActionGuard. Default guard None blocks execute_one, recover_stale_dispatches, release_unsubmitted, cancel with 403 BEFORE any transport is constructed or DB mutation is invoked. Returning truthy 1/string is not enough; guard must return literal True. SP-13 must connect real authenticated local owner and project ACL; injecting a lambda returning True is acceptable in offline tests ONLY.

Each execute_one invokes exactly one execute_one_paid_batch, after owner-approved execute_paid_batch permission and independently persisted SQG consent+quote. This is NOT a background auto-drain and doesn't imply prior C9 spending or Collector execution consent.

Safe reads: execution_snapshot is a project-scoped REPEATABLE READ READ ONLY PostgreSQL transaction that returns batch/attempt state counts and authorized/spent/reserved USD, omitting raw prompts, API keys and provider output. preflight exposes only metadata/price/request digest, not the full OpenRouter payload.

## Restart decision table

| Durable attempt state | Safe action |
| --- | --- |
| pending | Separate authorized step can claim it |
| reserved, no dispatch_intent | A stale record can be listed read-only; only after operator approval/age threshold may release_unsubmitted_attempt atomically refund and fail run |
| dispatch_intent | May be billed. Operator triggers mark_stale_dispatches → uncertain; keep reservation, zero paid retries |
| uncertain | Hold funds, no resubmission or refund without verifiable provider invoice; invoice reconciliation is still outstanding |
| completed | Historical charge/candidates/outbox immutable |
| cancelled | Stop new claims; preserve ambiguous charged attempts |

The existing SQG row locks give durable at-most-one batch claim. A separate process lease/heartbeat manager, actual deployed worker supervision and verifiable billing reconciliation are STILL OUTSTANDING; do not treat status as fully production hardened yet.

## QA and known release blocks

Run:
- python -m unittest discover -s tests -p 'test_search_planner_operations_stage04.py' -v
- python -m unittest discover -s tests -p 'test_search_planner_stage_d.py' -v
- python -m unittest discover -s tests -p 'test_search_planner_engine_stage_c.py' -v
- python -m unittest discover -s tests -v  (with disposable PostgreSQL 17)

Final SP-04 acceptance also requires: two-process race on reserve, restart before/after dispatch intent, test of reserved USD on ambiguous paid POST, actual provider invoice verification/reconciliation design and hooks, application-authenticated owner sessions (SP-13), Windows/Linux green CI and supervised process runtime (SP-15). No live paid provider calls were run here.

This remains a module-only implementation in the SP branch. Never merge PR #34 or write to main before the full SP-16 gate and owner authorization.
