# QDP FIX-15 — Human veto, source invalidation, explicit refresh

**QDP implementation complete.** The QDP module owns SQL 046/047, `intelligence.qualified_data.invalidation`, `revocation_delivery`, and a fenced positive outbox ACK. Expanded QA **pending** at document authoring. Do not infer QA success from the 215/215 older [run 40329425758](https://github.com/BanSins7/ContentMachine/actions/runs/40329425758).

## Trusted event flow

`invalidate_run(project,run_id,origin=...,reason_code=...,provenance_sha256=...,video_id=None,group=None,connect=...)` is an **internal authenticated producer port**, not an arbitrary public JSON endpoint. Origin may be SRE/human_review/d1_source/retention/configuration/operator; reason is strictly typed. The same project/run/video/group/origin/SHA replays exactly once; conflicting meaning is rejected. Under `qdp_runs FOR UPDATE`, it increments the run generation and appends immutable `qdp_invalidations`. It aborts pending/leased **original** prepared-event deliveries without deleting immutable historical qdp_preparations or earlier consumer receipts, then writes distinct `qdp-event/v1 preparation_invalidated` outbox events for affected historically ready A/B/C groups, each with its own pending consumer delivery and signed provenance.

`reconcile_current(project,run_id,limit=100)` is a **bounded explicit** change-handler hook: it checks current QDP/SRE/D1 facts and emits trusted invalidations for stale formerly-ready inputs. It does not autonomously schedule background polling and must be invoked by an authenticated source or operator. Missing dependencies do not imply that the source is automatically revoked.

## Current human-review and ACK race

Forward-only `047_qdp_current_human_review.sql` compares each latest non-superseded SRE human label ID/effective decision to the original pinned SRE category evidence. Changed human verdict immediately hides old `qdp_group_current_preparations`; historical rows remain auditable. The QDP UI read-side masks old item as blocked when all formerly ready groups are no longer current. `claim_delivery` persists the run's invalidation generation; final `settle_delivery` acquires the run lock, rechecks current project/run/video/group and validates that same generation and active run before ACK. A source revoke or human veto committed between receiver write and producer ACK cannot produce an invalid positive producer receipt.

## Downstream compensation (not yet peer ACK)

`revocation_delivery.dispatch_invalidation_once('group_a'|'group_b'|'group_c',receiver=...)` accepts only an explicitly connected peer with `revoke(pointer, qdp_payload_sha256, signed_event)`. That peer must first commit its own idempotent revocation inbox and stop-work barrier, then return an exact `{state:'committed',event_id,consumer,payload_sha256,receipt_key}`. A missing receiver/method returns 503 **without claiming or ACKing**. Group A/C pinned branches presently expose `receive()` only, **not** `revoke()`. Thus the compensation outbox is pending; QDP never asserts already accepted external paid work was stopped. The peers must recheck rights at execution and implement their own revocation inbox.

## Explicit refresh

`explicit_refresh(project,prior_run_id,new_idempotency_key,config=None)` reuses trusted `create_run` with the original verified LIVE SRE run, current human labels and D1 inputs. Requires a distinct idempotency key, creates a new QDP run+manifest and never mutates or resurrects the original revoked run. A retry of the new key reuses the new run idempotently.

## Tests and limits

`tests.test_qdp_invalidation` covers bad scopes, events and provenance. Expanded disposable PG17 suite tests human veto before/after worker, source rights, consumer commit/ACK race, Group A-only revocation preserving B, replay, and refresh. Source/policy cleanup and execution-time revocation in independent peer modules remain separate integration release gates. The QDP revocation receiver in PostgreSQL tests is a **test-only durable inbox**, not production Group A/C.
