# SRE M33 — Reproducible Human-Gold Benchmark, Honest F1 and Calibration Status

**Scope:** SRE-only `feature/sre-production-v1-20261023`, [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22), 2026-10-28. **CODE authored and committed; exact-head CI/PG17 PASS not observed.** No raw D1 content, paid model POST, real human-review UI or accuracy claim is fabricated.

## Eligibility and what is *not* gold

M33 only accepts a real, project/run-scoped original **M30 source-grounded provider judgment** with a **latest M32 explicit human-reviewed revision** for the same `(item_id, C9 category_key)`. SQL joins the original M28 authenticated generation/validated M21 evidence and immutable M26 quote/catalog/model revision SHA; a charged receipt alone, a model-produced `no_match`, a generic operator assertion, or an unreviewed category are **not human gold**.

A human judgment `uncertain` is not silently treated as negative, and a model abstention `uncertain` is not treated as true negative. The M33 benchmark evaluates **original model decisions** against independently recorded M32 human decisions. It never evaluates the human-corrected decision against itself as though it were a model. Model labels and reviewer labels may both have errors: reviewer authenticity and sampling quality require real-world acceptance, not synthetic fixtures.

## SRE-only benchmark services

`intelligence/relevance/live_gold_benchmark.py`:

- `preview_live_sre_gold_benchmark(conn,project,run_id,*,actor_id,actor_authorizer,split_seed='sre-gold-v1',holdout_percent=30,min_evaluation_examples=10)`: dedicated authenticated host action `read_sre_gold_benchmark`, idle autocommit PostgreSQL. Reads only genuine latest M32 source-verified C9 pairs with repeatable-read/read-only transaction; fresh D1 `before_publish` rights check for every source. If there is **no** human-reviewed gold returns `status=no_authenticated_human_gold`, N=0 and **null metrics**, not 0%/100%. The source cohort is capped at 1,000 rows; a larger cohort is rejected instead of silently truncated.
- Stable seed and per-video deterministic assignment, with approximate stratification by the **set of human-gold labels per video**. All C9 categories of a video stay entirely in `train` or `eval`. Train is explicitly **NOT used for fitting**, and there is no probability calibration. Small or single-video strata can lead to no train groups: that limitation is reported. Frozen rows contain only item/video/content version and SHA, category UUID, M30 result UUID/original verdict, M32 review UUID/revision/human verdict, original quote, catalog **and request/prompt SHA256**, model revision and train/eval label — never source text, prompt, LLM reason, API key or free-form review notes.
- Binary positive `match` metrics computed **only on human-gold match/no_match** evaluation rows. Human-gold `uncertain` rows are excluded and counted separately. Model `uncertain` on gold positive is a missed positive (included in FN); model `uncertain` on gold negative remains an explicit negative abstention, **not counted as TN**. Denominators exposed: binary gold count, class counts, confusion TP/FP/FN/TN, positive/negative abstentions, prediction coverage. Precision = TP/(TP+FP), recall = TP/(TP+FN), F1 = 2TP/(2TP+FP+FN), strict known-gold accuracy = (TP+TN)/N with abstentions scored as incorrect. Undefined ratios are **null**, never fabricated 0. Ratios are reproducible Decimal 6 decimals. `status=insufficient_gold_or_class_coverage` unless at least the frozen minimum evaluation sample count is present, **both binary classes occur, and at least two independent evaluation videos are represented**. The human-reviewed cohort is not guaranteed representative of all C9/model outputs: `selection_bias_warning=human_reviewed_only_nonrandom_review_population` remains explicit.
- Without **genuine authenticated model probabilities**, Brier score, ECE/calibration error, confidence intervals and significance remain **null**, with `confidence_calibration_status=unavailable_no_authenticated_probabilities`. No source text, model training or auto-shipping occurs.

