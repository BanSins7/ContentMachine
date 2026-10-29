# Group A GA-MOD-16 — Image Post & Photo Carousel Extraction

**Implementation:** GA16 local opt-in extractor and PostgreSQL A004 writer. **QA:** implementation committed; Python/Pillow/Tesseract/PG17 GitHub runner and actual end-to-end test success are **NOT VERIFIED** (check current Actions run). **GA17 mixed carousel and GA18 full atomic snapshot are separate stages.**

## Contracts and source of truth

- Input is exactly `ga-media-manifest/v1` from GA05 `VerifiedMediaCatalog.read_verified_manifest`. Its ordered `items` are authoritatively selected by the host: slide 1 is manifest item 1. Asset filenames, IDs, URLs and discovery ranks **never** infer sequence.
- Only `image_post` (exactly 1 verified image) or `photo_carousel` (2–100 verified images) are supported. `mixed_carousel`, `video`, `slideshow_video`, animated GIF/multi-frame WebP, inferred remote links and non-image slides fail closed with stable sanitized error codes.
- The caller supplies `open_ref(ref)` that yields GA05 already-open, SHA-checked local file descriptors via `VerifiedMediaCatalog.open_verified_ref`. GA16 independently checks image byte-size and SHA-256 and decodes through Pillow. Byte budget 32 MiB per item; dimensions ≤16000 px per axis and ≤32 million pixels per item; respect EXIF orientation before OCR/ratio.
- `verify_current(scope)` must be the authoritative host callback: exact project, QDP run, publication, QDP input SHA, media manifest SHA, source, active source policy, asset ID + SHA. Called before reading and after OCR **for every slide**. A URL or digest by itself does not grant rights.
- OCR reuses GA12 `OCRConfig`, pinned engine `identity` and strict `_checked_frame` engine-normalization semantics on the full image rendered as local RGB PNG. Backend is optional and never downloaded or called remotely by this stage. Each slide gets independent OCR; text recognition remains an engine observation, not guaranteed human ground truth.
- Canonical output `ga-photo-carousel/v1`: ordered original IDs, media SHA, source dimensions, reduced ratio, image-only `duration_sec=null`, actual per-slide OCR, model/config provenance and `observation_sha256`. Schema: `intelligence/group_a/schemas/ga-photo-carousel-v1.json`.
- A measured empty slide has `on_screen_text=""` and `screen_word_count=0`, only after a successfully completed OCR pass for all required languages. Missing backend/errors/partial-language results use `text_status=unavailable/partial` and `screen_word_count=null`. `first_text_slide` and `total_screen_word_count` are **NULL** unless *every* slide OCR is complete. A partial positive OCR observation may carry text evidence but never a definitive total.
- Entire photo publication remains `has_audio=unknown`, `audio_status=not_run`, `cta_slide_index=NULL` and **never** invents speech, scene times or how long users swipe. These fields belong to different proven capabilities/stages.

## Explicit opt-in invocation

```python
from intelligence.group_a import GroupAService, OCRConfig, VerifiedMediaCatalog

# manifest has been returned by an authorized GA05 catalog on a permitted
# disposable/local instance, never a raw public URL. The host supplies a
# current QDP/run/source-policy/asset authorization callback.
ga = GroupAService()
evidence = ga.analyze_local_photo_carousel(
    manifest,
    open_ref=catalog.open_verified_ref,
    verify_current=check_current_rights,
    backend=installed_pinned_tesseract_or_none,
    config=OCRConfig(languages=("eng", "rus")),
)
# Only after explicit local verification and current DB permissions:
a004_id = ga.save_photo_carousel_a004(
    postgres_connection, manifest, evidence,
    verify_current=check_current_rights,
)
```

The workflow does **not** mount routes, background workers, or QDP host hooks. Use only authorized local assets and explicit method invocation. No paid provider, remote download or PSI model inference occurs.

## A004 transaction, replay and integrity

`persist_photo_carousel` validates manifest and observation digest/lineage and starts one psycopg3 transaction. It locks the source row (`status=enabled`, `can_analyze_media=true`), active permitted policy, publication kind, and every exact `media_assets` row (`analysis_allowed`, unexpired, source/video/kind/SHA equal to manifest). It inserts a `pending` A004 analysis with a stable `ga16-` observation-based key, inserts all numbered items, then transitions the parent to `partial` or `completed` *after* rows exist. The SQL A004 trigger validates complete slide order and enforces immutable completed rows. The callback rechecks QDP/source rights just before commit; any exception rolls back the entire transaction. Identical immutable observation replay returns the same analysis ID after comparing stored rows; conflicting same-key data is rejected.

This writer is specific to the A004 still-image feature: it **does not** claim GA18's multi-stream A003/A004+GA run snapshot transaction, does not create a `group-a-to-b/v1` handoff, and does not implement GA20 job retries. Do not start production automation until GA18/20/23 qualification.

## Reproducible QA

```bash
python -m compileall -q intelligence/group_a tests/test_group_a_carousel.py
python -m unittest -v tests.test_group_a_carousel
# Full existing Group A regression:
python -m unittest discover -s tests -p 'test_group_a_*.py' -v
# PostgreSQL 17: use disposable DB only; never point at production:
CM_TEST_PG_DSN=postgresql://postgres:password@localhost:5432/ga_ci \
  python -m unittest -v tests.test_group_a_carousel
```

The tests include actual PNG/JPEG (EXIF) bytes, three ordered photo slides, single empty-text image, Russian/English OCR contract fakes, absent/partial OCR, rights revocation, descriptor mutation, duplicate ordering, mixed-kind rejection, animated image rejection, forged digest/aggregation, optional strict JSON Schema and **conditional** real PG17 A004 summary/replay/source revocation. Fake OCR proves contract correctness, **not** production OCR accuracy. PG17 tests run only if disposable `CM_TEST_PG_DSN` is configured. There is no credible PASS report for those test executions until runner/local logs are available.

## Remaining gates

- **GA17:** handle mixed image/video slides and exact A003 video-slide IDs/durations without fake swipe timing.
- **GA18:** typed all-stream assembly, run/manifest snapshots and durable multi-stream cross-table commits.
- **GA20:** bounded process leases, cancellation, resource budgets and current revocation/invalidation at job level.
- **GA22/23:** licensed real OCR benchmark/gold corpus, Windows/Linux compatibility, throughput and reproducible CI/PG17 full-system E2E.
