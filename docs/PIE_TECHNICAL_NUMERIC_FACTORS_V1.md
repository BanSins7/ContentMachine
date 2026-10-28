# PIE-MOD-22 — Frozen Numeric Technical Factor Bins

**Owner:** intelligence/patterns_insights; independent branch feature/patterns-insights-full-module.
**Schema:** pie-numeric-technical-factors-candidate/v1.
**Status:** descriptive candidate computations implemented; inferential uncertainty and real UAE verification BLOCKED.

## Scientific scope and typed source

This module audits five predeclared technical metric classes: Group A duration, speech WPM, speech coverage; Group B cuts/minute and cuts in the first five seconds (the B5 origin is an unverified source claim). Stage11 FeatureMatrixPolicy exposes only **numeric_int**, with an explicit source unit. It does not accept uncalibrated float or native decimal values. Each TechnicalNumericFactor therefore pins the exact source unit plus a positive fixed-point value_scale. No conversion is inferred from a field name; raw/value_scale is an explicit *claimed interpretation*, not a verified unit conversion. If UAE supplies native fractions, the authoritative Stage03/11 contract must be explicitly extended later, rather than casting a float silently.

The Group A vs Group B mapping is type-checked. No Group C7 breakout-score column may serve as a technical predictor. High/low roles are already **defined by** Group C7 and are observational, outcome-selected, not interventions.

Typed stage07 snapshot, stage08 strata, stage09 outcome, stage10 matched controls, stage11 feature matrix, stage17 creator/media disjoint holdout, explicit holdout policy and external media lineage are required. The builder replays stage10/11/17 artifacts, rejects mismatched project, study SHA, controls/matrix/split SHA, wrong row/role or numeric column. In addition, each numeric cell must carry the exact Group A or Group B analysis SHA matching the *frozen source row*; that SHA does not authenticate producer origin or rights.

Only full matched high/low pairs with **both** posts inside discovery contribute. A pair crossing discovery and the confirmation/embargo scope rejects the entire report. Reserved pairs and unmatched discovery IDs remain visible. Preregistration ID and timestamp are explicit but **not attested**; the declared timestamp must precede the discovery cutoff.

## Frozen bins, exact estimates and support gates

TechnicalNumericPolicy freezes algorithm/policy version, five source SHA pins, registration claim, discovery pair minimum, and the entire sorted unique family of 1–16 TechnicalNumericFactor descriptions. Each factor pins a known metric kind, A/B family, exact observed numeric feature and source unit, positive integer fixed-point scaling, 1–15 strictly ascending positive integer cutoffs, per-role minimum posts in **each bin**, and distinct creator minimum in **each bin**. The implementation permits 1–16 half-open raw integer bins and up to 1000 discovery pairs. Negative, float, Boolean, absent, null, overflowing numeric values, silently inferred units and source-mismatched SHA are rejected or block the factor. No threshold mining or trimming sparse bins after looking at the results.

For boundaries c1 < ... < ck, bins are [0,c1), [c1,c2), ..., [ck,∞). For n original matched high/low pairs and each bin j:

- High-bin proportion = high_n_j / n.
- Low-bin proportion = low_n_j / n.
- Descriptive bin contrast = (high_n_j - low_n_j) / n.
- High fraction within bin = high_n_j / (high_n_j + low_n_j).

All proportions and contrasts use **exact Fraction arithmetic**, with recorded numerator and denominator, plus 12-place deterministic Decimal display. The monotonicity diagnostic checks the sequence of high fractions within **all predefined bins**, returning descriptive nondecreasing, nonincreasing, flat or nonmonotone shape. Every bin must meet the frozen per-role and creator minima, otherwise shape and paired mean are **blocked**, not calculated using only convenient supported bins.

The raw paired mean difference is sum(high_raw-low_raw)/n; the explicitly scaled mean is sum(high_raw-low_raw)/(n * value_scale). The report preserves observed min/max for both roles, complete high-bin × low-bin pair transitions and counts where high>low, high<low or equal. Those observed data ranges are **not uncertainty intervals**.