`record_live_sre_gold_benchmark` uses a separate `record_sre_gold_benchmark` host authorization, exclusive workspace lock and one transaction to freeze a fully recomputed real M30/M32 sample set with deterministic video split, **seed, holdout fraction and minimum accepted denominator**, source pin hashes and SHA256 fingerprint. Identical source/policy returns the original UUID idempotently. A new human supersession creates a **different snapshot**; it cannot mutate historical records.

`read_saved_sre_gold_benchmark` checks the SHA fingerprint, deterministic no-video-leak split, frozen evaluation threshold and fresh D1 publication rights. Its gold labels remain **historical as of capture** even if a later M32 supersession exists. Re-reading with a different caller threshold does not rewrite the frozen report.

`compare_saved_sre_gold_benchmarks` requires `compare_sre_gold_benchmarks` authorization. It only compares paired evaluation entries with **identical (video_id, content_sha256, category_key), identical human-gold label**, freshly authorized source and verified snapshot SHA. It reports original vs candidate descriptive metrics and signed deltas, including exact original M26 model revision/catalog/quote/request hashes; drifted/unpaired selections return `not_comparable_unpaired_or_label_drift`. No statistical significance or model superiority is claimed.

## Forward-only SQL 067

`intelligence/migrations/067_sre_live_gold_benchmarks.sql` creates project/run-scoped immutable `sre_live_gold_benchmark_snapshots` with unique source/policy SHA256, explicit bounded seed, 10–90% holdout, immutable 1–1,000-row ID/hash-only JSONB manifest and frozen minimum sample threshold. SQL BEFORE INSERT checks the original noncancelled LIVE run, exact newest **human_verified** M32 review, corresponding genuine M30 original result, M28 authenticated and evidence-validated receipt, M26 quote/catalog/model revision and original D1 content version/SHA for **each** manifest entry; rejects extra/missing 15 source fields, duplicate source/category and invented labels. SQL UPDATE/DELETE always rejected, PUBLIC privileges revoked, project/run FK and indexes.

The Python trusted writer computes the deterministic cohort fingerprint, and the Python saved-reader independently recomputes it; SQL validates the original source for **each row**, but does **not independently cryptographically recompute Python's canonical JSON SHA** or enforce video split. This is explicit defense-in-depth, not a tamper-proof guarantee against the database owner.

## QA authored; not passed on exact head

- `tests/test_sre_live_gold_benchmark.py`: TP/FP/FN/TN arithmetic and null denominators; known-positive/negative model abstentions, human-gold uncertain exclusion, six decimal formatting, no manufactured Brier/ECE; deterministic video-group no leakage; no genuine gold => null metrics; immutable snapshot replay + later human revision; pairwise comparison refuses gold drift; invalid percent/seed, duplicates, actor spoof/cancelled.
- `tests/test_sre_live_gold_benchmark_schema.py`: 067 exact M32 head/M30 evidence/M28 receipt/M26 model, immutable bounded 14-field records and privacy, required next migration prefix 068.
- Disposable PostgreSQL17 M26→M29 synthetic lost-completion regression extended: charging a generation while no M30/M32 gold exists cannot generate a benchmark, and direct SQL insertion of a fabricated gold cohort fails. **Positive end-to-end human review→benchmark with real provider content is not accepted**.
- Linux/Windows + PG17 SRE workflow updated. Queued, in progress and not-run are NOT `PASS`.

## Limitations / next stage

The ordinary multi-category paid LIVE pipeline remains blocked by the preserved M26 one-intent-per-run and M25 one-consent-bound reservation. M16–M18 actual pretrained ONNX QA separately blocked. No real authenticated human review UI source or gold-label quality acceptance; no significance/true calibration without real gold and model probabilities. The offline test fixtures illustrate **algorithms**, not product accuracy.

**Next M34:** retention and revocation purge with cross-table source-related redaction, no post-revocation benchmark/diagnostic leaks, idempotent recovery and outbound invalidation. Maintain SRE-only branch and PR #22 Draft; do not merge or modify other modules without explicit request.
