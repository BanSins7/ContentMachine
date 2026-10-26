# Content Machine QDP — independent module integration guide

**Branch:** `feature/qualified-data-pipeline-v1-20261023`, PR #24. QDP is independently developed, not a container for other modules. This guide is the entry point for future integration owners. See `QDP_IMPLEMENTATION_TRACKER.md`, `QDP_DEPENDENCY_MATRIX.md`, `QDP_EVENT_AND_ID_CONTRACTS.md`, and `QDP_MIGRATION_RECONCILIATION.md`.

## Current implementation boundary

Baseline QDP provides read-only preview, explicit run creation, deterministic preliminary qualified-set selection, D1 enrichment, A/B/C preparation rows, namespaced PostgreSQL worker, local API/UI. It is NOT presently production live-safe because strict LIVE SRE ingestion needs peer migration integration and some source/read gates require hardening. Never run it against synthetic/offline SRE data as if production.

FIX-00 adds typed QDP-owned ports in `intelligence/qualified_data/integrations/contracts.py`. Ports do not connect to peer implementations yet. Real adapters and actual publication/manifest guards are delivered in later FIX stages.

## Plugging in a completed SRE

SRE owner supplies: project-scoped completed run with `execution_mode='live'`, immutable taxonomy publication/category keys, content×hypothesis expected manifest, evidence and effective human label revisions, validated event producer. QDP adapter reads DB-authoritative refs and rejects missing/corrupt coverage. Importing a standalone fake fixture does not make SRE integration complete.

## Plugging in a completed Collector/D1

Collector commits canonical `videos.id`, discovery/source request, exact metadata observation and policy version. QDP never writes external source rows. It resolves exact SRE content observation metadata and compatible metrics/media, no cross-source guessed fallback. Any missing source relation gets an explicit blocker.

## Plugging in Group A/B/C

QDP commits independently gated `qdp-preparation/v1` group rows. Outbox dispatcher sends versioned pointer; consumer atomically records inbox receipt+job (and rechecks current eligibility). Group A can adapt to PR #26 `create_task` after exact API/schema compatibility is verified. B/C do not become successful simply because they ACK. External provider budget and execution remain owned by consumers.

## Plugging in Analytics

Analytics must consume independently completed, verified A/B/C artifacts with matching source/project/version, not QDP readiness flags or delivery ACK alone.

## Checklist for a future integration chat

1. Read this guide, tracker and dependency matrix; read real peer head SHA, not old claims.
2. Compare peer schema and producer/consumer payload with QDP proposed ports; record differences.
3. Implement only QDP-owned adapter shims in this branch or controlled integration commit; avoid importing peer source until actual integration ref is stable.
4. Reconcile migrations in integration branch/owner; do not rename applied SQL.
5. Run unit, isolated contract tests and disposable PG17 cross-module integration.
6. Run an explicit real LIVE smoke only with approved provider budget; never run it from GET or default CI.
7. Review API/UI, currentness/invalidation, outbox ACK and analytics actual completed gates.
8. Record SHA, migration inventory, CI links, blockers and owner approval before main merge.

## Local verification

`python -m unittest tests.test_qualified_data_pipeline tests.test_qdp_integration_contracts -v`

`CM_TEST_PG_DSN=<disposable PG17 URL> python -m unittest tests.test_qualified_data_db -v`

`python -m compileall -q intelligence/qualified_data`

`node --check site/qualified-data.js`

Use the dedicated `.github/workflows/qdp-tests.yml` for isolated PostgreSQL 17. Do not run migrations or destructive DB tests against production data. For current baseline CI evidence see tracker.


## FIX-01 SRE compatibility (added)

SRE run intake now **requires** an actual `execution_mode` column from the SRE owner. Without that column, preview/create return 503 `sre_live_schema_unavailable`, never attempt legacy fallback. With that column, only `completed` + `live` + approved project taxonomy and non-fixture resolved model metadata may proceed; tests simulate the column in disposable PG until real SRE branch integration. The QDP port is `intelligence/qualified_data/intake.py`. Real semantic model attestation and full video/leaf coverage still require FIX-02 and a completed real SRE integration.


## FIX-03 QDP schema integration

