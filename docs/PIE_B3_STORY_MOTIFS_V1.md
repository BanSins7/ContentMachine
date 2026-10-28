# PIE-MOD-21 — B3 Semantic Story Motifs, Ordered n-grams and Media Coordinates

**Owner:** `intelligence/patterns_insights`; canonical branch `feature/patterns-insights-full-module`  
**Schema:** `pie-b3-sequence-motifs-candidate/v1`  
**Status:** B3 discovery-only descriptive **candidate** implementation, NOT independent verification or a statistically significant pattern.

## Why this module exists

Group B3 supplies ordered **semantic narrative roles**, for example `hook → problem → demonstration → cta`. Technical Group A scene segmentation or cuts, visual timeline, and video timestamps are **not equivalent** to B3 narrative roles. Stage21 therefore accepts **only** an existing `('b', b3_feature_name, 'ordered_sequence')` typed feature matrix column, verifies its frozen source row `group_b_sha256` lineage, and never reconstructs a B3 story from Group A shots, captions, timing or C7 success scores.

The stage-11 `ordered_sequence` value is a list of tokens with exact ordering and repetition, but **contains no attested media time or carousel slide identity**. The default `sequence_only_no_geometry` mode preserves this explicit absence. A separate optional, all-or-nothing `full_unverified_b3_role_coordinates` mode accepts externally provided `StoryRoleCoordinates` with exact video ID, source row SHA, source B3 analysis SHA, media kind (`video_timestamp_ms` vs `carousel_slide_number`) and one strictly increasing integer coordinate per exact ordered narrative role. The two coordinate systems are never converted into one another. These annotations are **unverified external claims**, not automatically validated producer annotations. If media kinds differ between paired records or across the sample, **all pooled motif contrasts are blocked** instead of quietly comparing timestamps to slide positions or dropping inconvenient publications.

## Versioned preregistration and immutable source scope

`StoryMotifPolicy` pins policy/algorithm versions, study `design_sha256`, stage07 snapshot, stage10 matched controls, stage11 feature matrix, stage17 creator/media-disjoint temporal holdout, claimed registration ID/time, exact B3 feature name, discovery pair minimum, per-arm minimum motif-positive post count, a registered 1–32 ordered `StoryMotif` family, permitted coordinate mode, and fail-closed missing/inference policies.

`registered_at` must be no later than the **discovery cutoff**, not chosen after discovery or confirmation. This timestamp is a claim, **not an authenticated preregistration**. Each motif `motif_id` must be unique and alphabetically ordered. Its ordered roles are a tuple of 2–5 nonempty bounded text tokens; repeated tokens are allowed, and duplicate motif predicates under new IDs are rejected. Unknown B3 feature columns, wrong type or source family, unsorted hypothesis IDs, noncanonical IDs and missing pins fail closed.

Every builder invocation replays frozen stage10 controls, stage11 matrix and stage17 holdout with its original stage07–09 typed parents and explicit external holdout media-family lineage. Project/design/source SHA mismatch, bad B3 row SHA, creator/media arm leakage and pairs crossing discovery/confirmation abort the report. Only complete original matched high/low pairs inside stage17 **discovery** are scored. All reserved confirmation and quarantine pairs are recorded, as are discovery records not included in matched controls. Confirmation data is never used to choose motif lengths, thresholds or winning hypotheses.

Coverage is explicit for *every* motif and every matched role: original discovery pair denominator, observed post counts, hit posts, total n-gram occurrences, pair-discordance counts (`both_hit`, `high_only`, `low_only`, `neither_hit`). Null and absent B3 are **not** converted into an empty story, and missing data blocks the entire registered family. A legitimately observed empty list has zero motif occurrences (not missing).

## Exact semantic contiguous n-gram algorithm

`count_story_ngram` checks each length-`k` contiguous slice, where `2 <= k <= 5`, against the exact preregistered ordered token tuple. It preserves repeated roles **and overlapping starts**. `('hook','hook','hook')` contains `('hook','hook')` **twice**, not once. `('hook','intro','cta')` does **not** contain a contiguous `('hook','cta')` motif. Sequence length is bounded to 64 roles; no edit-distance fuzzy match, reordering, deduplication, language normalization or technical cut interpolation is performed.

