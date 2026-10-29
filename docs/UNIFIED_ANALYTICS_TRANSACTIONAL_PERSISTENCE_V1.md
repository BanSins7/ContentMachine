# UA-MOD-15 — Transactional persistence and versioned UAE snapshots

**Module branch:** `feature/unified-analytics-full-module` only. **Goal:** make the UA14 historical `partial/blocked` snapshot writer concurrency-safe and prevent SQL-created fake `qualified` or `checked=true` approvals before the actual reviewed SRE/QDP/Group B/Group C producer protocols exist. `main` and all peer branches remain unchanged.

## Existing data ownership

The canonical UAE tables remain those created in immutable 080: `uae_qualification_requests`, `uae_unified_snapshots`, `uae_snapshot_evidence`, `uae_snapshot_invalidations`, `uae_audit_events`. Migration 081 enforces actual project/video membership, 082 owns only QDP handoff inbox/quarantine, 083 returns **candidate IDs only**, 084 adds an immutable reference-only JSON payload with a parent-scope guard. None of 080–084 is rewritten in this stage, preserving installed migration checksums. The existing UNIQUE constraints on `(project_id,request_key)`, `(project_id,request_id,inputs_sha256)` and `(project_id,video_id,source_id,snapshot_version)` remain authoritative.

## 1. Atomic save and concurrent replay

`ImmutableSnapshotRepository.assemble_and_save()` now refuses nested, non-IDLE psycopg sessions: each attempt must begin a **new** outer REPEATABLE READ transaction with new DB MVCC snapshot, current UTC DB-clock check, source-backed UAE12 joined selection and UAE13 qualification derivation. A previously selected/read observation is NEVER reused on retry. The original request row is locked, and transaction advisory lock serializes version assignment by project/video/source across different request UUIDs. It rechecks an exact (project,request,inputs_sha256) replay and loads the **actual 084 JSON** and **all seven 080 evidence rows**; only identical normalized JSON/checksums, ordered seven identities, `checked=false` and `is_current=false` can return the same snapshot ID/version. Missing 084 payload on a matching old 080 fingerprint triggers `snapshot_legacy_payload_missing` instead of inserting a duplicate.

PostgreSQL REPEATABLE READ freezes a snapshot at the first query; waiting on row/advisory locks does **not** guarantee an updated snapshot. Two concurrent writers can therefore hit SQLSTATE `40001` or the composite UNIQUE constraint even though the desired write already committed. The new bounded retry is **at most four transactions**, and it retries only:
- PostgreSQL `40001` serialization failure;
- `40P01` deadlock;
- `23505` only when the database-reported constraint is a `uae_unified_snapshots_*_key` unique index (same fingerprint/version race).

Any other source/policy failure, invalid request, SQL error or unrelated unique key is NOT retried. After four attempts return sanitized `snapshot_concurrency_exhausted`, with no partial transaction; do not silently emit a new snapshot or claim idempotency. No sleep or user-controlled repeated side effects are introduced.

The `REPLAY_ORPHAN_SQL` guard detects a pre-084/corrupted matching snapshot lacking 084 payload and refuses version-skipping or data fabrication. A distinct signed review/provenance fingerprint or changed observation time appends a **new historical version** only for an active request; resolved requests allow exact replay only. The SQL UPDATE immutability guards on snapshot, children and payload remain active.

## 2. New append-only 085/086 SQL integrity guards

### 085 — unapproved producer data can never be qualified by SQL

080 originally validated structural fields and a count of seven `complete/checked/current` evidence rows, but such flags and SHA-256 could be manufactured by a privileged SQL caller. The real effective human approval wire is not present in the audited Group B/Group C peers. Migration `085_unified_analytics_transaction_guards.sql` adds two **BEFORE INSERT fail-closed triggers**:

- Reject **every new `uae_unified_snapshots.status='qualified'`** with SQLSTATE `23514`.
- Reject every new `uae_snapshot_evidence` with `checked=true`, `is_current=true` or `state='complete'` until genuine external attestation checks exist.

