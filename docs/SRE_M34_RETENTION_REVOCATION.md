# SRE M34 — Permanent Source Revocation, Read Suppression, Disposable Purge, Durable Cancellation Outbox

**Branch:** `feature/sre-production-v1-20261023`, [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22), 2026-10-28. **M34 protective implementation committed; full physical-retention DoD remains BLOCKED. No exact-head CI/PG17 SUCCESS observed.** No paid POST.

## Critical semantics

SRE M34 does NOT equate a revocation callback with total deletion. It immediately blocks SRE retrieval and new derived processing for an exact tenant-scoped D1 content version, physically removes disposable M18 embedding vectors and S4 offline item contexts and S4-only fake result/evidence rows in the same transaction, and persists an immutable source tombstone plus LIVE run cancellation outbox. Immutable M25–M33 financial/receipt and integrity evidence, and the original base `sre_content_versions.content`, are **not** physically erased. The system reports this limitation explicitly and will NOT answer `full_physical_deletion_complete=true`.

This design protects against silent regrant of revoked content, re-creation of a cache, invented UNKNOWN financial refund/retry, and loss of pending cancellation after process crash. True source erasure involving D1 source-of-truth retention, immutability FKs, provider-side retention, logs/backups and connected modules requires separately coordinated authority and data lifecycle decisions; **SRE cannot guarantee deletion of third-party copies or external D1 database**.

## New forward-only schema 068

`intelligence/migrations/068_sre_live_source_revocation.sql`:

- `sre_live_source_tombstones`: permanent `(project_id,content_version_id)` primary key, original video/source-policy/sha256 pin, structured reason (`rights_revoked`, `source_deleted`, `retention_expired`, `privacy_delete`) and authorized actor. SQL INSERT checks exact existing original tenant-owned source row, project workspace synchronization; UPDATE/DELETE forbidden. No raw D1 title/content/transcript or free-form reason.
- AFTER INSERT cleanup atomically deletes exact SRE M18 embedding cache vectors and S4 offline-fixture item `contexts` and fake result/evidence rows and synthetic S4 classification result/evidence rows; inserts one unique `sre_live_revocation_outbox` row per affected LIVE run. Any error rolls back all changes, so there is no half-committed tombstone/cache state. Historical paid receipts/reservations/intent/status records remain untouched.
- `sre_live_revocation_outbox`: project-content-run FK, fixed `pending` → `acknowledged` monotone state, original server timestamps and actor; trigger prohibits ACK while run is still active and not cancel-requested, forbids update after ACK and DELETE. Durable outbox can be retried after worker process failure; **no automatic paid retry**.
- S3 authoritative `sre_readable_content_versions` SQL view is replaced **in forward-only 068** with the unchanged old columns and all previous D1 dynamic policy/expiry/media requirements, plus `NOT EXISTS` original tombstone. SRE `sre_live_classification_results` projection similarly filters tombstones even if source policy is later re-enabled.
- BEFORE INSERT SQL guard blocks new SRE LIVE/offline item, OpenRouter quote/paid dispatch intent, M30 source-based result, M32 human review and M33 gold benchmark snapshot for tombstoned sources. Separate cache BEFORE INSERT guard prohibits vector regeneration. The permanent tombstone prevents data re-creation, not merely temporarily hides a record.

## Trusted internal service

`intelligence/relevance/live_source_revocation.py`:

- `record_sre_source_revocation(conn,project,content_version_id,*,expected_content_sha256,reason_code,actor_id,actor_authorizer)` requires `revoke_sre_source` host action and dedicated idle autocommit PostgreSQL connection. Acquires exclusive project workspace lock, checks **actual tenant-owned stored SHA and source-policy version**, writes idempotent tombstone, verifies all exact M18 cached vectors deleted. The first reason wins; a later replay cannot silently change provenance.
- `record_sre_source_policy_revocation(...)` offers a source-policy-level callback that enumerates up to 1,000 project-owned SRE versions and invokes the exact-idempotent callback for each. >1,000 is blocked rather than silently truncated. This is crash-replayable **per version**, not an atomic all-policy delete; the D1 owner must enforce rights denial during the entire process.
- `process_sre_source_revocation_outbox(...)` requires `process_sre_source_revocation`, calls the existing M15 `cancel_live_run` on safely cancellable LIVE runs, never attempts paid OpenRouter retries, and acknowledges only after SQL sees a cancellation flag/terminal state. A possibly charged `unknown` run with no cancel flag remains pending and is reported as requiring operator reconciliation. Crash leaves the outbox durable; repeated invocation is idempotent. **No background schedule automatically runs these callbacks.**
- `inspect_sre_source_revocation(...)` requires `read_sre_source_revocation`, is REPEATABLE READ READ ONLY, returns bounded counts/states/opaque run IDs, no actual source bytes.
- `intelligence/relevance/intake.py::read_content` and `intelligence/relevance/live_diagnostics.py` reject permanent tombstones; M32/M33 repeat rights via existing D1 read gate. The S3 SQL readable view and result projection also suppress revoked content.

## Authored QA, not observed passed

- `tests/test_sre_live_source_revocation.py`: simulated idempotent tombstone, cache removal, permanent first reason, exact tenant SHA/actor, source-policy replay/limit, M15 cancellation ACK/replay, unverified possibly paid UNKNOWN remains frozen, crash resilience, no false physical-erasure claims.
- `tests/test_sre_live_source_revocation_schema.py`: SQL 068 project-content original pin, immutable tombstone, purge targets, SQL view suppression and new derived-data guards, protected acknowledgment and no finance purge, migration next 069.
- Disposable PostgreSQL17 `tests/test_sre_offline_pg.py::test_m34_pg_source_tombstone_cancel_replay_and_no_revived_content`: genuine approved D1/C9 source, LIVE run with no paid attempt, after fresh authenticated callback D1 read denied, cache suppressed, replay reason unchanged, direct delete/update/foreign event rejected, M15 cancellation and outbox acknowledgment idempotent; readable classification-result view has no revoked entries. This is no proof of full D1/provenance erasure and NO real provider send.
- `.github/workflows/sre-contracts.yml` Linux/Windows standalone plus PG17 application and negative paths, **CI unverified / QA [ ]**.

## Production blockers / next step

The full M34 task's words *“physically purge permitted content-derived embeddings, prompts, evidence, outputs, caches; no post-revoke text leak via API, export, analytics or logs”* are **only partially met**. Disposable SRE vector and S4 context deletion + SRE read/write suppression are implemented; immutable M30–M33 model/evidence/benchmark hashes and financial receipts still exist, base S3 raw content still exists behind suppression, logs/backups and non-SRE exports require a comprehensive data inventory and permitted delete/redaction plan with FK/finance custody. Do not label M34 production Done. Independent validation of all read/export/analytics paths on PG17 and external D1 callback is outstanding.

Next workflow stage once M34's remaining purge inventory is established: **M35 optional Collector inbox**. Because full M34 erasure is not accepted, any integration must retain the same irrevocable tombstone and no-restoration boundary; do not merge PR #22 or enable paid POST.
