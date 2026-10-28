# PIE-MOD-20 — Bounded Multifeature Interactions

**Owner:** `intelligence/patterns_insights` (independent PIE branch)  
**Contract:** `pie-pairwise-interactions-candidate/v1`  
**Status:** implemented **exploratory candidate arithmetic only**. Not a production statistical test, inferential significance claim, or confirmed discovery.

## Purpose and limits

PIE-MOD-20 enumerates a **frozen, explicitly preregistered claimed** 1–32 A/B interaction family. It measures observed two-feature conjunction prevalence within the matched **discovery** cohort and compares it with the original matched low-score baseline. It never generates hypotheses automatically from the top-scoring sample, silently culls weak features, or reuses the held-out confirmation cohort.

The UAE `uae-to-pie/v1` and stage11 typed matrix expose actual A/B features (Boolean, text category, B1-style multilabel, B3-style ordered semantic sequence) by row. **No raw Group C7 scores are allowed in predicates**. The current source and human policy are still unverified; registered timestamps are unauthenticated claims.

The stage-20 engine does **not** imply that a feature pair *caused* a higher C7. A strong conjunction can reflect preexisting creator, niche, topic, platform, observational selection, or how high/low Group C7 outcomes were chosen. Stage18 standardization and stage19 cohort stability apply to earlier separately registered estimands, not to these new interaction contrasts; they **cannot** be recycled as interaction-specific p-values or calibrated confidence limits.

## Frozen inputs and exact scope

The builder accepts the typed stage07–11 source snapshot, strata, outcome plan, matched controls and feature matrix, plus a stage17 holdout, explicit holdout policy/media lineage and `InteractionPolicy`. It **replays** matched controls, feature matrix and split from the same pinned upstream records, requiring matching `project_id`, `design_sha256`, snapshot, controls, matrix and holdout fingerprints. `InteractionPolicy.registered_at` must be **claimed** no later than the stage17 discovery cutoff. This does not prove actual preregistration or current rights.

Only original, full matched pairs with *both* posts inside `split.discovery_video_ids` are analyzed; any case/control crossing the holdout boundary aborts the entire report. The original outside pairs remain explicitly listed as `confirmation_reserved` or `embargo_or_quarantine`. `unmatched_discovery_video_ids` records discovery records not in matched pairs; their exclusion from the paired estimand must not be silently equated to whole-population inference. Duplicate reused matched IDs and wrong role/row SHA fail closed.

Maximum 32 registered hypotheses, 1,000 discovery matched pairs, and 100,000 pair×hypothesis operations prevent combinatorial explosion and implicit exhaustive feature mining. Policy specifies minimum discovery pairs and minimum **joint-conjunction hits per high/low role**. No global statistical magic thresholds are invented.

## Typed predicates and frozen family

Each `InteractionHypothesis` has a unique, sorted ID and **two distinct, canonically ordered feature keys**, each declared as `InteractionPredicate(family, feature_name, operation, target)`. The source column representation must match:

| Predicate | Matrix representation | Exact truth rule |
|---|---|---|
| `boolean_equals` | `boolean` | Exact `True` or `False`, never integer `0/1` |
| `category_equals` | `category` | Exact text equality |
| `multilabel_contains` | `multi_label` | Membership in B1 semantic label set; one post counted once |
| `sequence_equals` | `ordered_sequence` | Entire ordered B3 semantic sequence, including repeated positions |

Allowed recipes therefore include hook×subtitles (Boolean/Boolean), hook×format (Boolean/category), hook×multilabel tag, or ordered-story×CTA (ordered sequence/Boolean). Importantly, stage20 does **not** transform technical A scenes into B3 story roles. Numeric thresholds and n-grams belong to explicitly separate future designs; there is no silent type casting.

The family ID SHA covers the **complete ordered hypothesis specifications**. A duplicated pair under a new ID, reversed feature ordering, unsorted IDs, unknown column, unsupported operation or inconsistent target type fails. Null/absent values are not treated as `False`: every affected hypothesis remains in its complete registered family with `blocked_incomplete_or_sparse_joint_support` and explicit missing row/role reasons. The report then receives `blocked_incomplete_registered_family` rather than hiding blocked hypotheses and reporting a favorable subset.

## Exact pair-based descriptive arithmetic

For every registered interaction `h`, evaluate the predicates on each original high/low post (not post × label) for *n* matched discovery pairs. Count per arm the left hits, right hits, and joint hits. When every observation is present, the sample supports the declared minimum and both arms reach the explicit positive joint-hit threshold:

