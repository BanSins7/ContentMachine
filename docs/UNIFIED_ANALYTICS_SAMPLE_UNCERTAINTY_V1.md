# UA-MOD-23 — Sample size, observed missingness & statistical uncertainty

**Branch:** `feature/unified-analytics-full-module`  
**Code:** `intelligence/unified_analytics/statistical_uncertainty.py`  
**Tests:** `tests/test_unified_analytics_statistical_uncertainty.py`, `tests/test_unified_analytics_statistical_uncertainty_pg17.py`  
**Status:** IMPLEMENTED CORE, runtime QA **NOT RUN**, live positive qualified E2E **BLOCKED**.

## Purpose and internal contract

This step adds a **qualified-only** reporting facade over the already implemented UA20 (descriptive aggregate), UA21 (eight typed B segments), and UA22 (source-compatible two-cohort comparisons). The only supported entry point is `QualifiedUncertaintyReader(conn)` with methods:

```python
reader = QualifiedUncertaintyReader(authorized_connection)
single = reader.summarize(current_visibility_pins, min_sample=5)
segments = reader.segment(current_visibility_pins, "hook_mechanism", min_sample=5)
comparison = reader.compare(left_visibility_pins, right_visibility_pins, comparison_spec)
```

Do not instantiate a report directly from submitted raw rows or serialize fabricated Group C scores. The facade calls the **actual UAE authorized DB readers**; authorization, live rights/retention and upstream SRE/QDP/A/B/C checks, project/video/metric source provenance and numeric reads occur within the underlying reader's **single fresh PostgreSQL REPEATABLE READ transaction**. The facade only annotates the immutable minimal result **after** that read. There is **no additional post-commit DB read**, no migration, no public API/SELECT grants and no provider modifications.

If even one member fails the existing UA19/current gate, if the configured min-N is not met or if the version/source/cohort is incompatible, the underlying read returns `None`; **the quality facade returns `None` as well**, not missingness counts for suppressed rows. Never use quality metadata to reconstruct hidden group membership.

## Exact definitions and denominators

- **Sample size:** the count of distinct, currently authorized, `qualified` videos already disclosed by the underlying result: **N ≥ 5** per UA20 summary, per UA21 published category, or per UA22 arm per published age bin (max 50 selected videos in a batch; min-N cannot be lowered). This is a hard disclosure minimum, **not** scientific evidence of a representative sample.
- **Observed quality inside the released group:** `ObservedMissingness` gives `included_qualified_n=N`, `breakout_observed_n=N`, `views_observed_n=N`, `breakout_missing_within_included_n=0`, `views_missing_within_included_n=0`. These zeros are exact **conditional on the strict previous readers**, which reject missing score/views completely. They do **not** say all project videos had complete metrics.
- **Missingness outside the qualified output:** `whole_population_n=None`, `excluded_or_unqualified_n=None`, `outside_status='not_observed_unqualified_or_excluded'`. The full project sampling frame, hidden rows, peer-unavailable records and denied entries are NOT enumerated. This is **unknown**, never fabricated as zero or `N` and never exposed through the quality layer.
- **B multilabel caveat:** multiple B1/B3/B7 labels may represent the *same* video in different categories. Each category's sample count is separately valid and per-value deduped by UA21, but **segment Ns must never be summed** to produce project size.
- **Cohort caveat:** UA22's left/right samples are controls selected by approved project logic and observation ages, *not* randomized experimental treatment assignment. Baseline right-minus-left median differences are descriptive contrasts only.

### Empirical intervals versus statistical confidence

`EmpiricalInterquartileRange` reports **observed sample** `[p25, p75]`, empirical median and `iqr_width = p75-p25` with exact `Decimal` arithmetic. This measures **spread of the observed authorized sample**. It does **NOT** estimate uncertainty in the population median, a bootstrap confidence band or a confidence interval for the baseline median difference. The explicit field `confidence_interval=None` marks the absence of justified inferential coverage.

A frequentist confidence interval or p-value would require a defendable population sampling/independence design, data-generation assumptions and a specified target estimand; none is independently authenticated by the current UAE peer adapters. Consequently:

- `inferential_status='unsupported_nonprobability_sampling'`
- `statistical_tests_performed=0`, `p_value=None`, `confidence_level=None`, `statistically_significant=None`
- `correction_for_multiple_testing=None` (no formal tests are performed)
- `observational_only=True`, `causal_claims_allowed=False`
- `exploratory_contrasts` counts **released** UA21 categories or UA22 paired age bins, not raw/unqualified candidates. `multiple_testing_warning=True` when there is more than one such exploratory contrast. For a UA20 single overall descriptive summary the count is zero.

No "95% CI" is shown just because N≥5, and no `p<0.05` or high breakout difference becomes a significance/causality claim. The notes explicitly apply to even perfectly constant `[7,7,7,7,7]` (empirical IQR zero, **confidence still unknown**).

### Independent golden mathematics

| Valid qualified sample | p25 | Median | p75 | IQR |
|---|---:|---:|---:|---:|
| `[1,2,3,4,5]` | 2 | 3 | 4 | 2 |
| `[0.1,0.2,0.3,0.4,0.5]` | 0.2 | 0.3 | 0.4 | 0.2 |
| `[-20,-10,0,100,300]` | -10 | 0 | 100 | 110 |
| `[7,7,7,7,7]` | 7 | 7 | 7 | 0 |

UA22 paired comparison golds remain medians `3` versus `5`, `right-minus-left = 2`, and source views `30` versus `50`, `+20`. UA23 adds *no invented confidence interval* on this contrast; each arm/bin has its own empirical IQR, with an explicit `difference_confidence_interval=None`.

## Privacy, assumptions and future public release

**Only server-selected project-authorized pins** may reach these readers. The code is an internal Python capability, not a query endpoint. Returning no output for 0/1/2/3/4 or any unauthorized input does not solve timing/correlated-query attacks, and min-N is not differential privacy. Before external release, project roles and source rights, fixed/nonoverlapping cohorts, query auditing/rate limits, anti-differencing protections and sampling design must be reviewed. Do not publish a full-project missingness percentage or creator-level inferential result from these internal sample counts.

## QA and production gate

**Authored:**
- `tests/test_unified_analytics_statistical_uncertainty.py`: **33** independent pure/golden/missingness/no-false-CI and DB mock integration tests; positive fake records are synthetic fixtures, not actual producer authorization.
- `tests/test_unified_analytics_statistical_uncertainty_pg17.py`: **2 conditional** disposable PostgreSQL 17 real-table negative tests: historical UAE15 `partial` payloads cannot generate coverage/IQR or author/cohort statistics; no Group C score, B label or creator metadata read occurs after a failed UA19 gate.

**Not executed in this tool-only environment:** Python/compileall/unittest, disposable PG17, full LIVE production SRE/QDP/Group A/B/C E2E. An invariant source audit is not executable QA. On developer's isolated machine:

```bash
python -m compileall -q intelligence/unified_analytics
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
# Optional: export CM_TEST_PG_DSN to an isolated PostgreSQL17 test DB
```

**Release blocker:** UA13 effective producer approval unavailable; UAE084 immutable payload supports historical `partial`/`blocked` only and UAE085 forbids forged `qualified` snapshots/checked-complete peer evidence. A positive live quality report therefore remains **unavailable**. This stage neither loosens those guards nor invents sample/population denominators. No public API, migration, grant, PR merge, changes to main or other module branches.

**Next:** UA-MOD-24 — Project-level summaries & rankable views; rank only exact compatible source and current authorized qualified snapshots, stable pagination and provenance, no status elevation.