This is intentionally stricter than 080: neither user-set flags nor seven synthetic references may unlock UAE publication. Future security-reviewed migration must explicitly replace these guards **only after** producer approval contracts and verification have been implemented. Existing 080 history is not rewritten.

The same 085 migration installs a DEFERRABLE INITIALLY DEFERRED PostgreSQL constraint trigger on **INSERT to 084 payloads**. At COMMIT, it requires EXACTLY seven persisted 080 evidence rows whose actual JSON values match all seven ordered reference objects embedded in that payload. Seven JSON descriptors without actual DB children are rejected, as are mismatched checked/current flags or invented SHA values. Rows inserted in any order inside a transaction are permitted as long as the *committed* matrix is complete.

### 086 — immutable payload cannot lose an evidence member later

An 084/085 historical payload that was complete on COMMIT could otherwise lose one evidence child through direct DELETE. `086_unified_analytics_payload_delete_integrity.sql` adds a deferred trigger after DELETE on the children: if the associated 084 payload still exists, the post-delete matrix must remain exactly seven and identical. A direct one-row DELETE therefore fails and rolls back. A legitimate parent snapshot retention/privacy purge cascades away the associated payload and children, so there is no residual 084 envelope and the trigger permits the whole-record purge. Historical 080 diagnostic snapshots with no 084 payload remain compatible. Neither migration exposes qualified data or gives public SELECT rights.

## 3. PostgreSQL 17 and Python regression suite

Existing UA14 tests were extended with 7 new mock cases covering: a first `40001`/deadlock or UAE-only unique collision retried in a new transaction and re-reading the source-backed join; unrelated `23505` not retried; retry exhaustion after exactly four attempts; pre-existing non-IDLE transaction denial; legacy 080 record without a payload rejected. The original UA04 schema fixture that previously considered seven forged checked children structurally sufficient was updated to assert **denial** under 085; old positive direct-SQL qualification behavior is no longer acceptable.

New `tests/test_unified_analytics_transactional_persistence.py` contains portable SQL static checks plus conditional disposable PostgreSQL 17 tests for:
- two concurrent writers to the same (project, request, inputs SHA) returning the **same immutable ID/version**, without duplicate rows;
- two different request UUIDs for the same project/video/source yielding distinct immutable IDs with sequential versions;
- exact replay and new version under changed as_of, all seven child records and payload SHA;
- an injected failure when writing 084 payload causing a full rollback of parent and all seven references;
- direct SQL `qualified` denial and direct inserted checked/current evidence denial despite plausible IDs;
- deferred 085 rejection of seven JSON-only descriptor entries without real persisted evidence;
- deferred 086 rejection of post-commit removal of one evidence member;
- read-only UPDATE immutability and whole-parent retention purge.

Fixtures seed **disposable** project/source/video/workspace/membership/QDP request rows under unique UUID-derived IDs, then purge their own rows. Positive synthetic joined selections in these tests are *deliberately unapproved* `partial` diagnostics, not authenticated live SRE, QDP, Group A/B/C data. A real PG17 pass can verify persistence invariants, but never substitutes for a LIVE cross-module approval E2E test.

Execute with:
```bash
python -m unittest discover -s tests -p 'test_unified_analytics_*.py' -v
python -m compileall -q intelligence/unified_analytics
```

With `CM_TEST_PG_DSN` set, run only against **PostgreSQL 17 disposable DB**, never the production `CM_DB_DSN`. GitHub Actions runs both pure tests and a PG17 service job; a queued job is NOT an executed PASS.

**Operational limitations:** current Group C GC057 reviews, Group B effective approvals, SRE LIVE settlement, QDP carousel A/B readiness, source-backed candidate enumeration and actual public/Patterns read gates remain unresolved upstream. This module does not install an HTTP handler, automatic worker, outbox, or public analytics export.

**Next UA-MOD-16:** source-backed Patterns consumer read contract and outbox identity, preserving current rights and positive approval blockers; verify the roadmap's exact text before implementation.
