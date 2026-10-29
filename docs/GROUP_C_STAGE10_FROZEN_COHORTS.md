# Group C — GC-MOD-10: frozen cohort membership, exact D1 provenance and reproducible statistics

**Branch:** `feature/group-c-full-module` (do not merge to main yet)  
**Code:** `intelligence/group_c/cohort_freeze.py`, with GC-MOD-09 transaction-safe peer selection `PostgreSQLCohortCandidates.select_locked`  
**Migration:** `intelligence/migrations/057_group_c_cohort_freezes.sql` (additive, following 056)  
**Tests:** `tests/test_group_c_cohort_freeze.py`; updated `tests/test_group_c_runtime_schema.py`; CI `.github/workflows/group-c-module.yml`  
**Status:** CODE IMPLEMENTED; PostgreSQL 17 CI NOT VERIFIED; no human approval or C7 persisted results.

## Strict ownership and sequence

GC-MOD-09 selects **only candidate identities** using explicitly versioned, cross-project, source-/policy-/metric-version/age-window compatible D1 evidence. GC-MOD-10 atomically freezes that evidence and computes **sample statistics**, but **cannot approve** a cohort, activate a config, invent priors or produce a Breakout Score. GC-MOD-11 is the independent human review, GC-MOD-12 config activation, GC-MOD-20 persistence of C1–C7 outputs.

A complete cohort freeze does *not* mean a `comparison_cohorts` record is approved for scoring. Both `CohortFreezeReceipt.approved=False` and the new SQL `group_c_cohort_freezes.state='frozen_unreviewed'` prove this boundary. The only approved values in a future worker must come from the verified review records and frozen reference (not a plain legacy `comparison_cohorts.is_frozen` flag).

## Versioned PostgreSQL 17 storage

Migration 057 does **not** modify, rename or overwrite existing 003/004 or 056, and it does not copy sibling A/B migrations. It creates:

1. `group_c_cohort_freezes`: independent immutable evidence/identity, `project_id`, one exact parent `comparison_cohorts.id`, target `videos.id`, target `metric_snapshots.id` and `source_video_metadata.id` with composite FKs, purpose, source/policy ID, precise source metric definition/version, `rules_version`, `rules_sha256`, `selection_sha256`, `evidence_sha256`, canonical rules JSONB, estimator version, exact member count, median or observed rates, and `state='frozen_unreviewed'`.
2. `group_c_cohort_freeze_members`: exact peer `video_id`, `snapshot_id`, `metadata_id` with composite D1 foreign keys, source and policy revision, observed/published timestamps, original unmodified `views/likes/comments/shares`, and an immutable SHA-256 evidence digest for every row. One member per peer video ID and freeze.

Existing `comparison_cohorts` and `comparison_cohort_members` remain the D1-authoritative C schema for future C1/C2/C5 foreign keys. A GC-MOD-10 transaction inserts them, inserts 057 proof records and sets `comparison_cohorts.is_frozen=TRUE` in a **single transaction**. The historic 004 guard already prohibits modifying frozen `comparison_cohorts` and membership. New 057 guards reject UPDATE/DELETE on the proof and its members; a separate BEFORE INSERT trigger prohibits appending members once the legacy parent is sealed; explicit policy-compliant erasure is left to the reviewed GC-MOD-24 retention/revocation design, never silently performed.

A deferred PostgreSQL constraint trigger, evaluated at **COMMIT**, verifies parent `is_frozen=TRUE`, matching purpose/source/metric definition, statistic values, member counts and exactly corresponding legacy members. A transaction cannot commit a partial or unsealed proof.

Migration order: old 001..053 → 056 Group C runtime → **057 Group C cohort freeze**. Because `intelligence.migrate` uses a transactional advisory lock and migration SHA ledger, migration 057 is installed exactly once and must never be edited after deployment. In fresh/upgrade CI, `tests/test_group_c_runtime_schema.py` now expects both 056 and 057 when simulating a pre-Group-C database.

## Atomic freeze and idempotency

