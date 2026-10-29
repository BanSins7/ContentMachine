# QDP-FIX-09 — media readiness handoff to Group A and Group B

**Scope:** QDP-owned read-only eligibility for existing D1 media assets, not media acquisition, downloading, transcription, FFmpeg or downstream analysis.

## Verified real repository contracts

D1 `media_assets`: `id, video_id, source_id, media_kind, local_path, sha256, permission, expires_at, created_at`. The record must already exist. QDP never manufactures a video asset, a path or its SHA.

Group A `feature/group-a-full-module` requires `media_kind='video'`, canonical publication kind `video` or `slideshow_video`, exact source/video/policy, current rights and an authorized local file. Group A rechecks these and performs FFprobe/FFmpeg separately; QDP does not claim a media stream has been decoded.

Group B `feature/group-b-full-module` distinguishes `video` and `carousel`. One D1 `media_assets` row cannot establish ordered carousel slides, so QDP deliberately blocks photo/mixed carousel handoff until an independently validated slide manifest integration exists.

## Implemented QDP adapter

`intelligence.qualified_data.media_readiness.read_verified_media(conn,metadata)` reads at most 16 source/video-specific media candidates in descending creation order; `verify_media_candidates(rows,metadata,roots=...,now=...)` validates the latest one, **never silently falling back** to another source, older revoked asset or an inaccessible asset.

Checks: current source and policy `can_analyze_media`, non-metadata-only permission, expiry, exact media kind, absolute `local_path` within permitted root, symlink resolution, nonempty file at most 2 GiB, allowed `.mp4/.mov/.mkv/.webm` container extension, basic ISO BMFF/EBML header, canonical D1 SHA-256 matching the bytes and unchanged file stat.

The media root must be configured as `CM_QDP_MEDIA_ROOTS` (multiple paths separated by OS path separator) or `CM_GROUP_A_MEDIA_ROOTS`. If no root exists, **media is not ready**. A successful QDP check confirms a currently present, hash-matching container candidate, not codec/playback validity. The consumer must revalidate on its own host.

**Do not persist local_path** in QDP handoff or expose it in QDP public responses. Immutable QDP input contains only `media_asset_id`, `media_sha256`, `media_kind`, `media_size_bytes` plus previously pinned SRE/D1 IDs and current rights.

## Status and isolation

- `video` and `slideshow_video`: Group A/B may each be ready with one verified local D1 video asset.
- `photo_carousel`, `mixed_carousel`: Group A blocked `video_a_carousel_unsupported`; Group B blocked `media_manifest_required` until real slides are verified.
- `image_post`: video A unsupported, current B adapter unsupported.
- Unknown or NULL content kind: blocked `content_kind_unverified`.
- Missing, foreign, expired, metadata-only, outside-root, bad-container, bad-hash and missing-file cases yield explicit blocked reason codes, not fake readiness.
- **Group C is independent:** no media permission or file requirement can change a valid Group C metric pair, and missing C data does not authorize A/B.

## QA evidence

New tests: `tests/test_qdp_media_readiness.py` (15 unit cases), `tests/test_qualified_data_db.py` (4 PostgreSQL cases), integrated into `scripts/qdp_verify.py`.

[GitHub Actions 40312315651](https://github.com/BanSins7/ContentMachine/actions/runs/40312315651) on **exact code SHA `0ed73d76df65cffcc7b16ea965da417f3baed5fc`** passed PostgreSQL 17, **154/154 tests**, zero failed/errored/skipped, Python syntax PASS, JS syntax PASS. Earlier code-only run 40312069484 failed one obsolete expected reason in a test and is not misrepresented as green.

Unit tests use tiny synthetic container-header files to exercise authorization and hashing, **not real playable videos**. Full FFprobe validation and cross-branch Group A/B E2E remain external tasks.

**No new SQL migration** was necessary and no peer branch or `main` was changed.
