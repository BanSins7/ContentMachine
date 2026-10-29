# PIE-MOD-12 — Missingness, sample size and coverage QA

**Owner:** `feature/patterns-insights-full-module`. **Wire:** `pie-missingness-coverage/v1`. **Status:** implemented pure, **offline-only candidate quality report**. A source SHA is integrity evidence, **not** producer attestation or current rights; `CandidateCoverageAudit.verified_live` and `ready_for_analysis` are permanently `False`.

## What is audited

`build_candidate_coverage_audit(snapshot, strata, outcome, controls, matrix, policy)` is PIE-owned and reads no UAE/Group A/B/C/Group C DB directly. It rebuilds **all five exact upstream candidate artifacts** using their replay validators (not merely their supplied hash). The explicit `CoveragePolicy` fixes research design SHA, sample minima, minimum *present* observations per arm per feature, maximum allowed missing fraction, disclosure/reporting threshold, shared creator requirement and unmatched case rule.

- Original `scanned = included + excluded`; all **included** are counted as `matched + unmatched + neutral + other_strata`. Exact matched sample is `2 × pairs` with one high and one low per pair; known exclusions/unmatched reasons remain counted. Source-level and paired-sample fractions use **different, explicit denominators**, formatted using deterministic Decimal arithmetic; there is no inflated N from B1 multilabel or B3 sequences.
- Per feature, preserve `present`, **explicit** `null`, **absent key**, `false`, numeric `0` and known empty sequence separately. The original candidate dictionary supplies eligibility counts over *all included* videos, not only matched pairs. Matched per-arm counts use only the two arms' actual matrix cells and show both `present_fraction` and `missing_fraction=(null+absent)/arm_N`. No missing field is silently zero-filled or converted into `false`.
- **Important wire limitation:** The proposed `uae-to-pie/v1` row does **not** contain independently evidenced field execution statuses `not_run`, `partial`, `unknown` vs `NA`. Therefore `not_run_n` and `partial_n` are **`null` meaning NOT OBSERVABLE**, **never `0`**. The report states `missing_cause=not_observable_in_uae_to_pie_v1`, `not_run_and_partial_states_available=false`, `selection_bias_proven_absent=false`. Mechanisms of missing data (MCAR/MAR/MNAR) cannot be proven from source counts. PIE-MOD-12 does **not** guess/fit missingness mechanisms. Later UAE integration must offer a genuinely versioned status field before these claims can be upgraded.
- Per-role balance: raw high/low `N`, `role_n_difference`, `unique_creators`, maximum repeated videos per creator and number of shared creators. Exact one-to-one matching **does not** establish author equivalence, stable prior/niche cohorts, absence of selection bias, independence of observations, or causal balance. `creator_adjustment_claimed=false` and `statistical_balance_proven=false` in every report. Source `uncontrolled_dimensions` are disclosed.
- `min_cell_disclosure_n` flags role-specific detailed counts as `suppressed=true` with `present_n=null`, `null_n=null`, etc., **not fake zeros**; role total N is still displayed. This is a **quality/reporting guard**, **not a privacy guarantee**, because the linked source artifacts still contain exact rows and other aggregates may permit inference. Production access control, privacy aggregation, and disclosure rules must be enforced separately in future API/Studio stages.
- Offline structural thresholds fail closed with explicit `blockers` (min matched/arm/feature N, max missing fraction, shared creator, full case coverage). Warnings separately state unknown missingness mechanism and unproved selection representativeness. `offline_coverage_status=pass` means only declared **offline structural thresholds**, never verification of current qualified UAE input, credible confounder balance or authorization for statistical inference.

## Frozen semantics and replay

The audit is canonical sorted JSON with `artifact_sha256 = SHA256(canonical_json)`. Pinned `source_snapshot_sha256`, `source_strata_sha256`, `source_outcome_sha256`, `source_controls_sha256`, `source_matrix_sha256`, `project_id`, and `CoveragePolicy` hash bind the audit. `replay_candidate_coverage_audit()` rejects duplicate JSON keys, non-finite JSON, bad SHA, incompatible schema, elevated `approved_live`, malformed policy and **resealed content** with a freshly computed attacker's SHA; it recomputes the full audited payload from original frozen artifacts. A successful offline replay **never refreshes source rights or reviewer approval**; do not reuse it as current access permission.

No SQL or database migration is added: the stage is a pure candidate diagnostic. No p-values, discovered hypotheses, best-feature selection, effect estimates, LLM calls, background workers or published insights are created.

## Tests and reproducibility

Tests: `tests/test_pie_coverage.py`. Synthetic fixture imported from previous PIE 07–11 unittest fixtures, with explicit high/low score and one excluded row, plus adversarial mutated **checksummed synthetic** UAE manifests. Manually verified expected values for 2 included, 1 excluded, 1 pair: `included/scanned=2/3=0.666667`, `matched/included=2/2=1.000000`; the B CTA source has one false and one null, so high present N=1, low null N=1; low missing fraction =1.000000. Bool false is not numeric 0 and an empty B1/B3 sequence is present, not missing. New versions and threshold changes force new candidate hash. Coverage remains blocked if low has no observed CTA. Independent local stub smoke also checks rehashed-tamper detection and exact sample fractions.

To run from a **full checkout** (not a stub harness):

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_pie_*.py' -v
python -m compileall -q intelligence/patterns_insights tests/test_pie_coverage.py
```

For stage12 isolated checks: `python /mnt/data/pie_stage12/smoke_stage12.py` uses *simulated* earlier modules, not production UAE. Check actual local test counts and GitHub CI result in `docs/PIE_IMPLEMENTATION_TRACKER.md`. PostgreSQL 17 fresh/upgrade QA from PIE-MOD-04 and UAE UA-MOD-25 row-level handoff, current source rights, actual human review approvals remain blocked until separately verified. No peer or main modifications are made.

## Handoff PIE-MOD-13

Use only frozen, validated and human-authorized research data when the eventual service is available; candidate audit diagnostics alone cannot authorize inferential pattern discovery. Missing/unknown feature states must remain missing (no implicit 0), small-N and reporting suppression must be explicit, and hypothesis search must be predeclared rather than scanning unsafely for significance.