`PostgreSQLCohortFreezeWriter.freeze(conn, rules=..., target_snapshot_id=..., target_metadata_id=..., ports=None)` requires an **autocommit=True** PostgreSQL connection. It owns a transaction with `SERIALIZABLE` isolation and calls `PostgreSQLCohortCandidates.select_locked` inside **the same transaction**, so project scope, exact target/peer snapshots and metadata, same source/policy/metric definition+version, target age, latest unfiltered source record, current rights/retention and full candidate set are re-evaluated before any inserts. It then writes the legacy parent, legacy members, 057 frozen proof and 057 member evidence, and seals the original cohort. There is no partial ACK or durable partial freeze.

`UNIQUE(project_id,purpose,rules_sha256)` is the idempotency key. The exact same rules and live source evidence return the existing freeze ID, with no new rows. If evidence or selection changes under the same rules, the call fails `cohort_selection_conflict`: a deliberately versioned new rules cutoff or definition must be provided. Concurrent writers operate under `SERIALIZABLE`; serialization / uniqueness conflicts should be surfaced and retried by the caller with revalidation, **not** hidden by fallback or unstated re-use. A previously frozen cohort may survive as historical evidence but subsequent source revocation does not grant scoring rights (GC-MOD-24 will handle invalidation).

No SQL UPDATE or DELETE is issued on D1 facts, 003/004 migrations, QDP handoff rows or Analytics outputs.

## Exact statistics, no arbitrary priors

Pure function `build_frozen_evidence(selection: CohortSelection) -> FrozenCohortEvidence` requires nonempty **unapproved** GC-MOD-09 candidates:

- Creator and niche: exact median of **raw integer views**. Even member count takes the arithmetic average of the two central values; an observed zero is a legitimate value, never a missing observation. NUMERIC(30,12), explicit 12-place HALF_UP.
- Engagement prior: empirical **pooled counts**, not unweighted per-post rate averaging: `pL=Σlikes/Σviews`, `pC=Σcomments/Σviews`, `pS=Σshares/Σviews`. Every peer must have positive exact views and non-NULL valid likes, comments, shares. A missing value / zero denominator fails; it is not silently replaced. Ratios beyond 1 require manual review instead of coercion.
- `alpha`, `k`, default priors, minimum sample count, uncertainty thresholds and cohort size assumptions are **not** invented. This stage computes empirical candidates for later review. Any future minimum sample-size thresholds must be explicitly approved/versioned in GC-MOD-11.
- The exact sample evidence SHA includes version, rule hash, selected and rejected candidate hash, every admitted exact D1 video/snapshot/metadata ID, source policy identity, observed/published timestamps, original counters and reproducible statistics. The `selection_sha256` alone is not assumed to cover mutable counters; `evidence_sha256` does.
- Output status remains `frozen_unreviewed`; no C7 value is calculated, normalized or published.

## QA and blockers

- `tests/test_group_c_cohort_freeze.py`: new pure test inventory covers odd/even/zero median, niche, pooled weighted counts (vs per-post mean), rounding, empty and invalid peers, SHA variance on counter revisions and invariance to order and global decimal precision, blocked autoapproval, additive immutable SQL. PostgreSQL17 synthetic scenarios cover two exact peers/one target, parent and proof members, repeat idempotence, changed peer snapshot, empirical priors, legacy and new immutability, post-seal member insertion attempts, missing/revoked source / atomic rollback and migration checksums. **No production database or actual platform source data is used**.
- `.github/workflows/group-c-module.yml` runs pure tests on Ubuntu and Windows and dedicated Stage10 PG17 tests on both freshly migrated and main→056+057 upgraded disposable databases.
- Environment limitation: this session does not provide an available PostgreSQL17 service or psycopg. GitHub Actions status was **queued** at last check. Therefore exact committed unit/PG17 end-to-end results must be shown as **NOT VERIFIED** until a completed CI run is inspected; do not infer green from written tests.
- Live QDP producer, certified source metadata/rights, reviewed population/config and Analytics consumer E2E remain external dependencies and cannot be simulated into a production-ready score.

**Next:** GC-MOD-11, human review contracts and append-only approvals for exact frozen memberships, counts, estimator/rule SHA, source/policy/metric version, priors, alpha and k. A frozen cohort alone MUST NOT enable score completion.
