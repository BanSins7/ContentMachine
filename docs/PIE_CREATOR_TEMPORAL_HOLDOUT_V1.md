# PIE-MOD-17 — Creator/Media-Disjoint Temporal Holdout and Discovery CV

**Branch:** `feature/patterns-insights-full-module`  
**Contract:** `pie-creator-temporal-holdout-candidate/v1`  
**Owner:** PIE standalone research only. **State:** implemented candidate splitter, NOT certified for production/confirmatory inference.

## Purpose and upstream reality

Previous PIE-MOD-07–16 produce **unverified exploratory candidates**, never a real inferential p-value or a source-attested discovery. A frozen BH family is not confirmation. This stage adds a split **before future hypothesis search**; it does not retroactively confer independence on stages 13–16 or make their full-population results confirmatory.

Source is **only** a typed and independently replayed PIE-MOD-07 `CandidateResearchSnapshot`, bound by exact `snapshot_sha256`, `design_sha256`, `project_id`, and source `as_of`. No calls to Group A/B/C, QDP, SRE, UAE live, LLM, SQL, or `main`. The frozen UAE research wire exposes `creator_key`, canonical video ID, published timestamp, row fingerprint and global version pins. **It does NOT currently attest a trustworthy media-family/repost graph.** PIE therefore requires a complete explicit `HoldoutMediaLineage` for every included post, pinned to its `row_sha256`; the report still calls it `unverified_external_claim`. Do not interpret user-supplied linkage or a checksum as an authenticated provider claim.

## Deterministic algorithm (policy has no statistical magic defaults)

`HoldoutPolicy` pins versions, source SHA, design SHA, declared registration timestamp, `discovery_before`, `confirmation_not_before`, explicit nonzero minima for rows and creators per arm, `cross_validation_folds` (2–10) and uint64 seed. Every clock is timezone aware; registration must be claimed not later than the start of confirmation, the temporal embargo must be positive, and confirmation start cannot exceed frozen source `as_of`. This is **an unauthenticated timestamp claim** until a trusted approval/pregregistration authority signs it. The algorithm never reads C7, A/B predictors, performance or hypothesis p-values to make fold assignments.

1. All rows with `published_at < discovery_before` are candidate discovery. All rows with `published_at >= confirmation_not_before` are candidate confirmation. Rows in the intervening gap are explicitly `temporal_embargo`; no row is silently dropped.
2. Every creator appearing in *both* candidate arms is quarantined from **both** with `creator_cross_arm`. Creators are identified by upstream `creator_key` rather than cohort-version equality.
3. Every explicit `media_family_key` still spanning both arms is quarantined from **both** with `media_family_cross_arm`, irrespective of different creators or media revisions. Conflicting `media_revision_key -> media_family_key` claims, changed source row hashes, missing IDs or extra lineage entries fail closed before splitting.
4. From discovery-only remaining rows, union creators connected by a shared media family. Assign entire connected components (not individual videos) to 2–10 deterministic folds, sorted by SHA256(seed + stable creator-component IDs), round-robin. This prevents a repost shared by two authors, or several posts by one author, appearing in train and validation folds. Neither confirmation labels nor outcome-dependent balancing influence assignments.
5. The output reconciles **all** included IDs into discovery, confirmation and explicit embargo/quarantine, records distinct creator counts, CV components, fold assignments and leakage-audit facts. Disjointness of video IDs, creators, media-family keys and CV components is checked in code. Explicit sample/creator thresholds or too few CV components produce `blocked_insufficient_disjoint_sample`, not a smaller conveniently selected sample.

No held-out label or statistic is used to choose hypotheses in this stage; downstream learners must run on discovery-only scopes. `require_candidate_holdout_scope` refuses extra, missing, duplicate or reordered source rows; `require_candidate_cv_scope` requires exact train/validation IDs for one frozen validation fold. **These scope guards are candidate-only utilities, not authentication**: a production consumer must independently replay from a current eligible source and trusted policy/lineage before using any split.

## Serialization, replay and release boundary

`CandidateHoldoutSplit` contains deterministic canonical JSON and SHA256 plus `project_id`, source SHA, policy SHA and exact arm memberships. `replay_candidate_holdout_split(raw, sha, snapshot, policy, lineage)` rejects duplicate JSON keys, NaN, tampering, newly signed-looking versions, policy/source/lineage substitution and rehashed forgeries by **rebuilding** the entire deterministic artifact from independently supplied frozen inputs. `CandidateHoldoutSplit.verified_live` and `.ready_for_analysis` remain always `False`; output `approved_live=False`, `confirmed_hypotheses=null`, `p_values=null`, `causal_claim=False`.

Because the media graph, timestamp registration, current data rights and actual UAE exporter are not authenticated, **even a split labeled `candidate_exploratory_only` is NOT a confirmed pattern**. If creator or media linkage is incomplete, this stage cannot truthfully guarantee independence. Stage13's existing `build_candidate_marginal_discovery` is not retrofitted to automatically accept separate folds; existing stage13–16 artifacts have full-sample exploratory scope and MUST NOT be presented as held-out proof. No real post-hoc p-value or significance flag may be attached.

## Synthetic worked fixture (independent expected partitions)

13 invented rows, discovery before 2026-10-15, confirmation not before 2026-10-18:

- Early video IDs `1,2,3,4,11,13`; late `5,6,7,8,9,12`; `10` in the temporal embargo.
- Creator A owns early `1` and late `9`: both quarantined.
- Different creators own early `11` and late `12`, sharing a media family: both quarantined.
- Discovery = `(2,3,4,13)`; confirmation = `(5,6,7,8)`; purged = `(1,9,10,11,12)`. The 13 source rows reconcile exactly.
- Two different discovery creators share a media family on `2` and `13`; the creator/media connected-component rule assigns these to the **same** CV fold.
- Changing the minimum discovery rows from 3 to 8 retains the exact partitions but changes status to `blocked_insufficient_disjoint_sample`; there is no silent convenient pruning.

This is a handcrafted synthetic oracle, not a live content-performance experiment.

## QA and non-goals

Code: `intelligence/patterns_insights/research/holdout.py` (stage17), exported by both package init modules. Tests: `tests/test_pie_holdout.py` (16 synthetic test methods: manually calculated positive, temporal/creator/media quarantine, CV component and fold isolation, registration/timestamp requirements, small-sample block, forbidden live promotion, exact replay/forgery, linked source mismatch and malformed JSON). They **mock the earlier stage07 replay** to isolate the new stage; do not represent an end-to-end UAE handshake.

Suggested on full checkout:

```bash
python -m unittest discover -s tests -p 'test_pie_*.py' -q
python -m compileall -q intelligence/patterns_insights tests/test_pie_holdout.py
```

**Observed this step:** GitHub accepted stage17 code, test and export commits; SHA/content independently refetched via connector. **No local full checkout, Python test execution, PG17 or green GitHub CI was observed.** GitHub Actions on the branch was queued, not passed. Pure stage17 adds no new SQL migration; no PG17 claim. Production UAE export, signed media lineage, authenticated preregistration, current rights and independent inferential test generator remain BLOCKED.

**Next:** PIE-MOD-18 — confounder and adjustment strategy. Keep holdout candidates and existing discoveries explicitly exploratory until a validated discovery-only model, untouched out-of-sample confirmation, adequate sample support and live source/reviewer authority exist.