Add `040_qdp_integration_foundation.sql` after existing original `016_qualified_data_pipeline.sql` without changing its hash. The 040 schema adds trusted event inbox, full-result lineage, immutable manifests, delivery/outbox/receipts, and invalidation records. See `QDP_MIGRATION_RECONCILIATION.md`. To validate: `python -m unittest tests.test_qdp_schema_migrations -v`; with disposable `CM_TEST_PG_DSN` the test suite also checks schema checksums and constraints.

Importing a future SRE module must first install its original versioned migrations (not a QDP-crafted copy); before processing live data call `intelligence.qualified_data.schema.require_qdp_schema(conn)` plus `intake.require_sre_live_schema(conn)`. Both fail closed. No QDP table implies a real external SRE/Group A/Analytics executor is available.


## FIX-04 qualified set and human review fingerprint

The strict QDP-owned semantic resolver is `intelligence.qualified_data.qualification.resolve_qualified_set(rows, strict=True)`. It never calls a provider and MUST receive already authenticated completed LIVE SRE rows. It emits one state per canonical D1 video and sorted matched category/result IDs, plus a reproducible `selection_sha256` and `review_watermark`. Current LIVE SRE runs must supply unchanged SRE dataset manifest/content hashes and authoritative current human label IDs/revision times. A human veto of H1 leaves H2 eligible if H2 still matches. Updating a human label changes the selection fingerprint, invalidating replay of a previous idempotency key. Pre-existing v1 QDP runs without pinned review fingerprint are not silently upgraded; consumers will receive an explicit idempotency conflict for stale replay. Full durable run manifests and invalidation propagation are FIX-06/FIX-15.


## FIX-05 internal SRE completion-event adapter

Use `intelligence.qualified_data.sre_inbox.receive_sre_completion(payload, announced_sha256)` from an authenticated internal SRE transport **only**. See full code-level ownership, schema, ack, failure modes, migration preflight, and tests in [QDP_SRE_INBOX_HANDOFF.md](QDP_SRE_INBOX_HANDOFF.md). QDP strictly supports `sre-analytics-event/v1` `sre.classification_run_completed` and verifies run publication/taxonomy/coverage from DB; it does NOT accept `sre.classifications_ready/v1` item completion as whole-run authority. Durable tables `qdp_sre_inbox` and `qdp_sre_inbox_quarantine` require QDP SQL 040+041. Same event/hash ACK replay; changed hash append audit/no ACK; 503 schema missing is retriable. Upstream SRE owns committing its delivery ACK after successful QDP return. No automatic QDP run/jobs/LLM.


## FIX-06 immutable run manifest contract

`intelligence.qualified_data.run_manifest.build_run_manifest(project, sre_run_id, source, verified_selection, config)` builds a deterministic `qdp-run-manifest/v1` with SHA256 and selected video/leaf/source IDs and per-video hashes. `repository.create_run` inserts immutable `qdp_run_manifests` and ALL SRE result lineage inside its existing project-lock transaction, followed by one job for each qualified video. An empty qualified set creates a completed run with manifest/negative lineage and zero jobs. Worker revalidates per-video `selection_sha256`. Audit helper: `read_run_manifest(conn, project, run_id)` verifies its stored checksum; `read_pinned_video(...)` is O(1) JSONB key lookup. Before production use, combined-schema PostgreSQL 17 and SRE LIVE producer tests must pass.

## FIX-07 exact SRE → D1 metadata provenance

Module `intelligence.qualified_data.provenance.read_exact_metadata(conn, project, video_id, pinned)` returns `(metadata, None)` or `(None, stable_reason_code)`. This is QDP's only authorized metadata enrichment path. It never chooses latest `source_video_metadata` by video. It requires the precise SRE S3 `content_version_id`, `content_sha256`, `source_id`, `source_policy_version_id`, `metadata_observation_id`, and `provenance.discovery_id` from QDP's pinned manifest, plus a same-project row in `sre_project_discoveries`. It rechecks SRE-readable content, exact D1 observation and active rights. Missing SRE S3 migration produces explicit `sre_metadata_contract_unavailable`; no fallback to legacy/latest. Tests and full peer field mapping: [QDP_METADATA_PROVENANCE_HANDOFF.md](QDP_METADATA_PROVENANCE_HANDOFF.md).


