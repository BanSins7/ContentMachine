# UA-MOD-16 — Revision inbox/outbox and immutable invalidation lifecycle

**Canonical UAE branch:** `feature/unified-analytics-full-module`. **Python:** `intelligence/unified_analytics/revision_events.py`. **Forward-only migration:** `intelligence/migrations/087_unified_analytics_revision_events.sql`. **Tests:** `tests/test_unified_analytics_revision_events.py` plus disposable PostgreSQL 17 integration in `tests/test_unified_analytics_transactional_persistence.py`. No main, legacy PR23 or peer feature-branch changes. Existing UAE migration 080–086 SHA/checksums stay intact.

## 1. Audited producer contracts and supported events

Actual upstream peers were inspected independently, not inferred from enum names. This stage supports six peer/source revision forms, one source TTL sweep and the additional explicit GC058 rejected-review revocation:

| Event kind | Authoritative read-side record | Trust/activation |
|---|---|---|
| `source_policy_revoked` | Canonical `sources` + exact `source_policy_versions` + `sre_project_videos` | Accept **only when current source/policy rights are actually revoked** and source/policy/permission digest matches DB state |
| `source_retention_expired` | Same canonical source/policy + positive approved derived retention | Expire only historical snapshots past the min(source, derived) deadline; actual media/metric expiry can be earlier and is still checked by UA11 |
| `qdp_invalidated` | QDP feature migration 040 `qdp_invalidations` (id, run UUID, project, optional video, provenance SHA, created_at) | Missing/unmerged peer table -> UNAVAILABLE, not accepted |
| `sre_taxonomy_approved` | MAIN `sre_taxonomy_versions` + `sre_workspaces.active_taxonomy_id` | Exactly current **approved taxonomy**, its version/checksum and approved_at. This is an invalidation signal, NOT proof of LIVE classifications |
| `group_a_result_ready` | GA feature `ga_outbox` + `ga_runs` + completed `ga_results` | Exact run/video/source/policy, outbox payload SHA and published result SHA; invalidate older pinned DIFFERENT A result, not a new Group B approval |
| `group_b_reviewed` | GB feature `group_b_review_events` + `group_b_runs` | Immutable ID/revision/decision SHA and exact project/video/source/policy; invalidates older matching B results, NOT proof review was approved |
| `group_c_revised` | GC feature `group_c_revisions` + `group_c_runs` | Exact UUID, numeric revision, SHA and source/policy/video; correction/config change/revocation invalidates the previous score ID, or earlier C observations when previous ID absent |
| `group_c_review_rejected` | GC feature 058 `group_c_review_decisions` joined to exact `group_c_review_requests` | Only a durable **rejected** human review, matching same project/video/source/policy and calculation_config_id; invalidates older C lineage pinned to that configuration, never treated as positive approval |

**Observed newer Group C peer:** its branch now also contains `058_group_c_human_reviews.sql` and `059_group_c_config_activation.sql` (authenticated human review and config activation). Their existence is NOT by itself an independently verified, currently approved complete Group C score in UAE main. UAE does not copy or fabricate such records; the effective Group C read port still blocks qualification until a reviewed approved score contract is verified on the same exact rows. Peer migrations in standalone UAE baseline are not silently assumed installed.

A `RevisionEvent` is an untrusted *locator* until `RevisionEventProcessor` independently resolves the matching producer record on the **same PostgreSQL connection**. All source-backed fields must match: project/video/source/policy, the exact producer record ID, immutable revision number, evidence SHA, recorded timestamp and (for QDP) specific QDP run. No trust based only on event JSON, claimed `verified=True`, event_id or a SHA supplied by the caller. Producer schema absent or query failing -> typed `unavailable/revision_producer_unavailable`; mismatched row -> `unverified/revision_source_not_authoritative` with **no inbox/invalidation/outbox write**. SRE taxonomy approval signals *staleness*, not SRE LIVE result approval. No direct web callback registration is exposed.

## 2. Transactional identity, collision and out-of-order behavior

`RevisionEventProcessor(conn,as_of=UTC).consume(event)` requires a project-authorized, **IDLE** psycopg session. In one new READ COMMITTED transaction it compares injected UTC evaluation time to PostgreSQL `clock_timestamp()` (not in future and <=5 minutes old), checks for an exact prior event, verifies a real producer record, inserts the immutable `uae_revision_inbox`, finds *only* matching project/video/source/policy historical UAE snapshots, inserts `uae_snapshot_invalidations`, and inserts one pending `uae_revision_outbox` per newly invalidated snapshot. Failure at any step rolls back ALL changes; no partial outbox state.

