# PIE-MOD-18 — Confounder Inventory and Candidate Direct Standardization

**Owner / branch:** `intelligence/patterns_insights` / `feature/patterns-insights-full-module`  
**Wire:** `pie-covariate-standardization-candidate/v1`  
**Output status:** `candidate_descriptive_association_only` or `blocked_insufficient_joint_support`. Never confirmed/causal or statistically significant.

## Purpose, statistical question, scientific limits

PIE-MOD-18 addresses confounding and comparability of the descriptive PIE high-vs-low **feature-prevalence** comparisons. High/low are *defined by breakout_score*, so estimating how much a hook, niche or topic **causes** a score increase from these data would be invalid. The estimated quantity is explicitly a descriptive frequency difference of an already-selected Boolean A/B feature across score-defined roles **within a predeclared discovery sample**.

The first supported estimator is *exact pooled-cell direct standardization*, not regression. Regression is deliberately unavailable until population adequacy, overlap, model diagnostics, clusters, collinearity and current source eligibility are attested. The code cannot issue inferential p-values, adjusted claim of FDR significance, creator-specific causal estimates or platform uplift.

## Versioned input, identity and scope

`AdjustmentPolicy` freezes: source `snapshot_sha256`, study design SHA, matched-controls SHA, feature-matrix SHA, temporal holdout SHA, algorithm and policy versions, claimed preregistration clock, discovery-only arm, exact Boolean A/B feature, six permitted covariate dimensions, explicit age/UTC-hour buckets, minimum observations *per arm per joint cell*, number of cells, minimum distinct creators and bounded total cells. It prohibits `confirmation` mode, silent partial-overlap estimates, unsupported regression and causal label promotion.

The builder requires exact typed artifacts and **replays the earlier stage10 matched controls, stage11 feature matrix and stage17 holdout** from the same stage07-09 inputs; it refuses altered wrappers, project/design/source SHA mismatch, wrong matrix/controls lineage or invalid holdout status. Any matched pair with one side in discovery and the other outside discovery **fails the whole stage**. Pairs entirely outside discovery are recorded with `confirmation_reserved` or `embargo_or_quarantine`, never used. Only pairs entirely in discovery contribute to standardization. All included source rows require explicitly supplied, sorted, typed `AdjustmentCovariate` evidence. Matching external covariates to exact `row_sha256` prevents accidental substitution; a claimed `asserted_at` after publication is refused.

**Warning:** `niche` and `topic` are supplied as `unverified_external_prepublication_claim`, **not** a new authenticated upstream source. A claim timestamp and SHA cannot prove that these topics were really known pre-publication. `creator` and `platform` come from pinned source rows. `age` means elapsed observation-after-publication seconds; `posting_time` uses **UTC hour**, never invented local hours. The included source versions are pinned via upstream replays. This stage must not be allowed to publish if real pre-outcome status/current source rights and reviewer authorization are unavailable.

## Dimensions and full-support adjustment

Permitted dimensions are uniquely and canonically ordered as:

`creator`, `niche`, `topic`, `age`, `posting_time`, `platform`.

The caller explicitly chooses 1–6 dimensions. Creator identity is available but will often produce single-arm cells and hence a **blocked** report: that is an appropriate positivity diagnostic, not a reason to secretly pool authors. No arbitrary invented global bucket sizes/thresholds exist. The policy explicitly specifies cutoffs and minima.

For every discovery-only pair, use one Boolean observation per role, with strict distinction of `False` and `None`/missing. For joint cell `s`, `n_{Hs}`, `n_{Ls}` count unique observations of the high/low roles, and `y_{Hs}`, `y_{Ls}` are True counts. Each cell must have at least the explicitly requested number of both roles, and all cells must pass. The empirical **pooled matched-sample** standardization weight is `w_s = (n_{Hs}+n_{Ls})/(n_H+n_L)`, and the descriptive adjusted association is:

```text
Δ_adjusted = Σ_s w_s × (y_Hs / n_Hs - y_Ls / n_Ls)
Δ_unadjusted = (Σ_s y_Hs / n_H) - (Σ_s y_Ls / n_L)
```

