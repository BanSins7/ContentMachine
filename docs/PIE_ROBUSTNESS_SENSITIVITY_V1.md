# PIE-MOD-23 — Robustness, Sensitivity and Negative Controls

**Branch:** feature/patterns-insights-full-module (only PIE files).  
**Wire version:** pie-robustness-sensitivity-candidate/v1.  
**Status:** implemented *offline exploratory sensitivity* of stage22 numeric factors only. **NOT** causal, inferential or production verified.

## Frozen sources, legitimate scope and registered controls

The candidate consumes stage07 snapshot, stage08 strata, stage09 high/low C7 outcome, stage10 **whole original matched pairs**, stage11 typed numeric feature matrix, stage17 disjoint holdout split/policy/media lineage and stage22 frozen technical-factor report/policy. Its builder replays the stage22 artifact against all these typed original sources (stage22 itself replays controls, feature matrix and holdout) and checks project, study design, snapshot, controls, matrix, holdout, technical report and policy SHA identity.

Stage23 RobustnessPolicy freezes source digests, method/version IDs, claimed registration timestamp and ID, deterministic uint64 seed, 32–512 sign-flip replicates, minimum complete matched pairs, minimum independent creator keys, minimum surviving pairs after deleting any one creator, number of prespecified largest-absolute-difference pairs to trim, exact decimal negative-control exceedance tolerance, and a **complete ordered sensitivity plan for every original stage22 numeric factor**. Each SensitivityFactor declares 1–8 unique sorted positive raw integer *alternative exposure cutoffs*, never estimated from the same C7 outcomes.

Registration time is constrained to the stage17 discovery cutoff; authenticity of real preregistration remains unverified. No source-rights, human-review, niche, independent provider or valid C7 causal/measurement decomposition is implied.

Only original full matched pairs with **both** videos in stage17 discovery are used. Confirmation and embargo samples remain reserved; original high/low roles are never redefined. Duplicate matched video IDs, changed row SHA, wrong role, cross-arm pair, changed upstream artifact, bogus metric value or mismatched registered factor list fail closed. Missing and insufficiently supported stage22 factors remain present in the full family with blocked status rather than disappearing from analysis.

## Reproducible sensitivity tests

For each eligible stage22 numeric factor define exact per-pair raw differences d_i = high_i − low_i, with n pairs; original descriptive raw difference D = sum(d_i)/n. All calculations use **fractions.Fraction** and deterministic 12-decimal display formatting.

1. **Leave-one-creator-out:** For every distinct creator key, remove *all* original matched pairs in which that creator appears in either role. Recompute the exact average over remaining complete pairs; insufficient surviving pairs result in **unsupported**, not a fake nonzero estimate. The full sorted list of creators, retained pair counts, difference and direction is kept.
2. **Outlier sensitivity:** Before outcome inspection, fix how many largest |d_i| pair differences to remove. Ties are broken by original stable pair key. Remove each pair intact, preserve the removed pair keys, then re-estimate the exact signed difference. This is a robustness diagnostic and **does not** repair confounding, selection or outliers causally.
3. **Alternative exposure thresholds:** Recompute for the original stage22 first registered numeric bin cutoff *and* every registered alternative raw cutoff: [high value≥threshold]−[low value≥threshold], divided by n. Record high/low hits, complete denominator and exact threshold-based direction. This is a difference in observed **exposure threshold prevalences**, not a new C7 endpoint, adjusted association, causal uplift, or replacement for the original continuous raw estimand. Compare alternative **signs against the original threshold sign**; if any change, downgrade.
4. **Negative controls:** Deterministic SHA256-counter whole-matched-pair sign flips, using explicit uint64 seed, factor ID and draw+pair indexes. Exactly 32–512 signs per outcome-defined discovery sample: count how often |random signed sum| ≥ |original sum| and report exact *descriptive* exceedance fraction. No global RNG state, nonreproducible floating point, independent post reshuffling, inferred p-value, or calibrated significance is claimed. If that descriptive fraction exceeds the **preregistered** tolerance, mark fragile. The threshold itself is not interpreted as a valid frequentist alpha cutoff.
5. **Discovery CV subgroup audit:** Stage17's frozen creator/media-disjoint **discovery-only** folds must partition discovery exactly, with ordered nonduplicate indices. A matched pair crossing folds is recorded and **blocks this CV audit**, not dropped. Each fully allocated fold must have at least the declared minimum complete pairs; its exact mean raw direction is compared against the overall descriptive direction. Insufficient/contrary fold directions downgrade the factor. This **does not touch stage17 confirmation arm**, and creator/media CV within one discovery sample is not independent external replication.

Stages 09–10 C7 outcome observations and windows CANNOT be redefined from an already fixed stage22 high/low role assignment. Authentic **alternate C7 outcome windows** require a newly qualified UAE source snapshot, C7 endpoint recomputation, frozen new outcomes, new matching and fresh split/replay. Report explicitly says alternate_outcome_window_status=blocked_requires_new_qualified_uae_replay. Likewise no *held-out confirmation subgroup* is inspected, to avoid leaking the untouched confirmatory sample.

