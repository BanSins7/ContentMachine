# PIE-MOD-13 — governed marginal candidate discovery v1

**Owner:** `feature/patterns-insights-full-module`. **Schema:** `pie-marginal-candidates/v1`. **Status:** implemented **offline exploratory candidate generator**, not authenticated UAE data, real pre-approval, statistical discovery, significance, publishable evidence or causal effect.

## Input boundary and operation

`build_candidate_marginal_discovery(snapshot, strata, outcome, controls, matrix, coverage, policy)` requires **six** exact frozen upstream PIE candidate types and a bounded typed `MarginalDiscoveryPolicy`. It reconstructs every upstream candidate using its own exact offline replay function, verifies project, fixed source SHA lineage, research design and counts, and blocks **all discovery** when the stage12 coverage gate is blocked or any audited high/low cell was suppressed. A checksum alone is not a live producer attestation; full replay does not establish current source rights or active human design approval.

The policy requires `policy_version`, original `design_sha256`, a timestamp `registered_at` no later than the approved observation window start, a `registration_claim_id` **not interpreted as trustworthy authorization**, `minimum_observed_per_role` and between 1 and 64 hypotheses sorted by unique stable `hypothesis_id`. Duplicate predicates are disallowed even under different IDs. Policy hash and full frozen registry hash travel with the output. No post-hoc feature enumeration, automatic top percentile, threshold tuning, winner ranking or suppression of negative hypothesis results.

Supported single-feature predicates, **only for columns already explicitly permitted by PIE-MOD-11 and recorded in the stage12 QA report**:

| Hypothesis kind | Eligible feature | Reported quantities (each high/low arm) |
|---|---|---|
| `numeric_distribution` | A/B `numeric_int` | Exact Decimal median of observed integers, deterministic histogram with explicitly registered, ordered half-open `numeric_boundaries`; no manufactured numeric feature |
| `boolean_equals` | `boolean` | Observed denominator, exact predicate hits, descriptive fraction |
| `category_equals` | `category` | Observed denominator, label hits/fraction |
| `multilabel_contains` | `multi_label` B1 | **One** hit per video for the label even when source lists repeated labels |
| `sequence_equals` | `ordered_sequence` B3 | Exact match with original order and duplicate positions preserved |

All groups preserve **total, observed, explicit null and absent counts** separately; `false`, `0`, and empty present sequences remain observed, not missing. The denominator for a fraction is the **observed** count, not scanned source N. The source population audit independently retains original scanned, included, excluded-by-reason, unmatched, neutral, other-strata and matched totals. A study's high/low sample **never** stands in for the source inclusion population. Unknown upstream `not_run`/`partial` states remain unknown.

The result `CandidateMarginalDiscovery` is immutable (`frozen dataclass`) and JSON-only with an exact canonical SHA-256. `replay_candidate_marginal_discovery(...)` rejects rehashed data tampering by reconstructing all hypothesis counts from the pinned source six-artifact chain and frozen policy; unknown keys, duplicate JSON keys, NaN/Infinity, bad types and fake `approved_live` are refused. Policy/hypothesis ordering deterministically controls output ordering, not success magnitude. No paid model requests, background tasks, DB writes, API publication or external UAE/Group A/B/C imports.

## Inference barrier and next stages

Stage13 reports **descriptive counts, medians and histograms only**. The report has `p_values=null`, `adjusted_p_values=null`, `causal_claim=false`, `approved_live=false`, `evidence_state=unverified`; the object always has `verified_live=False` and `ready_for_analysis=False` even when all *offline* coverage thresholds pass. It does not estimate effect size (PIE-MOD-14), sampling uncertainty (15), multiplicity correction (16), holdout confirmation (17), confounders (18) or stability. Do **not** interpret a descriptive frequency difference as statistically supported, replicable or causal. The registry is a **local untrusted research-design claim**, not proof of verified historical preregistration.

Even a fixture with min-N=1 is **test-only**. A production study must use real UAE qualified row-level evidence/rights, authoritative human approval and a scientifically appropriate sample gate. This stage deliberately cannot upgrade that status. If stage12 returns `blocked` due to insufficient observed N, unknown retention or low coverage, discovery is withheld rather than inventing imputation or silently dropping columns.

## Reproduction and verification

Full branch unit suite **when a complete checkout is available**:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_pie_*.py' -v
python -m compileall -q intelligence/patterns_insights tests/test_pie_marginal.py
```

For the currently available isolated workspace (early stages are stubbed, stage11/12 source is real):

```bash
python /mnt/data/pie_stage13/smoke_stage13.py
```

`tests/test_pie_marginal.py` uses real stage7–12 synthetic fixture harnesses in the full repository. Independent hand-calculated histogram and median expected values are pinned in `tests/fixtures/pie/pie_marginal_golden_v1.json`; this fixture is **synthetic only**, not producer-authorized. Tests cover fixed hypothesis order/count, a deliberately repeated B1 label counted once, exact B3 sequence, missing states, arbitrary feature/score bans, invalid numeric bins, registration after observation, role minimum, blocked stage12 QA, fully resealed tampering and no statistics/leakage methods. Small-N reference cases prove arithmetic, **not** population applicability.

PostgreSQL 17 fresh/upgrade schema tests and real UAE UA-MOD-25 exporter integration are independent remaining blockers, recorded in the tracker. This stage adds **no SQL** and changes **no main or peer branch**.
