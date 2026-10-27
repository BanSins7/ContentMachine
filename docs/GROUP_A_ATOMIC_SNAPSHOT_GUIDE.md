# GA-MOD-18 — Atomic A003/A004 Assembly, Coverage and Snapshot Contracts

**Branch:** `feature/group-a-full-module` only. **Implementation:** staged/committed; **QA:** GitHub static checks only unless actual Python/PG17/Windows/Linux logs are attached. **NOT production or merge ready.** Other modules, main and historical migrations 003/004 are not edited. New additive GA-owned migration: `056_group_a_immutable_video_snapshots.sql`.

## Exact scope

1. Technical `TechnicalMeasurement` from verified GA06 probe/scene detection is linked by `bind_technical(measurement, asset_sha=GA05_manifest_video_sha)`. The output envelope `ga18-technical-bound/v1` and its digest are integrity checks, not a replacement for trusted media/worker origin authentication.
2. `assemble_video_snapshot(...)` reads a **single GA05 video asset** from `video`, `slideshow_video` or `mixed_carousel`, rejecting foreign video IDs/SHA, cross-version stream schemas and noncanonical/beyond-video timestamps. It never consumes an image as video.
3. Stage-local raw observations must be **unmodified canonical-digest-pinned** `ga-speech-metrics/v1` (GA11), `ga-screen-text-tracking/v1` (GA13), `ga-subtitle-presence/v1` (GA14), `ga-audio-events/v1` (GA15). A003 rows are derived only from each extractor's explicit A003 projections. Scenes must completely cover the actual video duration. Duplicate or out-of-order segments are rejected. The sparse GA13 `a003_screen_text_projection=[]` creates **no fabricated continuous OCR intervals**. Subtitle `false` requires a separate GA14 dense-negative review, never sparse absence.
4. Independent `scene/speech/screen_text/audio` statuses and verified coverage are recorded with source stream digests, reason codes, `measured_at` (UTC), QDP run SHA, source/policy/media IDs and bytes SHA. `spoken_words`, `speech_duration_sec`, summary `first_speech_sec`, WPM, coverage come only from authenticated A003 measured rows and existing SQL views; unknown values remain `NULL`. Group B CTA and carousel-wide swipe times remain `NULL`.
5. GA11/13/15 currently mark stream evidence partial or unavailable by default. **A003 `completed` is forbidden unless each stream has an externally independently authorized full-asset coverage certificate AND subtitle presence has an authenticated true/false proof.** A certificate is `ga-stream-coverage/v1`, carrying exact stream, video SHA, stream observation SHA, `full_video_coverage:true`, full-asset scope and certificate digest. The trusted host verifier `verify_coverage(cert) is True` MUST validate actual independent review/coverage authority; a SHA alone is not proof. Without these certificates, partial A003 is the correct result even if every stage produced useful partial data.
6. `persist_group_a_snapshot(conn,manifest,video_snapshots=[...],prepared_carousel=...)` is an **explicit opt-in psycopg transaction**. All external OCR, ffmpeg, ASR and image decode happens *before* acquiring SQL locks. The writer **requires** `verify_current(scope)` (live project/QDP/run/policy/source/asset rights), `verify_origin(snapshot)` (trusted origin, current verified media bytes and extractor provenance) and `verify_coverage` whenever full-coverage claims exist. No permissive production default for those callbacks.
7. Transaction holds real current source/policy/publication/media SQL locks and verifies exact media SHA, permission `analysis_allowed`, kind and expiration. A003 rows are written as pending parent → ordered scene/speech/text/audio child rows → partial/completed parent. `measurement_methods.ga_asset_sha256` is always set, allowing the existing GA17 exact-asset `PinnedA003SlideReader` to consume new video analyses. The writer rejects conflicts from same stable `ga18-<snapshot_sha256>` key, checks ALL persisted row values on replay, rechecks rights and origin before commit, and rolls back the complete transaction on failure.
8. For mixed carousels, extract an **ordinary GA17 mixed OCR observation beforehand**, even if its A003 bindings are initially explicitly absent. In a *single* DB transaction GA18 writes every required video slide's A003, rebinds only the already-prepared mixed metadata to those new exact IDs (no video/image processing in the DB), rechecks source rights and then uses the existing GA17 A004 writer. Thus A003 and linked A004 succeed or roll back together. Mixed slides can be completed while their global text status is partial — absent continuous video-wide text and unknown carousel swipe time are never manufactured. For photo/image posts GA18 can include the existing GA16 observation and its A004 writer in the same transaction.
9. `056_group_a_immutable_video_snapshots.sql` adds **version-scoped** A003 triggers preventing updates to completed GA18 A003 analysis facts and INSERT/UPDATE/DELETE of their scene/speech/text/audio rows. It does not restrict legacy 003 writers. Only safe retention FK nulling is allowed on a completed parent; historical checkpoint remains in `measurement_methods`. A separate `ga18_video_snapshot_receipts` view exposes IDs, SHA, verified-coverage receipts and reasons, leaving `video_feature_a_summary` and `carousel_feature_a_summary` unchanged. Partial GA18 analyses are versioned and the writer never mutates an old partial analysis for retry.
10. This does NOT solve GA20 global revocation/invalidation or full cross-project current-snapshot selection; historical rows remain for audit and must not be advertised as currently authorized solely because they exist. GA19 defines the Group B consumer. GA23 is responsible for full production E2E and model gold accuracy.

