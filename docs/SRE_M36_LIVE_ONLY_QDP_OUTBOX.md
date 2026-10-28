# SRE M36 — LIVE-only result outbox, invalidation and QDP contract kit

**Implementation committed on SRE feature branch (2026-10-28); QA on exact HEAD is NOT accepted. No real paid Opus POST. No changes to QDP, Collector, main, or another feature branch.** Draft [PR #22](https://github.com/BanSins7/ContentMachine/pull/22).

## Intent, data authority and scope

M36 is an **SRE-produced outbox**, not a direct QDP integration. It cannot make an offline/fake S4 classification a real observation. `sre.classifications_ready/v1` is generated only from a real M11 LIVE run with **M30 authenticated receipt/source-backed full C9 pair evidence** and the latest **M32 authenticated human decision** for each of the 1..48 approved category UUIDs of that video. Entire LIVE run must be `completed`; all items and batches must meet the preexisting M30 terminal gate; no partial, UNKNOWN, cancelled, unreviewed or failed run may publish. M08/D1 rights and approved C9 H1/H2 are rechecked in the same project-locked database transaction. Current S3 content must match original video, version, policy and SHA. No provider inference or billing is performed here.

One canonical result envelope is emitted **per video/item** of a genuinely completed LIVE run, using the preexisting strict `ports/outbound.py` version `sre.classifications_ready/v1`. It includes exact project/publication/run IDs, source/version SHA, complete sorted C9 hypothesis UUIDs and complete effective human-reviewed match/no_match/uncertain arrays, rights timestamp and content hash. It excludes source text, provider prompt/answer, receipt body, account balances, API keys and human free-form comments. `event_sha256` is canonical payload integrity, **not cryptographic sender authentication**; downstream must authenticate transport identity and check server rights.

## Forward-only SQL 070

`intelligence/migrations/070_sre_live_result_outbox.sql` creates:

- `sre_m36_live_reviewed_pairs` **read-time PostgreSQL view**: real `sre_live_verified_pair_results` + authenticated M28 receipt + latest M32 human review + M30 original C9 expected pair, completed M11 LIVE run and item, readable S3 content, no M34 tombstone. No S4 results or legacy tables are joined.
- `sre_live_result_outbox`: immutable source event `event_id`, payload and SHA, unique project/run/item ready and unique original-event invalidation; project and LIVE-item foreign keys; `pending → leased → acked` monotone ACK, expiry-based lease CAS with incrementing generation, strict owner/lease token validation, bounded attempt count. No secret or raw media stored.
- `sre_live_result_invalidation_intents`: project+ready UUID durable and immutable. SQL AFTER INSERT triggers on **M34 source tombstone** and **M32 revision>1 human supersession** create an intent immediately in the same transaction, blocking ready claims. There is no dependency on worker uptime.
- `sre_m36_live_only_export`: read-only effective per-category LIVE + latest-human-review projection; source rights and absence of invalidation are live predicates, not static claims that old ready events remain allowed. SQL trigger rejects direct fabricated ready with wrong project/C9/source, a non-completed LIVE run, incomplete reviewed M30 matrix, incorrect decision arrays or unsettled paid intent. SQL UPDATE guard prevents payload edits, forged ACK and lease downgrade. PUBLIC grants are revoked.

**Critical ordering:** an invalidation intent blocks ready outbox claiming/acknowledgment immediately; a trusted worker later materializes a versioned immutable `sre.results_invalidated/v1` envelope linked to exact earlier ready event UUID+SHA and stores it in the same outbox. If a downstream consumer receives invalidation *before* the old ready event, it MUST retain a durable invalidated prior-ID tombstone; never resurrect a late ready event. At-least-once delivery is intentional; no "delivered exactly once" network claim.

## Trusted host operations

`intelligence/relevance/live_result_outbox.py` (no QDP module import):

1. `publish_live_sre_result(conn,project,run_id,item_id,actor_id=...,actor_authorizer=...)` → one ready receipt per approved LIVE video, idempotent replay; new RUN required after old publication invalidation.
2. `request_sre_result_invalidation(conn,project,ready_event_id,reason_code,actor_id=...,actor_authorizer=...)` → separately authenticated request; `rights_lost` requires a **real D1 denial**, `source_revoked` requires an M34 tombstone, `review_superseded` requires M32 revision>1, `operator_revoked` requires trusted operator permission. A durable intent immediately suppresses ready distribution.
3. `materialize_sre_invalidation_intents(...,limit<=100)` → checks original immutable ready SHA, emits a versioned, hash-bound invalidation without resurrecting rights or calling QDP.
4. `claim_sre_result_event(...)` → atomic project-authorized 60-second PostgreSQL lease with DB lease token, generation and renewed M08/D1+C9/M30/M32 gate. Invalidations are prioritized. A claim is **not** a delivery receipt.
5. `acknowledge_sre_result_event(...event_id,token,generation,sha...)` → CAS ACK only for the same project, still-valid lease and same original SHA. A ready ACK after tombstone/intent or lost source readability fails closed. No refund or provider retry.

All entrypoints require an idle autocommit PostgreSQL connection, a trusted host actor and project-bound permission; no user-provided hash, project ID or JSON is authority. Host must perform transport authentication, persist downstream delivery receipts, call the worker explicitly and recheck rights immediately before the consumer applies results. Concurrent external D1 revocation is not magically fenced by an SRE workspace lock; SQL view and invalidation callbacks are mandatory.

`ports/qdp_reference.py` defines the strict `sre.results_invalidated/v1` format and a pure reference consumer with persistent `seen`, `ready`, `invalidated` state. It supports idempotent duplicates, changed SHA collision denial, tombstone-before-ready and late-ready rejection. This is **not QDP code**; the owning QDP branch must implement its own transaction-safe LIVE-only gate and replay semantics. No cross-module integration is claimed before M37.

## Tests and blockers

Authored `tests/test_sre_live_result_outbox.py` pure host/consumer tests for no-fake, full C9+M30/M32 gate, duplicate/collision, invalidation first, no late resurrect, stable hash and owner permission. Authored `tests/test_sre_live_result_outbox_schema.py` static and disposable PG17 negative checks; updated migration preflight and Linux/Windows SRE CI. Exact-head outcomes **PENDING / NOT PASS** until actually observed.

**Known independently confirmed upstream SRE-migration blocker:** `intelligence/migrations/062_sre_live_opus_receipts.sql` currently contains malformed SQL text around `evidence_sha256`: the CHECK regex is truncated, and `input_tokens` declaration is missing. A clean database cannot reach forward-only 070 until this is reconciled safely. It was **not silently rewritten**, because an already-applied schema-migration checksum must not be invalidated without checking real database deployments. Dedicated migration reconciliation/PG17 clean-install/upgrade is required before declaring any M36 SQL PASS. This blocker was found during M36 read and is not fixed by writing 070.

M34 full physical erasure and downstream D1/backup/third-party deletion remain independently BLOCKED. M35 actual producer Collector Stage10 E2E remains BLOCKED. M16–M18 genuine pretrained CPU ONNX and M25/M26 true paid full-multi-request C9 flow remain BLOCKED. No paid POST or merge.

**M36 code [x]; exact-head QA [ ]; true LIVE→QDP positive production E2E [ ] BLOCKED. Next code stage M37 (cross-branch compatibility) only after recording these limits; neither M34 nor M35 is silently closed.**
