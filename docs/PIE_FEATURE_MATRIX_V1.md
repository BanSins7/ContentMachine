# PIE-MOD-11 — Feature Matrix A/B/C Selection

**Branch/owner:** `feature/patterns-insights-full-module` · **Schema:** `pie-feature-matrix/v1` · **State:** pure **candidate-only**, no production authorization or statistical inference.

## Ownership and exact inputs

`build_candidate_feature_matrix(snapshot, strata, outcome, controls, policy)` requires four matching, frozen PIE 07–10 artifacts and explicitly versioned `FeatureMatrixPolicy`. Before extracting any feature, it replays the entire source research snapshot, comparable strata, outcome plan and matched-control plan against their original digests and exact upstream data. No import from live UAE, QDP, SRE, Group A/B/C, media stores, DB or LLM. Any mismatch fails closed, not by silently removing videos.

The wire `uae-to-pie/v1` currently carries only `features_a` and `features_b` maps and **raw unbounded** Group C `breakout_score`. Names and values may be selected only if present in the original frozen UAE feature dictionary, not inferred from titles, filenames, URLs or category guesses. There is **no automatic mapping** of arbitrary B1–B8 names: the caller explicitly declares the feature key and its intended typed role, and the observed source type must match. B1-style multi-label features and B3-style ordered sequences are two *different explicit representations*, despite sharing the original wire type `list[str]`. There is no invented Group C feature predictor.

### Explicit column contract

Each `FeatureColumn` has `family` (`a`/`b`), exact `name`, `representation`, `allow_absent`, `allow_null`, and numeric `unit` when applicable. The immutable tuple must be sorted and unique with at most 64 columns. Allowed representations:

| Representation | Original UAE shape | Candidate feature matrix |
|---|---|---|
| `numeric_int` | integer (never bool/float/string) | exact integer, **zero stays zero**, mandatory explicit unit (e.g. `seconds`, `words_per_minute`) |
| `boolean` | boolean | `false` stays boolean false, never zero |
| `category` | source text | exact recorded category label |
| `ordered_sequence` | list of strings | **original order and repetitions** retained, including an empty list |
| `multi_label` | list of strings | sorted unique labels for set membership **plus** original full ordered list with repeats in `observed_order`; one post still = one sample |

Every cell is explicitly `state=present|null|absent`. `null` and `absent` keep `value=null`, but are **not the same state**. Empty list is a present (observed) value. The frozen dictionary is checked across the **entire included population** for type conflicts, missingness and nulls, not only matched posts; callers must affirm any allowance in the policy. Stage PIE-MOD-12 will later quantify coverage and missingness bias, not impute data here.

## Frozen row-level lineage and leakage safety

Rows are projected **only** from the high/low videos in exact 1:1 pairs of PIE-MOD-10. They retain pair index and role, original canonical video ID, UAE snapshot ID+SHA, row SHA, original sample weight, and one typed value per selected column. For each A/B feature the matrix carries `group_a_analysis_id` / `group_b_analysis_id`, version, SHA, `features_sha256`; this freezes the source of the selection. The Group C `breakout_score` is copied exactly as **`target.kind=raw_c7_decimal_label`**, with persisted calculated-metric ID+SHA, config ID, source metric definition/version and outcome policy SHA. **It is never listed as a predictor feature.** This structure does not make causal claims.

The audit tracks scanned, included, matched, unmatched, neutral, other-strata and source-excluded counts; it never multiplies rows for multi-label features or suppresses samples because a requested field is absent. The matrix is immutable canonical JSON + SHA-256 with full source/policy lineage, and `replay_candidate_feature_matrix` *rebuilds it from the original four artifacts*, blocking modified data even when an attacker rehashes the JSON. Neither checksum nor synthetic success can prove rights, independent approval or genuine UAE qualification. `verified_live=False`, `ready_for_analysis=False` are unconditional.

## Example test-only policy

```python
FeatureMatrixPolicy(
    policy_version='feature-map/v1',
    study_design_sha256='c' * 64,  # fixture only, not proof of preregistration
    features=(
        FeatureColumn('a','duration_sec','numeric_int',False,False,'seconds'),
        FeatureColumn('b','cta_has_cta','boolean',False,True),
        FeatureColumn('b','hook_mechanisms','multi_label',False,False),
        FeatureColumn('b','story_roles','ordered_sequence',False,False),
    ),
)
```

This is a structural projection of existing values, not an instruction to recalculate Group A/B, derive unsupported B8 topics, evaluate Group C or start inference.

## Tests and blockers

New `tests/test_pie_feature_matrix.py` contains positive/replay, 0/false/null/missing/empty-list cases, B1 dedup and B3 ordered repeats, lineage, exact N, unknown features, wrong type, missing/null denial, design/version changes, SHA resealing attacks and no-live-authority assertions. The module needs **no new PostgreSQL migration**; stage04 PG17 acceptance remains pending independently.

Local isolated contract smoke executes against **stubbed** PIE 07–10 upstream modules and is **not** live integration. Run full integration with actual branch checkout / GitHub Actions:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_pie_*.py' -v
python -m compileall -q intelligence/patterns_insights tests/test_pie_feature_matrix.py
```

Real UAE UA-MOD-25 exporter, current independently validated source rights, reviewed design, PostgreSQL17 CI and full trusted E2E are not yet confirmed. PIE-MOD-12 implements missingness/coverage QA next; feature effects, multiple-testing correction and insight generation follow only with valid data and approved design.
