# PIE-MOD-14 — Descriptive Effect Size Estimation

**Owner:** `feature/patterns-insights-full-module`. **Wire:** `pie-descriptive-effects/v1`. **Status:** pure **candidate-only descriptive estimates**; not statistically significant, causal, population-representative, or authenticated.

## Input and immutable lineage

`build_candidate_descriptive_effects(snapshot, strata, outcome, controls, matrix, coverage, discovery, policy)` requires the exact typed frozen **seven** preceding PIE artifacts and a mandatory versioned `EffectEstimationPolicy`. It **replays and reconstructs** the complete Stage07–13 chain via Stage13 `replay_candidate_marginal_discovery`, including checked scope, feature dictionary, matched controls, coverage suppression/eligibility and the entire predeclared hypothesis family. SHA integrity alone is never a source authorization or reviewer signature. No UAE, SRE, QDP, A/B/C, PostgreSQL or model runtime is imported.

Effect policy pins the research `design_sha256`, estimator method version, independent minimum per-arm observed `N`, rounding precision (0–12 places), comparator direction **high minus low**, strictly **unweighted observed only** estimator, **undefined** zero-baseline risk ratio, and numeric **discrete-bin total variation**. The effect stage must calculate **every** preregistered marginal hypothesis without ranking, silently dropping outcomes, or increasing the registered search space.

## Exact effect definitions

| Typed Stage13 hypothesis | Effect | Definition | Unit / zero policy |
|---|---|---|---|
| `numeric_distribution` | Median difference | `median(high observed) − median(low observed)` | Exact `Decimal` raw difference, **declared numeric unit from frozen Stage11 feature column** (e.g. `seconds`) |
| `numeric_distribution` | Discrete-bin total variation | `½ × Σ_k abs(h_k/H − l_k/L)` over frozen explicit numeric histogram boundaries | Dimensionless fraction `[0,1]`, exact reduced rational and rounded presentation |
| `boolean_equals`, `category_equals`, `multilabel_contains`, `sequence_equals` | Risk difference | `h_hits/H − l_hits/L` | Difference of **observed sample frequencies**, dimensionless probability fraction; can be negative |
| Same predicates | Risk ratio | `(h_hits/H)/(l_hits/L)` | Dimensionless; if `l_hits = 0`, return `null` and `undefined_zero_baseline`, **never** infinity, `+1` pseudocount or an invented adjusted estimate |

`H`/`L` are **observed values only**, not all scanned or selected records; null, absent and row counts are kept separately. All proportion arithmetic uses Python `fractions.Fraction`, records the **reduced exact numerator and denominator** and applies an explicitly configured decimal display rounding using `ROUND_HALF_EVEN`. Histograms must reconcile with observed `N` and have exactly the predeclared bins. Numerical medians remain exact strings, are never normalized to `[0,100]`, and the raw Group C Breakout Score remains an **outcome label only** (not a predictor or matching covariate). B1 duplicate labels count **once per publication** through Stage13, and B3 requires exact ordered sequences. Sample weights are pinned upstream but are **not used** by this explicitly unweighted estimator (no implicit inverse-probability or importance weighting).

## Quality gates, hard refusals, negative cases

- Recompute and validate the **entire** exact Stage07–13 candidate chain; reject cross-project/cross-version, truncated/changed search family, missing or mismatched artifacts, malformed JSON, duplicate JSON keys, nonfinite input and resealed effects even if the attacker updates the outer SHA.
- Refuse numeric effects without an explicit source feature unit. Refuse observed sample counts below explicit minimum; impossible counts, zeros and broken histogram reconciliation must fail rather than be imputed. Refuse weakening the source-registered minimum. Fail the **whole** effect artifact instead of publishing only attractive comparisons.
- Preserve selection universe and actual exclusions by copying the pinned Stage13 source population, along with source discovery/coverage/matrix hashes, registered-family hash, exact `pairs_n`, hypothesis count, declared policy and unit.
- Always `evidence_state='unverified'`, `approved_live=false`, `verified_live=False`, `ready_for_analysis=False`, `causal_claim=false`, and `p_values`, `adjusted_p_values`, `confidence_intervals` = `null`. Mandatory warnings include matched-pair dependence not modelled, no multiple-test correction, selection bias unknown and no uncertainty intervals. A positive offline policy test **is not** an actual approval or rights check. This stage cannot publish conclusions, request a model, or claim effect/causality.

## Regression and independent hand-computed goldens

New `tests/test_pie_effects.py` includes 15 full-repository `unittest` methods covering correct frequencies/denominators, B1 and B3, numeric units, zero denominators, immutable replay, changed policy versions, adversarial modifications and bad counts. `tests/fixtures/pie/pie_effects_golden_v1.json` independently pins **invented, non-authoritative** results: risk difference `3/4 − 1/5 = 11/20 = 0.55`, risk ratio `(3/4)/(1/5) = 15/4 = 3.75`; histogram total variation `1/2` and median difference `8 − 3 = 5`; a zero-baseline ratio is undefined.

Suggested full-branch QA:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_pie_*.py' -q
python -m compileall -q intelligence/patterns_insights tests/test_pie_effects.py
```

Local isolated smoke is separately provided in `/mnt/data/pie_stage14/smoke_stage14.py`, and independent arithmetic goldens in `/mnt/data/pie_stage14/golden_oracles.py`. These use **real local stages 11–14 but stubbed earlier stages 07–10**; do not present them as a full-repository result. SQL/PostgreSQL 17 fresh/upgrade and live UAE E2E are independent checks requiring their own environments. No migration is required for this pure compute-only stage.

## Subsequent work / strict boundaries

PIE-MOD-15: uncertainty intervals and appropriate creator clustering/paired-sample design; PIE-MOD-16: multiplicity correction; PIE-MOD-17: holdout/confirmation; PIE-MOD-23/24: robustness and release quality gates. Live integration also requires UAE UA-MOD-25 and actual current source-rights/reviewer authorization. Do not claim research-ready just because a descriptive fraction is mathematically correct. No `main`, UAE, or other-module changes in this stage.
