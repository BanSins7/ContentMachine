# GB-MOD-12 — B5 Editing: deterministic local shot-boundary analysis

**Owner:** `intelligence/group_b/blocks/b5_editing.py`; opt-in `worker.make_b5_worker`. **Contract:** `group-b-b5-editing/v1`. **Branch:** `feature/group-b-full-module`. **No LLM, API key, HTTP, OCR inference, video decoding, provider charge or SQL schema change in this stage.**

## 1. Original 005 compatibility

The frozen `intelligence/migrations/005_content_features_b.sql` defines exactly:

| Column | Algorithm | Constraint |
|---|---|---|
| `cut_count` | Number of **measured shot boundaries** between full-coverage contiguous shot intervals = `n_shots - 1` | Nonnegative integer |
| `cuts_per_minute` | `Decimal(cut_count * 60000) / Decimal(duration_ms)`, quantized **4 decimal places, ROUND_HALF_UP** | Nonnegative `NUMERIC(12,4)` |
| `avg_shot_duration` | `Decimal(duration_ms) / Decimal(n_shots * 1000)`, quantized **4 places, ROUND_HALF_UP** | Positive `NUMERIC(12,4)` seconds |
| `cuts_first_5_sec` | Count of confirmed shot-start boundaries `0 < t_ms < 5000` | Integer in `[0,cut_count]` |
| `editing_type` | From **quantized cuts/minute**, not absolute cut_count: `single_take` when zero cuts; `slow_cuts` when rate below 10; `medium_cuts` when 10 ≤ rate < 30; `fast_cuts` when rate ≥ 30 | Original SQL `CHECK` |

**Important:** 11 cuts across 2 minutes is `slow_cuts` at 5.5 cuts/min, not `medium_cuts`. A boundary **exactly at 5.000 s** is *outside* the first five seconds; 4.999 s is inside. These rules are fixed, versioned and tested. Rounding before type classification aligns with the actual `005` SQL constraint and avoids a type/rate mismatch near thresholds.

## 2. Only complete, authentic shot observations count

`B5EditingExecutor` receives an already freshly verified `group-b-media-evidence/v1` manifest via the GB11 local worker, which checks:
1. Exactly one original video asset with positive, measured duration (at most 24 hours), SHA-pinned to the current project/run/GA result and QDP source-rights/version, and **all real bytes checked** in GB06/07.
2. Authenticated `observation_catalog.resolve_shot_timeline(manifest)` returning **exactly** `group-a-shot-timeline/v1`, the same current **manifest SHA**, exact GA result SHA, original media asset ID/SHA/duration, `status='complete'`, `segmentation_kind='shot_boundaries'`, ordered `shot_refs`, and canonical `timeline_sha256`.
3. Every shot reference must be one and only one already byte-verified Group A `scene` observation. The catalog cannot add or silently omit references. Its original `start_ms/end_ms` must be measured absolute video time; intervals must be contiguous with no gap/overlap/zero duration, starting exactly at 0 and finishing exactly at measured video end.
4. Semantic topic/scene chapters are **not** reliable evidence of *edit cuts*. Merely seeing one chapter does NOT justify `single_take`. The trusted catalog must explicitly attest `segmentation_kind=shot_boundaries` and complete coverage of the actual image sequence. Missing catalog or unknown measurement returns `B5MeasurementUnavailable` / `partial`, never fabricated zero cuts.

The catalog is an **injected trusted internal adapter**, not an untrusted JSON attachment or a raw request parameter. The `timeline_sha256` is a reproducibility hash; it is **not** a digital signature and cannot authenticate a malicious catalog. Operator must map this adapter to actual verified Group A/D1 shot segmentation. Existing Group A independent branch does not yet demonstrate a deployed `group-a-shot-timeline/v1` producer, so live B5 remains **blocked/partial** until it exists. Unit fixtures are not live video evidence.

## 3. Audit-ready output and independent verifier

`B5Metrics.db_fields()` produces exactly the five B5 columns of `content_b_editing`, with `Decimal` values ready for a later staging/validation step. `B5Metrics.wire()` produces normalized decimal strings for deterministic canonical JSON. `B5EditingExecutor.execute_local(claim,manifest,catalog)` emits a `LocalResult` whose versioned output also contains project/run/video, pinned GA result SHA, GB06 manifest SHA, shot timeline SHA, and `measurement_kind=verified_shot_boundaries`. It is a **measured proposal**, not a persisted canonical B analysis.

`B5EditingVerifier.verify_local_result(claim,result,manifest,catalog)` independently rereads the exact trusted catalog and recomputes the full output. A forged output with a correctly recomputed SHA, a different project/run, stale GA, altered evidence reference or an untrusted incomplete catalog is rejected. `make_b5_worker(connection_factory)` is explicit/opt-in and connects the executor plus verifier to the GB11 lease-fenced worker. It rereads QDP/Group A/media rights before running; the final lease fence prevents a cancelled or stolen worker from marking the job complete.

Stage12 **does not write** `content_b_editing` or fabricate `content_b_analyses`: those writes and full output validation are owned by GB-MOD-21/22. GB11 `group_b_jobs` may mark an individually verified B5 job `complete` with its measured output SHA while the B run stays `partial` until all required blocks and review/storage gates pass. No direct paid model or provider dispatch is possible.

## 4. Test matrix / deployment limitations

`tests/test_group_b_b5.py` validates:
- One **confirmed** shot → exactly 0 cuts, `single_take`; missing A/catalog → unavailable, not 0.
- Exactly 10/30 cuts/min and low rate over long duration, SQL 005 classifier compatibility.
- 4.999 vs 5.000 s first-5-second boundary, Decimal ROUND_HALF_UP and stable digest.
- Missing first or last interval, gaps, overlaps, duplicate refs, 0 duration, missing scene proof, source/GA/media/policy SHA mismatch, semantic chapters, empty/partial data.
- Forged output despite valid canonical SHA, stale claim, independently recomputed trusted verifier; opt-in GB11 worker integration including positive fixture and missing-attestation partial.
- No API/model access; Windows/Linux and PostgreSQL workflow jobs invoke pure B5 tests.

Run:
```bash
python -m unittest discover -s tests -p 'test_group_b_b5.py' -v
python -m unittest discover -s tests -p 'test_group_b_jobs.py' -v
```

**QA statement:** GitHub workflow execution is queued; no unobserved Python, PG17, FFmpeg or real source-video E2E should be marked PASS. The existing `005` migration is unchanged. There is no new schema migration for this stage.

**Next:** GB-MOD-13 — B1 Hook classification and evidence-specific rules, gated by real Model/Cost Manager activation.
