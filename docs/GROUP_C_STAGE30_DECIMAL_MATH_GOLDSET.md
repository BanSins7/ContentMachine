# GC-MOD-30 — Independent C0–C7 Decimal Math Gold-Set QA

**Branch:** `feature/group-c-full-module`. **Code:** independent test-only oracle, fixed gold vectors, cross-stage tests and PostgreSQL17 exact persisted-C7 comparison **IMPLEMENTED**. **Actual exact-head test execution/green numerical and error parity NOT VERIFIED**; stage "numeric and error parity tests green" DoD stays QA_NOT_VERIFIED until locally run. No change to production C0–C7 algorithms, signed approvals, QDP, Analytics, main or historic SQL migrations.

## Design / independence

1. `tests/fixtures/group_c_math_gold_v1.json` contains three manually reviewed, **fixed absolute expected strings** (not values generated on test execution by the module under test). They cover a 240-view baseline with optional niche, a high-growth case with **Breakout Score >300** (not P(viral), not a normalized 0–100 score), and a 1-microsecond elapsed-time case with zero interactions and fractional α/k.
2. `tests/group_c_independent_oracle.py` implements each mathematical formula from first principles with Python standard-library `decimal.Decimal` precision=96 and `ROUND_HALF_UP` to `NUMERIC(30,12)`. This **does not import** production `intelligence.group_c`, `pure_math`, `q`, `BREAKOUT_WEIGHTS`, or stage implementations. Values are computed from exact raw integer counters, timezone-aware ISO instants, reviewed frozen median/pooled priors and explicit α/k. No float timestamps, wall-clock "now", defaults, network calls or approval bypass.
3. `tests/test_group_c_math_goldset.py` first asserts that the independent oracle matches every **fixed fixture value**; then tests independent per-stage kernels C1, optional C2, C3, C4, C5, C6 and C7 and the historical `compute_c_metrics` monolithic independent path against identical frozen expectations. Pure C0 validation checks exact snapshots, raw source/metric version, strict views-pair time order, null/zero view/interaction handling and typed errors. C2 is explicitly not part of C7; approved immutable peer medians and pooled priors are checked separately.
4. Unit tests compare the known failure codes, including the deliberately different canonical names for counter correction: C0/C3 `counter_decreased`, monolith `counter_correction`. All are hard failures, never clamped zero; expected `incompatible_metric_version` is common across C0/legacy. Test data explicitly distinguish missing counters from zero, reject metric/source-policy mismatch, raw retractions and assumed observations, wrong frozen peer versions, invalid α/k/priors, 0-view undefined rates, precision overflow/rounding, NaN/float/unreviewed C7.
5. Time tests include actual **Amsterdam 2026-03-29 DST transition**, time-zone offset parity, a 1-microsecond interval measured exactly (not via float `total_seconds`), 48h/96h half-life, future/naive publication timestamps, and global Decimal context sabotage to ensure no hidden precision drift. C7 independently rounds each of the four components before summing; C2 niche does not contribute. Components preserve an **unbounded Breakout Score** and are not probabilities.
6. PG17-opt-in tests read a **real PG17 stored certified calculated_metrics row** created using the existing synthetic signed-C7 integration fixture, reconstruct the independent oracle input from the **original SQL snapshot/metadata/frozen priors/config**, and compare all four stored components and final C7 exactly to the independent 96-digit oracle. Explicit original IDs `video_id`, `snapshot_id`, `previous_snapshot_id`, `calculation_config_id` are checked. The fixture IAM is ONLY a disposable-test fake, **not production human authorization**. No C7 writes by the oracle.

## Immutable analytical contract

| Stage | Verified equation/condition | Negative boundary |
|---|---|---|
| C0 | latest exact D1 snapshot same project/video/source/policy/version + strictly later observation | NULL vs 0, assumed time, retraction, source mismatch, counter correction |
| C1 | `(views+alpha)/(creator_median+alpha)` | missing/zero α; median is frozen, positive denominator |
| C2 | optional `(views+alpha)/(niche_median+alpha)`, *not* C7 component | absent niche remains SQL NULL; changed topic/version peer denied |
| C3 | `(current_views - previous_views)*3,600,000,000/elapsed_microseconds` | <=0 elapsed or negative correction rejected |
| C4 | raw L/V, C/V, S/V and `(L+1.5C+3S)/V` as fractions | V=0 undefined; missing raw counters are NOT zero |
| C5 | `Ls=(L+k*pL)/(V+k)`, `Cs`, `Ss`; `Ls+1.5Cs+3Ss` from **unrounded rates** | k/priors review required; reject p>1; peer priors pooled not mean per-video rate |
| C6 | `100*2^(-age_hours/48)`, age between metadata `published_at` and original raw `observed_at` | never guessed publication/discovery time; no float truncation |
| C7 | independently round `0.45*C1`, `0.30*C3`, `0.15*C5`, `0.10*C6` then sum | no C2, no 0–100 cap, no normalization, signed trust remains outside math |

## How to run exact-head QA

Pure cross-platform, no database:

```bash
python -m unittest discover -s tests -p "test_group_c_math_goldset.py" -v
```

Disposable PostgreSQL17 (first configure/create a separate disposable PG17 database; **NEVER** production):

```bash
export GC_TEST_DISPOSABLE_DB=YES
export CM_TEST_PG_DSN='postgresql://<test-user>:<test-password>@127.0.0.1:5432/contentmachine_gc_test'
python -m unittest discover -s tests -p "test_group_c_math_goldset.py" -v
```

Windows PowerShell:

```powershell
$env:GC_TEST_DISPOSABLE_DB = 'YES'
$env:CM_TEST_PG_DSN = 'postgresql://<test-user>:<test-password>@127.0.0.1:5432/contentmachine_gc_test'
python -m unittest discover -s tests -p "test_group_c_math_goldset.py" -v
```

CI `.github/workflows/group-c-module.yml` references the test and gold fixtures explicitly on Linux and Windows pure contracts, and fresh/upgrade disposable PG17. Tests are **authored and configured, not observed passing** in this task; user's local full QA remains necessary.

## Known limitations and next stage

- Pure Decimal parity does NOT imply real authorization, signed human review, current live D1/source rights, expired peer revocation, provider/QDP handoff or production Analytics integration have succeeded.
- The older `compute_c_metrics` is a diagnostic parity reference, not an approved production scorer. New score computation and durable writes remain GC20/GC25 originals.
- If any exact-head test fails, fix the failing stage implementation or oracle under a reviewed, independently verified spec; do not change expected vectors to silence errors.
- **Next GC-MOD-31:** PostgreSQL17 fresh/upgrade Windows/Linux complete cross-platform E2E with real external ports and no mock-as-production claims. Real environment and CI SHA evidence will be necessary before full release readiness.
