# Group B — GB-MOD-24 Downstream Invalidation & Refresh

**Scope:** fail-closed current-version QDP/Group A/media/policy/rights/human-review freshness checks, immutable historical results, atomic downstream invalidation and durable 055 outbox. **Status:** implementation committed, tests written but NOT observed PASS; no actual provider, GA/QDP receipt, FFmpeg, Analytics callback or paid request was executed.

## 1. Two different notions of version

Historical `content_b_analyses` rows and their B1–B8 original labels MUST remain immutable. A completed analysis from yesterday is not necessarily **current qualified** today.

`integration/freshness.py` defines source-pinned `LineagePins` (project, original run, source/video/type, QDP ID/preparation SHA, exact A result ID/SHA, original media manifest SHA, source-policy version and the latest immutable correction revision/SHA) and a distinct `CurrentLineage` attested by a **fresh independent host** that has actually checked upstream QDP, GA, D1 media bytes, revocation/expiry and project access. A signed/digest-equivalent payload alone is **NOT an attestation**. `compare_lineage` distinguishes:
- `fresh` only for exact current version and independently trusted live proof, with no corrections.
- `stale` for changed QDP event/input, replaced A result ID or digest, revised media, source policy/rights revocation, or authenticated human-correction revision. Multiple reason codes are retained.
- `unverified` for unavailable/failed attestation. Neither `unverified` nor `stale` is a qualified B result. Cross-project/video/source/kind snapshot mismatches fail closed.

No module may compute “freshest GA result” by assuming the highest SQL ID. A trusted producer/host must bind the actual current version and original bytes at time of decision.

## 2. Atomic invalidation and job fencing

`integration/invalidation.py` exports `read_pinned_lineage`, `assess_current_run`, `invalidate_run`. It locks the canonical `group_b_runs` row and independently verifies source/media/prov lineage, comparing stored and current evidence under the saving transaction. Known-local sources and source-policy rows must not be revoked. The external `verify_current_lineage` and `verify_invalidation_admission` host callbacks must both return exactly `True` for a proved change; missing callbacks deny write.

On confirmed staleness, ONE psycopg transaction:
1. Marks the historical Group B run `needs_review` (existing 055 runtime state; no invented enum/migration). Retains `result_analysis_id` and **does not update/delete original SQL005 `content_b_*` rows**.
2. Cancels only outstanding **ready/running/partial** jobs with lease fencing and immutable `group_b_job_events` records; worker completed/failed jobs and the paid attempt/ledger **remain untouched**. Local block states that have not completed are marked `unavailable` (never fabricated `false`).
3. Writes versioned `group_b.invalidated.v1` to the existing `group_b_outbox`, with source SHA, reasons, old analysis ID, `qualified_for_downstream=false`, `requires_new_run=true`. Event keys derive from canonical event SHA and are unique per project; a retry verifies the identical event rather than creating duplicate delivery.

**Authorized human corrections automatically trigger exactly this historical invalidation in the SAME transaction as the immutable GB23 `group_b_review_events` INSERT.** If either action fails, neither may commit. Immutable gold annotations do not invalidate the original inference; they are for scoring only.

A transient `unverified` upstream dependency does not fabricate a “changed” source or silently enqueue any model call. The current-read qualification gate still denies publication.

## 3. New QDP handoff and safe refresh

`request_refresh` queues `group_b.refresh_requested.v1` in the existing outbox for a run with a previously committed invalidation, but does NOT invent a QDP event or a production Group A analysis. `integration/refresh.py:create_refresh_draft` requires a **different new `group_b_inbox` with status `accepted`**, exact project/source/video, intact source rights, an authenticated fresh `verify_refresh_draft` verifier, and a new version/config SHA. A deterministic idempotency key prevents duplicate refresh runs.

A created refresh run is **`draft` only** with 8 `not_run` block states, no A/GA, media pins, provider requests, costs or SQL005 analyses. It emits `group_b.refresh_draft_created.v1` to the outbox. Subsequent stages must bind GA054 exact A results, rehash original media, authenticate QDP receipt, evaluate permissions and explicitly quote/approve paid models before scheduling B1–B8. This is a correctly **queued reanalysis REQUEST**, not proof any automatic upstream producer or paid reprocessing executed.

## 4. Downstream and outbox consumer contract

- `read_group_b_result` is an audit-only SQL005 projection and now defaults `qualified_for_downstream=False` and `requires_current_lineage_attestation=True`. No unverified raw “completed” flag may silently cross into Unified Analytics.
- `read_verified_current_result(conn,project_id,run_id,current,verifier)` compares fresh authoritative current evidence, source-rights, existing invalidation outbox events and the exact completed block states under a transaction. Its `eligible_for_qualified_handoff` is only a **candidate** for the stage25 Analytics+Group C quality gate, not an authorized Analytics write.
- `integration/outbox.py:read_pending_outbox` requires an injected authenticated project-scoped transport reader, checks event schema+SHA and returns `queued/failed` items. `acknowledge_outbox_delivery` may mark an event `delivered` ONLY after an independently verified durable consumer receipt for the specific payload SHA/project/event; repeated matching ACK is idempotent. No network dispatch here, no fabricated `delivered` status.
- Previously completed SQL005 rows remain queryable for audit; consumer-facing “current qualified” is revoked by status, review revision, or persisted invalidation event. Actual Analytics handoff and synchronized refresh/invalidation handling remain GB-MOD-25.

## 5. Tests, boundaries, blockers

`tests/test_group_b_invalidation.py` covers QDP change/invalidation, source rights, A ID/SHA revisions, changed media, manual corrections vs gold annotations, wrong project/source and forged payload digest. `tests/test_group_b_invalidation_db.py` uses disposable PostgreSQL17 fixtures and fake host only: source revocation, one-transaction stale state/job fencing/outbox, duplicate event retry, rollback after forced outbox failure, automatic human correction, distinct QDP inbox draft+8 not-run children/no paid attempts, project scope and verified consumer ACK. Wired to Linux/Windows + PG17 `.github/workflows/group-b-module.yml`. No main/peer edits or changes to 005/055/056/058.

```bash
python -m unittest discover -s tests -p 'test_group_b_invalidation.py' -v
CM_TEST_PG_DSN='postgresql://.../cm_group_b_test' python -m unittest discover -s tests -p 'test_group_b_invalidation_db.py' -v
```

**QA truth:** code/tests/docs COMMITTED; actual local tests NOT RUN; GitHub Actions pending/queued until observed otherwise. Fixture `SyntheticHost` has NO authority outside tests. This stage does not make QDP/GA producer, full original-media bytes, signed provider receipt, expired-rights poller, production AuthN/AuthZ or Analytics receiver available; those are explicit external blockers. No paid model endpoint called.
