# GB-MOD-04 — QDP → Group B durable inbox handshake (producer status: NOT VERIFIED)

**Owner:** `intelligence/group_b` only. Branch: `feature/group-b-full-module`. No changes to QDP, Group A, existing migrations 005/055, SRE or Analytics.

## Upstream contract actually inspected

At stage entry, QDP branch `feature/qualified-data-pipeline-v1-20261023` @ `7a48245dcc45176116ee94beb7edbb159e29d2f3` defines `QDPHandoffPointer` in `intelligence/qualified_data/integrations/contracts.py`. Its fields are exactly:

```json
{
  "event_id": "canonical-uuid",
  "project_id": "32-lowercase-hex",
  "qdp_run_id": "canonical-uuid",
  "video_id": 123,
  "analysis_group": "B",
  "input_sha256": "64-lowercase-hex",
  "schema_version": "qdp-handoff/v1"
}
```

QDP owns the proposed `qdp_outbox` (schema `qdp-event/v1`, `event_type='preparation_ready'`, `payload_sha256`) and `qdp_outbox_deliveries` (`consumer='group_b'`) in its SQL 040. Its SQL 016 owns `qdp_runs`, `qdp_items`, `qdp_enrichments`, `qdp_preparations`. **A deployed QDP producer/dispatcher sending this exact pointer-shaped JSON payload to Group B has NOT been verified.** In the inspected QDP Python repository/worker, no invocation of `qdp_outbox`/Group B delivery was found. This integration is strictly **CONSUMER IMPLEMENTED / LIVE HANDSHAKE BLOCKED** until FIX-12 and a cross-branch end-to-end test prove it.

The Group B adapter does not import QDP, duplicate its migrations, create fake QDP events, or claim a schema is present. Missing peer tables, old layout, wrong version, no outbox delivery, wrong project or invalidation return typed `dependency_unavailable`.

## Group B consumer

- `intelligence/group_b/adapters/qdp.py::QDPHandoff.parse` accepts only the seven frozen fields, exact B group, canonical project/UUID/D1 video ID/input SHA. No conversion of C hypothesis/source-node IDs, no URL-to-video guesses, no `image_post`.
- `read_current_preparation` re-reads the authoritative QDP `qdp_outbox` event, Group B delivery, completed/running/partial non-cancelled run, `qdp_items` readiness, matching unrevoked `qdp_preparations[B]` exact input SHA, pinned source/policy/metadata/media and D1 rights/expiry. Event `payload_sha256` is compared to the caller and the canonical event payload; preparation hash is independently recomputed.
- `intelligence/group_b/intake.py::receive_qdp_delivery(connection_factory, payload, announced_event_sha256, acknowledger=...)` owns its connection and transaction; it creates or reuses an inbox row, an **inert `draft`** Group B job with no A analysis, 8 `not_run` block statuses and an immutable receipt in one commit.
- After connection exit / successful outermost commit **only**, the optional `acknowledger.acknowledge(event_id=..., durable_receipt=...)` is called. With no installed acknowledger, the returned receipt has `acknowledged=False`; upstream delivery is NOT considered acknowledged. If remote ACK fails after commit, local state is retained; same-event replay reuses the receipt and retries ACK idempotently.
- Same event+hash+preparation+source is idempotent. The same event with conflicting event/preparation SHA or identity is written to append-only `group_b_inbox_conflicts`, commits the audit, then raises a stable error **without ACK**. A malformed first event is rejected before any durable receipt. No side-effectful model or worker is started.

## Schema ownership

`056_group_b_qdp_receipts.sql` adds only `group_b_inbox_receipts` and `group_b_inbox_conflicts`, their project-scoped FKs, indexes and immutable triggers. `055_group_b_runtime.sql` and historical `005_content_features_b.sql` remain byte-for-byte unchanged. Inbound processing requires both local migrations' recorded SHA256s before accepting any QDP event. The database does not gain a cross-feature FK into absent QDP tables; identity is established by the authoritative reader and frozen Group B pointers.

## Security and readiness truth

A QDP B-ready pointer means **prepared input only**. It does NOT mean completed Group A, a verifiable frame/word/slide manifest, positive B labels, approved topic registry, a paid model authorization, or a qualified Analytics result. For `photo_carousel`/`mixed_carousel`, the current QDP stream lacks a verified per-slide B manifest; this intake intentionally reports `carousel_media_manifest_unavailable` rather than using one media asset as evidence. Group A exact snapshot binding is GB-MOD-05; media rehash/rights/read-time checks are GB-MOD-06/07; semantic execution/charged retries require GB-MOD-09/10/11.

Source/policy/media rows are locked `FOR SHARE` during local durable commit; QDP invalidation inserts can still race immediately after a read. This is NOT a durable authorization grant, and every downstream execution/read must check current source and invalidations again.

Inbound transport must be authenticated by the containing service: JSON+SHA+DB re-read does not authenticate the network caller. HTTP API and access controls are deferred to GB-MOD-26; do not expose this low-level function on an unauthenticated route.

## QA: exact scope

- `tests/test_group_b_qdp.py`: pure negative payload/signed-hash tests, QDP SQL lookup mocks, blocked/expired/mismatched evidence, commit-before-ACK ordering, sanitized ACK failure, fail-closed missing peer.
- `tests/test_group_b_qdp_db.py`: actual Group B PostgreSQL 17 tables on a disposable test DB, while only the **upstream QDP reader is mocked**. Covers atomic durable receipt/draft/8 statuses, same-event replay, changed-SHA quarantine, policy revocation rollback, identity substitution and remote-ACK failure.
- `tests/test_group_b_db.py`: 055/056 migration sequence/static and PG17 installer checks. All schema tests must run without production DSN; no paid model or media file is used.
- `.github/workflows/group-b-module.yml`: Linux/Windows pure and PostgreSQL17 tests.

**Not yet tested / external:** real QDP producer payload, leased outbox dispatcher and QDP ACK persistence, full QDP/SRE/Group A schema co-install, carousel per-slide manifests, first-install DB concurrency, final production access/invalidation E2E. None of these may be declared PASS based on mocks or SQL existence.

## Continuation

Next GB-MOD-05 will implement the version-exact Group A adapter. QDP FIX-12 producer integration remains an external handshake dependency; pin its latest SHA before the first real event is dispatched.