Per-post hit = **at least one occurrence**, so a single post contributes at most once to role prevalence even if a motif occurs multiple times; occurrence counts are separate audit evidence.

For each registered motif, `n` original discovery matched pairs:

```text
D = (high_only_pairs - low_only_pairs) / n
  = (high_motif_positive_posts - low_motif_positive_posts) / n
```

Compute with exact `Fraction`; retain numerator/denominator and a fixed 12-place Decimal display value. The difference is an *observed matched-post prevalence contrast in score-defined groups*. It does not establish that a motif causes an improved score, or that the samples are independent.

Both high and low arms must meet the preregistered positive-post count. If a motif lacks support, its registered hypothesis remains visible as `blocked_missing_sparse_or_incomparable_geometry` with a **null contrast**, and the entire report becomes `blocked_incomplete_registered_motif_family`; no selective promotion. Cross-medium pooled comparisons are similarly blocked, even if individual per-post motif hit counts exist.

## Independent toy arithmetic oracle

Eight hypothetical *discovery* publications (four high/low matched pairs) and eight reserved confirmation records. The first high publication has role sequence `hook,problem,cta,hook,problem,cta` (two contiguous `hook→problem` occurrences); other high posts #3 and #5 contain one each; low post #4 contains one; the remaining discovery posts have no `hook→problem`.

Consequently for `hook→problem`:

| Audit | High | Low |
|---|---:|---:|
| Matched posts | 4 | 4 |
| Motif-positive posts | 3 | 1 |
| Total contiguous occurrences | 4 | 1 |

Matched discordance: `both_hit = 1`, `high_only = 2`, `low_only = 0`, `neither_hit = 1`. Thus `D = (2−0)/4 = 1/2` exactly. A second registered `problem→cta` motif has high 3 and low 2 positive posts, `D = 1/4`. A third `hook→problem→cta` motif is fully supported. The negative fixture adds an extremely sparse role pattern that is retained but blocked. This verifies **arithmetic only**, not the existence of real high-performing story structures.

## Integrity, rights and inference boundaries

`CandidateStoryMotifReport` stores canonical sorted UTF-8 JSON and sha256, a full immutable report status, complete motif family SHA and all role/discovery/geometry audit data. `replay_candidate_story_motifs` rejects duplicate JSON object keys, malformed UTF-8, nonfinite JSON, unexpected policy schema, false `approved_live`, changed source/geometry inputs, noncanonical whitespace and rehashed forged motif scores by independently rebuilding the artifact.

`verified_live=False`, `ready_for_analysis=False`, `approved_live=False`, `inferential_hypotheses_tested=0`, `confirmed_discoveries=null`, `can_publish_significance=false`, `causal_claim=false`, `multiplicity_status=blocked_no_valid_p_values` are invariant. As for PIE-MOD-16/20, actual multiple testing, valid inferential confidence limits, reviewer authorization, live UAE producer rights, authenticated B3 role geometry and untouched independent confirmatory inference remain blocked. Candidate status is never a publication license.

## Changes and verifiable QA

Files:

- `intelligence/patterns_insights/research/story_motifs.py`
- `tests/test_pie_story_motifs.py` (**24 synthetic unittest methods**, including a pure n-gram arithmetic oracle)
- PIE package and `research/__init__.py` public exports
- `docs/PIE_IMPLEMENTATION_TRACKER.md`.

The integrated fixture **MOCKS stage10/11/17 replay** and extends a test-only earlier stage07 snapshot with synthetic Group B analysis fingerprints; no live producer or valid real source digest is claimed. The new stage21 algorithm, coverage, geometry, SHA replay and adverse-case checks execute in the tests when they are run. No database migration required.

Commands for complete checkout:

```bash
python -m unittest discover -s tests -p 'test_pie_*.py' -q
python -m compileall -q intelligence/patterns_insights tests/test_pie_story_motifs.py
```

**QA truth:** Stage21 source/test/docs commits can be refetched from GitHub. Local Python `unittest`, `compileall`, PG17 and real UAE end-to-end have **NOT RUN** in this session; GitHub Actions stage21 status must be verified separately. Test methods being committed is **not PASS**.

**NEXT PIE-MOD-22:** continuously valued technical features (Group A duration/speech and Group B5 cuts metrics) in explicitly registered bins, with baseline/comparability QA and no post-selection threshold discovery.
