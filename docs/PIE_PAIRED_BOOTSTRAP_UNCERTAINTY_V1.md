# PIE-MOD-15 — Uncertainty Estimation & Confidence Intervals

**Owner:** `feature/patterns-insights-full-module`. **Contract:** `pie-paired-bootstrap-uncertainty/v1`. **Status:** implemented exploratory/offline candidate. **Not** authenticated UAE evidence, confirmatory confidence coverage, real human approval, statistical significance or causal uplift.

## Exact estimator and assumptions

`build_candidate_uncertainty_intervals(snapshot, strata, outcome, controls, matrix, coverage, discovery, effects, policy)` recomputes the full stage14 source artifact using `replay_candidate_descriptive_effects`, which replays stages 07–13. It then extracts the original 1:1 case/control pairs and the *original observed* Group A/B feature cells; raw Group C score is never a resampling covariate or predictor. It compares every re-derived full-data point estimate against stage14 before resampling.

`UncertaintyPolicy` requires all of: explicit `policy_version`, `method_version`, research `design_sha256`, integer `seed` in [0,2**64), `resamples` in 400..2000 and divisible by 40, `minimum_independent_pairs` in 8..1000; exactly `confidence_level='0.95'`, `resampling_unit='matched_pair'`, `creator_dependence='reject_creators_reused_across_pairs'`, `missingness='block_if_any_pair_missing_feature'`, `interval_method='nearest_rank_percentile'`, and `ratio_zero_rule='undefined_if_any_resample_has_zero_low_hits'`. Unsupported methods fail closed. Also enforce `pairs * hypotheses * resamples <= 1,200,000` for bounded work.

**Pair bootstrap:** sample whole original case/control pairs with replacement, never individual videos. SHA-256 counter-based uint64 sampling with rejection avoids modulo bias and makes all bootstrap draws independent of Python's `random` implementation. The *same* resampled pair indices are applied to all registered hypotheses to retain across-feature dependence; all observations and their pair relationships are fixed within each draw. Any creator appearing in more than one original matched pair blocks the entire artifact: a true creator-cluster estimator belongs in a later revision. Two posts from the same creator within the **same** matched pair are allowed; no creator may occur across different pairs.

**No silent complete-case shrinkage:** each selected feature must be `state='present'` in **both** members of **every** frozen matched pair; `null`, `absent`, partial/unobserved and unknown states block, rather than changing denominator per draw. Every stage13 registered hypothesis is evaluated; none may be dropped after observing results. No implicit sample weights or constructed controls.

**Statistics per bootstrap draw:** binary/boolean/category/B1 multilabel/B3 exact-sequence indicators derive from the same locked target predicates as stage13. Compute unweighted high-minus-low difference of proportions as exact `Fraction`. Compute the high/low risk ratio only if all bootstrap draws have nonzero low hits; otherwise ratio interval is **null** with explicit `undefined_bootstrap_zero_baseline`. For numeric Group A features, recompute two medians with `Decimal` and the binned distribution total-variation distance with exact `Fraction`; bins come from the immutable hypothesis. All numeric units come from stage14's original frozen feature dictionary, never an inferred unit.

**Reported bounds:** for 400/800/etc draws, lower index `ceil(0.025*B)-1`, upper index `ceil(0.975*B)-1`, zero-indexed sorted draw statistics. These are **nonparametric empirical percentile** bounds and can be biased (e.g. discreteness, strongly skewed medians, dependency, pre-selection); not calibrated confidence guarantees. They are NOT adjusted for multiple tests, post-selection discovery, creator clustering, nonrandom sampling, missingness, selection/survivorship bias or causal confounding. Calling them 95% intervals describes the resampling percentile *method*, not validated population coverage. `p_values=None`, `adjusted_p_values=None`, `causal_claim=False` always.

## Artifact and trust boundary

The returned immutable `CandidateUncertaintyIntervals` contains source effect/discovery/matrix/control SHA links; the complete research family hash and N; explicit methodology, seed, resample count, bound values and units; a conservative reason for undefined ratio; design/coverage warnings and canonical SHA256. It is always `evidence_state='unverified'`, `approved_live=False`, `verified_live=False`, `ready_for_analysis=False`.

`replay_candidate_uncertainty_intervals` does *not* trust a self-rehashed JSON. It rejects malformed JSON/duplicate keys/nonfinite values, cross-schema upgrades, mismatched SHA, incomplete policy and any altered intervals by **rebuilding the entire artifact** from original inputs. Offline replay **never reauthorizes** current source rights or reviewer approval; a successful hash cannot be used to publish insights.

## Unit test truth

`tests/test_pie_uncertainty.py` contains independent synthetic 8-pair oracle cases: constant median difference 10 seconds, TV=1, binary difference=1, undefined zero-control ratio, SHA256 sampler prefix, deterministic replay/seed changes, forced missingness and creator overlap refusal, resource/size caps, negative policy/JSON tests, forged/rehashed result refusal. The synthetic fixture `tests/fixtures/pie/pie_uncertainty_golden_v1.json` pins digest for eight fabricated pairs and 400 draws; its synthetic upstream effect replay is **mocked**. An additional repository test exercises the real stages 07–14 synthetic *single-pair* chain and expects `uncertainty_insufficient_pairs`. No fabricated positive case is evidence of production research authorization.

Run in full standalone branch:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_pie_*.py' -q
python -m compileall -q intelligence/patterns_insights tests/test_pie_uncertainty.py
```

**Local execution environment:** Python 3.13 can load stage11–14 real algorithm source with stubbed stage07–10 interfaces; see `docs/PIE_IMPLEMENTATION_TRACKER.md` for actual local/new CI results. Disposable PG17 fresh/upgrade must be verified when a test database is available. This is a pure-Python stage without SQL migrations, and those outstanding historical PG17 checks remain outstanding. GitHub Actions is not PASS until a completed result is observed. Actual UAE `uae-to-pie/v1` authenticated row exporter UA-MOD-25, current rights and approved research review remain upstream dependencies.

## Deferred by design

No creator-cluster or block bootstrap for authors appearing in multiple pairs, weighted estimation, BCa/studentized coverage, multiplicity correction, holdout validation, missingness adjustment, full-scale performance benchmarking, confidence calibration study, production inference or LLM/insight publication. Follow-on PIE-MOD-16 governs multiplicity; PIE-MOD-17 governs holdout. Never integrate to `main` without explicit approval.
