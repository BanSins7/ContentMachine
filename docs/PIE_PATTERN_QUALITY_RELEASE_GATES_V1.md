# PIE-MOD-24 — Pattern Quality Rubric and Release Gates (Candidate v1)

Owner: intelligence/patterns_insights, branch feature/patterns-insights-full-module.
Schema: pie-pattern-quality-gates-candidate/v1.
Evidence: stage22/23 descriptive research candidates only. Not qualified live UAE.

## Scope and deliberately absent numerical scoring

This stage applies an immutable, explicit **11-axis quality rubric** to the entire registered stage22 technical-numeric factor family, preserving original stage23 robustness statuses. There is **no weighted score**, no post-selected effect threshold, no invented 0–100 scale, and no significance claim.

PatternQualityPolicy pins the design, snapshot, matched controls, feature matrix, stage17 holdout, stage22 numeric report and stage23 robustness report by SHA. All stage22/23 source reports are independently replayed through the original upstream source chain before quality evaluation. No score, source authorization, or genuine rights/reviewer attestation can be inferred from a matching SHA, a claimed registration time or a review-protocol ID. The claimed policy registration must predate discovery, but is explicitly unauthenticated.

## Required rubric axes (all 11 included for every factor)

| Axis | Evidence and possible rubric state |
|---|---|
| sample_support | Original matched discovery pairs, all registered bins and existing explicit support minima; descriptive_pass or blocked |
| effect_magnitude_and_direction | Stage22 raw/scaled exact difference and descriptive monotonicity; descriptive_pass or blocked, no new arbitrary magnitude cutoff |
| calibrated_uncertainty | Blocked; no valid creator-cluster-aware interval for this estimator |
| multiple_testing | Blocked; no real inferential family p-values, synthetic stage16 BH arithmetic is not validation |
| holdout_confirmation | Blocked; confirmation cohort intentionally untouched |
| creator_and_fold_stability | Stage23 whole-pair creator deletion, outlier trim, alternative exposure, deterministic sign flips and discovery CV; descriptive_pass, failed or blocked |
| missingness | Stage22 absent/null and full-bin audit; descriptive_pass or blocked, never impute zero |
| confounding_and_c7_growth | Unverified; creator/niche confounders and C7 growth dominance remain unresolved |
| source_identity_and_rights | Unverified; UAE live-qualified identity, authentic A/B5 provenance, source license and rights not established |
| preregistration | Unverified; frozen design timestamp is a claim, not authenticated registration |
| reviewer_authorization | Unverified; review-protocol claim is not a human signoff |

Each dimension stores name, state, machine-readable reason and source-bound evidence. The 11-axis ordered rubric is immutable. The only axis states are descriptive_pass, failed, blocked and unverified.

## Deterministic state transitions and release restrictions

Each factor is evaluated in exact original family order, without dropping any unsupported member. Decision taxonomy: exploratory, supported, rejected, needs_review.

- **exploratory** — original stage22 numeric bins pass registered descriptive support and stage23 marks the factor directionally_robust_descriptive_only. It is only descriptive exploratory evidence, not a justified production recommendation.
- **rejected** — stage23 identifies an explicit fragile_* sensitivity: leave-one-creator or outlier sign flip, alternate exposure sign reversal, high *descriptive* negative-control exceedance, or opposed discovery-fold direction. This is an internal quality downgrade, **not** a frequentist statistical rejection.
- **needs_review** — stage22 data are sparse/missing, or stage23 has blocked source, zero baseline, insufficient creator/fold support or a matched pair crossing discovery folds.
- **supported** — reserved in the declared taxonomy, **unreachable with this source and pipeline**. Requires real authenticated UAE and source rights, genuine reviewer approval, calibrated inferential family with proper multiple-testing adjustment and independent held-out confirmation. A forged approved_live flag, mock approval token or synthetic p-value cannot create this state.

Decision transitions always specify original unassessed status, target and exact reason_code. The complete decision_counts table contains the four taxonomy labels; supported always remains zero. Quality is not silently coerced into a numeric score. For each factor quality_score=null, eligible_for_supported=false and eligible_for_release=false.

Invariant report gates: evidence_state=unverified; approved_live=false; supported_state_reachable_with_current_source=false; authentic_reviewer_approval=false; source_rights_authenticated=false; independent_holdout_confirmed=false; calibrated_multiple_testing_p_values_available=false; can_release_to_registry_as_supported=false; can_publish_patterns=false; causal_claim=false; confidence_intervals=null.

A stage22 fake p_value, adjusted_q_value, uncertainty_interval or confirmed_effect=true raises quality_inference_forgery. A stage23 forged may_promote_as_universal_pattern=true raises quality_publication_forgery. Changed sample size, registered family, source/report SHA or stage23 status without the required complete trace fails closed.

## Synthetic hand-check examples

Stage22 fixture: four discovery matched pairs, high raw=(2,6,8,9), low raw=(1,3,4,7), original mean raw difference=5/2. The high/low groups are C7-defined, not interventions. Stage23 runs original whole-pair leave-creator-out, outlier trimming, registered exposure thresholds, seeded sign-flip diagnostics and discovery CV subgroup checks.

- Stage22 full-bin support + stable descriptive stage23 result -> exploratory, never supported.
- Stage22 full-bin support + fragile negative-control or creator direction flip -> rejected.
- Stage22 missing cells/unsupported bins + blocked stage23 -> needs_review.
- Stage22 full-bin support + cross-fold matched pair -> needs_review.
- Any candidate source + claimed supported/publishable/significant output -> typed error, no release.

These are synthetic arithmetic cases. They establish no genuine video performance findings, calibrated p-values or legal rights.

## Immutable replay and integration

The builder requires exact typed stage07 snapshot, stage08 strata, stage09 outcome, stage10 matched controls, stage11 feature matrix, stage17 holdout/policy/media lineage, stage22 numeric policy/report, stage23 robustness policy/report and explicit stage24 quality policy. No other module or main branch is edited.

CandidatePatternQualityReport stores canonical sorted JSON plus SHA256. replay_candidate_pattern_quality_report rebuilds the frozen quality decision from the whole source family, rejects malformed UTF-8, NaN, duplicate JSON keys, noncanonical spacing, wrong source SHA, rehashed forged decisions and false live publication flags. PIE root and research package export both functions and the policy/report types.

No SQL migration, publication flow, external model, real reviewer action, permanent registry record or API access occurs in this stage. Stage25 Immutable Pattern Registry may ingest these states only as **candidate evidence**, with distinct immutable revisions and no unsupported automatic promotion.

## QA

Files: intelligence/patterns_insights/research/pattern_quality.py, tests/test_pie_pattern_quality.py (**27 authored synthetic unittest methods**), docs/PIE_IMPLEMENTATION_TRACKER.md and this document.

The synthetic fixture MOCKS stage23 replay, and its stage23 fixture mocks stage22 and earlier controls/matrix/holdout replay. New quality rubric arithmetic and canonical replay are executed when tests run, but the fixture is NOT a live UAE E2E validation.

Run on a full checkout:

    python -m unittest discover -s tests -p 'test_pie_*.py' -q
    python -m compileall -q intelligence/patterns_insights tests/test_pie_pattern_quality.py

**QA truth at authorship:** GitHub commits accepted. Local Python unittest, compileall, PG17 and real UAE integration NOT RUN; the most recently checked GitHub workflow was QUEUED, not green. Stage24 rule implementation can be tracked as IMPLEMENTED while production authorization, accepted source rights, reviewer attestation, genuine held-out replication and statistical inference remain explicitly BLOCKED.

Next: PIE-MOD-25 — Immutable Pattern Registry.
