# GA-MOD-17 — Mixed Carousel & A003 Video Slides

**Stage:** IMPLEMENTED in `feature/group-a-full-module`; verification independent. Python/PG17/Windows runtime QA **NOT VERIFIED** until actual logs exist. The dependency on pre-existing trusted A003 video slide analyses is intentional; GA18 produces cross-stream committed snapshots. No main/other branch changes.

## Input/authority

- Only the signed-by-canonical-SHA (not cryptographically signed) `ga-media-manifest/v1` from GA05 is accepted, with **mixed_carousel** and at least one image and one video. The ordered manifest (not media IDs, URL order or provider metadata) defines 1-based slide order. Every slide references a GA05 opened byte-verifying local file descriptor, exactly the stored media SHA, and a live project/QDP/run/source-policy/asset rights check **both before and after** extraction.
- Image steps decode with GA16's original-pixel Pillow/EXIF and GA12 OCR, with per-slide measured aspect ratio and `duration_sec=NULL`, `video_a_analysis_id=NULL`. No fabricated autoplay or human swipe interval.
- Video steps **reopen GA05 descriptor and verify the complete file SHA**; GA17 never reads a raw URL or trusts only a DB metadata blob. Every video requires an **explicit** `media_asset_id → video_feature_a_analyses.id` selection via `PinnedA003SlideReader(conn, analysis_ids=...)`. No arbitrary latest-analysis lookup. If no A003 analysis was created, the selector must explicitly map its asset ID to `None`; the carousel result is **partial**, never fabricated complete.
- Reader requires A003 publication, exact video asset ID, source, current source policy, SHA and media permissions to match the manifest. A003 `measurement_methods.ga_asset_sha256` **must exactly match** the original GA05 video asset SHA. This is an integration requirement for the producer of new A003 measurements; legacy unpinned A003 rows are **not reusable** by GA17. A003 must have a genuine positive duration and pixel aspect ratio to support a `completed` video slide. A003 writes or backfilling this evidence belong to GA18, not this stage.

## Per-video clocks and evidence

- The read-only A003 adapter retrieves bounded scene/speech/screen-text/audio segment timestamps. Every interval must satisfy `0 <= start_sec < end_sec <= that video asset's A003 duration_sec`, with exact three-decimal second strings and no nonfinite values. Times are **relative to each video slide's media start**, not to a composite carousel duration or global playback clock. Timeline data is stored in immutable GA17 evidence alongside `video_binding_sha256`, allowing the publication writer to re-check the exact A003 snapshot.
- `ga-mixed-carousel/v1` strictly records slide order, asset and manifest hashes, nullable video A003 IDs for partials, optional video-only durations, null image durations, per-video observed windows, optional true GA09-derived audio presence and independent text-coverage state. It never serializes GA09 PCM or private file paths.
- A003 `audio_status` **is not audio presence**. For a publication-wide `has_audio=true` or `false`, the host supplies exact SHA-bound GA09 `DecodedAudio` objects for video assets; `true` if at least one verified video audio stream exists; `false` only if **every** video slide has independently proven absent audio streams; otherwise `unknown`. The corresponding `audio_status` is `complete/partial/unavailable` according to the number of authenticated audio probes.
- GA17 does not convert A003 timed screen OCR spans to a static-slide word count. As a result mixed `text_status` may be partial even if its overall A004 analysis state is `completed` (all required A003 video measurements present and all images OCR-complete). The A004 summary's `first_text_slide` and `total_screen_word_count` remain **NULL** unless an independently complete mixed text contract is added later; GA17 never fabricates them. CTA remains NULL because Group B owns interpretation.

## Opt-in service interface