```text
P_high_joint = high_joint_hits / n
P_low_joint  = low_joint_hits / n

Matched-baseline prevalence difference:
  D = P_high_joint - P_low_joint

Prevalence ratio vs. matched low baseline:
  R = P_high_joint / P_low_joint   (undefined if denominator = 0)

Excess within each high/low arm over marginal product:
  E_high = P_high_joint - P_high_left * P_high_right
  E_low  = P_low_joint  - P_low_left  * P_low_right

Excess contrast:
  E_high - E_low
```

All arithmetic is exact `Fraction` with stored numerator, denominator and deterministic 12-decimal display approximation. `E_high` / `E_low` are **descriptive co-occurrence-minus-marginal-product** measurements, not causal interaction effects and not claims of independent sampling. No direction is selected post hoc.

### Synthetic golden worked example

Four invented discovery matched pairs, with conjunction `hook=True AND subtitles=True`:

| Role | N | Hook True | Subtitles True | Both True |
|---|---:|---:|---:|---:|
| High | 4 | 3 | 2 | 2 |
| Low | 4 | 1 | 1 | 1 |

Hand-calculated values: **difference = 1/4**, **matched-baseline prevalence ratio = 2**, **excess high = 1/8**, **excess low = 3/16**, **excess contrast = −1/16**. Synthetic fixture also contains format, ordered story, CTA, B1 multilabel, a confirmation-reserved sample and deliberately absent feature variants. These calculations are *arithmetic fixtures*, not observations of real videos or statistically significant patterns.

## Multiplicity, missing inferential p-values and confidence

**All 1–32 original interaction IDs are always retained**, along with `registered_hypothesis_count`, SHA and per-feature support/exclusion audit. `multiplicity_status=blocked_no_valid_inferential_p_values`. Consequently:

- `tested_inferential_hypothesis_count=0`
- Each interaction: `p_value=null`, `adjusted_q_value=null`, `multiplicity_adjusted_confidence_interval=null`, `hypothesis_confirmed=false`
- Top-level: `simultaneous_confidence_interval=null`, `can_publish_significance=false`, `heldout_confirmatory_analysis=false`, `confirmed_discoveries=null`, `causal_claim=false`
- Candidate wrapper properties `verified_live=False`, `ready_for_analysis=False`.

This is the honest state: the roadmap mentions multiple-testing-corrected confidence. **It is blocked by lack of defensible inferential p-values, calibrated cluster/dependence sampling assumptions, authenticated preregistration and untouched confirmatory evaluation.** No percentile bootstrap from the discovery sample, crude effect, standard error or artificial p-value is substituted for it. PIE-MOD-16's synthetic-only BH arithmetic is *not* a production p-value generator.

## Tamper-resistant replay and integration

`replay_candidate_interaction_report(raw, sha, snapshot, strata, outcome, controls, matrix, split, holdout_policy, lineage)` rejects duplicate JSON keys, NaN, bad UTF-8, malformed policy, changed source hashes, noncanonical JSON bytes and **rehashed forged effects** by independently reconstructing the full report from the provided typed upstream inputs. Structural SHA reproduction does not authenticate live source rights or reviewer authority. There are no automatic external model calls, migrations or publishing flows.

Code: `intelligence/patterns_insights/research/interactions.py`. Tests: `tests/test_pie_interactions.py` (**18 synthetic unit methods**), including an independently computed fraction oracle and negative policy/replay/type/holdout/selection QA. Public exports: PIE package root and `research/__init__.py`. No peer branches or `main` edits.

## QA commands and current constraints

On a full branch checkout:

```bash
python -m unittest discover -s tests -p 'test_pie_*.py' -q
python -m compileall -q intelligence/patterns_insights tests/test_pie_interactions.py
```

**Verification status when authored:** GitHub connector accepted code/tests/exports and can refetch them. Local isolated/new integrated Python suite and PG17 **NOT RUN** in this working environment; previous PIE GitHub CI observed **QUEUED**, not passed. Test methods mock earlier stage10/11/17 replay but execute actual new stage20 arithmetic and replay. Do not claim end-to-end UAE or valid significance until observed.

**Next PIE-MOD-21:** story/sequence motifs and n-grams preserving B3 ordered role repetitions and technical A/B ownership; stay independent of unsupported causal conclusions.
