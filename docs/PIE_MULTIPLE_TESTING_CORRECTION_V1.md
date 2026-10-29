# PIE-MOD-16 — Multiple Testing & Discovery Correction

**Branch:** `feature/patterns-insights-full-module`  
**Contract:** `pie-multiplicity-candidate/v1`  
**Owner:** PIE, independent research module; **candidate-only**. **No real inferential p-value producer exists in PIE-MOD-00–15.**

## Scientific meaning and safety boundary

Previous PIE stages create a frozen `uae-to-pie/v1` snapshot (07), a strict stratum (08), preregistered outcome design (09), matched pairs (10), typed A/B matrix (11), coverage QA (12), registered marginal hypothesis *family* (13), descriptive effects (14), and exploratory paired bootstrap intervals (15). **Stage 15 contains `p_values: null` and `adjusted_p_values: null` by design**. Neither descriptive effects, frequency differences nor percentile bootstrap confidence limits can be repurposed into valid p-values.

PIE-MOD-16 implements **two distinctly labeled, nonproduction paths**:

1. `build_candidate_multiplicity_report(..., policy)` always preserves **all** 1–64 registered hypotheses (exact SHA and order) with `p_value=null`, `adjusted_q_value=null`, `tested_hypothesis_count=0`, and `correction_status=blocked_no_inferential_p_values`. This is the truthful default; it must not be interpreted as no effects or as no false discoveries.
2. `build_candidate_multiplicity_report(..., policy, synthetic=SyntheticPValueEvidence(...))` accepts **only** an explicit `provenance=synthetic_test_fixture_only` payload, with full frozen family and exact source SHA binding. This exercises BH arithmetic using **invented** p-values. It is always `correction_status=synthetic_fixture_arithmetic_not_inference`, `evidence_kind=synthetic_test_fixture_only`, and `can_publish_significance=false`; any `passes_synthetic_bh_threshold` field is a *test-only arithmetic assertion*, never a real hypothesis rejection.

No production p-value input path is provided. This is intentional: future real p-values require a separately trusted inferential producer, validated model assumptions, authenticated authorization and confirmatory holdout evaluation. A checksum alone cannot attest inference validity or current rights.

## Algorithm: Benjamini–Hochberg FDR

- `MultiplicityPolicy` freezes policy/method versions, `design_sha256`, `hypothesis_family_sha256`, exact `family_size` and decimal-text `fdr_level` in `(0,1]`. It forbids selecting convenient hypotheses, dropping missing p-values or changing method/ranking silently.
- Registered hypothesis IDs remain unique and sorted, as in PIE-MOD-13. The BH ranking is `(p-value, hypothesis_id)` to make ties deterministic.
- For sorted p-values p_(1)..p_(m), compute q_(i) = min(1, min_{j>=i}(m * p_(j) / j)). Traverse right to left so the adjusted q-values are monotone by rank. Python `Decimal` precision 100 avoids binary floating-point and ambient decimal-context changes.
- The denominator m **always includes the entire registered family**. A single missing member invalidates the synthetic correction vector. Zero and one p-values are allowed; negative, >1, nonfinite, scientific notation and float inputs are refused.
- A BH-FDR statistical claim requires genuinely valid p-values and appropriate dependence assumptions (independence or specified positive dependence). These assumptions are **not established** for PIE's matched pairs, shared creators or exploratory feature selection. Synthetic q-values are for arithmetic QA only.

## Lineage, offline replay and immutable output

The function takes typed frozen artifacts from PIE-MOD-07..15: source snapshot, strata, outcome, controls, A/B matrix, coverage, marginal candidates, effect sizes and uncertainty. It calls the stage15 offline replay (which in turn replays prior stages), checks project ID, registered family digest, hypothesis order and count, and the exact versions/design and source SHA values. The full original family is included even if no testable p-values exist.

`CandidateMultiplicityReport` stores immutable canonical JSON, a SHA256 digest and explicit provenance. `replay_candidate_multiplicity_report` checks duplicate JSON keys, malformed/NaN input, modified family or policy, and independently rebuilds the report. Rehashing a forged q-value does not make it a valid replay. However, offline replay still does not authenticate real provenance.

The result **always** has `verified_live=False`, `ready_for_analysis=False`, `can_publish_significance=False`, `confirmed_discoveries=null`, and `causal_claim=False`. There is no production p-value ingestion or insight publication API. No UAE, Group A/B/C, SRE, QDP, PostgreSQL, model runtime, or main-branch changes.

## Synthetic golden and QA

For invented p-values `(0.01, 0.02, 0.03, 0.20)` with m=4 and BH level 0.05, manually computed adjusted values are `(0.04, 0.04, 0.04, 0.20)`. The golden file `tests/fixtures/pie/pie_bh_golden_v1.json` pins both blocked and synthetic artifact digests. It is **synthetic and does not prove a significant result**.

Tests cover all-null default p-values, complete-family requirements, exact p-value and policy syntax, ties, unsorted values, zero and one, Decimal-context independence, changing seed/versions, forgery, rehashed tampering and replay, and rejecting attempts to claim live production evidence. The current local execution uses the actual PIE-MOD-16 code with *stubbed prior-stage imports and offline replay*. The committed test methods need full GitHub checkout/CI to exercise real upstream contracts. Do not claim a green full-suite or PostgreSQL run without observing it.

Suggested commands in the full branch:

```bash
python -m unittest discover -s tests -p 'test_pie_*.py' -q
python -m compileall -q intelligence/patterns_insights tests/test_pie_multiplicity.py
```

See `docs/PIE_IMPLEMENTATION_TRACKER.md` for recorded executed test counts and CI state. Current upstream stage15 workflow completion was **cancelled**, not passed.

## Next stage

PIE-MOD-17 will freeze creator-aware and temporal holdout splits prior to hypothesis search. No inferential p-values or actual corrected discoveries should be published until a statistically valid test generator, confirmatory evaluation and authenticated UAE source/rights/review are available.
