# PIE-MOD-19 — Temporal, Cohort and Provider Stability (Candidate Only)

**Branch/owner:** `feature/patterns-insights-full-module` / `intelligence/patterns_insights`  
**Schema:** `pie-temporal-cohort-stability-candidate/v1`  
**State:** pure *descriptive* stability audit implemented. **NOT** a confirmatory discovery, causal effect, a trusted live pipeline or multi-provider replication.

## Scientific contract

PIE-MOD-18 produces an observational high-minus-low **binary A/B feature prevalence difference** on outcome-defined Group C7 high/low role assignments, using only the discovery arm of a stage17 holdout. Stage19 repeats **the paired, unadjusted descriptive difference** across **predeclared** subgroups of this *same* discovery sample. The stage19 grouped differences are not the same estimand as the pooled-cell *adjusted* stage18 estimate; they cannot be interpreted as a decomposition or independently confirmed p-value for that estimate.

A report with consistent signs is *directional robustness within a frozen exploratory sample only*, **not an independent holdout replication or a causal uplift**. In particular, correlation between windows, repeated creators, outcome-dependent matching and source selection prevent generalization. The confirmation arm remains untouched. No p-values, BH-FDR inference, population uncertainty bounds, approval, pattern publication or LLM claims are created.

## Frozen policy and scope

`StabilityPolicy` requires explicit source snapshots and SHA for stage10 controls, stage11 A/B feature matrix, stage17 holdout, and stage18 adjustment report; both algorithm/policy versions; frozen `registered_at` declared no later than the discovery cutoff; *contiguous, timezone-aware, half-open* published-at windows with 2–32 distinct labels; an ordered unique subset of predefined dimensions; minimum matched pairs and independent creator IDs **per group**; at least **two groups** to classify stability; explicit nonzero Decimal direction floor and required pair coverage. The sample scope is strictly `discovery_only`, pair boundary `reject_cross_group_without_dropping` and classification `descriptive_directional_robustness_only`. Neither cohort definitions nor cutoffs may be changed post hoc without a new SHA and explicit replay.

The source is **not fetched directly** from UAE/Group A/B/C/QDP/SRE. The builder requires the existing `CandidateAdjustmentReport` to be structurally supported and **replays** it against all pinned stage07–18 typed artifacts. Exact project/design/controls/feature/holdout/adjustment digests must match. Stage17 has already excluded cross-creator and cross-media identity leakage between discovery and confirmation. Stage18 additionally blocks any original matched pair that crosses the holdout split.

For each retained discovery matched pair, stage19 reads one original typed Boolean A/B feature `True` or `False` per high/low publication, with row ID, role, source row SHA and exact stage18 feature name. Null/missing are refused, not mapped to `False`. Publication and observation times are timezone-normalized; future-dated observations are rejected. Rows outside every registered publication window block the run rather than being silently excluded.

## Explicit stability dimensions

The allowed ordered dimension set is:

- `publication_window`: explicit registration-time contiguous half-open `published_at` windows;
- `creator`: exact source `creator_key` (will often block when each pair contains two creators);
- `niche`, `topic`: previous stage18 *unverified* external prepublication claims, not a trusted live taxonomy;
- `age`: frozen observation-after-publication seconds in stage18-approved brackets;
- `posting_time`: frozen **UTC** hour buckets; local audience time is not assumed;
- `platform`: source platform. A single strict outcome stratum generally contains one platform, so cross-platform stability remains blocked;
- `source_provider`: exact stage07 UAE source pointer. One frozen source cannot establish cross-provider replication and yields insufficient groups.

If high and low matched members land in different labels for a dimension, the **entire pair is listed** as `cross_group_pair_not_comparable`; it never contributes to any group for that dimension. Since complete discovery-pair coverage is required, even one such crossing makes that **dimension blocked**, rather than cherry-picking a favorable reduced subset. All discovery pairs remain accounted for. At least two supported groups must be available. Creator reuse across groups is counted and `cross_group_creator_independence_established=False` always; repeated creators do not become independent observations by appearing in two windows.

## Exact descriptive direction algorithm

Each group comprises whole matched high/low pairs only, with original cases counted once. For group `g`:

```text
D_g = (number of True high publications - number of True low publications) / matched_pairs_g

positive: D_g >= explicit direction_floor
negative: D_g <= -direction_floor
near_zero: otherwise
```

