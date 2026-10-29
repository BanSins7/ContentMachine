# GB-MOD-15 — B3 Story Sampling Engine (deterministic evidence schedule, no inference)

**Branch:** `feature/group-b-full-module`; **code:** `intelligence/group_b/blocks/b3_sampling.py`; **contract:** `group-b-b3-sampling/v1`. This stage owns *sampling and source-proof attachment only*. It does not run a model, decode video, create frames, invent semantic intervals, assign story roles, or write `content_b_story_*` rows. `005_content_features_b.sql` remains unchanged.

## 1. Frozen B3 rates and bounded scope

Original SQL 005 `content_b_story_sampling` defines `first_5_sec_fps=2.00`, `remaining_fps=1.00`, `max_sampled_frames_target=60`, `extra_at_scene_cuts=true`, `extra_at_text_changes=true` and `sampling_notes` JSON object.

`SamplingPolicy` freezes the v1 **2 fps** target grid at half-second offsets **0, 500, ..., 4500 ms**, then **1 fps** at 5000, 6000, ... until the measured duration, with every target **strictly earlier than video end**. Default `max_sampled_frames_target=60` and `max_video_window_ms=200000` bound the sampled request. A video shorter than 5 s naturally uses only the available half-second timestamps; a 200+ second original video cannot be relabelled short to pass coverage.

For video longer than 200 seconds: schedule only the first 200 seconds and mark `duration_truncated=true`; **never** claim the whole video was examined. A complete 200-second 2/1 fps regular grid requires 205 positions; because the target budget is 60, the plan also reports `omitted_regular_grid_targets`. Even when every *selected* frame is present, `coverage.complete=false` whenever any regular point was dropped for budget or source coverage is missing. The slots are an auditable **sampling proposal**, not a statement of understanding/vision, and `model_delivery_state=not_sent` always.

When the regular grid is larger than the budget, v1 preserves the entire measured first-five-second 2fps window, the last scheduled grid point, then deterministically selects farthest-apart regular points (stable timestamp and SHA ties), while allocating no more than one quarter of remaining capacity to actual GA **scene** and **OCR** timing hints. Off-grid adaptive hints count toward the same 60 slots. Scene observations are chapter/shot hints only; they are **not** assumed semantic story transitions or actual verified edit cuts. OCR hints are only allowed if their original referenced frame is present on the same media with matching absolute time. Optional hint flags can disable scene/OCR-adaptive sampling but cannot change the frozen 2/1 fps cadence.

## 2. Original A proof, never synthetic frames

Each selected video target is matched to the *nearest unused* `sampled_frame` from the same currently SHA-verified stage06 media manifest, using at most **250 ms** early (<5s) or **500 ms** later tolerances; ties resolve deterministically by measured time/ref SHA. A matched slot keeps **both** requested target and actual observed frame timestamp, plus media asset ID/original SHA and original sampled frame SHA reference. A requested slot with no real existing frame is `missing`, `matched_ref_sha256=null`, **not** a generated frame. One A frame cannot fill two slots. Frames outside measured original duration, non-absolute timebases, duplicate refs or mismatched asset bytes are rejected before scheduling.

`MediaEvidenceManifest.verify_digest()` pins the stage06 actual-media-byte hashes, QDP/GA result/source-policy version and source observations. The plan revalidates all original observed video times/assets/IDs against that digest. It does not itself reopen a media file; the caller must recheck current QDP, GA rights and bytes via GB07/06 at point of use. The `verify_b3_plan_against_manifest()` function independently recomputes the plan to reject a forged timestamp/ref even if a malicious caller correctly recalculated a plan's own canonical hash.

An identical manifest and policy yields identical plan SHA; a changed policy, source-ref order, project, media SHA or GA version yields a different SHA or outright failure. A plan digest is a **content checksum, not an origin signature or media-delivery receipt**.

## 3. Photo and mixed carousel

Photo/mixed carousels keep original **1-based slide ordinals** and never invent millisecond positions for image slides. Every original media asset must have a unique slide index and exact asset SHA, with contiguous ordinals 1..N. Each planned slide selects only its own verified `slide_ref` if it is an image; mixed-carousel **video** slide may use its own `sampled_frame` explicitly carrying that same slide index, asset ID and SHA. No frame from the next slide can replace a missing one. A missing slide proof is `missing`. More than 60 slides preserve all ordinals in the plan but mark nonselected slots `omitted_budget`; full source coverage is false, not silently collapsed to 60 slides. A mixed carousel requires both video and image media kinds. **`slide_index` is not a video timecode.**

## 4. SQL/output and GB16 integration boundaries

`B3SamplingPlan.payload` includes version, exact project/run/QDP/GA/media manifest and policy versions, content kind, every requested target slot, original matched SHA or explicit missing status, coverage counts (total, selected, matched, missing, omitted), duration truncation, unselected grid targets, and **`analysis_state=planned_not_inferred` / `role_labels_assigned=false`**. `sampling_db_fields()` returns exactly the six original `content_b_story_sampling` metadata columns with numerical SQL rates as Decimal and audit details under `sampling_notes`.

No role is assigned or row persisted to `content_b_story_frames`, `content_b_story_intervals`, or `content_b_story_slides` in this stage. Those rows require one of the original **26 story role enums**, proper `before_after_phase`, `boundary_quality`, and `needs_review` rules; **GB-MOD-16** implements the semantic labelling/validation layer. In particular, original A `scene` cuts must never become B3 semantic story intervals by default. The plan's `matched_refs` are merely pointers to real available A images; even these **were not sent to any model**. GB09 requires separately SHA-verified image bytes and future paid execution and delivery receipts before it can infer visually seen roles.

## 5. Tests, deployment and current blockers

`tests/test_group_b_story_sampling.py` is an offline synthetic A-fixture suite:
- <5 seconds and first5 boundary timing (0..4500 at 2fps, 5000+ at 1fps), 200 seconds and >200-second truncation, 60-target cap, dropped 2/1 fps grid points and their effect on `coverage.complete`.
- Absent frames, negative/out-of-duration timestamps, source video absolute time only, 250/500ms matching tolerance, duplicate and untrustworthy media refs, one-frame-one-slot.
- GA `scene`/OCR timing hints, OCR real referenced frame, stable determinism under shuffled observation order, adaptive budget without >60 overflow.
- Photo/carousel missing slides, exact original slide ordinal, mixed video+image carousel, 61-slide cap with omitted-budget status and no fake timestamps.
- Independent plan recomputation detects forged slot with fresh valid self SHA; no generated roles, HTTP, paid calls or DB writes.

Run:
```bash
python -m unittest discover -s tests -p 'test_group_b_story_sampling.py' -v
python -m unittest discover -s tests -p 'test_group_b_context.py' -v
```

CI wired into Windows/Linux and PG17 Group B jobs. **Committed source/tests are not confirmed PASS until actual CI finishes.** Real upstream Group A sampled frame producer, video/FFmpeg byte decoding, QDP/GA integration, model image delivery and full B3 semantic result E2E remain **NOT RUN / BLOCKED_EXTERNAL**. No paid OpenRouter route was enabled and the frozen `005` schema remains unmodified.

**Next:** `GB-MOD-16` — B3 Story Semantics & Patterns using exactly the 26 frozen role enums, true frame/slide evidence and needs_review/partial semantics.