Group C7 raw growth dominance remains unknown: c7_raw_growth_dominance=unknown_requires_source_metric_decomposition. The module refuses to fabricate a numeric growth-driver attribution from raw Group C7 scores.

## Fragility and downgrade semantics

Each factor and its full reproducible log are classified using immutable thresholds and complete support:

| Classification | Meaning |
|---|---|
| directionally_robust_descriptive_only | all defined sensitivity directions preserved, adequate discovery-fold coverage, descriptive null-control fraction below registered tolerance |
| fragile_leave_creator_out_or_outlier_flip | some creator deletion or original-pair outlier removal changes signed direction |
| fragile_alternate_exposure_definition | a registered alternative exposure cutoff changes sign relative to original stage22 cutoff |
| fragile_negative_control_high_exceedance | exact uncalibrated diagnostic exceeds registered threshold |
| blocked_insufficient_support_or_zero_baseline | too few creators/pairs for a defined check, or zero original signed contrast |
| blocked_matched_pair_crosses_discovery_cv_folds | an original matched pair crosses distinct discovery CV folds |
| blocked_insufficient_discovery_cv_fold_pairs | one or more frozen discovery CV folds lack enough complete pairs |
| fragile_discovery_cv_fold_direction | subgroup fold direction differs from original raw direction |
| blocked_upstream_numeric_or_missing | stage22 source was sparse/blocked or raw observations incomplete |

*All* statuses are **candidate QA labels**, never independently confirmed patterns. The report lists all downgraded factor IDs and never grants publication. A stable descriptive check does not establish a valid confidence interval or p-value.

For a factor with missing stage22 data, report that factor as blocked, keep its identity, do not silently impute or omit it. All formulas use original n matched pairs, fixed semantics and fixed decimal formatting.

## Independent synthetic arithmetic oracles

Hand-calculated test source: four original discovery pairs, high raw (2,6,8,9), low raw (1,3,4,7). Differences (1,3,4,2) give original mean **5/2**. Removing the largest |difference| pair (third pair, key 5:6) leaves (1,3,2) with mean **2**. Leaving out the first creator removes the first matched pair and gives (3+4+2)/3 = **3**.

Original exposure threshold 5 gives high hit count 3, low hit count 1, prevalence contrast (3−1)/4 = **1/2**. Registered alternative threshold 7 gives high count 2, low count 1 and contrast **1/4**. With frozen two-fold assignment pairs 1–2 in fold 0 and pairs 3–4 in fold 1, the fold means are **2** and **3**. Any pair split across the folds blocks this check.

Negative oracle: one extreme positive pair and three mild negative pairs can leave the total mean positive while removing the extreme pair flips sign negative; it must be downgraded. An exposure cutoff yielding a zero prevalence contrast must **not** inherit the nonzero continuous mean direction. A null raw mean cannot be called robust. Creator overlap among pairs is not transformed into independent samples. Hand-calculated checks and reproducible SHA controls are covered in tests.

These values are *test fixtures*, **not discoveries about real videos or verified stage22 data**.

## Replays, evidence limits, running tests

CandidateRobustnessReport serializes canonical sorted JSON + SHA256. replay_candidate_robustness_report rebuilds policy/registered complete factors and reruns all source checks, preserving exact bytes. Duplicate keys, nonfinite JSON, wrong hashes, malformed UTF-8, modified upstream rows/policies, rehashed forged effects and fake approved_live are rejected.

Top-level permanent restrictions: evidence_state=unverified; approved_live=false; ready_for_analysis=false; verified_live=false; confirmed_discoveries=null; can_publish_significance=false; causal_claim=false; valid_inferential_p_value_count=0; independent_out_of_sample_replication=false. Negative controls are **not** inferential p-values because high/low were selected by outcome, assumptions for arbitrary sign-flipping are not established, creators can repeat and current source rights/review have not been checked.

Files: intelligence/patterns_insights/research/robustness.py; tests/test_pie_robustness.py (**27 synthetic unit methods**); PIE research and root export modules; docs/PIE_IMPLEMENTATION_TRACKER.md.

The test fixture mocks stage22 replay and earlier stage10/11/17 replays, but runs stage23 arithmetic, CV partition, provenance and canonical report replay. No SQL, PostgreSQL schema, CI workflow or peer branch changes.

Suggested on full checkout:

    python -m unittest discover -s tests -p 'test_pie_*.py' -q
    python -m compileall -q intelligence/patterns_insights tests/test_pie_robustness.py

**QA status:** Github commits accepted/refetched; *no executed local unittest, compileall, PostgreSQL17, live UAE end-to-end or observed green GitHub CI* in this turn. Do not mistake 27 authored tests for 27 PASS. Peer source, rights/reviewer, calibrated inference and authentic alternate outcome windows remain blocked.

**NEXT PIE-MOD-24:** pattern quality scoring and explicit human-approved release gates, without arbitrary numerical thresholds or promotion from candidate-only evidence.
