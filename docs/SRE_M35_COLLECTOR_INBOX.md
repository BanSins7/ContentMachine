# SRE M35 — Optional Collector inbox, real D1 provenance, immutable ACK/quarantine

**Status (2026-10-28): implementation committed in `feature/sre-production-v1-20261023`; exact-head Linux/Windows/PG17 CI success NOT established. Collector producer-side delivery and full ecosystem E2E NOT demonstrated. No paid provider POST.** [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22). M34 full physical retention/erasure remains independently BLOCKED and is not declared complete.

## Contract and trust boundary

M35 accepts only `sre.discovery_ready/v1` from the existing pure `ports/inbound.py` contract. Exact keys include immutable `event_id` UUID, `producer=video_collector`, project ID, D1 BIGINT `video_id/discovery_id/source_policy_version_id`, source ID, optional D1 BIGINT `source_request_id`, **Collector UUID-only** `collector_query_ids`, two timezone-aware UTC-compatible timestamps and a canonical `event_sha256` fingerprint. The digest detects accidental or malicious alteration **but is NOT a digital signature or authentication**.

There is no network listener or webhook in M35. A trusted CSRF-protected host MUST independently authenticate the caller, current project owner and accepted producer, then supply `actor_id` and `actor_authorizer(project,actor,permission)` as server-only functions. Never accept the project or actor asserted in the request as evidence of ownership. M35 requires exact `receive_sre_collector_delivery` or `backfill_sre_d1` permission respectively. Failed host authorization causes no D1 or SRE reads/writes. M35 is importable without Collector, QDP, Analytics, provider credentials, background workers or externally installed services.

## Transaction and idempotency

`intelligence/relevance/collector_inbox.py` has:

1. `receive_collector_delivery(conn,delivery,approved_publication_id,*,actor_id,actor_authorizer)`: validates schema and digest, requires an **idle autocommit PostgreSQL connection**, begins REPEATABLE READ, locks the exact existing SRE project workspace, verifies exact C9 RTB two-human-approved publication and SRE mirrored taxonomy in the same database snapshot, and checks the real `discoveries/sources/source_policy_versions/source_requests` D1 lineage against the event pins. It performs the existing `intake.attach_discovery` on a PostgreSQL SAVEPOINT, re-verifies the resulting S3 revision with `read_d1_provenance` and atomically inserts a single immutable ACK. Denied source-policy, missing D1 row, expired source or mismatched provenance instead roll back the inner attach and persist a minimal non-content quarantine receipt. Unexpected database failures are not converted to a success or synthetic quarantine: the outer transaction aborts.
2. `backfill_selected_d1_discoveries(conn,project,approved_publication_id,discovery_ids,*,actor_id,actor_authorizer)`: requires exactly 1..100 explicit, unique real D1 positive BIGINT IDs and an approved C9 publication; performs deterministic D1-to-S3 import under the same workspace lock. Each imported item is checked against the genuine database; no global D1 catalog scan or automatic cross-project attach. S3 fingerprint dedup makes repeated selected backfills idempotent.

The inbox primary key is the global event UUID. Replay of the **same ID and the same exact event SHA, project, publication, discovery, video, source, policy, request** reads the immutable receipt and cannot repeat S3 attachment. Any changed pin on the same ID fails closed. For an old ACK, `readable_now` is freshly determined by the authoritative D1/S3 reader: a previously ACKed source can be revoked or expired and remains unreadable. A quarantined event remains quarantined on replay; a corrected new source event requires a new event ID. Collision or retry exceptions under concurrent clients should be retried by the trusted host with the *identical* event (not a newly invented event ID); exact concurrency acceptance remains unverified.

`d1_persisted_at` and `occurred_at` are validated transport timestamps only; they **do not prove** a D1 source actually exists. The authoritative evidence is real D1 PostgreSQL and its current policy. Collector textual query UUIDs are validated/sorted only; they are **never converted to or queried as** D1 `search_queries.id BIGINT`. A no-Collector direct D1 backfill remains first-class.

## Database migration 069

New additive forward-only `intelligence/migrations/069_sre_collector_inbox.sql` creates project-scoped `sre_collector_inbox` with event UUID PK, data digests, C9/D1 source references and `acked/quarantined` statuses. A PostgreSQL trigger refuses updates and deletes, and direct SQL ACK unless a real project-associated S3 content revision, exact D1 discovery/request, an approved C9 link, current reviewed D1 policy and no M34 source tombstone match its immutable pin. Quarantine contains no raw text, prompt, transcript, tokens, metadata body, secret or user-supplied free-form reason. `PUBLIC` table/function grants are revoked. Existing SQL versions, including historical checksum-locked 016/017, were not rewritten; migration preflight now requires 069 and the next prefix is 070.

**Retention caveat:** immutable ACK receipt proves historical ingestion at the time of the write, not continued source rights or full physical source erasure. This source-ID receipt may still be present after M34 revocation. Deletion/retention governance for this small immutable event ledger is a separate policy decision; do not claim M34 completion.

## QA and acceptance evidence

Authored tests:
- `tests/test_sre_collector_inbox.py`: deny untrusted actor/noncommittable connection, SHA/payload tamper, exact D1+approved C9 ACK, inner-savepoint quarantine, wrong provenance, event replay and replay after rights revocation, collision denial, explicit backfill without Collector and Collector UUID isolation.
- `tests/test_sre_collector_inbox_schema.py`: migration, SQL forged ACK/UPDATE/DELETE denial, project link, migration prefix; optional disposable PG17 tests.
- `tests/test_sre_collector_inbox_pg.py`: disposable PostgreSQL17 using genuine two-human-approved C9 and real D1 discovery; atomic ingestion and replay, exact one S3 revision, direct D1 backfill, unauthorized actor and nonexistent D1 quarantine.
- `.github/workflows/sre-contracts.yml`: Linux+Windows offline contracts and PG17 M35 schema + actual C9/D1 test jobs wired.

Operator commands (run on an exact code SHA; dedicated **disposable** PostgreSQL17 with `CM_TEST_PG_DSN`, never a live database):

```bash
python -m unittest discover -s tests -p test_sre_collector_inbox.py -v
python -m unittest discover -s tests -p test_sre_collector_inbox_schema.py -v
python -m unittest discover -s tests -p test_sre_migration_guard.py -v
CM_TEST_PG_DSN='postgresql://...disposable...' python -m unittest discover -s tests -p test_sre_collector_inbox_pg.py -v
```

**Implementation [x]; exact-head Linux/Windows/PG17 PASS [ ]; independently authenticated real Collector Stage10 producer E2E [ ] BLOCKED until Collector owns and implements its emitting side.** M35 does not modify Collector, D1, RTB, Search Planner, QDP or main branches and does not trigger external inference or billing. Readiness for M36 is **source implementation only**, not a full module release; M16–M18 pretrained ONNX tests, M25/M26 real multi-request billing, and M34 full physical erasure remain independently blocked.
