# VC-MOD-13 — Crash recovery, quota fencing, cancellation and retention

**Branch:** `feature/video-collector-v1`, PR #33. **CODE IMPLEMENTED / QA PENDING**. This is a self-contained Video Collector module, not Group A/B/C. The exact-head Linux/Windows/PostgreSQL17 outcome must be reported separately. No live DTK search, SRE M35 delivery or paid provider work was performed.

## Hardening: no duplicate paid POST

Existing `worker.py` follows a durable `submit_intent` before the external request, consumes exactly one reserved request via `collector_execution_grants` and `source_quota_ledger`, and persists a known DTK `task_id`. A crash after the POST but before the response is necessarily an **unknown external outcome**: it cannot be reconstructed from local SQL, and no automatic second POST is permitted. The supported recovery for known IDs remains GET-only; a crash after D1 commit but before Worker ACK uses the persisted immutable D1 checkpoint to avoid an unnecessary GET. Attempt increments for GET polling do not consume a second submit request.

**VC-MOD-13 additionally disallows claiming `submitted` tasks while `collector_runs.stage='cancelling'` or `search_runs.cancel_requested` is true.** A late worker must not process a newly cancelled run as if released.

`intelligence/collector/recovery.py::reconcile_cancelled(project_id,limit=50,dry_run=True)` is a bounded **explicit project-scoped** operation. It scans cancelling runs' ambiguous `submit_intent/submitted` tasks; the default is a **read-only dry-run**. With `dry_run=False`, each task is locked under `search_runs → collector_runs → provider task/job`; current unexpired running leases are never stolen. Only after the lease has expired or is no longer running does it mark the task and source request `unknown`, job `failed`, and call `_finalize` in **one transaction**. Known provider ID is retained for audit, no provider POST or GET, no spoofed completed state, and **no refund of consumed or uncertain provider requests**. It is not mounted on unauthenticated HTTP and does not run automatically in the background; operators may call it from an authorized backend recovery procedure. A later stage may provide an explicitly authorized CLI wrapper.

Quota release in `execution.cancel_released` now requires both `source_quota_ledger` and `collector_execution_grants` UPDATE operations to return a row. If either reservation is missing due to a cancellation/claim race, it raises `quota_release_race` and the enclosing transaction rolls back. No floating quota accounting; `request_cap`, `reserved_remaining`, `consumed` and the source quota window remain DB-enforced. The zero-USD operator consent is not proof of external pricing; no actual DTK spend is independently asserted.

## Retention: reviewed source policy, bounded and non-destructive

Previous VC-MOD-09 inserted `source_video_metadata.expires_at=NULL` and left `collector_ingest_pages.retention_until`/`collector_sre_outbox.retention_until` unset even when the reviewed source policy specified a retention period. VC-MOD-13 corrects **new writes** without changing applied migrations:

- `ingest._retention_deadline` re-reads the active reviewed DTK policy and `sources.retention_days` **inside the current D1 transaction**; both durations must be actual positive bounded integers. The **shorter** period determines `expires_at = fetched_at + period`. The database clock is checked within the same transaction: an already-expired incoming observation is rejected as `ingest_retention_expired` **before** any canonical D1 write.
- New `source_video_metadata`, immutable `collector_ingest_pages` and `collector_sre_outbox` rows receive this deadline on initial INSERT. All D1 video/metadata/QV/checkpoint/SRE rows remain in one atomic transaction and idempotent receipt replay remains unchanged.
- `sre_delivery._strict_event` now requires an uncancelled search run and unexpired, undeleted source metadata/page/outbox before constructing an outbound SRE candidate. Retention expiry is never interpreted as a semantic SRE match. Legacy `NULL expires_at` cannot silently be called safe for external delivery.
- `retention_audit(project_id, limit=50)` returns project-linked expired/legacy metadata IDs with explicit `verify_legacy_retention_policy` for unknown expiry, **read-only** and without leaking old titles or raw TikTok responses.
- `scrub_expired_metadata(project_id,limit=50,confirm=True)` requires **explicit server-side confirmation** and a project-linked existing D1 page, checks expiry in the SQL UPDATE, then sets mutable source-specific `title/description/creator_name=NULL`, `is_current=FALSE`, `deleted_at=now()`. It does **NOT DELETE** source metadata IDs, immutable page/video/query ledgers, `discoveries`, or SRE outbox identity. It cannot scrub a row whose expiry is unknown/NULL or not yet reached.
- **Important scope limit:** the global canonical `videos.title` column and any independent discovery/analytics projections are **outside this metadata-only scrub**. This operation is not comprehensive erasure or proof that all copies of a title are gone. Full cross-module erasure/retention policy awaits a separately reviewed global data-retention implementation; never advertise `scrub_expired_metadata` as a universal delete. Immutable FK-audit rows remain until a separately designed lawful retention disposition.