## API and integration

```python
from intelligence.group_a import (
    GroupAService, PinnedA003SlideReader, bind_technical
)

service = GroupAService()
manifest = catalog.read_verified_manifest(authorized_qdp_pointer)  # trusted host
video_item = next(x for x in manifest["items"] if x["kind"] == "video")

# All extractor calls happen BEFORE opening a DB transaction.
technical = bind_technical(
    ga06_technical_measurement,
    asset_sha=video_item["sha256"]
)
snapshot = service.assemble_local_video_snapshot(
    manifest,
    media_asset_id=video_item["media_asset_id"],
    technical=technical,
    speech=verified_ga11_evidence,             # optional
    screen_text=verified_ga13_evidence,        # optional
    subtitles=verified_ga14_evidence,          # optional
    audio_events=verified_ga15_evidence,       # optional
    certificates={},                          # partial unless separately certified
    verify_current=host_check_live_qdp_and_asset,
)

# One atomic A003 transaction; for a mixed carousel add every video
# snapshot and a *precomputed* GA17 carousel observation.
receipt = service.save_atomic_group_a_snapshot(
    conn, manifest,
    video_snapshots=[snapshot],
    # prepared_carousel=ga17_image_ocr_prepared_before_transaction,
    verify_current=host_check_live_qdp_and_asset,
    verify_origin=host_confirm_worker_and_bytes_sha,
    # verify_coverage=host_verify_independent_full_coverage,
)
```

The writer never calls models, imports external QDP implementation internals or alters other module tables outside A003/A004. The host owns trusted media descriptor continuity, authoritative QDP rights and actual reviewer credentials; sample callback names are interfaces, not installed defaults.

## Adversarial and PG17 tests

```bash
python -m compileall -q intelligence/group_a tests/test_group_a_atomic_snapshot.py
python -m unittest discover -s tests -p 'test_group_a_atomic_snapshot.py' -v
python -m unittest discover -s tests -p 'test_group_a_*.py' -v

# Disposable PG17 database ONLY (never production):
CM_TEST_PG_DSN=postgresql://postgres:password@localhost:5432/ga_ci \
 python -m unittest discover -s tests -p 'test_group_a_atomic_snapshot.py' -v
```

Committed tests cover pure verified technical-only partial, full video with **mock independent verifier** (not real human gold), stage-local ASR/sparse text/audio partial, false subtitles review, mismatched source SHA and time, scene coverage gap, forged completed state and digest, missing certificate, unauthorized review, duplicate inputs, revoked rights. Conditional PG17 tests exercise original A003 `video_feature_a_summary` exact scene count/WPM/speech coverage/NULL first text, real DB A003 completed immutability and child guards, idempotent replay, origin revocation rollback after insert, technical-only partial, plus a mixed **image → video → image** A003+A004 single transaction and linked summary.

**Status distinction:** Source/SQL/static checks do not prove the Python suite passes or that the migration runs on real PG17. Pending tests must remain explicitly marked **NOT VERIFIED** if no runtime logs are observed. Human QA model accuracy (RU/EN ASR/OCR, music/SFX, dense subtitle review) and Windows/Linux media pipelines are not claimed. Do not promote to main until stage GA23/24 acceptance gates.