Statistics use exact `fractions.Fraction` for all estimands and threshold comparisons (the floor is parsed from decimal text), plus explicitly labeled 12-place decimal display formatting with deterministic Decimal context. No floating-point rounding changes which side of a threshold an effect occupies.

A dimension result can be:

| Status | Meaning |
|---|---|
| `consistent_direction_descriptive_only` | 2+ adequately supported registered groups, all positive or all negative |
| `unstable_direction_reversal` | 2+ adequately supported groups include both positive and negative |
| `unstable_weak_or_mixed_direction` | Complete support but near-zero or mixed directional classifications |
| `blocked_insufficient_subgroup_support` | Too few groups, posts/pairs or distinct creators for any group |
| `blocked_incomplete_or_cross_group_coverage` | A pair could not be fully and comparably allocated to that dimension |

**All** statuses are scientific QA flags only. Even consistent groups must not be promoted to confirmed/verified patterns. The top-level report `audit_status=candidate_descriptive_stability_audit_only` contains `eligible_for_pattern_promotion=false`, `validated_multi_provider_stability=false`, `confirmed_discoveries=null`, `p_values=null`, `causal_claim=false`, `verified_live=false` and `ready_for_analysis=false`.

## Independent synthetic arithmetic examples

Hand-checked synthetic test groups with two matched pairs each:

- Group Early `[(high=1, low=0), (high=1, low=0)]`: `D_Early = 1`.
- Group Late with the same two hypothetical pair values: `D_Late = 1`; both positive = **descriptive consistent**.
- Change Late to `[(0,1), (0,1)]`: `D_Late = -1`; explicit **direction reversal**.
- Change Late to `[(1,0), (0,1)]`: `D_Late = 0`; **weak/mixed** rather than stable.
- Only one covered group when two are required: **blocked**. A one-role cohort or a group below the explicit minimum: **blocked**.

The integrated synthetic tests reuse the prior stage18 16-publication fixture (eight discovery, eight reserved for confirmation) and calculate real stage19 logic while **mocking** the stage18 replay. When the two discovery niches each contain two comparable pairs, their descriptive differences are `1/2` and `1/2`. When the second niche's outcomes are synthetically inverted, the pair differences become `1/2` and `-1`, correctly classified as a reversal. If a pair spans different niches, the unallocated crossing is visible and the niche dimension is blocked; a single source provider remains blocked.

These are synthetic regressions, not live UAE video performance findings. No scientific conclusion or p-value is inferred from them.

## Serialization, replay and operational limitations

`CandidateStabilityReport` is canonical UTF-8 JSON hashed with SHA256. `replay_candidate_stability_report` independently rebuilds against every supplied pinned source, refuses duplicate keys/NaN/malformed bytes, forbids fake `approved_live`, and rejects rehashed forgeries and noncanonical byte layouts. It is a reproducible *local* calculation, not an authenticated signing service.

Current constraints:

1. No live qualified UAE row-level provider, source-rights gate, authenticated model-topic/creator claims, current reviewer or real preregistration authority.
2. Stage18 adjustment, stage17 split and stage10 matching remain candidate-only. Matching into strict comparability strata may make cross-platform/creator/provider replication infeasible; **blocked is expected**, not a bug to work around by weakening strata.
3. No holdout-confirmatory hypothesis testing, valid inferential p-values, FDR, causal regression, robust random effect model, temporal causal transportability or publication.
4. Stage19 does not modify other modules' branches, SQL migrations, main, model providers, costs or web UI.

## Files, QA and following stage

- `intelligence/patterns_insights/research/stability.py`: independent candidate reporting and deterministic replays.
- `tests/test_pie_stability.py`: 22 synthetic pure-arithmetic/integration unit methods, deliberately stubbing stage18 replay (stage18 fixture also stubs prior stage10/11/17 replay).
- PIE root/research `__init__.py` exports and durable `docs/PIE_IMPLEMENTATION_TRACKER.md`.

On a complete checkout:

```bash
python -m unittest discover -s tests -p 'test_pie_*.py' -q
python -m compileall -q intelligence/patterns_insights tests/test_pie_stability.py
```

**QA truth at implementation:** GitHub connector accepted/refetched code and tests; full Python suite, isolated local stage19 tests, source compileall and PostgreSQL17 were **not run**. GitHub Actions previously observed `queued`, NOT passed. Do not infer QA green from a queued workflow. All peer and live UAE authority/verified-data constraints remain BLOCKED.

**NEXT:** PIE-MOD-20 — bounded predeclared multi-feature interactions, no uncorrected significance or retroactive holdout inference.