Inbox primary key is `(project_id,producer,event_id)`, plus uniqueness on **exact project/video/source/producer/kind/producer_record_id/revision/producer_sha256**. Thus a repeated identical event is `replayed`, a repeated producer fact with a DIFFERENT event UUID is also `replayed` (no duplicate invalidation), whereas the same event_id with a different event SHA is `quarantined`, preserving both hashes and never applying the conflicting payload. A different video on the same source may legitimately receive its own revocation, never lost to producer-wide dedup.

The invalidation dedup fingerprint binds `producer`, `kind`, exact producer record ID/revision/SHA, snapshot ID and typed reason, **not the unreliable delivery event UUID**. The existing UAE080 invalidation uniqueness `(project_id,snapshot_id,reason_sha256)` ensures duplicate delivery does not rewrite history. Outbox rows are append-only in identity and tied by FK+BEFORE INSERT guard to the same project/snapshot's actual invalidation, with `pending` initial state. Delivery-state transitions are separately constrained (leased/delivered/failed, worker fence and lease clock) but there is **no live dispatcher/automatic Patterns notification** in this stage; a future authorized worker must claim and deliver. No public table/view grant is added.

**Out-of-order policy:** For producer revisions, only snapshots whose own `verified_as_of` precedes the exact producer `recorded_at` are eligible. Old revisions arriving late do not invalidate snapshots created from later verified inputs. Current source-policy revocation is different: any historical snapshot under that exact revoked policy becomes invalid immediately (at the next new transaction) regardless of a previously saved status. Approved source-derived retention expiration is measured per historical snapshot; a snapshot newer than the min retention window is not invalidated by an older TTL sweep. New source-backed pipeline reruns append new snapshot versions through UA15; no automatic retroactive editing or score reuse.

A peer `group_c_revised` event with an exact previous calculated-metric ID targets that score only; with no prior ID, it conservatively invalidates older scoped C observations. Group B review events cite the exact prior analysis where available; a new A result never validates an unrelated B parent. A taxonomy approval event only invalidates older snapshots that pinned another taxonomy version. These are invalidation-only decisions, never new approval claims.

## 3. Data ownership and read safety

- Forward-only UAE087 owns `uae_revision_inbox`, `uae_revision_quarantine`, `uae_revision_outbox`. Scope FKs include actual `sre_project_videos`, sources, policy and snapshot. Inbox/quarantine are immutable on UPDATE. Outbox identity cannot be rewritten; only fenced delivery metadata can change. Existing `uae_unified_snapshots`, `uae_snapshot_payloads` and seven evidence child rows are **never UPDATEd** by revision handling.
- UAE080 `uae_snapshot_invalidations` is the *only* invalidation ledger. UA11 existing `GuardedUAEReads.authorize_snapshot()` and 083 candidate-only view check for invalidation, while current source rights/TTL must independently be checked for **every** future external read. Absence of invalidation never grants eligibility.
- No source media, creator text, reviewer credentials, raw classification or Group C scores are copied to UAE event storage. Outbox contains reference-only IDs and digest. A pending outbox is not proof that anything was sent to Patterns or the UI.
- Missing peer schema cannot be treated as a positive new revision. Real Group B effective review, Group C reviewed result, SRE LIVE settlement, QDP carousel and authenticated project/worker orchestration remain separate production prerequisites.

## 4. QA and pending release checks

Portable tests cover source revoked/current source mismatch, retention-window safe expiry, exact QDP provenance and out-of-order event, Group C correction, **GC058 rejected cohort review with exact config**, and new version, Group B review, Group A result replacement, SRE approved taxonomy, conflicting event UUID quarantine, independent video-level dedup, cross-UUID producer replay, DB-absence/unavailable, stale/future clock, full rollback on outbox failure, historical row immutability and SQL migration guards.

A disposable PostgreSQL 17 fixture (appended to prior UA15 tests) exercises a real source permission revocation after an unapproved partial snapshot and checks one immutable invalidation plus one scoped pending outbox, replay idempotency, and SQL disallowing inbox tamper or foreign outbox invalidation. This is NOT a LIVE peer/producer review E2E; it is a real database integrity scenario only when runner execution is confirmed.

```bash
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
python -m compileall -q intelligence/unified_analytics
```

Until completed successful Python/PG17 CI runner logs exist, mark **IMPLEMENTED, TESTS AUTHORED, EXECUTION NOT VERIFIED**. Never claim real SRE/QDP/A/B/C end-to-end approval, public analytics delivery or Pattern Engine push.

**Next UA-MOD-17:** UAE job queue, idempotent worker claim/lease/recovery, using validated revision/source-capability gates rather than accepting unverified status flags.
