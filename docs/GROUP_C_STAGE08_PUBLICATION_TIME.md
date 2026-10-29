# Group C — GC-MOD-08: Publication Time Evidence

**Branch:** `feature/group-c-full-module`  
**Code:** `intelligence/group_c/publication_time.py`  
**Tests:** `tests/test_group_c_publication_time.py`  
**Contract version:** `group-c-publication-evidence/v1`  
**Implementation:** CODE IMPLEMENTED; **production C6 computation and PG17 E2E NOT enabled**.

## Authoritative published_at

- The only authoritative publication date is the `published_at` value of an **exact** `source_video_metadata.id` owned by the canonical D1 `videos.id` and pinned `source_id` / `source_policy_version_id`. `videos.published_at`, discovery dates, `fetched_at` and `now()` MUST NEVER replace a missing publication timestamp.
- The raw metadata row must be `is_current=True`, `deleted_at=NULL`, `expires_at=NULL` or `expires_at>checked_at`. Exact integer metadata/video/policy IDs are mandatory; `True`, floating or coerced IDs are rejected.
- `published_at`, `fetched_at`, both observations and any expiry must be timezone-aware instants. All normalized to UTC. Required order: `published_at <= fetched_at <= checked_at`; `published_at <= previous.observed_at < current.observed_at <= checked_at`. Missing or contradictory times fail closed as `invalid_publication_timestamp`; expired metadata raises `publication_expired`. Future fetched dates are rejected.
- Source policy and source/video/project membership are checked in the **existing** `read_scoped_d1_bundle` from GC-MOD-05: source enabled, active exact policy version, explicit metadata/metrics/score permissions, fetched_at retention, non-retracted and unexpired pinned snapshots, project link and classified publication kind.
- `source_video_metadata` remains canonical D1 storage; **no Group C shadow publication table or migration**.

## Reproducible historical age

Immutable frozen `PublicationTimeEvidence` pins:

`video_id, metadata_id, source_id, source_policy_version_id, previous_snapshot_id, snapshot_id, published_at, fetched_at, previous_observed_at, observed_at, expires_at, checked_at, evidence_sha256`.

`age` is **`current.observed_at - source_video_metadata.published_at`**, not `datetime.now - published_at`. The `age_microseconds` property uses integer timedelta days/seconds/microseconds. `age_hours` is an exact Decimal conversion with local precision 70; no floating-point `total_seconds()`.

The stable `evidence_sha256` is over canonical IDs plus verified published/fetched/observation timestamps and expiry, excluding current wall-clock `checked_at`. Rechecking the same immutable source and observations later gives the same fingerprint and historical age. Changing publication metadata must be tracked as new provenance/revision downstream: **this stage only provides evidence/fingerprint; it does not write revisions or persist any C7 score**.

`evidence.calculation_publication()` returns `{"published_at": evidence.published_at}` as the exact publication input needed by the pure Decimal formula in GC-MOD-07. It deliberately does not compute freshness; C6 computation and complete-score authorization remain GC-MOD-18 and the worker/persistence stages.

## API

```python
from intelligence.group_c import (
    validate_publication_time,
    read_publication_time,
    PublicationTimeEvidence,
)

# Pure function: metadata is an exact, source-loaded D1 record.
proof = validate_publication_time(
    metadata,
    video_id=video_id,
    metadata_id=metadata_id,
    source_id=source_id,
    source_policy_version_id=policy_id,
    previous_snapshot_id=older_id,
    snapshot_id=newer_id,
    previous_observed_at=older_observed_at,
    observed_at=newer_observed_at,
    as_of=trusted_clock,
)
assert proof.time_source == "source_video_metadata.published_at"
age_for_C6 = proof.age_hours
```

Production read:

```python
proof = read_publication_time(
    conn, project_id=project_id, video_id=video_id,
    metadata_id=metadata_id, source_id=source_id,
    source_policy_version_id=policy_id,
    previous_snapshot_id=older_id, snapshot_id=newer_id,
)
```

The DB reader requires **`conn.autocommit=True`** and opens its own `REPEATABLE READ, READ ONLY` transaction, so D1 registry/project/source/policy/publication and observations are evaluated on one consistent snapshot. Production DB reader intentionally does **not** accept an `as_of` override; current retention cannot be bypassed with an historical timestamp. The pure function exposes `as_of` for deterministic tests only.

## QA and limitations

- GitHub-authored `tests/test_group_c_publication_time.py`: **13 pure unit methods + 6 opt-in PostgreSQL 17 tests**. The DB test fixture uses only disposable `CM_TEST_PG_DSN` together with `GC_TEST_DISPOSABLE_DB=YES`.
- GitHub Actions `group-c-module.yml`: Linux/Windows pure discover glob and explicit PG17 fresh/upgrade test jobs. Do **not** report PG tests PASS unless the actual CI run is complete and green.
- Isolated local Python3.13 mirror: `intelligence/group_c/publication_time.py` compared with GitHub via `git hash-object`; exact Git blob `c59063bd25a5c1e2f9e1fc28bcd8c4111db62c17`. **14/14 independent smoke tests PASS** against this byte-identical module, using minimal stubs only for DB-dependent imports. These tests do not claim full repository or PG17 validation.
- The exact authored GitHub test file and PG17 SQL-backed scenarios are **NOT RUN locally** (no complete repo checkout / PG17 service); GitHub CI is pending at the last observation.
- Current `003_performance_c.sql`, `004_performance_c_integrity.sql`, `056_group_c_runtime.sql` not altered. Source permission and external QDP emitter E2E remain separate prerequisites.

## Follow-up

**GC-MOD-09 — Cohort rules:** define compatible creator, niche and prior peer strata with same platform/content type, creator/topic, pinned metric definition/version, time and age windows and explicit target exclusion. Stage 10 freezes membership; stages 11/12 approve config/cohorts; stage 18 applies C6 formula from this proven observed_at-based age; stage 20 persists the result only after end-to-end revalidation.