```python
from intelligence.group_a import (
    GroupAService, PinnedA003SlideReader, OCRConfig
)

# manifest = catalog.read_verified_manifest(qdp_pointer)
# Existing A003 IDs are selected by trusted host/provenance, not guessed.
read_a003 = PinnedA003SlideReader(
    pg_conn, analysis_ids={
        video_asset_id_1: a003_analysis_id_1,
        video_asset_id_2: a003_analysis_id_2,
    }
)
ga = GroupAService()
evidence = ga.analyze_local_mixed_carousel(
    manifest, open_ref=catalog.open_verified_ref,
    verify_current=current_authorized_project_qdp_asset,
    read_a003=read_a003,
    backend=installed_sha_pinned_ocr_backend,
    config=OCRConfig(languages=("eng","rus")),
    # optional asset ID → GA09 DecodedAudio, never PCM via HTTP
    audio_proofs={},
)
a004_id = ga.save_mixed_carousel_a004(
    pg_conn,manifest,evidence,
    verify_current=current_authorized_project_qdp_asset,
    audio_proofs={},
)
```

Use a disposable DB for testing. There are **no automatic workers, HTTP endpoints, downloads, paid model calls or production activation** from GA17.

## A004 transaction, idempotency and revocation

- An explicit psycopg transaction checks and locks every current source, policy, publication and image/video `media_assets` row (the GA16 rights helper now compares each actual manifest media kind). The writer repeats live QDP/project/asset authorization before and after A004 mutation.
- It resolves every selected A003 ID with the exact reader **again inside the transaction**, locking A003 parent `FOR UPDATE` and existing scene/audio/speech/screen-text rows `FOR SHARE` to prevent concurrent inserts/updates while deriving the snapshot. It checks the exact digest, relative segment evidence, duration, aspect ratio, selected IDs and completed/partial states. New video A003 state or media SHA mismatch fails closed.
- Insert order is A004 pending parent → exact 1-based image/video child rows with `media_asset_id` and video-only `video_a_analysis_id`/duration → parent `partial` or `completed`. This honors existing 004 completed trigger and never changes A003 itself. Commit failures roll back the complete A004 analysis.
- The stable `ga17-<observation_sha256>` analysis key makes identical replays idempotent. Existing A004 rows and the independently current A003 bindings are compared before returning an earlier ID; conflicting replays are rejected. SHA-bound GA09 proof digests are retained in analysis JSONB metadata (not audio bytes).
- **Immutability caveat:** the legacy A003 schema permits later changes by convention, so the transaction protects the commit boundary, while long-term invalidation/revocation of old snapshots and cross-stream immutability are explicitly GA18/GA20 work. An A003 edit after publication makes a subsequent GA17 replay fail closed, but it does not automatically revoke an old published A004 row.

## Test matrix / truthful status

```bash
python -m compileall -q intelligence/group_a tests/test_group_a_mixed_carousel.py
python -m unittest discover -s tests -p 'test_group_a_mixed_carousel.py' -v
python -m unittest discover -s tests -p 'test_group_a_*.py' -v
# Disposable DB only — never production:
CM_TEST_PG_DSN=postgresql://postgres:password@localhost:5432/ga_ci \
  python -m unittest discover -s tests -p 'test_group_a_mixed_carousel.py' -v
```

Tests committed: image-video-image, two separately pinned video slides, explicit audio presence/absence and unknown, partial/missing A003, foreign/mismatched SHA/asset/source, bad local scene boundaries, malformed slide order, image durations, file mutation, forged aggregate/digest, strict JSON Schema, optionally real locally generated FFmpeg MP4. Conditional PG17 test exercises actual A003 scene rows and A004 completed summary, idempotent replay, immutable A004, changed A003 timeline and source revocation.

**No Python, real FFmpeg, PostgreSQL 17 or end-to-end PASS is claimed without execution logs.** Static source checks are not runtime tests. CI workflow: [Group A module](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml).

## Handshake for next stages

GA18 must produce A003 by the exact **video slide asset** and pin `measurement_methods.ga_asset_sha256`, authentic duration/ratio and immutable processing evidence; it must then atomically assemble the full A003/A004 Group A snapshot with quality/coverage. GA19 adds the stable Group B consumer pointer and resolves CTA circularity. GA20 handles durable task retry, revocation/invalidation, resource timeouts and stale result exposure. GA22–23 supply real RU/EN video+OCR/audio gold, Linux/Windows and PG17 E2E.