## FIX-08 D1 metric history / Group C handoff

See [QDP_METRIC_HISTORY_HANDOFF.md](QDP_METRIC_HISTORY_HANDOFF.md). The read-only `intelligence.qualified_data.metric_history.read_metric_history(conn, metadata)` queries at most 64 source/policy-scoped D1 metric observations. The pure selector `resolve_metric_history(rows, metadata)` produces `qdp-metric-history/v1` with raw nullable measured history, `history_sha256`, chosen `growth_pair_ids`, compatible metric definition/version, and specific reason code when C cannot receive a pair. `qdp_preparations[C].inputs` carries only original D1 IDs, not invented growth/score. Actual Group C must revalidate freshness/revocation and calculate its own result. A/B readiness remains independent.


## FIX-09 strict real media asset readiness

QDP `media_readiness.read_verified_media(conn,metadata)` requires existing exact video/source D1 media, current media rights, approved local root, content header and SHA-256. No local path is included in QDP output. Group A/B video preparation is separate from Group C metric eligibility. No automatic media acquisition or playback validation. Carousel Group A is unsupported by video A1; Group B needs an independent verified slide manifest. Contract, failure reasons, root configuration and QA: [QDP_MEDIA_READINESS_HANDOFF.md](QDP_MEDIA_READINESS_HANDOFF.md).


## FIX-10 project-scoped independent preparation

`intelligence.qualified_data.preparation.build_preparations(metadata,pinned,media,metrics,...)` creates `qdp-preparation/v2` immutable A/B/C separately with group-scoped input keys and SHA. `read_current_preparation(project,run_id,video_id,group)` checks `qdp_group_current_preparations`, immutable SHA, current LIVE SRE snapshot, exact D1 metadata and media file or metric source/config. A policy revocation hides only its own group; historical facts are not silently changed. `get_run` uses `qdp_current_base_enrichments` for legal D1 metadata and separate group view for displayed readiness. Migrations 042 (new views) + 043 (forward-only metadata status filter correction) mandatory. Never rewrite applied migrations or claim QDP read authorizes paid execution; peers recheck.


## FIX-11 fenced worker / atomic outbox

`intelligence.qualified_data.worker_hardening` implements bounded retry, exhausted lease reaper, atomic QDP preparation event persistence. QDP Worker claims ONLY `qdp.prepare_video` with `FOR UPDATE SKIP LOCKED`; commits completion only while lease generation/owner/unexpired time still match. It writes immutable enrichment + 3 A/B/C group rows + group outbox facts + ready-group pending delivery records **in one transaction**. A failed transaction produces no partial work; a completed job is not replayed. `qdp_outbox` pending delivery is not an ACK and does not start any external task: that is FIX-12. Verified 180/180 PG17 on SHA `3b623e1b`. [QDP_WORKER_HARDENING_HANDOFF.md](QDP_WORKER_HARDENING_HANDOFF.md).


## FIX-12 durable QDP outbox dispatcher

`intelligence.qualified_data.outbox_delivery.dispatch_once(consumer,receiver=...,connect=...)` requires an explicit durable receiving adapter for `group_a`, `group_b` or `group_c`; if absent it fails closed. It leases one ready event using `SKIP LOCKED` and validates the committed `qdp-event/v1` hash, canonical event ID, exact project/run/video/group and `read_current_preparation` (including up-to-date source/media/metric rights). Only `QDPHandoffPointer` + payload SHA are passed, no raw media/paid work. Receiver must transactionally commit its own inbox+idempotent job and return a `state='committed'` receipt before QDP commits `qdp_delivery_receipts` and ACK. Per-consumer retries, dead letters, generation fencing, and immutable journal (migrations 044/045) protect crash/replay. Ready QDP preparation is not Group completion. See [QDP_OUTBOX_DELIVERY_HANDOFF.md](QDP_OUTBOX_DELIVERY_HANDOFF.md).


## FIX-13 real Group A durable intake opt-in