No defensible cluster-aware inferential p-values or per-bin uncertainty intervals are available yet. Accordingly, uncertainty_interval=null, p_value=null, adjusted_q_value=null, confirmed_effect=false, valid_inferential_p_value_count=0, uncertainty_status=blocked_no_calibrated_cluster_uncertainty. Stage15 bootstrap results cannot automatically be reused for the new selection and matched-bin family. The roadmap's valid uncertainty DoD remains **scientifically blocked**, not fabricated.

## Hand-calculated independent synthetic golden

Eight discovery publications form four original matched pairs; another eight are reserved for confirmation.

| Pair | High raw | Low raw | High bin (cutoff 5) | Low bin |
|---|---:|---:|---|---|
| 1 | 2 | 1 | 0 | 0 |
| 2 | 6 | 3 | 1 | 0 |
| 3 | 8 | 4 | 1 | 0 |
| 4 | 9 | 7 | 1 | 1 |

Bin0 high=1, low=3; high share within bin is 1/4. Bin1 high=3, low=1; high share is 3/4. The two descriptive bin contrasts are −1/2 and +1/2. The exact paired transition matrix (high bin row, low bin column) is [[1,0],[2,1]], and the paired raw mean difference is (1+3+4+2)/4=5/2. For a toy fixed-point value_scale=100, the scaled difference is exactly 1/40=0.025 — NOT a real UAE WPM or metric conversion.

An independent three-bin example with cutoffs (4,7), high counts (2,1,1), low counts (1,2,1), and high shares (2/3,1/3,1/2), is classified **nonmonotone**. Negative tests cover inadequate bins, both sides missing within one matched pair, incorrect unit or A/B analysis SHA, cross-holdout contamination, floats and Booleans, and rehashed forged outputs. These are mathematical fixtures, not empirical facts or calibrated inference.

## Reproducibility, rights and publication restrictions

CandidateTechnicalNumericReport serializes fully sorted canonical JSON plus SHA256. Replayer reconstructs the policy, original entire factor family and upstream inputs; rejects duplicate JSON keys, malformed UTF-8, NaN, noncanonical bytes, changed source or claimed units, fake approved_live and forged coefficients even when rehashed.

All outputs remain evidence_state=unverified, approved_live=false, verified_live=false, ready_for_analysis=false, confirmed_discoveries=null, can_publish_significance=false, causal_claim=false and heldout_confirmation_used=false. The source is not proven current, eligible, licensed, human reviewed or independently replicated. The calculated monotonic shape is purely descriptive and must **not** be advertised as performance uplift or a universal production recommendation.

No code outside the PIE branch, no Group A/B/C, UAE, QDP, SRE, main, database migration, model invocation or publishing flow was changed.

## Files and QA

- intelligence/patterns_insights/research/technical_numeric.py — strict typed numeric factor bins, paired arithmetic and canonical replay.
- tests/test_pie_technical_numeric.py — **24 synthetic/adversarial unittest methods**.
- intelligence/patterns_insights/research/__init__.py and intelligence/patterns_insights/__init__.py — exported APIs.
- docs/PIE_IMPLEMENTATION_TRACKER.md — durable stage progression and blockers.

The test fixture **MOCKS** previous stage10/11/17 replay and extends synthetic stage07/11 rows with A/B analysis hashes. This is not an authenticated producer export. Run on a full branch checkout:

    python -m unittest discover -s tests -p 'test_pie_*.py' -q
    python -m compileall -q intelligence/patterns_insights tests/test_pie_technical_numeric.py

**QA truth:** GitHub accepted stage22 code and test commits; checkout, local stage22 unittest, full suite, compileall, PG17 and real UAE E2E were **NOT RUN** in this environment. Direct GitHub network access from the local container failed DNS resolution. GitHub Actions status must be independently checked; **queued does not equal pass**. The 24 written methods are not 24 observed passes.

**NEXT PIE-MOD-23:** robustness, sensitivity and negative controls; retain the same descriptive-only/rights/inference restrictions.