Every arithmetic ratio is evaluated as Python `Fraction` (exact rational), with a clearly labeled 12-decimal display approximation, not as a floating estimate. If **any** cell lacks support, if there are too few independent creators/cells, or no eligible pairs, the entire adjusted and unadjusted estimates are `null`, status `blocked_insufficient_joint_support`. It does **not** drop unsupported cells and silently change the estimand. If dimensions create more than the approved cell budget, computation fails. Matched-pair selection bias and source exclusion remain explicit warnings. Group C7 is never silently used as a predictor.

## Independent synthetic arithmetic oracle

Handcrafted 16-row source (8 pre-cutoff, 8 held out), eight frozen matched pairs, every creator/media family distinct. Exactly four pairs are entirely inside discovery; the other four are reserved for confirmation.

In the discovery subset, an invented binary hook feature has:

| Niche | High N | High True | Low N | Low True | High-minus-low risk difference |
|---|---:|---:|---:|---:|---:|
| alpha | 3 | 3 | 1 | 0 | 1 |
| beta | 1 | 0 | 3 | 1 | -1/3 |
| **Pooled** | **4** | **3** | **4** | **1** | **unadjusted 1/2** |

Both joint cells weigh 4/8, so `Δ_adjusted = (1/2)×1 + (1/2)×(-1/3) = 1/3`. This is a toy *arithmetic* validation, **not** a scientific claim. If any cell has only a high role or only a low role, **both** estimates become null rather than conveniently excluding that cell.

## Artifact replay, serialization and security

`CandidateAdjustmentReport` preserves the source/project/design/holdout hashes, exact joint tables, full observed counts, excluded-pair reasons and creator coverage, policy hash, external-covariate SHA, per-cell support flags and Fraction numerators/denominators. `replay_candidate_adjustment_report` checks unique JSON keys, rejects malformed/nonfinite JSON, refuses noncanonical bytes and rebuilds from all pinned source inputs. Rehashing a forged coefficient is not a valid replay.

`verified_live=False`, `ready_for_analysis=False`, `approved_live=False`, `p_values=null`, `confirmed_effect=null`, `causal_claim=False` are invariant. Report status `candidate_descriptive_association_only` means **only that the local finite-sample arithmetic and structural checks passed**; it is not a green peer handshake, authorization, publication or confirmatory signal.

## Scope exclusions and unresolved blockers

- PIE-MOD-13–16 existing candidate reports still operate on their original frozen designs; PIE-MOD-18 **does not retroactively validate them** or rewrite their hypothesis family.
- Cross-validation predictions and held-out estimation are not created here. PIE-MOD-19 will test temporal and cohort stability; it cannot independently confirm findings without a production-grade split, authenticated source and pre-registered testing.
- No causal identification, propensity scoring, regression model, independent p-value producer, confidence interval correction or feature imputation.
- Actual UAE qualified row-level exporter, producer-authenticated media lineage, attested pre-publication niche/topic timestamps, live source rights, authorizing reviewer and production split enforcement remain unavailable or unverified.
- No SQL schema changes, peer-branch edits, paid LLM calls or changes to `main`.

## QA commands and truthful status

New: `intelligence/patterns_insights/research/adjustment.py`, `tests/test_pie_adjustment.py` (**21 synthetic/adversarial unittest methods**) and both PIE package exports. Tests mock replay results of existing stage10/11/17 artifacts but execute the actual stage18 arithmetic, gates, replay and policy checks. They cover the hand-calculated 1/2 versus 1/3 discrepancy, full overlap, unsupported-cell blocking, creator/cell minima, cross-holdout pair rejection, null ≠ False, covariate changes, post-publication claims, time-cutoff (including a negative subsecond age) and source/version mismatch, rehashed forgeries, malformed JSON and fake live promotion.

Commands to execute on a full checkout:

```bash
python -m unittest discover -s tests -p 'test_pie_*.py' -q
python -m compileall -q intelligence/patterns_insights tests/test_pie_adjustment.py
```

**Observed at code authoring:** GitHub accepted code, tests and exports, but automated PIE workflow was **queued**. Local checkout/test execution and PostgreSQL17 were **not available/NOT RUN** here. Do not claim passing tests or integration E2E. The stage18 implementation is recorded as **IMPLEMENTED; QA NOT RUN / CI QUEUED** pending actual test execution.

**Next:** PIE-MOD-19 — temporal and cohort stability, using the predeclared candidate split and respecting all authority, significance and dependence limitations.