QDP operator configures `from intelligence.qualified_data.integrations.group_a import from_installed_group_a`; constructs `adapter=from_installed_group_a(ga_connect,media_roots=[root],enabled=True)` **only after** Group A package, GA migrations 054/055, the current QDP schema and a shared authorized local media root are installed. `ga_connect` must return GA's expected *tuple-row* psycopg connection; `dispatch_once('group_a',receiver=adapter,connect=qdp_connect)` uses QDP's own dict-row connection factory. Adapter emits separately signed GA `qdp.preparation_ready/v1`, pins original QDP `qdp-event/v1` digest into optional `event_payload_sha256`, and translates real `group-a-receipt/v1 state=recorded` to QDP `committed` only after GA returns a valid run and step identity. Group A itself owns all GA inbox and initial step INSERTs and media catalog checks; QDP never creates GA tasks directly. Tested with actual GA code in [CI 40321549312](https://github.com/BanSins7/ContentMachine/actions/runs/40321549312). Details: [QDP_GROUP_A_HANDOFF.md](QDP_GROUP_A_HANDOFF.md).


## FIX-14 independently validated Group C vs blocked Group B/UAE

`from intelligence.qualified_data.integrations.group_c import from_installed_group_c` returns an opt-in real consumer only if the Group C package is installed, autocommit PostgreSQL factory provided, and `enabled=True`. `dispatch_once('group_c',receiver=from_installed_group_c(c_connect,enabled=True),connect=qdp_connect)` forwards validated `qdp-handoff/v1`, retains original QDP outbox digest; C itself commits `group_c_inbox + group_c_runs + group_c_jobs` in its transaction; only its verified post-commit `ack=true/run_id/job_id` becomes QDP `committed` receipt. The result is a **queued score job**, never finished C7. Real C consumer tested in [CI 40324710488](https://github.com/BanSins7/ContentMachine/actions/runs/40324710488) on pinned SHA `37d78b76` with D1 current rights. `GroupBConsumerAdapter` detects legacy peer contract mismatch and fails closed with 503; do not install a fake 'compatibility bridge' rewriting immutable QDP outbox. `UAECompletedResults` requires a future authoritative reader with all completed Group A/B/C and active rights; no QDP readiness/ACK becomes qualified analytics. See [QDP_FIX14_PEER_COMPATIBILITY.md](QDP_FIX14_PEER_COMPATIBILITY.md).


## FIX-15 human/source invalidation

Trusted source hooks use `invalidation.invalidate_run(project,run_id,origin,reason_code,provenance_sha256,video_id=?,group=?)` and explicit `reconcile_current()`. 046 run generation fences ACK; 047 current SQL view immediately rejects changed human labels. Pending/leased ready deliveries become dead-letter; a new signed `preparation_invalidated` outbox + delivery row is created per affected previously ready Group, with independently durable peer `revoke()` receipt required to ACK. Missing peer revoke port fails closed; no guarantee accepted Group jobs stopped. `explicit_refresh` always creates a different immutable QDP run, never resets old verdicts. See [QDP_FIX15_INVALIDATION_HANDOFF.md](QDP_FIX15_INVALIDATION_HANDOFF.md).


## FIX-16 local QDP Studio and public API compatibility

The QDP localhost app has `GET /api/projects/:project/qualified-data/eligible-sre-runs` for completed LIVE approved SRE run picker (schema must be present; no offline/legacy fallback), `preview?sre_run_id` for read-only qualification, `POST runs` for explicit idempotent free QDP run, `GET runs` and `GET runs/:id` with keyset cursor options, read-only `runs/:id/events`, `diagnostics`, `manifest`, `lineage?video_id` with CURRENT project/source/group human-rights gates. `POST runs/:id/refresh` accepts a distinct idempotency key+config and creates a NEW immutable run; `POST runs/:id/cancel` remains local-only. The parent `app.py` Host/Origin/X-ContentMachine authorization gate is unchanged; QDP has no remote login or paid-work API. The old bare `GET runs/:id` still defaults to 100 video items. Acknowledged delivery is not finished analysis; unknown active peer A/B/C analysis output is not displayed as completed. See [QDP_FIX16_STUDIO_HANDOFF.md](QDP_FIX16_STUDIO_HANDOFF.md).
