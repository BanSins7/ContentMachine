# UA-MOD-20 — Qualified-only descriptive aggregation baseline

**Branch:** `feature/unified-analytics-full-module` · **Python:** `intelligence/unified_analytics/descriptive_aggregation.py` · **No new migration / GRANT / HTTP hook**.

## What is implemented

This stage ports the *pure ideas* from legacy `intelligence/analytics/aggregate.py` (`_percentile`, `summarize_qualified`), but **not** its unsafe direct `analytics_visible_snapshots` SQL, silent `DISTINCT ON(video_id)` version choice, arbitrary raw row acceptance, Group C metrics recomputation, or segmentation. A project-owned `QualifiedAggregationReader` only returns descriptive statistics from snapshots which independently pass the entire UA-MOD-19 **CURRENT** evidence/rights gate inside **one fresh PostgreSQL REPEATABLE READ transaction**. There is no public aggregate endpoint, no raw SQL with caller-provided segments, no topic/category breakdown (UA-MOD-21), no Patterns publication, and no external model/provider calls.

`summarize_qualified(reader, pins, min_sample=5)` and `QualifiedAggregationReader(conn).summarize_qualified(pins, min_sample=5)` require a trusted server-side caller and an **already project-authorized** `dict_row` PostgreSQL session. `pins` is a tuple of `VisibilityPins(snapshot_id, current JoinedCandidatePins)`, NOT untrusted row JSON. Unlike the legacy pure `summarize_qualified(rows,...)`, the new entrypoint refuses arbitrary numeric rows or forged `status='qualified'`.

### All-or-nothing privacy rule

1. First validate the exact pinned request: at most 50 distinct snapshot IDs, **one project, one occurrence of each video**, a `min_sample` integer **5–50**; boolean, float or client-supplied string floors are refused. Empty input and 1–4 items are **suppressed to `None` without SQL**. A requested size below 5 cannot override the privacy floor.
2. Within one PG transaction and fresh DB clock, call `VisibleSnapshotReader._read(db, pins, as_of)` for **each and every** member. This enforces UA19's same-project membership, source policy+retention and pinned metadata/ALL metrics/ALL media, live QDP/SRE/Group A/B/C/D1 checks, historical evidence checksum and invalidation ledger. UA-MOD-13 effective approval must be `True`; the current UA85 and UA13 mechanisms admit **none**.
3. **Only after** a positive current-visibility result, fetch exactly one C `calculated_metrics.breakout_score` and source `metric_snapshots.views` by **this project's exact snapshot ID/video ID** via `QUALIFIED_METRIC_SQL`. Require persisted `score_status='complete'`, `calculation_status='complete'`, reviewed active C config, non-normalized C, frozen source-coherent cohorts, exact metric snapshot, complete/exact quality, known source metric definition and version, valid expiry, and all pinned metric/C/config/cohort IDs. This read is inside the **same transaction**, not performed after a previously authorized `visible()` call has committed.
4. Explicitly compare **every sample's** platform, source, content kind, source metric definition/version, config ID/version, creator/prior/niche cohort definition versions and frozen cohort hashes, Group A/B analysis versions, Group B taxonomy, and SRE taxonomy version. For UA20, mixing any incompatible stratum **suppresses the entire result**; no partial averages or silent version pooling. Multiple snapshots of one video also fail before SQL. Segmentation and compatible-stratum selection are later stages.
5. Require nonnull **BIGINT views** and nonnull **Decimal persisted C Breakout Score**; unknown, NaN, Infinity, booleans, floats, huge exponents, contradictory source/lineage or malformed query responses **suppress the entire result**. Missing views are not imputed as zero. Do **not** recalculate C's component weights, normalize to 100, clip negative/large scores, smooth missing values or claim confidence intervals.
6. **No count**, sample size, suppressed count, aggregate, error detail about hidden member, or per-video payload is returned if **any** requested snapshot fails or if fewer than the threshold remain. Only a full eligible compatible ≥5 sample produces `DescriptiveAggregate`: project ID, compatibility SHA, qualified video count, `MetricStatistics` for breakout and views, and immutable disclaimer flags (`descriptive_only=True`, `confidence_intervals_computed=False`, `causal_claims_allowed=False`). There are no platform/source segment counts or raw metrics included in the result.

**Note:** This protects the internal aggregate read from accidentally counting hidden input; it is **not** a complete user-facing differential disclosure defense for arbitrary overlapping cohort queries. No public query interface is installed. A future external API must bind project authorization and approved server-defined cohort sets, limit correlated/adaptive queries, and audit aggregation privacy beyond min-N alone.

### Decimal mathematics and golden values

All metrics use `Decimal` (never binary float). Type-7 / inclusive linear interpolation for sorted values `v`:

`index = (n-1)*p`; `Q(p)=v[floor(index)] + (v[ceil(index)]-v[floor(index)]) * fractional_part(index)`.

For independently known values `[1,2,3,4]`: **p25 1.75, median 2.5, p75 3.25**. Other independent tests cover mean `[0.1,0.2,0.3,0.4,0.5] = 0.3`, repeats, p0/p100, missing data, and unnormalized score `[-8,0,100,203,444]` (no clamping). The pure helpers `_percentile_linear`/`_statistic` are *math only* and do not themselves establish authorization; the public UAE callable is always DB-backed.

### QA / integration status

- `tests/test_unified_analytics_descriptive_aggregation.py`: 27 authored standalone Decimal/golden/boundary/mock cases (future-positive fixtures are deliberately synthetic, **never current approvals**).
- `tests/test_unified_analytics_descriptive_aggregation_pg17.py`: 2 authored *conditional* disposable PostgreSQL 17 tests; legacy UAE15 partial historical rows cannot pass UAE19 or read Group C score in a verified real DB, and `085` forged-qualified triggers remain enabled.
- Source re-fetch/static review is distinct from executed Python. The environment lacks a local checkout and GitHub DNS, and does not provide disposable PostgreSQL 17. Do not record actual unit/PG17 E2E PASS unless successfully executed.
- Manual verification commands:

```sh
python -m compileall -q intelligence/unified_analytics
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
# PG17 optional: set CM_TEST_PG_DSN to an isolated disposable PG17 database.
```

**Non-negotiable blockers:** UA85 currently disallows `qualified` snapshot insertion; UA13 does not emit effective `qualified`; UA19 therefore returns no positive member. Source-backed approved Group C cohorts/configuration, LIVE QDP/SRE/B and actual A data, caller's project authorization and real PG17 migration/runtime testing remain necessary to activate the statistical read path. This code does **not** relax any of those conditions.

**Next:** UA-MOD-21 — eight exact typed segmentation dimensions on **already approved** samples; enforce per-segment unique video and no category privacy side channels.
