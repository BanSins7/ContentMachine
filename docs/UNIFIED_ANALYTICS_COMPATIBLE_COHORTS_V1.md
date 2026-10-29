# UA-MOD-22 — Compatible strata and controlled cohort comparisons (v1)

**Branch:** `feature/unified-analytics-full-module` · **Code:** `intelligence/unified_analytics/cohort_comparison.py` · **Tests:** `tests/test_unified_analytics_cohort_comparison.py` and `tests/test_unified_analytics_cohort_comparison_pg17.py`.

## The read contract

`CompatibleCohortReader(authorised_dict_row_connection).compare(left_pins,right_pins,spec)` compares *two explicit, server-selected and project-authorized sets of UA19 `VisibilityPins`*. It is **not** a public endpoint or a generic score/producer table query. There is no external API/Studio, no unrestricted SQL, no arbitrary caller-controlled per-video facts or SQL column names, and no direct cross-platform scoring.

The `ComparisonSpec` binds:
- `left_window`, `right_window`: UTC half-open publication time ranges, equal duration, up to 366 days. They must be exactly equal or fully nonoverlapping; partial overlap is forbidden. Windows may not end after the fresh DB clock.
- `author_mode="same_creator"` with an **explicit positive** `creator_id`: every selected item on both sides must belong to the exact same canonical `videos.creator_id`, joined to `creators` on platform; or `author_mode="disjoint_creators"` with no user-supplied ID: two groups' canonical creator sets must have no shared members. Missing author data is not guessed.
- `age_bin_days`: exactly **7, 14, or 30**, evaluated against the source-pinned publication metadata `source_video_metadata.published_at` and the exact Group C input `metric_snapshots.observed_at`. Buckets use half-open ranges `age_days_000_007`, `age_days_007_014`, etc, sorted lexicographically. Negative-age, future, missing or older-than-365-day observations are denied. This ensures age-compatible view counters rather than mixing a first-hour sample with a first-month sample.
- `min_sample`: explicit integer **5..50** on *each side in each emitted age bin*. Up to 50 requested unique video IDs **combined** (no duplicate video/snapshot between cohorts, one project). 0..4 input members in either group suppress before database I/O. An age bin with insufficient members on either side is excluded entirely, with **no count or name of suppressed bins**.

No author or date filtering is inferred from weak D1 `videos.published_at`: `AUTHOR_TIME_SQL` binds exact `uae_unified_snapshots`, Group C `calculated_metrics.published_metadata_id`, current `source_video_metadata`, exact metric snapshot and canonical video/creator platform. Missing source publication, changed media/source policy, deleted or expired metadata, wrong metric version, non-current Group C config or unapproved frozen cohort all deny comparisons.

## End-to-end authorization and compatibility

1. Validate the entire request before I/O (project match, no repeated video or snapshot across arms, explicit author/time spec, min sample, total max).
2. Start **one new PostgreSQL REPEATABLE READ transaction**. Obtain a DB clock. **Recheck UA19 `VisibleSnapshotReader._read` for every input BEFORE reading even the first Group C score, creator ID or publication date**. A denied/invalidated/revoked peer or source suppresses **the entire output** to `None`, never a partly populated result.
3. Read the exact persisted Group C `breakout_score` and D1 `views` using UA20 `QUALIFIED_METRIC_SQL`; independently load exact author+publisher metadata using fixed `AUTHOR_TIME_SQL` in the **same** transaction. No Group C arithmetic is repeated or score normalized/capped at 100. Unknown/NaN/float views or score, invalid age, wrong source, metadata/metric mismatch, or incomplete score immediately suppress the request.
4. `_common_compatibility` calls the strict UA20 `_compatibility` for *each individual* snapshot, then compares **same platform, content kind, source/source-policy version, source metric definition/version, Group C configuration ID/version, creator/prior/niche cohort DEFINITION versions, Group A/B analysis versions, B taxonomy and SRE taxonomy** across all inputs. Different *already independently verified frozen cohort identities* may legitimately occur for different authors, and thus their SHA-256 identities are not used as the group-level equality key. This is **not** permission to skip exact UA19 per-item cohort/config/lineage checks. Incompatible groups return **no pooled statistic**, not one overall cross-provider average.
5. For each age bucket represented by at least five verified distinct videos on **each** side, use existing Decimal baseline `_statistic` to report left/right n, min/p25/median/p75/max/mean for stored Breakout Score and observed views. Report **right minus left** median differences as exact Decimal strings. No significance, intervals, causal inference, lift ratio or relationship direction is asserted; differences do **not** imply a better hook or a real intervention effect.

The output `CompatibleCohortComparison` includes only the project identifier, opaque comparability SHA, author-control mode, age-bin days and ordered `CohortBinComparison` values. It **never** exposes individual video IDs, creator IDs, publication metadata, unpublished source media, hidden/denied cohorts, or suppressed bin counts. No persistent comparison table, cache, materialized view, new migration, GRANT, external route or API is added.

**Privacy boundary:** Minimum-N and no partial output protect this *internal read contract*. They do **not** prevent reconstruction through repeated *arbitrarily overlapping* cross-query comparisons. Before future user-facing query functionality, a separate reviewed cohort-specification, overlap/rate limits, account-specific authorization, and stronger statistical/privacy controls are mandatory. This is intentionally deferred, not misrepresented as safe public access.

## Deterministic golden examples

For one comparable age bucket, left scores `1,2,3,4,5` and right scores `3,4,5,6,7` have medians `3` and `5` respectively: descriptive median difference `+2`. Left observed views `10,20,30,40,50` and right views `30,40,50,60,70` have median difference `+20`. The half-open 7-day bucket `age_days_000_007` includes observations at age 0–6d 23h but excludes age 7d, which enters `age_days_007_014`. Independent multi-bin tests require at least five per arm for each emitted bin.

## QA and present production blocker

- Added **36 authored unit/mock/golden tests**: strict UTC windows, exact calendar bins, author modes, same/disjoint/overlap, two-sided sample floor, ordered bins, exact Decimal deltas, duplicate/cross-platform/source/metric/C config/B taxonomy mismatch, historical expired metadata, unknown scores, cross-author frozen cohort definitions, and zero score reads when even the *last* pin is denied.
- Added **2 conditional disposable PostgreSQL 17 tests**: actual UAE15 partial snapshot must never disclose owner/publisher facts or Group C score before UA19 positive, and source revocation + UA85 historical qualification guards remain enforced.
- **Source static structural audit** is not executed Python tests. In this tool-only session a disposable PG17 database is unavailable; exact Python/PG17 results and live positive E2E must be recorded `NOT RUN` until actually executed. Run on the developer machine:

```sh
python -m compileall -q intelligence/unified_analytics
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
# PG17: CM_TEST_PG_DSN must point to disposable PostgreSQL17.
```

**Current release status:** UA13/UA84/UA85 still prevent effective `qualified` creation and authenticated SRE/QDP/Group A/B/C producer approvals are not present. Therefore current production queries intentionally return **no positive comparisons**. Positive test fixtures are synthetic math/security *mocks*, not approvals. No changes to `main`, legacy PR23, SRE, QDP, Group A/B/C or public rights. **Next UA-MOD-23:** sample-size uncertainty and missingness reporting; only upon actual live qualification.
