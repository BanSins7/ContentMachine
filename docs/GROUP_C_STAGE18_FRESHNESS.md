# GC-MOD-18 — Актуальность публикации (C6 Freshness Score)

**Branch:** `feature/group-c-full-module` (standalone). **Code:** `intelligence/group_c/freshness.py`. **Tests:** `tests/test_group_c_freshness.py`. **Status:** CODE IMPLEMENTED; exact-head PG17/CI execution **NOT VERIFIED**; production QDP/source/IAM E2E **BLOCKED_EXTERNAL**. No main/other branch edits, no new migrations or modified historical SQL.

## Verified formula and units

```text
C6 Freshness Score = 100 × 2 ** (-age_hours / 48)
age_hours = (current.observed_at - source_video_metadata.published_at)
            / 3_600_000_000 microseconds per hour
```

The authoritative source is always the exact `source_video_metadata.id/published_at` of the selected D1 video/source/policy, never a legacy `videos.published_at`, discovered/collected/fetched_at, processing wall clock or assumed publication date. `observed_at` is the *verified historical source observation*, not the current read/worker time. The half-life is the immutable Group C `FRESHNESS_HALF_LIFE_H = Decimal("48")`; no new adjustable rate, hardcoded production clock or invented config.

The pure `compute_freshness_score(published_at, observed_at)` uses timezone-aware `datetime` instants, normalizes to UTC, computes precise *integer* timedelta microseconds, divides with a local Decimal context at precision=80, and applies the exact exponential expression before a single `ROUND_HALF_UP` to 12 decimal places via existing Group C `q` for PostgreSQL NUMERIC(30,12). Non-aware/missing/invalid/future publication dates fail closed. No floats, rounding age first, 0..100 clipping or synthetic freshness values. A very old valid observation may quantize to `0.000000000000` only because the prescribed output scale has been reached, not because of an arbitrary cutoff.

| Elapsed at `observed_at` | Freshness |
|---|---:|
| 0 hours (pure boundary) | 100.000000000000 |
| 48 hours | 50.000000000000 |
| 96 hours | 25.000000000000 |
| 144 hours | 12.500000000000 |

The standalone pure zero-age boundary is mathematically valid. A *database* C6 read requires two distinct increasing actual C0 observations and historical `published_at <= previous.observed_at`, so that proof may not produce a zero-age current observation in the standard verified pair.

## GC08 publication evidence + C0/D1 source lineage

`compute_verified_freshness(PublicationTimeEvidence)` checks the expected version, field identity, source timestamp and recomputed GC08 immutable SHA-256 before any arithmetic. It is a pure integrity check **not an authentication mechanism**: only DB reread verifies current rights.

`PostgreSQLFreshness.derive` uses **one** independent `REPEATABLE READ, READ ONLY` transaction and a non-overridable present-time retention check. It invokes `read_scoped_d1_bundle` to verify real project/video/source/policy, canonical pinned `source_video_metadata`, current permissions and retention. It validates both raw C0 observations, their view definition/version, observed timestamps, quality and view-counter consistency, but does **not** require L/C/S counters for this standalone time-only C6 diagnostic. It independently checks that the selected pair is the two most recent **unfiltered** raw D1 observations for source/video, including any newer corrected/retracted/changed-version entry. There is no last-good fallback. It builds a new GC08 `validate_publication_time` proof **inside the same transaction**, then derives C6 from that proof; two independent source transactions would permit a stale/changed intermediate version and are intentionally prohibited.

The immutable `FreshnessResult` includes project/video/source, source-policy version, metric definition/version, previous/current snapshot IDs, publication metadata ID, publication and source observation instants, microsecond age and exact Decimal hour age, half-life=48, C6 Decimal score, independent GC08 publication evidence SHA-256 and deterministic C6 evidence SHA-256. `checked_at`/wall-clock processing time is never part of score or immutable C6 digest. Repeated reads of unchanged eligible facts produce the same score and hash.

**Status:** `freshness_derived_unapproved`; `ready_for_breakout=False`. No score persistence, approval, provider API, QDP inbox ACK or Unified Analytics outbox publication. GC-MOD-19 still owns C7 assembly, and GC-MOD-20 later persists complete reviewed results.

## QA scope and known limits

`tests/test_group_c_freshness.py`: pure gold 0/48/96, exact microseconds, timezone offsets, Decimal global-context isolation, large valid ages, negative/future/naive timestamps, missing publication, GC08 fingerprint integrity, immutable outputs; disposable PG17 fixture cases for 48/96h, source policy/metadata/metric-version mismatch, expiry, raw latest retraction, revoked rights, views-only freshness, idempotency and no score persistence. Existing Linux/Windows pure discovery uses `test_group_c_*.py`; separate PostgreSQL17 fresh and upgrade steps were added to `.github/workflows/group-c-module.yml`.

Execution statuses must distinguish **CODE IMPLEMENTED** from **QA PASS**. This authored change has not demonstrated a successful exact-head full test or PG17 run in this environment; CI and production IAM/QDP E2E remain **NOT VERIFIED / BLOCKED_EXTERNAL**.

**Next stage:** GC-MOD-19 — C7 Breakout Score from C1 + C3 + C5 + C6, with fixed weights, no normalization and C2 excluded.