All new operations are **internal backend-only**. No new public mutating HTTP route and no automatic paid/network operation or background job. Existing DTK V1 remains deliberately **single-page**: `ingest_page(page_ordinal=0)`, source policy `max_pages=1`, no cursor replay to DTK.

## Failure matrix and recovery policy

| Failure | Durable local truth | Allowed recovery | Forbidden action |
| --- | --- | --- | --- |
| Crash BEFORE POST after committed submit-intent | `submit_intent`, consumed reservation | `unknown`, manual provider-side reconciliation | Blind second POST/refund |
| POST succeeded, response lost | `submit_intent` without verified task ID | `unknown`, manual reconciliation | Invent task ID or auto submit |
| DTK GET failed with known task ID | `submitted`, known ID | fenced deferred GET with bounded local rate | Second POST |
| Provider `done`, D1 SQL crash before commit | `submitted`, no D1 page | Existing known task GET, retry atomic D1 once | False `completed` |
| D1 COMMIT succeeded, Worker ACK failed | Immutable checkpoint and SRE outbox exist | Fenced `finish_from_checkpoint` (no GET/POST) | Re-ingest |
| Run cancelled while submitted task is outstanding | `cancel_requested`, `cancelling` | project-scoped dry-run + explicit `reconcile_cancelled`, only after lease expiry | New claim, provider GET/POST, quota refund |
| Quota release UPDATE mismatch | Two reservations disagree | Roll back cancel, investigate grant/ledger | Partial release accounting |
| Source rights revoked or retention expired | blocked source policy or expired metadata | block SRE outbound, audit/scrub due metadata | Deliver stale data as relevant |
| SRE unavailable / ACK lost | `collector_sre_outbox` pending/leased | existing bounded retry, same immutable event ID, lease fencing | Fabricate ACK or match |
| Query Validation no_queries / zero videos | QV manifest coverage, D1 checkpoint with zero counts | truthful UI, no paid provider call for no_queries | Create synthetic D1 videos |
| DTK cursor says has_more | current first page, cursor-presence bit only | one-page V1 supported | Auto-follow opaque cursor |

## Verification / limits

- `tests/test_video_collector_recovery_v1.py`: 15 offline cases for dry-run, explicit cancellation reconciliation, active lease immunity, tenant isolation, rollback, retention policy bounds, expired/legacy audit, explicit scrub and SQL/source quota guarantees.
- `tests/test_video_collector_ingest_v1.py` updated to validate exact retention timestamps on all new D1 receipt/metadata/outbox inserts.
- `tests/test_video_collector_worker_stage8.py` updated for returned, checked cancellation quota reservations. Existing completion/recovery/DTK inspector/SRE outbox suites remain.
- CI dedicated VC-MOD-13 Linux step, Python `compileall`. Actual PostgreSQL17 concurrent tests, source rights races, end-to-end provider and reviewed retention policy migration are **NOT proven** without successful exact-head CI. A queued or cancelled run is NOT PASS.
- No claim that a real external DTK zero-spend guarantee was independently validated; deployment must recheck provider commercial contract. Actual SRE M35 is not installed.

**NEXT:** VC-MOD-14 standalone packaging/deployment + opt-in flags and safe operator recovery commands; VC-MOD-15 complete seeded PG17 integration and fault-injection QA; VC-MOD-16 merge readiness.
